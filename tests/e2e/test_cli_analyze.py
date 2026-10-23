"""`log-tracer analyze` end to end: output contract and exit codes (SPEC §5)."""

import io
import json
import os
import re
import runpy
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner, Result

from log_tracer import __version__, cli
from log_tracer.cli import app

ROOT = Path(__file__).resolve().parents[2]
runner = CliRunner()


def invoke(*args: str) -> Result:
    return runner.invoke(app, list(args))


def report_of(result: Result) -> dict[str, Any]:
    assert result.exit_code == 0, result.stderr
    data: dict[str, Any] = json.loads(result.stdout)
    return data


def latency_row(finding: dict[str, Any]) -> tuple[object, ...]:
    evidence = finding["evidence"]
    return (
        evidence["service"],
        evidence["slow_events"],
        evidence["measured_events"],
        evidence["max_ms"],
        evidence["p95_ms"],
        finding["severity"],
    )


@pytest.fixture
def in_repo(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(ROOT)


@pytest.fixture
def in_tmp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    return tmp_path


# --- Successful analysis ------------------------------------------------------


@pytest.mark.usefixtures("in_repo")
def test_sample_log_produces_the_expected_report() -> None:
    result = invoke("analyze", "examples/logs/sample.log", "--format", "json")
    report = report_of(result)
    assert result.stderr == ""  # CLI-2: nothing on stderr when all is well

    (source,) = report["inputs"]
    assert source["path"] == "examples/logs/sample.log"
    assert (source["lines"], source["blank_lines"], source["events"], source["unparsed"]) == (
        27,
        1,
        25,
        1,
    )
    assert report["summary"]["levels"] == {
        "CRITICAL": 0,
        "ERROR": 8,
        "WARNING": 5,
        "INFO": 10,
        "DEBUG": 1,
        "TRACE": 0,
        "UNKNOWN": 1,
    }
    assert report["data_quality"]["field_issues"]["timestamp.assumed_utc"] == 1
    assert report["data_quality"]["field_issues"]["level.unknown"] == 1
    assert report["data_quality"]["unparsed_samples"] == [
        {
            "path": "examples/logs/sample.log",
            "line": 15,
            "reason": "no_match",
            "excerpt": "    at PgPool.connect (pool.js:88)",
        }
    ]

    findings = report["findings"]
    assert {
        (f["evidence"]["service"], f["evidence"]["fingerprint"], f["severity"])
        for f in findings
        if f["rule"] == "recurring_error"
    } == {
        ("payments", "Payment gateway timeout after <num> ms", "medium"),
        ("api-gateway", "POST /checkout failed", "medium"),
    }
    # RUL-2 with the default thresholds (1000 / 5000 ms), computed from the file by hand.
    assert {latency_row(f) for f in findings if f["rule"] == "high_latency"} == {
        ("api-gateway", 4, 5, 2797.0, 2797.0, "medium"),
        ("payments", 3, 4, 2500.0, 2500.0, "medium"),
        ("orders-db", 1, 1, 1840.0, 1840.0, "medium"),
    }
    assert [(g["service"], g["count"]) for g in report["top_errors"]] == [
        ("api-gateway", 3),
        ("payments", 3),
        ("scheduler", 2),
    ]


@pytest.mark.usefixtures("in_repo")
def test_text_is_the_default_format() -> None:
    result = invoke("analyze", "examples/logs/sample.log")
    assert result.exit_code == 0, result.stderr
    assert result.stdout.startswith("Log Tracer 0.1.0 · 1 file · 25 events · 1 unparsed line\n")


@pytest.mark.usefixtures("in_repo")
def test_stdout_contains_exactly_one_json_document() -> None:  # CLI-1
    result = invoke("analyze", "examples/logs/sample.log", "--format", "json")
    assert result.stdout.endswith("}\n")
    decoder = json.JSONDecoder()
    _, end = decoder.raw_decode(result.stdout)
    assert result.stdout[end:] == "\n"


def test_empty_file_is_a_valid_analysis(in_tmp: Path) -> None:
    (in_tmp / "empty.log").write_text("", encoding="utf-8")
    report = report_of(invoke("analyze", "empty.log", "--format", "json"))
    assert report["summary"]["events"] == 0
    assert report["summary"]["time_range"] == {"start": None, "end": None}
    assert report["findings"] == []


def test_malformed_lines_do_not_stop_the_analysis(in_tmp: Path) -> None:
    (in_tmp / "bad.log").write_text('garbage\n{"json": true}\n', encoding="utf-8")
    report = report_of(invoke("analyze", "bad.log", "--format", "json"))
    assert report["summary"]["events"] == 0
    assert report["summary"]["unparsed_lines"] == 2


# --- Formats, directories and warnings (ING-1, ING-2, ING-9) ------------------------


@pytest.mark.usefixtures("in_repo")
def test_examples_directory_combines_text_and_json_lines() -> None:
    result = invoke("analyze", "examples/logs", "--format", "json")
    report = report_of(result)
    assert result.stderr == ""

    assert [(i["path"], i["format"], i["events"], i["unparsed"]) for i in report["inputs"]] == [
        ("examples/logs/batch.log", "text", 11, 0),
        ("examples/logs/sample.jsonl", "jsonl", 9, 2),
        ("examples/logs/sample.log", "text", 25, 1),
    ]
    assert report["skipped_files"] == []
    assert report["summary"]["events"] == 45
    assert (report["summary"]["correlation_groups"], report["summary"]["uncorrelated_events"]) == (
        5,
        20,
    )
    by_service = {g["service"]: g for g in report["top_errors"]}
    assert by_service["api-gateway"]["correlation_ids"] == [
        "4bf92f3577b34da6",
        "c3d4e5f6a7b8c9d0",
        "e5f6a7b8c9d0e1f2",
    ]
    assert by_service["inventory"]["correlation_groups"] == 0
    assert report["summary"]["levels"] == {
        "CRITICAL": 1,
        "ERROR": 19,
        "WARNING": 6,
        "INFO": 16,
        "DEBUG": 2,
        "TRACE": 0,
        "UNKNOWN": 1,
    }
    assert report["data_quality"]["unparsed_by_reason"] == {
        "no_match": 1,
        "invalid_timestamp": 0,
        "invalid_json": 1,
        "not_an_object": 1,
        "no_known_fields": 0,
    }
    assert report["data_quality"]["field_issues"]["timestamp.missing"] == 1
    assert [(g["service"], g["count"]) for g in report["top_errors"]] == [
        ("scheduler", 9),
        ("api-gateway", 3),
        ("inventory", 3),
        ("payments", 3),
        ("scheduler", 2),
    ]
    assert ("inventory", "Stock sync with warehouse failed: HTTP <num>") in {
        (f["evidence"]["service"], f["evidence"]["fingerprint"])
        for f in report["findings"]
        if f["rule"] == "recurring_error"
    }
    assert {latency_row(f) for f in report["findings"] if f["rule"] == "high_latency"} == {
        ("api-gateway", 4, 5, 2797.0, 2797.0, "medium"),
        ("payments", 3, 4, 2500.0, 2500.0, "medium"),
        ("inventory", 1, 4, 1320.0, 1320.0, "medium"),
        ("orders-db", 1, 1, 1840.0, 1840.0, "medium"),
    }
    # RUL-3, computed from the files by hand: in req-184, req-187 and req-188 the
    # payments warning precedes the payments timeout by 2492, 2502 and 2503 ms.
    (precursor,) = [f for f in report["findings"] if f["rule"] == "warning_before_error"]
    assert precursor["severity"] == "medium"
    assert precursor["evidence"] == {
        "warning_service": "payments",
        "warning_fingerprint": "Payment provider slow, retrying",
        "error_service": "payments",
        "error_fingerprint": "Payment gateway timeout after <num> ms",
        "groups": 3,
        "median_gap_ms": 2502.0,
        "undetermined_groups": 0,
    }
    assert [(e["path"], e["line"]) for e in precursor["events"]] == [
        ("examples/logs/sample.log", 4),
        ("examples/logs/sample.log", 5),
        ("examples/logs/sample.log", 17),
        ("examples/logs/sample.log", 18),
        ("examples/logs/sample.log", 24),
        ("examples/logs/sample.log", 25),
    ]
    assert precursor["correlation_ids"] == [
        "4bf92f3577b34da6",
        "c3d4e5f6a7b8c9d0",
        "e5f6a7b8c9d0e1f2",
    ]
    # RUL-4, by hand: errors per minute from 14:03 are 2, 5, 2, 1, 9 -> median 2,
    # threshold max(5, 3 x 2) = 6 -> only 14:07 (batch.log) is a spike, medium
    # because 9 is below twice the threshold.
    assert [(r["rule"], r["status"]) for r in report["rules"]] == [
        ("recurring_error", "ok"),
        ("high_latency", "ok"),
        ("warning_before_error", "ok"),
        ("error_spike", "ok"),
    ]
    (spike,) = [f for f in report["findings"] if f["rule"] == "error_spike"]
    assert (spike["severity"], spike["event_count"]) == ("medium", 9)
    assert spike["title"] == "Error spike from 2026-10-16T14:07:00Z: 9 errors in 60 s"
    # RUL-1: the nightly-export group has a CRITICAL event, so it is high.
    severities = {
        f["evidence"]["service"]: f["severity"]
        for f in report["findings"]
        if f["rule"] == "recurring_error"
    }
    assert severities == {
        "scheduler": "high",
        "api-gateway": "medium",
        "payments": "medium",
        "inventory": "medium",
    }
    assert len(report["findings"]) == 10


def test_input_format_reads_a_log_file_as_json_lines(in_tmp: Path) -> None:
    (in_tmp / "app.log").write_text(
        '{"ts": "2026-10-16T14:00:00Z", "level": "error", "msg": "boom"}\n', encoding="utf-8"
    )
    default = report_of(invoke("analyze", "app.log", "--format", "json"))
    forced = report_of(invoke("analyze", "app.log", "--input-format", "jsonl", "--format", "json"))
    assert (default["summary"]["events"], default["summary"]["unparsed_lines"]) == (0, 1)
    assert (forced["summary"]["events"], forced["summary"]["unparsed_lines"]) == (1, 0)
    assert forced["inputs"][0]["format"] == "jsonl"


def test_warnings_go_to_stderr_and_stdout_stays_valid_json(in_tmp: Path) -> None:  # CLI-1/2
    (in_tmp / "app.log").write_text(
        '{"ts": "2026-10-16T14:00:00Z", "level": "error", "msg": "boom"}\n', encoding="utf-8"
    )
    result = invoke("analyze", "app.log", "--format", "json")
    report = report_of(result)
    assert result.stderr == (
        "Warning: 100% of the non-blank lines in app.log could not be parsed as text\n"
        "Hint: if the file uses another format, choose it with --input-format\n"
    )
    assert report["data_quality"]["warnings"] == [
        {
            "code": "mostly_unparsed",
            "path": "app.log",
            "message": "100% of the non-blank lines in app.log could not be parsed as text",
        }
    ]


@pytest.mark.parametrize("command", ["analyze", "top-errors"])
def test_analysis_warnings_go_to_stderr_too(in_tmp: Path, command: str) -> None:  # COR-6, CLI-2
    line = "2026-10-16T14:00:00Z DEBUG [api] Health check ok request_id=req-health\n"
    (in_tmp / "app.log").write_text(line * 10_001, encoding="utf-8")
    result = invoke(command, "app.log")
    assert result.exit_code == 0
    assert result.stderr == (
        "Warning: sequence req-health contains 10001 events: "
        "an identifier may be reused by unrelated requests\n"
    )


# --- Errors and exit codes (SPEC §5.4, §9) ----------------------------------------


def test_missing_path_exits_with_code_3(in_tmp: Path) -> None:
    result = invoke("analyze", "nope.log")
    assert result.exit_code == 3
    assert result.stdout == ""
    assert result.stderr == "Error: path not found: nope.log\n"


def test_unsupported_extension_exits_with_code_3_and_a_hint(in_tmp: Path) -> None:
    (in_tmp / "data.csv").write_text("a,b\n", encoding="utf-8")
    result = invoke("analyze", "data.csv")
    assert result.exit_code == 3
    assert "Error: cannot determine the format of data.csv" in result.stderr
    assert "Hint: supported extensions: .log, .txt, .jsonl, .ndjson" in result.stderr


def test_directory_without_supported_files_exits_with_code_3(in_tmp: Path) -> None:
    (in_tmp / "logs").mkdir()
    (in_tmp / "logs" / "notes.md").write_text("# notes\n", encoding="utf-8")
    result = invoke("analyze", "logs")
    assert result.exit_code == 3
    assert result.stdout == ""
    assert result.stderr == (
        "Error: no supported log files found in logs\n"
        "Hint: skipped logs/notes.md (unsupported_extension); "
        "supported extensions: .log, .txt, .jsonl, .ndjson\n"
    )


def test_unknown_input_format_is_a_usage_error(in_tmp: Path) -> None:
    (in_tmp / "app.log").write_text("", encoding="utf-8")
    assert invoke("analyze", "app.log", "--input-format", "xml").exit_code == 2


def test_missing_arguments_are_a_usage_error() -> None:
    assert invoke("analyze").exit_code == 2


def test_unknown_format_is_a_usage_error(in_tmp: Path) -> None:
    (in_tmp / "app.log").write_text("", encoding="utf-8")
    assert invoke("analyze", "app.log", "--format", "xml").exit_code == 2


def test_internal_error_exits_with_code_70_and_a_traceback(
    in_tmp: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def explode(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("kaboom")

    monkeypatch.setattr(cli, "run_analysis", explode)
    (in_tmp / "app.log").write_text("", encoding="utf-8")
    result = invoke("analyze", "app.log", "--format", "json")
    assert result.exit_code == 70
    assert result.stdout == ""
    assert "Internal error" in result.stderr
    assert "Traceback" in result.stderr
    assert "RuntimeError: kaboom" in result.stderr


# --- Entry points ---------------------------------------------------------------


def test_version_option() -> None:
    result = invoke("--version")
    assert result.exit_code == 0
    assert result.stdout == f"log-tracer {__version__}\n"


def test_module_entry_point_runs_the_cli() -> None:
    completed = subprocess.run(
        [sys.executable, "-m", "log_tracer", "--version"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0
    assert completed.stdout == f"log-tracer {__version__}\n"


def test_main_writes_utf8_whatever_the_locale(monkeypatch: pytest.MonkeyPatch) -> None:  # CLI-1
    # newline="\n": the same bytes on Windows, where "\n" is written as "\r\n".
    stdout = io.TextIOWrapper(io.BytesIO(), encoding="cp1252", newline="\n")
    stderr = io.StringIO()  # not a TextIOWrapper: there is no encoding to change
    monkeypatch.setattr(sys, "stdout", stdout)
    monkeypatch.setattr(sys, "stderr", stderr)
    monkeypatch.setattr(sys, "argv", ["log-tracer", "--version"])
    with pytest.raises(SystemExit) as excinfo:
        cli.main()
    assert excinfo.value.code == 0
    assert stdout.encoding == "utf-8"
    stdout.flush()
    buffer = stdout.buffer
    assert isinstance(buffer, io.BytesIO)
    assert buffer.getvalue() == f"log-tracer {__version__}\n".encode()


def test_python_dash_m_runs_main(monkeypatch: pytest.MonkeyPatch) -> None:
    # In-process twin of the subprocess test above, so that coverage sees __main__.
    called: list[bool] = []
    monkeypatch.setattr(cli, "main", lambda: called.append(True))
    runpy.run_module("log_tracer", run_name="__main__")
    assert called == [True]
    runpy.run_module("log_tracer.__main__", run_name="log_tracer.__main__")  # imported only
    assert called == [True]


@pytest.mark.skipif(
    sys.platform == "win32" or (hasattr(os, "geteuid") and os.geteuid() == 0),
    reason="file permissions cannot make a file unreadable here",
)
def test_unreadable_file_is_reported_on_stderr_and_skipped(in_tmp: Path) -> None:  # §9
    (in_tmp / "ok.log").write_text("2026-10-16T14:00:00Z INFO [api] fine\n", encoding="utf-8")
    locked = in_tmp / "locked.log"
    locked.write_text("2026-10-16T14:00:00Z INFO [api] secret\n", encoding="utf-8")
    locked.chmod(0)
    try:
        result = invoke("analyze", "ok.log", "locked.log", "--format", "json")
    finally:
        locked.chmod(0o644)
    report = report_of(result)
    assert (
        result.stderr
        == "Warning: locked.log could not be read (Permission denied) and was skipped\n"
    )
    assert [(i["path"], i["error"]) for i in report["inputs"]] == [
        ("locked.log", "Permission denied"),
        ("ok.log", None),
    ]


@pytest.mark.skipif(
    sys.platform == "win32" or (hasattr(os, "geteuid") and os.geteuid() == 0),
    reason="file permissions cannot make a file unreadable here",
)
def test_only_unreadable_files_exit_with_code_3(in_tmp: Path) -> None:  # §9
    locked = in_tmp / "locked.log"
    locked.write_text("x\n", encoding="utf-8")
    locked.chmod(0)
    try:
        result = invoke("analyze", "locked.log")
    finally:
        locked.chmod(0o644)
    assert result.exit_code == 3
    assert result.stdout == ""
    assert result.stderr == (
        "Error: none of the input files could be read (locked.log: Permission denied)\n"
    )


# --- Text output (SPEC §5.3) --------------------------------------------------------


def cells(text: str) -> list[list[str]]:
    return [re.split(r" {2,}", line.strip()) for line in text.splitlines() if line.strip()]


@pytest.mark.usefixtures("in_repo")
def test_text_summary_of_the_examples() -> None:
    result = runner.invoke(app, ["analyze", "examples/logs"], env={"COLUMNS": "160"})
    assert result.exit_code == 0, result.stderr
    assert result.stderr == ""
    out = result.stdout
    assert "\x1b" not in out  # CLI-3: no colors when stdout is not a terminal
    lines = [line.strip() for line in out.splitlines()]
    assert lines[0] == "Log Tracer 0.1.0 · 3 files · 45 events · 3 unparsed lines"
    assert "Time range (UTC): 2026-10-16T14:03:21.004Z → 2026-10-16T14:07:38.900Z" in lines
    assert "Findings (10)" in lines
    assert [
        "medium",
        "error_spike",
        "Error spike from 2026-10-16T14:07:00Z: 9 errors in 60 s",
        "9",
    ] in cells(out)
    assert [
        "medium",
        "warning_before_error",
        "Warning before error in 3 sequences: Payment provider slow, retrying…",
        "6",
    ] in cells(out)
    assert [
        "medium",
        "high_latency",
        "High latency in api-gateway: 4 of 5 measured events over 1000 ms",
        "4",
    ] in cells(out)
    assert [
        "medium",
        "recurring_error",
        "Recurring error in inventory: Stock sync with warehouse failed: HTTP…",
        "3",
    ] in cells(out)
    assert "2. 3x api-gateway: POST /checkout failed" in lines
    assert (
        "first 14:03:24.910Z · last 14:05:23.897Z · 3 sequences · "
        "example examples/logs/sample.log:6"
    ) in lines
    assert "Levels: CRITICAL 1 · ERROR 19 · WARNING 6 · INFO 16 · DEBUG 2 · UNKNOWN 1" in lines
    assert "Correlation: 5 sequences · 20 uncorrelated events" in lines
    assert "examples/logs/sample.jsonl (jsonl): 9 events, 2 unparsed" in lines
    assert "Unparsed lines: no_match 1 · invalid_json 1 · not_an_object 1" in lines


def test_text_output_survives_a_non_utf8_stdout(in_tmp: Path) -> None:  # NFR-4, CLI-1
    # On Windows, redirected output uses the locale code page (e.g. cp1252),
    # which cannot encode characters such as "→" or "─".
    (in_tmp / "app.log").write_text(
        "2026-10-16T14:00:00Z ERROR [api] Zahlung fehlgeschlagen → 日本\n", encoding="utf-8"
    )
    for args in (["analyze", "app.log"], ["analyze", "app.log", "--format", "json"]):
        completed = subprocess.run(
            [sys.executable, "-m", "log_tracer", *args],
            capture_output=True,
            check=False,
            env={**os.environ, "PYTHONIOENCODING": "cp1252"},
        )
        assert completed.returncode == 0, completed.stderr.decode("utf-8", "replace")
        assert "→ 日本".encode() in completed.stdout  # written as UTF-8


# --- --output and --fail-on (SPEC §5.2) ----------------------------------------------


def test_output_writes_the_same_json_report_as_stdout(in_tmp: Path) -> None:
    (in_tmp / "app.log").write_text("2026-10-16T14:00:00Z ERROR [api] boom\n", encoding="utf-8")
    result = invoke("analyze", "app.log", "--format", "json", "--output", "report.json")
    assert result.exit_code == 0, result.stderr
    assert (in_tmp / "report.json").read_text(encoding="utf-8") == result.stdout


def test_output_is_written_whatever_the_stdout_format(in_tmp: Path) -> None:
    (in_tmp / "app.log").write_text("2026-10-16T14:00:00Z ERROR [api] boom\n", encoding="utf-8")
    result = invoke("analyze", "app.log", "--output", "report.json")
    assert result.exit_code == 0, result.stderr
    assert result.stdout.startswith("Log Tracer")
    saved = json.loads((in_tmp / "report.json").read_text(encoding="utf-8"))
    assert saved["summary"]["events"] == 1


def test_unwritable_output_exits_with_code_3_and_prints_nothing(in_tmp: Path) -> None:
    (in_tmp / "app.log").write_text("2026-10-16T14:00:00Z ERROR [api] boom\n", encoding="utf-8")
    result = invoke("analyze", "app.log", "--output", "missing/dir/report.json")
    assert result.exit_code == 3
    assert result.stdout == ""
    assert result.stderr == (
        "Error: cannot write the report to missing/dir/report.json (No such file or directory)\n"
    )


@pytest.mark.usefixtures("in_repo")
@pytest.mark.parametrize("threshold", ["low", "medium"])
def test_fail_on_exits_with_code_1_after_printing_the_output(threshold: str) -> None:
    result = invoke("analyze", "examples/logs", "--fail-on", threshold)
    assert result.exit_code == 1
    assert result.stdout.startswith("Log Tracer")
    assert result.stderr == (
        f"Found 10 findings with severity {threshold} or higher (--fail-on {threshold})\n"
    )


@pytest.mark.usefixtures("in_repo")
def test_fail_on_keeps_stdout_valid_json() -> None:
    result = invoke("analyze", "examples/logs", "--format", "json", "--fail-on", "medium")
    assert result.exit_code == 1
    assert len(json.loads(result.stdout)["findings"]) == 10


@pytest.mark.usefixtures("in_repo")
def test_fail_on_below_the_threshold_exits_with_code_0() -> None:
    # The two sample files alone have medium findings only (batch.log has a high one).
    result = invoke(
        "analyze", "examples/logs/sample.log", "examples/logs/sample.jsonl", "--fail-on", "high"
    )
    assert result.exit_code == 0
    assert result.stderr == ""


def test_unknown_fail_on_value_is_a_usage_error(in_tmp: Path) -> None:
    (in_tmp / "app.log").write_text("", encoding="utf-8")
    assert invoke("analyze", "app.log", "--fail-on", "critical").exit_code == 2


# --- Configuration file (SPEC §7) ------------------------------------------------------


@pytest.mark.usefixtures("in_repo")
def test_configuration_file_changes_the_thresholds() -> None:
    result = invoke(
        "analyze", "examples/logs", "--config", "examples/log-tracer.toml", "--format", "json"
    )
    report = report_of(result)
    # payments: warn_ms 3000 -> no slow event; api-gateway: critical_ms 2700 -> high.
    assert {latency_row(f) for f in report["findings"] if f["rule"] == "high_latency"} == {
        ("api-gateway", 4, 5, 2797.0, 2797.0, "high"),
        ("inventory", 1, 4, 1320.0, 1320.0, "medium"),
        ("orders-db", 1, 1, 1840.0, 1840.0, "medium"),
    }
    assert len(report["findings"]) == 9
    assert report["config"]["high_latency"]["services"] == {
        "api-gateway": {"warn_ms": 1000, "critical_ms": 2700},
        "payments": {"warn_ms": 3000, "critical_ms": 5000},
    }


@pytest.mark.usefixtures("in_repo")
def test_fail_on_high_with_the_example_configuration() -> None:
    result = invoke(
        "analyze", "examples/logs", "--config", "examples/log-tracer.toml", "--fail-on", "high"
    )
    assert result.exit_code == 1
    # api-gateway latency (critical_ms 2700) and the scheduler errors (one CRITICAL).
    assert result.stderr == "Found 2 findings with severity high or higher (--fail-on high)\n"


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (
            "[high_latency]\nwarn = 10\n",
            "Error: cfg.toml: unknown key 'high_latency.warn' (did you mean 'warn_ms'?)\n",
        ),
        (
            "[high_latency]\nwarn_ms = 6000\n",
            "Error: cfg.toml: high_latency.critical_ms (5000) must be greater than "
            "high_latency.warn_ms (6000)\n",
        ),
        (
            "[report]\nmax_examples = \n",
            "Error: cfg.toml: invalid TOML: Invalid value (at line 2, column 16)\n",
        ),
    ],
)
def test_invalid_configuration_exits_with_code_2(in_tmp: Path, content: str, message: str) -> None:
    (in_tmp / "app.log").write_text("2026-10-16T14:00:00Z INFO [api] ok\n", encoding="utf-8")
    (in_tmp / "cfg.toml").write_text(content, encoding="utf-8")
    result = invoke("analyze", "app.log", "--config", "cfg.toml")
    assert result.exit_code == 2
    assert result.stdout == ""
    assert result.stderr == message


def test_missing_configuration_file_exits_with_code_2(in_tmp: Path) -> None:
    (in_tmp / "app.log").write_text("2026-10-16T14:00:00Z INFO [api] ok\n", encoding="utf-8")
    result = invoke("analyze", "app.log", "--config", "nope.toml")
    assert result.exit_code == 2
    assert result.stderr == "Error: configuration file not found: nope.toml\n"


def test_configuration_is_checked_before_reading_the_logs(in_tmp: Path) -> None:
    (in_tmp / "cfg.toml").write_text("[bogus]\n", encoding="utf-8")
    result = invoke("analyze", "missing.log", "--config", "cfg.toml")
    assert result.exit_code == 2
    assert result.stderr.startswith("Error: cfg.toml: unknown key 'bogus'")


# --- error_spike end to end (RUL-4) ------------------------------------------------------


def write_spike_log(directory: Path) -> None:
    """One error per minute for ten minutes, except twelve in minute 5."""
    lines = [
        f"2026-10-16T14:{minute:02d}:{second:02d}Z ERROR [api] upstream failure\n"
        for minute in range(10)
        for second in range(12 if minute == 5 else 1)
    ]
    (directory / "spike.log").write_text("".join(lines), encoding="utf-8")


def test_error_spike_is_reported(in_tmp: Path) -> None:
    write_spike_log(in_tmp)
    report = report_of(invoke("analyze", "spike.log", "--format", "json"))
    (spike,) = [f for f in report["findings"] if f["rule"] == "error_spike"]
    # Median of [1, 1, 1, 1, 1, 12, 1, 1, 1, 1] is 1 -> threshold max(5, 3 * 1) = 5.
    assert spike["severity"] == "high"  # 12 >= 2 * 5
    assert spike["title"] == "Error spike from 2026-10-16T14:05:00Z: 12 errors in 60 s"
    assert spike["evidence"]["start"] == "2026-10-16T14:05:00.000000Z"
    assert (spike["evidence"]["baseline"], spike["evidence"]["threshold"]) == (1.0, 5.0)


def test_error_spike_parameters_come_from_the_configuration(in_tmp: Path) -> None:
    write_spike_log(in_tmp)
    (in_tmp / "cfg.toml").write_text("[error_spike]\nmin_errors = 15\n", encoding="utf-8")
    report = report_of(invoke("analyze", "spike.log", "--format", "json", "--config", "cfg.toml"))
    assert [f for f in report["findings"] if f["rule"] == "error_spike"] == []
    assert report["config"]["error_spike"] == {"window_s": 60, "factor": 3.0, "min_errors": 15}
