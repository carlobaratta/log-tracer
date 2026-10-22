"""Line-by-line reading of a binary stream (ING-4, ING-8).

The stream is read as bytes and split on ``\\n`` only. Decoding each line
separately pinpoints invalid UTF-8 to a single line, and avoids
``str.splitlines()``, which would also split on characters such as U+2028
and shift every following line number.
"""

import codecs
import hashlib
from collections.abc import Iterator
from dataclasses import dataclass
from typing import BinaryIO


@dataclass(frozen=True, slots=True)
class RawLine:
    number: int
    """1-based line number."""
    text: str
    """Decoded line, without its line terminator."""
    invalid_utf8: bool
    """True when undecodable bytes were replaced with U+FFFD."""


class DigestingReader:
    """Iterate the lines of a stream while hashing every byte read.

    The SHA-256 digest and the size identify the analyzed file in the report
    (REP-1); computing them here avoids reading the file twice. They are
    complete once iteration has finished.
    """

    def __init__(self, stream: BinaryIO) -> None:
        self._stream = stream
        self._sha256 = hashlib.sha256()
        self._size_bytes = 0

    def __iter__(self) -> Iterator[RawLine]:
        for number, raw in enumerate(self._stream, start=1):
            self._sha256.update(raw)
            self._size_bytes += len(raw)
            yield _decode(number, raw)

    @property
    def sha256(self) -> str:
        return self._sha256.hexdigest()

    @property
    def size_bytes(self) -> int:
        return self._size_bytes


def _decode(number: int, raw: bytes) -> RawLine:
    if number == 1 and raw.startswith(codecs.BOM_UTF8):
        raw = raw[len(codecs.BOM_UTF8) :]
    raw = raw.removesuffix(b"\n").removesuffix(b"\r")
    try:
        return RawLine(number, raw.decode("utf-8"), invalid_utf8=False)
    except UnicodeDecodeError:
        return RawLine(number, raw.decode("utf-8", errors="replace"), invalid_utf8=True)
