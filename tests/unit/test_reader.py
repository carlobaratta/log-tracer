"""Line reading: numbering, line endings, encoding and digest (ING-4, ING-8)."""

import hashlib
import io

from log_tracer.ingestion.reader import DigestingReader, RawLine


def read_all(data: bytes) -> tuple[list[RawLine], DigestingReader]:
    reader = DigestingReader(io.BytesIO(data))
    return list(reader), reader


def test_lines_are_numbered_from_one() -> None:
    lines, _ = read_all(b"first\nsecond\nthird\n")
    assert [(line.number, line.text) for line in lines] == [
        (1, "first"),
        (2, "second"),
        (3, "third"),
    ]


def test_last_line_without_newline_is_read() -> None:
    lines, _ = read_all(b"first\nsecond")
    assert [line.text for line in lines] == ["first", "second"]


def test_crlf_line_endings_are_removed() -> None:
    lines, _ = read_all(b"first\r\nsecond\r\n")
    assert [line.text for line in lines] == ["first", "second"]


def test_blank_lines_are_kept_with_their_number() -> None:
    lines, _ = read_all(b"first\n\n   \nfourth\n")
    assert [(line.number, line.text) for line in lines] == [
        (1, "first"),
        (2, ""),
        (3, "   "),
        (4, "fourth"),
    ]


def test_utf8_bom_is_ignored_on_the_first_line_only() -> None:
    lines, _ = read_all(b"\xef\xbb\xbffirst\n\xef\xbb\xbfsecond\n")
    assert lines[0].text == "first"
    assert lines[1].text == "﻿second"


def test_invalid_utf8_is_replaced_and_flagged() -> None:
    lines, _ = read_all(b"caf\xe9 ok\nvalid \xc3\xa8\n")
    assert lines[0].text == "caf� ok"
    assert lines[0].invalid_utf8 is True
    assert lines[1].text == "valid è"
    assert lines[1].invalid_utf8 is False


def test_other_unicode_line_separators_do_not_split_lines() -> None:
    # str.splitlines() would split on these and shift every following line number.
    lines, _ = read_all("a\u2028b\x0cc\nnext\n".encode())
    assert [line.text for line in lines] == ["a\u2028b\x0cc", "next"]


def test_empty_stream_yields_no_lines() -> None:
    lines, reader = read_all(b"")
    assert lines == []
    assert reader.size_bytes == 0
    assert reader.sha256 == hashlib.sha256(b"").hexdigest()


def test_digest_and_size_cover_every_byte_read() -> None:
    data = b"\xef\xbb\xbfone\r\ntwo\n\nthree"
    _, reader = read_all(data)
    assert reader.size_bytes == len(data)
    assert reader.sha256 == hashlib.sha256(data).hexdigest()
