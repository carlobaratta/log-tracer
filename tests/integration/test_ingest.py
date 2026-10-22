"""Ingestion of real files: counting, sampling, unreadable files (ING-4…8)."""

import hashlib
import os
import sys
from pathlib import Path

import pytest

from log_tracer.errors import InputError
from log_tracer.ingestion import ingest
from log_tracer.models import IssueCode, Level, SourceRef, UnparsedReason

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


@needs_permissions
def test_no_readable_source_is_an_input_error(workdir: Path) -> None:
    locked = write(workdir / "locked.log", "x\n")
    locked.chmod(0)
    try:
        with pytest.raises(InputError, match="none of the input files could be read"):
            ingest([Path("locked.log")], max_samples=10)
    finally:
        locked.chmod(0o644)
