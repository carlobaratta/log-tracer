"""Ingestion of real files: counting, sampling, unreadable files (ING-4…8)."""

import hashlib
import os
import sys
from pathlib import Path

import pytest

from log_tracer.errors import InputError
from log_tracer.ingestion import ingest
from log_tracer.models import (
    IngestionWarning,
    InputFormat,
    IssueCode,
    Level,
    SkippedFile,
    SkipReason,
    SourceRef,
    UnparsedReason,
    WarningCode,
)

MIXED_LOG = (
    "2026-10-16T14:00:00Z INFO [api] started\n"
    "\n"
    "2026-10-16T14:00:01Z ERROR [api] boom request_id=r1\n"
    "not a log line\n"
    "   \n"
    "2026-02-30T14:00:02Z ERROR [api] impossible date\n"
    "2026-10-16 14:00:03 NOTICE [db] vacuum\n"
    "    at Foo.bar(Foo.java:1)\n"
)


@pytest.fixture
def workdir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    return tmp_path


def write(path: Path, content: str | bytes) -> Path:
    data = content.encode("utf-8") if isinstance(content, str) else content
    path.write_bytes(data)
    return path


def test_every_line_is_accounted_for(workdir: Path) -> None:
    write(workdir / "app.log", MIXED_LOG)
    result = ingest([Path("app.log")], max_samples=10)

    (source,) = result.summary.sources
    assert source.path == "app.log"
    assert source.lines == 8
    assert source.blank_lines == 2
    assert source.events == 3
    assert source.unparsed == 3
    assert source.lines == source.blank_lines + source.events + source.unparsed
    assert source.error is None

    assert [e.ref.line for e in result.events] == [1, 3, 7]
    assert [e.level for e in result.events] == [Level.INFO, Level.ERROR, Level.UNKNOWN]


def test_unparsed_lines_are_counted_by_reason_with_samples(workdir: Path) -> None:
    write(workdir / "app.log", MIXED_LOG)
    summary = ingest([Path("app.log")], max_samples=10).summary

    assert summary.unparsed_counts == {
        UnparsedReason.NO_MATCH: 2,
        UnparsedReason.INVALID_TIMESTAMP: 1,
    }
    assert [(s.ref, s.reason) for s in summary.unparsed_samples] == [
        (SourceRef("app.log", 4), UnparsedReason.NO_MATCH),
        (SourceRef("app.log", 6), UnparsedReason.INVALID_TIMESTAMP),
        (SourceRef("app.log", 8), UnparsedReason.NO_MATCH),
    ]


def test_field_issues_are_counted(workdir: Path) -> None:
    write(workdir / "app.log", MIXED_LOG)
    summary = ingest([Path("app.log")], max_samples=10).summary
    assert summary.issue_counts == {
        IssueCode.TIMESTAMP_ASSUMED_UTC: 1,
        IssueCode.LEVEL_UNKNOWN: 1,
    }


def test_samples_are_capped_across_all_sources(workdir: Path) -> None:
    write(workdir / "a.log", "bad 1\nbad 2\nbad 3\n")
    write(workdir / "b.log", "bad 4\nbad 5\n")
    summary = ingest([Path("b.log"), Path("a.log")], max_samples=4).summary
    assert summary.unparsed_counts == {UnparsedReason.NO_MATCH: 5}
    assert [(s.ref.path, s.ref.line) for s in summary.unparsed_samples] == [
        ("a.log", 1),
        ("a.log", 2),
        ("a.log", 3),
        ("b.log", 1),
    ]


def test_invalid_utf8_on_an_event_line_is_reported(workdir: Path) -> None:
    write(workdir / "app.log", b"2026-10-16T14:00:00Z INFO [api] caf\xe9\n")
    result = ingest([Path("app.log")], max_samples=10)
    assert result.events[0].message == "caf�"
    assert result.summary.issue_counts == {IssueCode.ENCODING_INVALID: 1}


def test_file_identity_is_recorded(workdir: Path) -> None:
    data = MIXED_LOG.encode("utf-8")
    write(workdir / "app.log", data)
    (source,) = ingest([Path("app.log")], max_samples=10).summary.sources
    assert source.size_bytes == len(data)
    assert source.sha256 == hashlib.sha256(data).hexdigest()


def test_no_paths_is_an_input_error() -> None:
    with pytest.raises(InputError, match="no input files given"):
        ingest([], max_samples=10)


def test_empty_file_produces_no_events(workdir: Path) -> None:
    write(workdir / "empty.log", "")
    result = ingest([Path("empty.log")], max_samples=10)
    assert result.events == ()
    (source,) = result.summary.sources
    assert (source.lines, source.events, source.unparsed, source.error) == (0, 0, 0, None)


needs_permissions = pytest.mark.skipif(
    sys.platform == "win32" or (hasattr(os, "geteuid") and os.geteuid() == 0),
    reason="file permissions cannot make a file unreadable here",
)


@needs_permissions
def test_unreadable_file_is_recorded_and_others_are_analyzed(workdir: Path) -> None:
    write(workdir / "ok.log", "2026-10-16T14:00:00Z INFO [api] fine\n")
    locked = write(workdir / "locked.log", "2026-10-16T14:00:00Z INFO [api] secret\n")
    locked.chmod(0)
    try:
        result = ingest([Path("ok.log"), Path("locked.log")], max_samples=10)
    finally:
        locked.chmod(0o644)

    by_path = {s.path: s for s in result.summary.sources}
    assert by_path["locked.log"].error == "Permission denied"
    assert by_path["locked.log"].sha256 is None
    assert by_path["locked.log"].events == 0
    assert by_path["ok.log"].error is None
    assert [e.message for e in result.events] == ["fine"]
    assert result.summary.warnings == (
        IngestionWarning(
            WarningCode.UNREADABLE_FILE,
            "locked.log",
            "locked.log could not be read (Permission denied) and was skipped",
        ),
    )


@needs_permissions
def test_no_readable_source_is_an_input_error(workdir: Path) -> None:
    locked = write(workdir / "locked.log", "x\n")
    locked.chmod(0)
    try:
        with pytest.raises(InputError, match="none of the input files could be read"):
            ingest([Path("locked.log")], max_samples=10)
    finally:
        locked.chmod(0o644)


# --- Formats and directories (ING-1, ING-2, JL-1) -------------------------------------

TEXT_LINES = "2026-10-16T14:00:00Z INFO [api] text event\n"
JSON_LINES = (
    '{"timestamp": "2026-10-16T14:00:01Z", "level": "error", "message": "json event"}\n'
    "{broken json\n"
    '["not", "an", "object"]\n'
)


def test_directory_with_mixed_formats(workdir: Path) -> None:
    (workdir / "logs").mkdir()
    write(workdir / "logs" / "app.log", TEXT_LINES)
    write(workdir / "logs" / "svc.jsonl", JSON_LINES)
    write(workdir / "logs" / "notes.md", "# not a log\n")

    result = ingest([Path("logs")], max_samples=10)

    assert [(s.path, s.format, s.events, s.unparsed) for s in result.summary.sources] == [
        ("logs/app.log", InputFormat.TEXT, 1, 0),
        ("logs/svc.jsonl", InputFormat.JSONL, 1, 2),
    ]
    assert [e.message for e in result.events] == ["text event", "json event"]
    assert result.summary.unparsed_counts == {
        UnparsedReason.INVALID_JSON: 1,
        UnparsedReason.NOT_AN_OBJECT: 1,
    }
    assert result.summary.skipped == (
        SkippedFile("logs/notes.md", SkipReason.UNSUPPORTED_EXTENSION),
    )


def test_input_format_forces_the_parser(workdir: Path) -> None:
    write(workdir / "app.log", JSON_LINES)
    result = ingest([Path("app.log")], input_format=InputFormat.JSONL, max_samples=10)
    (source,) = result.summary.sources
    assert source.format is InputFormat.JSONL
    assert [e.message for e in result.events] == ["json event"]


def test_an_encoding_issue_is_counted_once_per_event(workdir: Path) -> None:
    # Invalid UTF-8 bytes (reader) and a lone surrogate escape (JSON parser)
    # on the same line are one problem for one event.
    line = b'{"level": "info", "message": "caf\xe9 \\ud800"}\n'
    write(workdir / "app.jsonl", line)
    summary = ingest([Path("app.jsonl")], max_samples=10).summary
    assert summary.issue_counts[IssueCode.ENCODING_INVALID] == 1


# --- Mostly unparsed files (ING-9) ---------------------------------------------------------


def text_file(events: int, unparsed: int, blank: int = 0) -> str:
    good = "2026-10-16T14:00:00Z INFO [api] ok\n"
    return good * events + "garbage\n" * unparsed + "\n" * blank


@pytest.mark.parametrize(
    ("events", "unparsed", "warned"),
    [(1, 1, True), (2, 2, True), (0, 3, True), (51, 49, False), (3, 0, False), (0, 0, False)],
)
def test_warning_when_at_least_half_of_the_lines_are_unparsed(
    workdir: Path, events: int, unparsed: int, warned: bool
) -> None:
    write(workdir / "app.log", text_file(events, unparsed, blank=5))  # blanks never count
    warnings = ingest([Path("app.log")], max_samples=10).summary.warnings
    assert [w.code for w in warnings] == ([WarningCode.MOSTLY_UNPARSED] if warned else [])


def test_mostly_unparsed_warning_names_the_file_share_and_format(workdir: Path) -> None:
    write(workdir / "app.log", text_file(events=1, unparsed=2))
    (warning,) = ingest([Path("app.log")], max_samples=10).summary.warnings
    assert warning == IngestionWarning(
        WarningCode.MOSTLY_UNPARSED,
        "app.log",
        "66% of the non-blank lines in app.log could not be parsed as text",
    )


def test_warnings_follow_the_source_order(workdir: Path) -> None:
    write(workdir / "b.log", text_file(events=0, unparsed=1))
    write(workdir / "a.log", text_file(events=0, unparsed=1))
    write(workdir / "c.log", text_file(events=1, unparsed=0))
    warnings = ingest(
        [Path("b.log"), Path("c.log"), Path("a.log")], max_samples=10
    ).summary.warnings
    assert [w.path for w in warnings] == ["a.log", "b.log"]
