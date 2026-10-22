"""`log-tracer top-errors` end to end (SPEC §5.1, RUL-1)."""

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner, Result

from log_tracer.cli import app

ROOT = Path(__file__).resolve().parents[2]
runner = CliRunner()


def invoke(*args: str) -> Result:
    return runner.invoke(app, ["top-errors", *args], env={"COLUMNS": "180"})


def as_json(result: Result) -> dict[str, Any]:
    assert result.exit_code == 0, result.stderr
    data: dict[str, Any] = json.loads(result.stdout)
    return data


@pytest.fixture
def in_repo(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(ROOT)


@pytest.fixture
def in_tmp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.mark.usefixtures("in_repo")
def test_list_of_the_examples() -> None:
    result = invoke("examples/logs")
    assert result.exit_code == 0, result.stderr
    assert result.stderr == ""
    assert "\x1b" not in result.stdout
    assert [line.strip() for line in result.stdout.splitlines() if line.strip()] == [
        "Top errors in 2 files (34 events)",
        "1. 3x api-gateway: POST /checkout failed",
        "first 14:03:24.910Z · last 14:05:23.897Z · example examples/logs/sample.log:6",
        "2. 3x inventory: Stock sync with warehouse failed: HTTP <num>",
        "first 14:04:20.000Z · last 14:04:50.000Z · example examples/logs/sample.jsonl:5",
        "3. 3x payments: Payment gateway timeout after <num> ms",
        "first 14:03:24.902Z · last 14:05:23.891Z · example examples/logs/sample.log:5",
        "4. 2x scheduler: Job cleanup-sessions failed: connection refused <ip>",
        "first 14:04:02.003Z · last 14:06:02.011Z · example examples/logs/sample.log:14",
    ]


@pytest.mark.usefixtures("in_repo")
def test_limit_reduces_the_list() -> None:
    lines = invoke("examples/logs", "--limit", "2").stdout.splitlines()
    assert [line.split(".")[0] for line in lines if line[:1].isdigit()] == ["1", "2"]


@pytest.mark.usefixtures("in_repo")
def test_json_document() -> None:
    data = as_json(invoke("examples/logs", "--format", "json"))
    assert list(data) == [
        "schema_version",
        "tool",
        "generated_at",
        "inputs",
        "skipped_files",
        "top_errors",
    ]
    assert [(g["service"], g["count"]) for g in data["top_errors"]] == [
        ("api-gateway", 3),
        ("inventory", 3),
        ("payments", 3),
        ("scheduler", 2),
    ]


def test_limit_can_go_beyond_the_report_default(in_tmp: Path) -> None:
    words = [
        "alpha",
        "beta",
        "gamma",
        "delta",
        "epsilon",
        "zeta",
        "eta",
        "theta",
        "iota",
        "kappa",
        "lambda",
        "mu",
    ]
    lines = [f"2026-10-16T14:00:00Z ERROR [api] failure {word}\n" for word in words]
    (in_tmp / "app.log").write_text("".join(lines), encoding="utf-8")
    assert len(as_json(invoke("app.log", "--format", "json"))["top_errors"]) == 10
    assert len(as_json(invoke("app.log", "--format", "json", "--limit", "12"))["top_errors"]) == 12


def test_limit_must_be_positive(in_tmp: Path) -> None:
    (in_tmp / "app.log").write_text("", encoding="utf-8")
    assert invoke("app.log", "--limit", "0").exit_code == 2


def test_file_without_errors(in_tmp: Path) -> None:
    (in_tmp / "app.log").write_text("2026-10-16T14:00:00Z INFO [api] fine\n", encoding="utf-8")
    result = invoke("app.log")
    assert result.exit_code == 0
    assert result.stdout == "No ERROR or CRITICAL events found in 1 file (1 event).\n"


def test_missing_path_exits_with_code_3(in_tmp: Path) -> None:
    result = invoke("nope.log")
    assert result.exit_code == 3
    assert result.stderr == "Error: path not found: nope.log\n"


def test_input_format_and_warnings(in_tmp: Path) -> None:
    (in_tmp / "app.log").write_text(
        '{"ts": "2026-10-16T14:00:00Z", "level": "error", "msg": "boom"}\n', encoding="utf-8"
    )
    default = invoke("app.log", "--format", "json")
    assert default.stderr.startswith("Warning: 100% of the non-blank lines in app.log")
    assert as_json(default)["top_errors"] == []
    forced = as_json(invoke("app.log", "--format", "json", "--input-format", "jsonl"))
    assert [g["fingerprint"] for g in forced["top_errors"]] == ["boom"]
