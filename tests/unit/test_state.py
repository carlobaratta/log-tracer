"""Saved state: atomic writing and reading of last-report.json (SPEC §8)."""

import json
from pathlib import Path

import pytest

from log_tracer import state
from log_tracer.errors import StateError
from log_tracer.reporting.json_report import dumps
from tests.factories import make_report


def files_in(directory: Path) -> list[str]:
    return sorted(path.name for path in directory.iterdir())


def test_save_creates_the_directory_the_report_and_a_gitignore(tmp_path: Path) -> None:  # STA-1
    directory = tmp_path / "nested" / ".log-tracer"
    state.save(make_report(), directory)
    assert files_in(directory) == [".gitignore", "last-report.json"]
    assert (directory / ".gitignore").read_text(encoding="utf-8") == "*\n"
    assert (directory / "last-report.json").read_text(encoding="utf-8") == dumps(make_report())


def test_existing_gitignore_is_left_alone(tmp_path: Path) -> None:  # STA-1: first write only
    (tmp_path / ".gitignore").write_text("custom\n", encoding="utf-8")
    state.save(make_report(), tmp_path)
    assert (tmp_path / ".gitignore").read_text(encoding="utf-8") == "custom\n"


def test_save_replaces_the_previous_report(tmp_path: Path) -> None:  # STA-1
    state.save(make_report(findings=()), tmp_path)
    state.save(make_report(), tmp_path)
    assert (tmp_path / "last-report.json").read_text(encoding="utf-8") == dumps(make_report())
    assert files_in(tmp_path) == [".gitignore", "last-report.json"]  # no temporary file left


def test_failed_rename_keeps_the_previous_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:  # STA-1: atomic
    state.save(make_report(findings=()), tmp_path)
    before = (tmp_path / "last-report.json").read_bytes()

    def refuse(_self: Path, _target: object) -> Path:
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(Path, "replace", refuse)
    with pytest.raises(StateError) as excinfo:
        state.save(make_report(), tmp_path)
    assert str(excinfo.value) == (
        f"cannot save the analysis in {tmp_path.as_posix()}/ (Permission denied)"
    )
    assert excinfo.value.hint == "use --no-save, or choose another directory with --state-dir"
    assert (tmp_path / "last-report.json").read_bytes() == before
    assert files_in(tmp_path) == [".gitignore", "last-report.json"]


def test_state_directory_that_is_a_file_cannot_be_used(tmp_path: Path) -> None:
    target = tmp_path / "state"
    target.write_text("", encoding="utf-8")
    with pytest.raises(StateError) as excinfo:
        state.save(make_report(), target)
    assert str(excinfo.value).startswith(f"cannot save the analysis in {target.as_posix()}/ (")


def test_load_returns_the_saved_report(tmp_path: Path) -> None:  # STA-2
    state.save(make_report(), tmp_path)
    assert dumps(state.load(tmp_path)) == dumps(make_report())


def test_unreadable_report_is_an_error(tmp_path: Path) -> None:
    (tmp_path / "last-report.json").mkdir()  # reading a directory fails
    with pytest.raises(StateError) as excinfo:
        state.load(tmp_path)
    assert str(excinfo.value).startswith(
        f"cannot read the saved analysis {tmp_path.as_posix()}/last-report.json ("
    )


@pytest.mark.parametrize("create", [False, True], ids=["no-directory", "empty-directory"])
def test_missing_report_explains_how_to_create_it(
    tmp_path: Path, create: bool
) -> None:  # STA-2, CLI-6
    directory = tmp_path / ".log-tracer"
    if create:
        directory.mkdir()
    with pytest.raises(StateError) as excinfo:
        state.load(directory)
    assert str(excinfo.value) == f"no saved analysis in {directory.as_posix()}/"
    assert excinfo.value.hint == "run 'log-tracer analyze PATH' first"


def test_incompatible_report_is_an_error(tmp_path: Path) -> None:  # STA-2
    data = json.loads(dumps(make_report()))
    data["schema_version"] = "2.0"
    (tmp_path / "last-report.json").write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(StateError) as excinfo:
        state.load(tmp_path)
    assert str(excinfo.value) == (
        f"cannot read the saved analysis {tmp_path.as_posix()}/last-report.json: "
        "unsupported schema version '2.0' (this version of log-tracer reads 1.x)"
    )
    assert excinfo.value.hint == "run 'log-tracer analyze PATH' again to replace it"


@pytest.mark.parametrize(
    ("content", "reason"),
    [
        (b"\xff\xfe", "not valid UTF-8"),
        (b"{", "not valid JSON: Expecting property name enclosed in double quotes"),
        (b"{}", "missing key 'schema_version'"),
    ],
    ids=["encoding", "json", "content"],
)
def test_damaged_report_is_an_error(tmp_path: Path, content: bytes, reason: str) -> None:
    (tmp_path / "last-report.json").write_bytes(content)
    with pytest.raises(StateError) as excinfo:
        state.load(tmp_path)
    assert reason in str(excinfo.value)
    assert excinfo.value.hint == "run 'log-tracer analyze PATH' again to replace it"
