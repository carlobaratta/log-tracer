"""--service, --since and --until end to end (SPEC §5.2, D-9, D-18…D-21)."""

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner, Result

from log_tracer.cli import app

ROOT = Path(__file__).resolve().parents[2]
runner = CliRunner()

LOG = (
    "2026-10-16T14:00:00Z ERROR [payments] Payment timeout after 2500 ms\n"
    "2026-10-16T14:00:10Z ERROR [payments] Payment timeout after 2610 ms\n"
    "2026-10-16T14:00:20Z ERROR [payments] Payment timeout after 2400 ms\n"
    "2026-10-16T14:00:30Z ERROR [api] Upstream failed\n"
    "2026-10-16T14:01:00Z INFO [api] recovered\n"
)
NO_TIMESTAMP = '{"level": "error", "service": "payments", "message": "lost"}\n'


def invoke(*args: str) -> Result:
    return runner.invoke(app, list(args), env={"COLUMNS": "160"})


def json_of(result: Result) -> dict[str, Any]:
    assert result.exit_code == 0, result.stderr
    data: dict[str, Any] = json.loads(result.stdout)
    return data


@pytest.fixture
def in_tmp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "app.log").write_text(LOG, encoding="utf-8")
    (tmp_path / "extra.jsonl").write_text(NO_TIMESTAMP, encoding="utf-8")
    return tmp_path


def rules_of(report: dict[str, Any]) -> list[str]:
    return [finding["rule"] for finding in report["findings"]]


# --- Scope of the analysis (D-9) ------------------------------------------------------------


@pytest.mark.usefixtures("in_tmp")
def test_without_filters_the_filters_section_is_empty() -> None:
    report = json_of(invoke("analyze", "app.log", "--format", "json"))
    assert report["filters"] == {
        "services": [],
        "since": None,
        "until": None,
        "excluded_events": 0,
        "excluded_without_timestamp": 0,
    }
    assert rules_of(report) == ["recurring_error"]


@pytest.mark.usefixtures("in_tmp")
def test_rules_see_only_the_filtered_events() -> None:  # D-9
    report = json_of(invoke("analyze", "app.log", "--service", "api", "--format", "json"))
    assert report["findings"] == []  # the payments errors are outside the scope
    assert [group["service"] for group in report["top_errors"]] == ["api"]
    assert report["summary"]["events"] == 2
    assert report["inputs"][0]["events"] == 5  # D-19: raw count
    assert report["filters"]["services"] == ["api"]
    assert report["filters"]["excluded_events"] == 3


@pytest.mark.usefixtures("in_tmp")
def test_repeated_service_options_are_sorted_and_deduplicated() -> None:  # NFR-2
    def report(*services: str) -> dict[str, Any]:
        options = [arg for name in services for arg in ("--service", name)]
        data = json_of(invoke("analyze", "app.log", *options, "--format", "json", "--no-save"))
        del data["generated_at"]
        return data

    first = report("payments", "api")
    assert first["filters"]["services"] == ["api", "payments"]
    assert report("api", "payments", "api") == first


@pytest.mark.usefixtures("in_tmp")
def test_time_bounds_and_events_without_timestamp() -> None:  # D-18
    report = json_of(
        invoke(
            "analyze",
            "app.log",
            "extra.jsonl",
            "--since",
            "2026-10-16T14:00:10Z",
            "--until",
            "2026-10-16T14:00:30Z",
            "--format",
            "json",
        )
    )
    assert report["filters"] == {
        "services": [],
        "since": "2026-10-16T14:00:10.000000Z",
        "until": "2026-10-16T14:00:30.000000Z",
        "excluded_events": 4,
        "excluded_without_timestamp": 1,
    }
    assert report["summary"]["events"] == 2  # 14:00:10 included, 14:00:30 excluded
    assert report["summary"]["time_range"] == {
        "start": "2026-10-16T14:00:10.000000Z",
        "end": "2026-10-16T14:00:20.000000Z",
    }


@pytest.mark.usefixtures("in_tmp")
def test_a_date_alone_and_a_naive_time_are_utc() -> None:  # D-20
    report = json_of(
        invoke(
            "analyze",
            "app.log",
            "--since",
            "2026-10-16",
            "--until",
            "2026-10-16 14:00:01",
            "--format",
            "json",
        )
    )
    assert report["filters"]["since"] == "2026-10-16T00:00:00.000000Z"
    assert report["filters"]["until"] == "2026-10-16T14:00:01.000000Z"
    assert report["summary"]["events"] == 1


@pytest.mark.usefixtures("in_tmp")
def test_text_summary_states_the_filters() -> None:
    result = invoke("analyze", "app.log", "--service", "api")
    assert result.exit_code == 0, result.stderr
    assert "Filters: service api · 3 events excluded" in result.stdout
    assert result.stdout.startswith("Log Tracer 0.1.0 · 1 file · 2 events · 0 unparsed lines")


@pytest.mark.usefixtures("in_tmp")
def test_unknown_service_is_a_warning() -> None:  # D-19
    result = invoke("analyze", "app.log", "--service", "Payments", "--format", "json")
    report = json_of(result)
    assert result.stderr == (
        "Warning: no event has service 'Payments' (service names are matched exactly)\n"
    )
    assert report["data_quality"]["warnings"] == [
        {
            "code": "unmatched_service_filter",
            "path": None,
            "message": "no event has service 'Payments' (service names are matched exactly)",
        }
    ]
    assert report["summary"]["events"] == 0


# --- top-errors and report ------------------------------------------------------------------


@pytest.mark.usefixtures("in_tmp")
def test_top_errors_with_filters() -> None:  # D-21
    data = json_of(
        invoke("top-errors", "app.log", "--since", "2026-10-16T14:00:25Z", "--format", "json")
    )
    assert list(data)[3] == "filters"
    assert data["filters"]["since"] == "2026-10-16T14:00:25.000000Z"
    assert [(g["service"], g["count"]) for g in data["top_errors"]] == [("api", 1)]
    text = invoke("top-errors", "app.log", "--service", "payments")
    assert text.stdout.splitlines()[:2] == [
        "Top errors in 1 file (3 events)",
        "Filters: service payments · 2 events excluded",
    ]


@pytest.mark.usefixtures("in_tmp")
def test_report_shows_the_saved_filters() -> None:
    analyzed = invoke("analyze", "app.log", "--service", "api", "--format", "json")
    assert invoke("report", "--format", "json").stdout == analyzed.stdout
    assert "Filters: service api" in invoke("report").stdout


def test_filters_on_the_examples(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(ROOT)
    report = json_of(
        invoke("analyze", "examples/logs", "--service", "payments", "--format", "json")
    )
    assert {g["service"] for g in report["top_errors"]} == {"payments"}
    assert sum(report["summary"]["levels"].values()) == report["summary"]["events"]
    all_events = sum(source["events"] for source in report["inputs"])
    assert report["summary"]["events"] + report["filters"]["excluded_events"] == all_events


# --- Usage errors (D-20) --------------------------------------------------------------------


@pytest.mark.usefixtures("in_tmp")
@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["--since", "yesterday"], "'yesterday' is not an ISO 8601 date or date-time"),
        (["--until", "2026-02-30"], "'2026-02-30' is not an ISO 8601 date or date-time"),
        (
            ["--since", "2026-10-16T15:00:00Z", "--until", "2026-10-16T14:00:00Z"],
            "--since must be earlier than --until",
        ),
        (
            ["--since", "2026-10-16T14:00:00Z", "--until", "2026-10-16T16:00:00+02:00"],
            "--since must be earlier than --until",
        ),
        (["--service", "  "], "a service name cannot be empty"),
    ],
    ids=["since", "until", "order", "equal", "empty-service"],
)
@pytest.mark.parametrize("command", ["analyze", "top-errors"])
def test_invalid_filters_are_usage_errors(command: str, args: list[str], message: str) -> None:
    result = invoke(command, "app.log", *args)
    assert result.exit_code == 2
    assert result.stdout == ""
    assert message in " ".join(result.stderr.split())  # Typer may wrap the box


def test_invalid_filters_are_checked_before_the_logs_are_read(tmp_path: Path) -> None:
    result = invoke("analyze", (tmp_path / "missing.log").as_posix(), "--since", "nope")
    assert result.exit_code == 2  # not 3: the option is checked first
