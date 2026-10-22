"""`log-tracer analyze` end to end: output contract and exit codes (SPEC §5)."""

import json
import os
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

    assert {
        (f["evidence"]["service"], f["evidence"]["fingerprint"], f["severity"])
        for f in report["findings"]
    } == {
        ("payments", "Payment gateway timeout after <num> ms", "medium"),
        ("api-gateway", "POST /checkout failed", "medium"),
    }
    assert [(g["service"], g["count"]) for g in report["top_errors"]] == [
        ("api-gateway", 3),
        ("payments", 3),
        ("scheduler", 2),
    ]


@pytest.mark.usefixtures("in_repo")
def test_json_is_the_default_format_for_now() -> None:
    explicit = report_of(invoke("analyze", "examples/logs/sample.log", "--format", "json"))
    default = report_of(invoke("analyze", "examples/logs/sample.log"))
    del explicit["generated_at"], default["generated_at"]
    assert explicit == default


@pytest.mark.usefixtures("in_repo")
def test_stdout_contains_exactly_one_json_document() -> None:  # CLI-1
    result = invoke("analyze", "examples/logs/sample.log")
    assert result.stdout.endswith("}\n")
    decoder = json.JSONDecoder()
    _, end = decoder.raw_decode(result.stdout)
    assert result.stdout[end:] == "\n"


def test_empty_file_is_a_valid_analysis(in_tmp: Path) -> None:
    (in_tmp / "empty.log").write_text("", encoding="utf-8")
    report = report_of(invoke("analyze", "empty.log"))
    assert report["summary"]["events"] == 0
    assert report["summary"]["time_range"] == {"start": None, "end": None}
    assert report["findings"] == []


def test_malformed_lines_do_not_stop_the_analysis(in_tmp: Path) -> None:
    (in_tmp / "bad.log").write_text('garbage\n{"json": true}\n', encoding="utf-8")
    report = report_of(invoke("analyze", "bad.log"))
    assert report["summary"]["events"] == 0
    assert report["summary"]["unparsed_lines"] == 2


# --- Formats, directories and warnings (ING-1, ING-2, ING-9) ------------------------


@pytest.mark.usefixtures("in_repo")
def test_examples_directory_combines_text_and_json_lines() -> None:
    result = invoke("analyze", "examples/logs")
    report = report_of(result)
    assert result.stderr == ""

    assert [(i["path"], i["format"], i["events"], i["unparsed"]) for i in report["inputs"]] == [
        ("examples/logs/sample.jsonl", "jsonl", 9, 2),
        ("examples/logs/sample.log", "text", 25, 1),
    ]
    assert report["skipped_files"] == []
    assert report["summary"]["events"] == 34
    assert report["summary"]["levels"] == {
        "CRITICAL": 0,
        "ERROR": 11,
        "WARNING": 6,
        "INFO": 14,
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
        ("api-gateway", 3),
        ("inventory", 3),
        ("payments", 3),
        ("scheduler", 2),
    ]
    assert ("inventory", "Stock sync with warehouse failed: HTTP <num>") in {
        (f["evidence"]["service"], f["evidence"]["fingerprint"]) for f in report["findings"]
    }


def test_input_format_reads_a_log_file_as_json_lines(in_tmp: Path) -> None:
    (in_tmp / "app.log").write_text(
        '{"ts": "2026-10-16T14:00:00Z", "level": "error", "msg": "boom"}\n', encoding="utf-8"
    )
    default = report_of(invoke("analyze", "app.log"))
    forced = report_of(invoke("analyze", "app.log", "--input-format", "jsonl"))
    assert (default["summary"]["events"], default["summary"]["unparsed_lines"]) == (0, 1)
    assert (forced["summary"]["events"], forced["summary"]["unparsed_lines"]) == (1, 0)
    assert forced["inputs"][0]["format"] == "jsonl"


def test_warnings_go_to_stderr_and_stdout_stays_valid_json(in_tmp: Path) -> None:  # CLI-1/2
    (in_tmp / "app.log").write_text(
        '{"ts": "2026-10-16T14:00:00Z", "level": "error", "msg": "boom"}\n', encoding="utf-8"
    )
    result = invoke("analyze", "app.log")
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
    result = invoke("analyze", "app.log")
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
        result = invoke("analyze", "ok.log", "locked.log")
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
