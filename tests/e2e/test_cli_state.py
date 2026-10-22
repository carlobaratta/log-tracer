"""Saved state end to end: `analyze` saves, `report` and `inspect` read (SPEC §8)."""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner, Result

from log_tracer.cli import app
from tests.e2e.test_cli_inspect import REQ_184

ROOT = Path(__file__).resolve().parents[2]
runner = CliRunner()

TEXT_LOG = (
    "2026-10-16T14:00:00Z INFO [api] request accepted request_id=r1\n"
    "2026-10-16T14:00:01Z ERROR [payments] Payment timeout after 2500 ms request_id=r1\n"
)
NO_STATE_HINT = "Hint: run 'log-tracer analyze PATH' first\n"


def invoke(*args: str) -> Result:
    return runner.invoke(app, list(args), env={"COLUMNS": "160"})


def text_lines(result: Result) -> list[str]:
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


@pytest.fixture
def state_dir(_state_outside_the_repository: Path) -> Path:
    return _state_outside_the_repository


@pytest.fixture
def in_tmp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "app.log").write_text(TEXT_LOG, encoding="utf-8")
    return tmp_path


# --- analyze saves (STA-1) ----------------------------------------------------------------


def test_default_state_directory(in_tmp: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LOG_TRACER_STATE_DIR")
    result = invoke("analyze", "app.log", "--format", "json")
    assert result.exit_code == 0, result.stderr
    saved = in_tmp / ".log-tracer"
    assert sorted(p.name for p in saved.iterdir()) == [".gitignore", "last-report.json"]
    assert (saved / ".gitignore").read_text(encoding="utf-8") == "*\n"
    assert (saved / "last-report.json").read_text(encoding="utf-8") == result.stdout


def test_state_dir_option_wins_over_the_environment(in_tmp: Path, state_dir: Path) -> None:
    result = invoke("analyze", "app.log", "--state-dir", "custom")
    assert result.exit_code == 0, result.stderr
    assert (in_tmp / "custom" / "last-report.json").is_file()
    assert not state_dir.exists()


def test_environment_variable_sets_the_state_directory(in_tmp: Path, state_dir: Path) -> None:
    assert invoke("analyze", "app.log").exit_code == 0
    assert (state_dir / "last-report.json").is_file()
    assert not (in_tmp / ".log-tracer").exists()


def test_no_save_writes_nothing(in_tmp: Path, state_dir: Path) -> None:  # STA-1
    result = invoke("analyze", "app.log", "--no-save")
    assert result.exit_code == 0, result.stderr
    assert not state_dir.exists()


def test_state_is_saved_when_fail_on_is_reached(in_tmp: Path, state_dir: Path) -> None:  # D-17
    (in_tmp / "app.log").write_text(TEXT_LOG.replace("INFO", "ERROR") * 3, encoding="utf-8")
    result = invoke("analyze", "app.log", "--fail-on", "low")
    assert result.exit_code == 1
    assert (state_dir / "last-report.json").is_file()


def test_unwritable_state_stops_before_stdout(in_tmp: Path) -> None:  # D-17
    (in_tmp / "blocker").write_text("", encoding="utf-8")
    result = invoke("analyze", "app.log", "--state-dir", "blocker")
    assert result.exit_code == 3
    assert result.stdout == ""
    assert result.stderr.startswith("Error: cannot save the analysis in blocker/ (")
    assert result.stderr.endswith(
        "Hint: use --no-save, or choose another directory with --state-dir\n"
    )


@pytest.mark.parametrize(
    "command",
    [["top-errors", "app.log"], ["inspect", "r1", "app.log"]],
    ids=["top-errors", "inspect"],
)
def test_only_analyze_writes_state(in_tmp: Path, state_dir: Path, command: list[str]) -> None:
    assert invoke(*command).exit_code == 0  # STA-4
    assert not state_dir.exists()


# --- report (STA-2) ---------------------------------------------------------------------


def test_report_shows_the_saved_analysis_without_the_logs(in_tmp: Path) -> None:  # STA-2
    analyzed_text = invoke("analyze", "app.log")
    analyzed_json = invoke("analyze", "app.log", "--format", "json")
    (in_tmp / "app.log").unlink()  # the logs are not read again
    text = invoke("report")
    as_json = invoke("report", "--format", "json")
    assert (text.exit_code, as_json.exit_code) == (0, 0), text.stderr + as_json.stderr
    assert as_json.stdout == analyzed_json.stdout  # D-14: same canonical document
    assert text.stdout == analyzed_text.stdout
    assert text.stderr == as_json.stderr == ""


def test_report_output_file(in_tmp: Path) -> None:
    analyzed = invoke("analyze", "app.log", "--format", "json")
    result = invoke("report", "--output", "copy.json")
    assert result.exit_code == 0, result.stderr
    assert (in_tmp / "copy.json").read_text(encoding="utf-8") == analyzed.stdout
    assert result.stdout.startswith("Log Tracer")  # text summary on stdout


def test_report_state_dir_option(in_tmp: Path) -> None:
    invoke("analyze", "app.log", "--state-dir", "elsewhere")
    assert invoke("report", "--state-dir", "elsewhere").exit_code == 0


def test_report_without_saved_analysis(in_tmp: Path, state_dir: Path) -> None:  # STA-2, CLI-6
    result = invoke("report")
    assert result.exit_code == 3
    assert result.stdout == ""
    assert result.stderr == (
        f"Error: no saved analysis in {state_dir.as_posix()}/\n{NO_STATE_HINT}"
    )


def test_report_with_incompatible_schema(in_tmp: Path, state_dir: Path) -> None:  # STA-2
    invoke("analyze", "app.log")
    saved = state_dir / "last-report.json"
    data = json.loads(saved.read_text(encoding="utf-8"))
    data["schema_version"] = "2.0"
    saved.write_text(json.dumps(data), encoding="utf-8")
    result = invoke("report")
    assert result.exit_code == 3
    assert "unsupported schema version '2.0'" in result.stderr
    assert result.stderr.endswith("Hint: run 'log-tracer analyze PATH' again to replace it\n")


def test_report_with_damaged_file(in_tmp: Path, state_dir: Path) -> None:
    state_dir.mkdir(parents=True)
    (state_dir / "last-report.json").write_text("{", encoding="utf-8")
    result = invoke("report")
    assert result.exit_code == 3
    assert "not valid JSON" in result.stderr


# --- inspect without paths (STA-3) --------------------------------------------------------


def test_inspect_reads_the_saved_inputs_again(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(ROOT)
    assert invoke("analyze", "examples/logs").exit_code == 0
    result = invoke("inspect", "req-184")
    assert result.exit_code == 0, result.stderr
    assert result.stderr == ""
    assert text_lines(result) == REQ_184


def test_inspect_warns_when_a_source_changed(in_tmp: Path) -> None:  # STA-3
    invoke("analyze", "app.log")
    with (in_tmp / "app.log").open("a", encoding="utf-8") as stream:
        stream.write("2026-10-16T14:00:02Z INFO [api] done request_id=r1\n")
    result = invoke("inspect", "r1")
    assert result.exit_code == 0
    assert result.stderr == (
        "Warning: app.log has changed since the saved analysis; "
        "the timeline shows its current content\n"
    )
    assert text_lines(result)[0].startswith("Sequence r1 · 3 events")


def test_inspect_stops_when_a_source_is_gone(in_tmp: Path) -> None:  # STA-3
    invoke("analyze", "app.log")
    (in_tmp / "app.log").unlink()
    result = invoke("inspect", "r1")
    assert result.exit_code == 3
    assert result.stderr == (
        "Error: app.log, listed in the saved analysis, no longer exists\n"
        "Hint: pass the log paths to inspect, or run 'log-tracer analyze PATH' again\n"
    )


def test_inspect_uses_the_saved_input_format(in_tmp: Path) -> None:  # D-16
    (in_tmp / "app.log").write_text('{"msg": "boom", "request_id": "j1"}\n', encoding="utf-8")
    invoke("analyze", "app.log", "--input-format", "jsonl")
    saved_format = invoke("inspect", "j1")
    assert saved_format.exit_code == 0, saved_format.stderr
    assert saved_format.stderr == ""
    forced = invoke("inspect", "j1", "--input-format", "text")
    assert forced.exit_code == 4
    assert forced.stderr.startswith("Warning: 100% of the non-blank lines in app.log")


def test_inspect_state_dir_option(in_tmp: Path) -> None:
    invoke("analyze", "app.log", "--state-dir", "elsewhere")
    assert invoke("inspect", "r1", "--state-dir", "elsewhere").exit_code == 0
    assert invoke("inspect", "r1").exit_code == 3


def test_inspect_with_paths_ignores_the_saved_state(in_tmp: Path, state_dir: Path) -> None:
    state_dir.mkdir(parents=True)
    (state_dir / "last-report.json").write_text("{", encoding="utf-8")
    assert invoke("inspect", "r1", "app.log").exit_code == 0
