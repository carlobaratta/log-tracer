"""Input discovery: existence, format by extension, deduplication, order (ING-1…3)."""

from pathlib import Path

import pytest

from log_tracer.errors import InputError
from log_tracer.ingestion.discovery import discover
from log_tracer.models import InputFormat


@pytest.fixture
def workdir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    return tmp_path


def touch(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("", encoding="utf-8")
    return path


@pytest.mark.parametrize("name", ["app.log", "app.txt", "APP.LOG"])
def test_text_extensions_are_recognized(workdir: Path, name: str) -> None:
    touch(workdir / name)
    (source,) = discover([Path(name)])
    assert source.format is InputFormat.TEXT
    assert source.display == name


def test_missing_path_is_an_input_error(workdir: Path) -> None:
    with pytest.raises(InputError, match=r"path not found: missing\.log"):
        discover([Path("missing.log")])


def test_unsupported_extension_is_an_input_error_with_a_hint(workdir: Path) -> None:
    touch(workdir / "app.csv")
    with pytest.raises(InputError, match=r"unsupported extension '\.csv'") as excinfo:
        discover([Path("app.csv")])
    assert excinfo.value.hint is not None
    assert ".log" in excinfo.value.hint


def test_file_without_extension_is_an_input_error(workdir: Path) -> None:
    touch(workdir / "messages")
    with pytest.raises(InputError, match="no extension"):
        discover([Path("messages")])


def test_directory_is_not_supported_yet(workdir: Path) -> None:
    (workdir / "logs").mkdir()
    with pytest.raises(InputError, match="is a directory"):
        discover([Path("logs")])


def test_display_path_uses_posix_separators(workdir: Path) -> None:
    touch(workdir / "logs" / "app.log")
    (source,) = discover([Path("logs") / "app.log"])
    assert source.display == "logs/app.log"


def test_sources_are_sorted_by_display_path(workdir: Path) -> None:
    for name in ("b.log", "a.log", "c.log"):
        touch(workdir / name)
    sources = discover([Path("c.log"), Path("a.log"), Path("b.log")])
    assert [s.display for s in sources] == ["a.log", "b.log", "c.log"]


def test_same_file_given_twice_is_analyzed_once(workdir: Path) -> None:
    touch(workdir / "app.log")
    (workdir / "logs").mkdir()
    sources = discover([Path("logs/../app.log"), Path("app.log"), Path("./app.log")])
    # The lexicographically smallest spelling wins, whatever the argument order.
    assert [s.display for s in sources] == ["app.log"]


def test_deduplication_does_not_depend_on_argument_order(workdir: Path) -> None:
    touch(workdir / "app.log")
    (workdir / "logs").mkdir()
    forward = discover([Path("app.log"), Path("logs/../app.log")])
    backward = discover([Path("logs/../app.log"), Path("app.log")])
    assert forward == backward
