"""Deterministic generator of large, realistic logs (NFR-3, ARCHITECTURE §8.2, D-27).

It writes a text file and a JSON Lines file covering about an hour, with the
problems real logs have, in known proportions: unparsable lines, a date that
does not exist, an unknown level, timestamps without an offset, events
without a timestamp, a burst of errors and a request_id reused by more than
10 000 events (COR-6).

While writing, it keeps its own account of what it wrote (:class:`Expected`).
Tests compare the analysis with that account, never with values computed by
the code under test. The same ``lines`` and ``seed`` always give the same
bytes.

Run it to reproduce the performance measurement in docs/demo.md::

    uv run python -m tests.large_logs DIR --lines 100000
"""

import argparse
import json
import random
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Final

T0: Final = datetime(2026, 10, 16, 10, 0, tzinfo=UTC)
TEXT_STEP: Final = timedelta(milliseconds=50)
JSONL_STEP: Final = timedelta(milliseconds=200)
BURST_START: Final = T0 + timedelta(minutes=30)
"""Every text line in this minute is an error: the only spike (RUL-4)."""
TEXT_SHARE: Final = 0.8
"""Share of the lines written to the text file; the rest go to the JSON Lines file."""

TEXT_FILE: Final = "app.log"
JSONL_FILE: Final = "events.jsonl"

# Error templates and their fingerprints, masked by hand following SPEC §2.5.
BACKGROUND_ERRORS: Final = (
    ("payments", "Payment timeout after {n} ms", "Payment timeout after <num> ms"),
    ("orders-db", "Connection refused by 10.0.4.{n}:5432", "Connection refused by <ip>"),
    ("api-gateway", "Upstream returned HTTP 5{n:02d}", "Upstream returned HTTP <num>"),
)
BURST_ERROR: Final = ("scheduler", "Job export failed: worker {n} crashed")
BURST_FINGERPRINT: Final = "Job export failed: worker <num> crashed"
JSONL_ERROR: Final = ("inventory", "Stock sync failed: HTTP 503", "Stock sync failed: HTTP <num>")
REQUEST_SERVICES: Final = ("api-gateway", "payments", "cart")
HEALTH_ID: Final = "req-health"


@dataclass
class Latency:
    measured: int = 0
    slow: int = 0
    """Durations strictly above the default warn_ms of 1000 ms."""
    max_ms: float = 0.0


@dataclass
class Expected:
    """What the generator wrote, counted while writing."""

    lines: dict[str, int] = field(default_factory=dict)
    events: dict[str, int] = field(default_factory=dict)
    unparsed: Counter[str] = field(default_factory=Counter)
    levels: Counter[str] = field(default_factory=Counter)
    issues: Counter[str] = field(default_factory=Counter)
    error_groups: Counter[tuple[str, str]] = field(default_factory=Counter)
    latency: dict[str, Latency] = field(default_factory=dict)
    request_ids: set[str] = field(default_factory=set)
    uncorrelated: int = 0
    health_events: int = 0
    burst_errors: int = 0
    warned: set[str] = field(default_factory=set)
    """Requests with a payments warning."""
    precursors: int = 0
    """Payment errors whose request had a warning 10 lines earlier (RUL-3)."""

    def event(self, file: str, level: str, request_id: str | None = None) -> None:
        self.events[file] = self.events.get(file, 0) + 1
        self.levels[level] += 1
        if request_id is None:
            self.uncorrelated += 1
        else:
            self.request_ids.add(request_id)

    def duration(self, service: str, value: int) -> None:
        latency = self.latency.setdefault(service, Latency())
        latency.measured += 1
        latency.slow += value > 1000
        latency.max_ms = max(latency.max_ms, float(value))


def generate(directory: Path, lines: int, seed: int = 0) -> Expected:
    """Write ``lines`` lines in total into ``directory`` and return what was written."""
    rng = random.Random(seed)
    expected = Expected()
    text_lines = round(lines * TEXT_SHARE)
    directory.mkdir(parents=True, exist_ok=True)
    text = [_text_line(i, rng, expected) for i in range(text_lines)]
    jsonl = [_jsonl_line(j, rng, expected) for j in range(lines - text_lines)]
    # newline="": the same bytes on every system, without CRLF on Windows.
    (directory / TEXT_FILE).write_text("".join(text), encoding="utf-8", newline="")
    (directory / JSONL_FILE).write_text("".join(jsonl), encoding="utf-8", newline="")
    expected.lines = {TEXT_FILE: len(text), JSONL_FILE: len(jsonl)}
    return expected


def _stamp(value: datetime, *, naive: bool = False) -> str:
    if naive:  # SPEC §2.2: a space instead of T, a comma, no offset
        return f"{value:%Y-%m-%d %H:%M:%S},{value.microsecond // 1000:03d}"
    return f"{value:%Y-%m-%dT%H:%M:%S}.{value.microsecond // 1000:03d}Z"


def _text_line(i: int, rng: random.Random, expected: Expected) -> str:
    ts = T0 + i * TEXT_STEP
    extra = f"host=web-{rng.randint(1, 12):02d} region=eu-west-{rng.randint(1, 3)}"
    if i % 1000 == 999:
        expected.unparsed["no_match"] += 1
        return f"    at Worker.run (worker.js:{rng.randint(10, 400)})\n"
    if i % 1000 == 998:
        expected.unparsed["invalid_timestamp"] += 1
        return "2026-02-30T10:00:00.000Z ERROR [billing] Invoice run on a day that does not exist\n"
    if BURST_START <= ts < BURST_START + timedelta(minutes=1):
        service, template = BURST_ERROR
        expected.event(TEXT_FILE, "ERROR")
        expected.error_groups[service, BURST_FINGERPRINT] += 1
        expected.burst_errors += 1
        message = template.format(n=rng.randint(1, 64))
        return f"{_stamp(ts)} ERROR [{service}] {message} job_id=exp-{i // 100} {extra}\n"
    if i % 20 == 0:
        service, template, pattern = BACKGROUND_ERRORS[(i // 20) % len(BACKGROUND_ERRORS)]
        message = template.format(n=rng.randint(0, 99))
        expected.error_groups[service, pattern] += 1
        if service != "payments" or i < 10:
            expected.event(TEXT_FILE, "ERROR")
            return f"{_stamp(ts)} ERROR [{service}] {message} {extra}\n"
        # The payment fails in the request of the warning written 10 lines earlier.
        failed = f"req-{(i - 10) // 4}"
        expected.event(TEXT_FILE, "ERROR", failed)
        expected.precursors += failed in expected.warned
        return f"{_stamp(ts)} ERROR [{service}] {message} request_id={failed} {extra}\n"
    request_id = f"req-{i // 4}"
    if i % 20 == 10:
        expected.warned.add(request_id)
        expected.event(TEXT_FILE, "WARNING", request_id)
        return (
            f"{_stamp(ts)} WARN [payments] Payment provider slow, retrying "
            f"attempt={rng.randint(1, 3)} request_id={request_id} {extra}\n"
        )
    if i % 7 == 5:
        expected.event(TEXT_FILE, "DEBUG", HEALTH_ID)
        expected.health_events += 1
        return f"{_stamp(ts)} DEBUG [api-gateway] Health check ok request_id={HEALTH_ID}\n"
    if i % 500 == 1:
        expected.event(TEXT_FILE, "UNKNOWN")
        expected.issues["level.unknown"] += 1
        return f"{_stamp(ts)} NOTICE [orders-db] Autovacuum completed on table orders\n"
    service = REQUEST_SERVICES[i % len(REQUEST_SERVICES)]
    duration = rng.randint(1, 1500)
    naive = i % 700 == 3
    if naive:
        expected.issues["timestamp.assumed_utc"] += 1
    expected.event(TEXT_FILE, "INFO", request_id)
    expected.duration(service, duration)
    return (
        f"{_stamp(ts, naive=naive)} INFO [{service}] Handled POST /orders/{rng.randint(1, 99999)} "
        f"user=u{rng.randint(1, 5000)} request_id={request_id} duration_ms={duration} {extra}\n"
    )


def _jsonl_line(j: int, rng: random.Random, expected: Expected) -> str:
    ts = T0 + j * JSONL_STEP
    if j % 1000 == 999:
        expected.unparsed["invalid_json"] += 1
        return '{"timestamp": "' + _stamp(ts) + '", "level": "info", "message": "truncated\n'
    if j % 1000 == 998:
        expected.unparsed["not_an_object"] += 1
        return '["not", "an", "object"]\n'
    record: dict[str, object] = {"timestamp": _stamp(ts)}
    if j % 25 == 0:
        service, message, pattern = JSONL_ERROR
        record |= {"level": "error", "service": service, "message": message, "attempt": j % 5}
        expected.event(JSONL_FILE, "ERROR")
        expected.error_groups[service, pattern] += 1
        return json.dumps(record) + "\n"
    request_id = f"inv-{j // 3}"
    duration = rng.randint(1, 1200)
    record |= {
        "level": "info",
        "service": "inventory",
        "message": f"Reserved stock for {rng.randint(1, 9)} items",
        "requestId": request_id,
        "durationMs": duration,
        "sku": rng.randint(10000, 99999),
    }
    if j % 600 == 7:
        del record["timestamp"]
        expected.issues["timestamp.missing"] += 1
    expected.event(JSONL_FILE, "INFO", request_id)
    expected.duration("inventory", duration)
    return json.dumps(record) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Write large, deterministic example logs.")
    parser.add_argument("directory", type=Path)
    parser.add_argument("--lines", type=int, default=100_000)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()
    expected = generate(args.directory, args.lines, args.seed)
    size = sum(path.stat().st_size for path in args.directory.iterdir())
    events = sum(expected.events.values())
    print(
        f"wrote {args.lines} lines ({size / 1_000_000:.1f} MB, {events} events) in {args.directory}"
    )


if __name__ == "__main__":
    main()
