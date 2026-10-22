"""`log-tracer inspect` end to end (SPEC §3, §5.1, REP-6)."""

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner, Result

from log_tracer.cli import app

ROOT = Path(__file__).resolve().parents[2]
runner = CliRunner()

REQ_184 = [
    "Sequence 4bf92f3577b34da6 · 6 events · 4 services · 2.790 s",
    "request_id req-184 · trace_id 4bf92f3577b34da6",
    "Order follows each service's own timestamps; clocks on different hosts may differ.",
    "1. 14:03:22.120Z      +0 ms  INFO     api-gateway: POST /checkout",
    "examples/logs/sample.log:2",
    "2. 14:03:22.135Z     +15 ms  DEBUG    cart: Cart loaded with 3 items",
    "examples/logs/sample.log:3",
    "3. 14:03:22.180Z     +60 ms  INFO     inventory: Reserving stock for 3 items",
    "examples/logs/sample.jsonl:1 · duration 41 ms",
    "4. 14:03:22.410Z    +290 ms  WARNING  payments: Payment provider slow, retrying",
    "examples/logs/sample.log:4 · attempt=1",
    "5. 14:03:24.902Z   +2.782 s  ERROR    payments: Payment gateway timeout after 2500 ms",
    "examples/logs/sample.log:5 · duration 2500 ms",
    "6. 14:03:24.910Z   +2.790 s  ERROR    api-gateway: POST /checkout failed",
    "examples/logs/sample.log:6 · duration 2790 ms · status=502",
]


def invoke(*args: str) -> Result:
    return runner.invoke(app, ["inspect", *args], env={"COLUMNS": "160"})


def text_lines(result: Result) -> list[str]:
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


@pytest.fixture
def in_repo(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(ROOT)


@pytest.fixture
def in_tmp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.mark.usefixtures("in_repo")
def test_timeline_of_a_request_across_text_and_json_lines() -> None:
    result = invoke("req-184", "examples/logs")
    assert result.exit_code == 0, result.stderr
    assert result.stderr == ""
    assert "\x1b" not in result.stdout
    assert text_lines(result) == REQ_184


@pytest.mark.usefixtures("in_repo")
@pytest.mark.parametrize("query", ["4bf92f3577b34da6", "  req-184  "])
def test_trace_id_and_padded_ids_find_the_same_sequence(query: str) -> None:
    result = invoke(query, "examples/logs")
    assert result.exit_code == 0, result.stderr
    assert text_lines(result) == REQ_184


@pytest.mark.usefixtures("in_repo")
def test_json_document() -> None:
    result = invoke("req-184", "examples/logs", "--format", "json")
    assert result.exit_code == 0, result.stderr
    data: dict[str, Any] = json.loads(result.stdout)
    assert data["query"] == "req-184"
    (sequence,) = data["sequences"]
    assert sequence["label"] == "4bf92f3577b34da6"
    assert sequence["services"] == ["api-gateway", "cart", "inventory", "payments"]
    assert sequence["event_count"] == 6
    assert [(e["path"], e["line"]) for e in sequence["events"]] == [
        ("examples/logs/sample.log", 2),
        ("examples/logs/sample.log", 3),
        ("examples/logs/sample.jsonl", 1),
        ("examples/logs/sample.log", 4),
        ("examples/logs/sample.log", 5),
        ("examples/logs/sample.log", 6),
    ]
    assert sequence["events"][5]["attributes"] == {"status": "502"}


@pytest.mark.usefixtures("in_repo")
@pytest.mark.parametrize("query", ["req-999", "REQ-184", "req-18"])
def test_unknown_identifier_exits_with_code_4(query: str) -> None:
    result = invoke(query, "examples/logs")
    assert result.exit_code == 4
    assert result.stdout == ""
    assert result.stderr == (
        f"Error: no event has request_id or trace_id '{query}'\n"
        "Hint: identifiers are matched exactly, including upper and lower case\n"
    )


@pytest.mark.usefixtures("in_repo")
@pytest.mark.parametrize("query", ["-", "null", "0", "   "])
def test_identifiers_that_mean_absent_are_a_usage_error(query: str) -> None:
    result = invoke(query, "examples/logs")
    assert result.exit_code == 2
    assert "means 'no identifier'" in result.stderr


def test_without_paths_and_without_a_saved_analysis(in_tmp: Path) -> None:  # STA-3
    result = invoke("req-184")
    assert result.exit_code == 3
    assert result.stderr.startswith("Error: no saved analysis in ")


def test_missing_path_exits_with_code_3(in_tmp: Path) -> None:
    result = invoke("req-184", "nope.log")
    assert result.exit_code == 3
    assert result.stderr == "Error: path not found: nope.log\n"


def test_input_format_and_warnings(in_tmp: Path) -> None:
    (in_tmp / "app.log").write_text(
        '{"ts": "2026-10-16T14:00:00Z", "level": "error", "msg": "boom", "requestId": "r1"}\n',
        encoding="utf-8",
    )
    default = invoke("r1", "app.log")
    assert default.exit_code == 4
    assert default.stderr.startswith("Warning: 100% of the non-blank lines in app.log")
    forced = invoke("r1", "app.log", "--input-format", "jsonl")
    assert forced.exit_code == 0, forced.stderr
    assert text_lines(forced)[0] == "Sequence r1 · 1 event · 0 services"
