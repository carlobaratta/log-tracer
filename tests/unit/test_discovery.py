"""Input discovery: files, directories, formats, deduplication, order (ING-1…3)."""

import os
import sys
from pathlib import Path

import pytest

from log_tracer.errors import InputError
from log_tracer.ingestion.discovery import discover
from log_tracer.models import InputFormat, SkippedFile, SkipReason

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="needs POSIX symlinks/FIFOs")
needs_permissions = pytest.mark.skipif(
    sys.platform == "win32" or (hasattr(os, "geteuid") and os.geteuid() == 0),
    reason="file permissions cannot make a directory unreadable here",
)


@pytest.fixture
def workdir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    return tmp_path


def touch(*names: str) -> None:
    for name in names:
        path = Path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")


def displays(paths: list[Path], **kwargs: InputFormat | None) -> list[tuple[str, InputFormat]]:
    return [(s.display, s.format) for s in discover(paths, **kwargs).sources]


# --- Explicit files -------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("app.log", InputFormat.TEXT),
        ("app.txt", InputFormat.TEXT),
        ("APP.LOG", InputFormat.TEXT),
        ("app.jsonl", InputFormat.JSONL),
        ("app.ndjson", InputFormat.JSONL),
    ],
)
def test_format_is_chosen_by_extension(workdir: Path, name: str, expected: InputFormat) -> None:
    touch(name)
    assert displays([Path(name)]) == [(name, expected)]


def test_missing_path_is_an_input_error(workdir: Path) -> None:
    with pytest.raises(InputError, match=r"path not found: missing\.log"):
        discover([Path("missing.log")])


def test_no_paths_is_an_input_error() -> None:
    with pytest.raises(InputError, match="no input files given"):
        discover([])


def test_unsupported_extension_is_an_input_error_with_a_hint(workdir: Path) -> None:
    touch("app.csv")
    with pytest.raises(InputError, match=r"unsupported extension '\.csv'") as excinfo:
        discover([Path("app.csv")])
    assert excinfo.value.hint is not None
    assert ".log, .txt, .jsonl, .ndjson" in excinfo.value.hint


def test_file_without_extension_is_an_input_error(workdir: Path) -> None:
    touch("messages")
    with pytest.raises(InputError, match="no extension"):
        discover([Path("messages")])


def test_input_format_accepts_any_explicit_file(workdir: Path) -> None:  # ING-2
    touch("app.csv", "messages")
    assert displays([Path("app.csv"), Path("messages")], input_format=InputFormat.JSONL) == [
        ("app.csv", InputFormat.JSONL),
        ("messages", InputFormat.JSONL),
    ]


def test_input_format_overrides_the_extension(workdir: Path) -> None:  # ING-2
    touch("app.log")
    assert displays([Path("app.log")], input_format=InputFormat.JSONL) == [
        ("app.log", InputFormat.JSONL)
    ]


@posix_only
def test_explicit_fifo_is_rejected_instead_of_blocking(workdir: Path) -> None:
    os.mkfifo("pipe.log")
    with pytest.raises(InputError, match=r"not a regular file: pipe\.log"):
        discover([Path("pipe.log")])


def test_display_path_uses_posix_separators(workdir: Path) -> None:
    touch("logs/app.log")
    assert displays([Path("logs") / "app.log"]) == [("logs/app.log", InputFormat.TEXT)]


# --- Directories (ING-1) -------------------------------------------------------------------


def test_directories_are_walked_recursively_in_path_order(workdir: Path) -> None:
    touch("logs/sub/deeper/c.txt", "logs/b.jsonl", "logs/sub/a.log", "logs/a.log")
    assert displays([Path("logs")]) == [
        ("logs/a.log", InputFormat.TEXT),
        ("logs/b.jsonl", InputFormat.JSONL),
        ("logs/sub/a.log", InputFormat.TEXT),
        ("logs/sub/deeper/c.txt", InputFormat.TEXT),
    ]


@pytest.mark.parametrize("spelling", ["logs/", "./logs"])
def test_directory_spelling_does_not_change_display_paths(workdir: Path, spelling: str) -> None:
    touch("logs/a.log")
    assert displays([Path(spelling)]) == [("logs/a.log", InputFormat.TEXT)]


def test_current_directory_gives_relative_display_paths(workdir: Path) -> None:
    touch("a.log", "sub/b.log")
    assert [d for d, _ in displays([Path()])] == ["a.log", "sub/b.log"]


def test_hidden_files_and_directories_are_ignored(workdir: Path) -> None:
    touch("logs/a.log", "logs/.hidden.log", "logs/.git/objects/x.log", "logs/.cache/notes.md")
    result = discover([Path("logs")])
    assert [s.display for s in result.sources] == ["logs/a.log"]
    assert result.skipped == ()


def test_unsupported_files_in_a_directory_are_skipped_and_listed(workdir: Path) -> None:
    touch("logs/a.log", "logs/notes.md", "logs/data.csv", "logs/sub/README")
    result = discover([Path("logs")])
    assert [s.display for s in result.sources] == ["logs/a.log"]
    assert result.skipped == (
        SkippedFile("logs/data.csv", SkipReason.UNSUPPORTED_EXTENSION),
        SkippedFile("logs/notes.md", SkipReason.UNSUPPORTED_EXTENSION),
        SkippedFile("logs/sub/README", SkipReason.UNSUPPORTED_EXTENSION),
    )


def test_input_format_changes_the_parser_but_not_the_file_selection(workdir: Path) -> None:
    # Decision approved for increment 2: inside directories, files are still
    # selected by extension, so README.md is never read as a log.
    touch("logs/a.log", "logs/b.jsonl", "logs/README.md")
    result = discover([Path("logs")], input_format=InputFormat.JSONL)
    assert [(s.display, s.format) for s in result.sources] == [
        ("logs/a.log", InputFormat.JSONL),
        ("logs/b.jsonl", InputFormat.JSONL),
    ]
    assert result.skipped == (SkippedFile("logs/README.md", SkipReason.UNSUPPORTED_EXTENSION),)


@posix_only
def test_symlinked_directories_are_not_followed_but_listed(workdir: Path) -> None:
    touch("logs/a.log", "elsewhere/b.log")
    Path("logs/link").symlink_to(Path("../elsewhere"), target_is_directory=True)
    result = discover([Path("logs")])
    assert [s.display for s in result.sources] == ["logs/a.log"]
    assert result.skipped == (SkippedFile("logs/link", SkipReason.SYMLINKED_DIRECTORY),)


@posix_only
def test_symlinked_files_are_read_and_broken_links_are_listed(workdir: Path) -> None:
    touch("target.log")
    Path("logs").mkdir()
    Path("logs/link.log").symlink_to(Path("../target.log"))
    Path("logs/dangling.log").symlink_to(Path("../missing.log"))
    result = discover([Path("logs")])
    assert [s.display for s in result.sources] == ["logs/link.log"]
    assert result.skipped == (SkippedFile("logs/dangling.log", SkipReason.NOT_A_REGULAR_FILE),)


@posix_only
def test_fifo_in_a_directory_is_listed_instead_of_blocking(workdir: Path) -> None:
    touch("logs/a.log")
    os.mkfifo("logs/pipe.log")
    result = discover([Path("logs")])
    assert [s.display for s in result.sources] == ["logs/a.log"]
    assert result.skipped == (SkippedFile("logs/pipe.log", SkipReason.NOT_A_REGULAR_FILE),)


@needs_permissions
def test_unreadable_subdirectory_is_listed_not_silently_ignored(workdir: Path) -> None:
    # os.walk ignores listing errors unless it is given an onerror callback.
    touch("logs/a.log", "logs/locked/b.log")
    locked = Path("logs/locked")
    locked.chmod(0)
    try:
        result = discover([Path("logs")])
    finally:
        locked.chmod(0o755)
    assert [s.display for s in result.sources] == ["logs/a.log"]
    assert result.skipped == (SkippedFile("logs/locked", SkipReason.UNREADABLE_DIRECTORY),)


def test_empty_directory_is_an_input_error(workdir: Path) -> None:
    Path("logs").mkdir()
    with pytest.raises(InputError, match="no supported log files found in logs"):
        discover([Path("logs")])


def test_directory_with_only_unsupported_files_lists_them_in_the_hint(workdir: Path) -> None:
    touch("logs/a.csv", "logs/b.md")
    with pytest.raises(InputError, match="no supported log files found in logs") as excinfo:
        discover([Path("logs")])
    hint = excinfo.value.hint or ""
    assert "logs/a.csv (unsupported_extension)" in hint
    assert "logs/b.md (unsupported_extension)" in hint


def test_hint_lists_at_most_five_skipped_files(workdir: Path) -> None:
    touch(*(f"logs/f{i}.csv" for i in range(8)))
    with pytest.raises(InputError) as excinfo:
        discover([Path("logs")])
    hint = excinfo.value.hint or ""
    assert "logs/f4.csv" in hint
    assert "logs/f5.csv" not in hint
    assert "and 3 more" in hint


# --- Deduplication and order (ING-3, NFR-2) -------------------------------------------------


def test_sources_are_sorted_by_display_path(workdir: Path) -> None:
    touch("b.log", "a.log", "c.log")
    assert [d for d, _ in displays([Path("c.log"), Path("a.log"), Path("b.log")])] == [
        "a.log",
        "b.log",
        "c.log",
    ]


def test_same_file_given_twice_is_analyzed_once(workdir: Path) -> None:
    touch("app.log")
    Path("logs").mkdir()
    sources = discover([Path("logs/../app.log"), Path("app.log"), Path("./app.log")]).sources
    # The lexicographically smallest spelling wins, whatever the argument order.
    assert [s.display for s in sources] == ["app.log"]


def test_file_given_explicitly_and_through_its_directory_is_analyzed_once(workdir: Path) -> None:
    touch("logs/a.log", "logs/b.log")
    sources = discover([Path("logs/a.log"), Path("logs")]).sources
    assert [s.display for s in sources] == ["logs/a.log", "logs/b.log"]


def test_deduplication_does_not_depend_on_argument_order(workdir: Path) -> None:
    touch("app.log")
    Path("logs").mkdir()
    forward = discover([Path("app.log"), Path("logs/../app.log")])
    backward = discover([Path("logs/../app.log"), Path("app.log")])
    assert forward == backward
