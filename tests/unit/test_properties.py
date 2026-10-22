"""Property-based tests: invariants that must hold for every input (NFR-2, NFR-7).

Example-based tests check the cases we thought of; these let Hypothesis search
for the ones we did not. Expected values are computed independently of the
code under test (from the generated data), never by calling it.
"""

import json
import re
from datetime import UTC, datetime
from pathlib import Path

import pytest
from hypothesis import assume, given
from hypothesis import strategies as st

from log_tracer.analysis.fingerprint import fingerprint
from log_tracer.ingestion.discovery import discover
from log_tracer.ingestion.jsonl_format import ALIASES, parse_jsonl_line
from log_tracer.ingestion.text_format import parse_text_line
from log_tracer.models import Event, Level, ParsedEvent, SourceRef, UnparsedLine

REF = SourceRef("app.log", 1)
ALIAS_KEYS = sorted({key for keys in ALIASES.values() for key in keys})
FIELD_TOKEN = re.compile(r"[a-z_][a-z0-9_.]*=[^ \t]+")

LEVEL_SPELLINGS = {
    "trace": Level.TRACE,
    "debug": Level.DEBUG,
    "info": Level.INFO,
    "warn": Level.WARNING,
    "warning": Level.WARNING,
    "err": Level.ERROR,
    "error": Level.ERROR,
    "crit": Level.CRITICAL,
    "critical": Level.CRITICAL,
    "fatal": Level.CRITICAL,
}

timestamps = st.datetimes(
    min_value=datetime(1970, 1, 1), max_value=datetime(2100, 1, 1), timezones=st.just(UTC)
)
no_newline = st.characters(exclude_characters="\n")
identifiers = st.from_regex(r"[A-Za-z0-9_-]{1,24}", fullmatch=True).filter(
    lambda s: s.lower() not in {"-", "null", "none"} and s.strip("0") != ""
)
# Strings that may contain lone surrogates once they go through json.loads.
surrogate_prone = st.lists(st.sampled_from(["a", " ", "é", "\U0001f600", "\ud800", "\udfff"])).map(
    "".join
)
json_values = st.recursive(
    st.none() | st.booleans() | st.integers() | st.floats() | st.text() | surrogate_prone,
    lambda children: (
        st.lists(children, max_size=4) | st.dictionaries(st.text(max_size=5), children, max_size=4)
    ),
    max_leaves=20,
)


def rfc3339(value: datetime) -> str:
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


def assert_utf8_safe(event: Event) -> None:
    texts = [event.message, event.raw_level, event.service, event.request_id, event.trace_id]
    texts += [*event.attributes.keys(), *event.attributes.values()]
    for text in texts:
        if text is not None:
            text.encode("utf-8")  # raises on lone surrogates


# --- Parsers never raise (NFR-7) ---------------------------------------------------------


@given(st.text())
def test_text_parser_accepts_any_string(line: str) -> None:
    assert isinstance(parse_text_line(line, REF), ParsedEvent | UnparsedLine)


@given(timestamps, st.sampled_from(["INFO", "ERROR", "NOTICE"]), st.text(no_newline))
def test_text_parser_accepts_any_tail_after_a_valid_header(
    timestamp: datetime, level: str, tail: str
) -> None:
    # A valid header makes the parser go past the first regular expression.
    outcome = parse_text_line(f"{rfc3339(timestamp)} {level} {tail}", REF)
    assert isinstance(outcome, ParsedEvent | UnparsedLine)


@given(st.text())
def test_jsonl_parser_accepts_any_string(line: str) -> None:
    assert isinstance(parse_jsonl_line(line, REF), ParsedEvent | UnparsedLine)


@given(st.dictionaries(st.sampled_from(ALIAS_KEYS) | st.text(max_size=8), json_values, max_size=10))
def test_jsonl_parser_accepts_any_object_and_returns_utf8_safe_events(
    obj: dict[str, object],
) -> None:
    outcome = parse_jsonl_line(json.dumps(obj), REF)
    assert isinstance(outcome, ParsedEvent | UnparsedLine)
    if isinstance(outcome, ParsedEvent):
        assert_utf8_safe(outcome.event)
        timestamp = outcome.event.timestamp
        assert timestamp is None or timestamp.tzinfo is UTC


# --- Fingerprint ------------------------------------------------------------------------------


@given(st.text())
def test_fingerprint_is_idempotent(message: str) -> None:
    once = fingerprint(message)
    assert fingerprint(once) == once


# --- Round trips: writing valid events and reading them back ----------------------------------


@st.composite
def text_events(draw: st.DrawFn) -> tuple[str, dict[str, object]]:
    timestamp = draw(timestamps)
    spelling = draw(st.sampled_from(sorted(LEVEL_SPELLINGS)))
    service = draw(st.none() | st.text(st.characters(exclude_characters="]\n"), min_size=1))
    if service is not None:
        service = service.strip()
        assume(service)
    message = draw(st.text(no_newline, min_size=1)).strip(" \t")
    assume(message)
    assume(not FIELD_TOKEN.fullmatch(re.split(r"[ \t]+", message)[-1]))
    if service is None:
        assume(not message.startswith("["))
    request_id = draw(identifiers)
    trace_id = draw(identifiers)
    duration = draw(st.integers(0, 10**7)) / 1000
    extra = draw(
        st.dictionaries(
            st.from_regex(r"[a-z_][a-z0-9_.]{0,10}", fullmatch=True).filter(
                lambda k: k not in {"request_id", "trace_id", "duration_ms"}
            ),
            st.from_regex(r"[^ \t\n]{1,10}", fullmatch=True),
            max_size=3,
        )
    )
    parts = [rfc3339(timestamp), spelling.upper()]
    if service is not None:
        parts.append(f"[{service}]")
    parts += [message, f"request_id={request_id}", f"trace_id={trace_id}"]
    parts += [f"duration_ms={duration!r}", *(f"{k}={v}" for k, v in extra.items())]
    expected = {
        "timestamp": timestamp,
        "level": LEVEL_SPELLINGS[spelling],
        "service": service,
        "message": message,
        "request_id": request_id,
        "trace_id": trace_id,
        "duration_ms": duration,
        "attributes": extra,
    }
    return " ".join(parts), expected


@given(text_events())
def test_text_events_round_trip(case: tuple[str, dict[str, object]]) -> None:
    line, expected = case
    outcome = parse_text_line(line, REF)
    assert isinstance(outcome, ParsedEvent), outcome
    event = outcome.event
    assert {
        "timestamp": event.timestamp,
        "level": event.level,
        "service": event.service,
        "message": event.message,
        "request_id": event.request_id,
        "trace_id": event.trace_id,
        "duration_ms": event.duration_ms,
        "attributes": dict(event.attributes),
    } == expected
    assert outcome.issues == ()


@st.composite
def json_events(draw: st.DrawFn) -> tuple[str, dict[str, object]]:
    spelling = draw(st.sampled_from(sorted(LEVEL_SPELLINGS)))
    spelling = draw(st.sampled_from([spelling, spelling.upper(), spelling.title()]))
    timestamp = draw(timestamps)
    service = draw(st.text(min_size=1).filter(lambda s: s.strip() == s and bool(s)))
    message = draw(st.text(min_size=1))
    duration = draw(
        st.integers(0, 10**12) | st.floats(0, 1e12, allow_nan=False, allow_infinity=False)
    )
    other_keys = st.text().filter(lambda k: k not in ALIAS_KEYS)
    extra = draw(st.dictionaries(other_keys, st.text(), max_size=3))
    obj = {
        "timestamp": rfc3339(timestamp),
        "level": spelling,
        "message": message,
        "service": service,
        "request_id": draw(identifiers),
        "trace_id": draw(identifiers),
        "duration_ms": duration,
        **extra,
    }
    expected = {
        "timestamp": timestamp,
        "level": LEVEL_SPELLINGS[spelling.lower()],
        "message": message,
        "service": service,
        "request_id": obj["request_id"],
        "trace_id": obj["trace_id"],
        "duration_ms": float(duration),
        "attributes": extra,
    }
    return json.dumps(obj), expected


@given(json_events())
def test_json_events_round_trip(case: tuple[str, dict[str, object]]) -> None:
    line, expected = case
    outcome = parse_jsonl_line(line, REF)
    assert isinstance(outcome, ParsedEvent), outcome
    event = outcome.event
    assert {
        "timestamp": event.timestamp,
        "level": event.level,
        "message": event.message,
        "service": event.service,
        "request_id": event.request_id,
        "trace_id": event.trace_id,
        "duration_ms": event.duration_ms,
        "attributes": dict(event.attributes),
    } == expected
    assert outcome.issues == ()


# --- Discovery order (NFR-2) ---------------------------------------------------------------


@pytest.fixture(scope="module")
def tree(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("tree")
    for name in ("a.log", "c.txt", "notes.md", "sub/b.jsonl", "sub/d.log"):
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text("", encoding="utf-8")
    return root


@given(order=st.permutations(["a.log", "sub", "c.txt", "sub/b.jsonl", "."]))
def test_discovery_does_not_depend_on_argument_order(tree: Path, order: list[str]) -> None:
    baseline = discover([tree / name for name in ["a.log", "sub", "c.txt", "sub/b.jsonl", "."]])
    assert discover([tree / name for name in order]) == baseline
