"""Normalization of raw field values, shared by every input format (SPEC §2.4).

Every function here is total: it returns a normalized value, or ``None`` when
the raw value is absent or invalid, and never raises. Deciding which problem
code to record is left to the caller, which knows the context.
"""

import math
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final

from log_tracer.models import Level

_LEVEL_ALIASES: Final = {
    "trace": Level.TRACE,
    "debug": Level.DEBUG,
    "info": Level.INFO,
    "warning": Level.WARNING,
    "warn": Level.WARNING,
    "error": Level.ERROR,
    "err": Level.ERROR,
    "critical": Level.CRITICAL,
    "crit": Level.CRITICAL,
    "fatal": Level.CRITICAL,
}

TIMESTAMP_PATTERN: Final = (
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}[T ][0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:[.,][0-9]+)?(?:Z|[+-][0-9]{2}:?[0-9]{2})?"
)
"""Shape of an accepted timestamp. Explicit ``[0-9]`` excludes non-ASCII digits."""

_TIMESTAMP_RE: Final = re.compile(TIMESTAMP_PATTERN)
_ID_SENTINELS: Final = frozenset({"", "-", "null", "none", "n/a"})
_ALL_ZEROS_RE: Final = re.compile(r"0+")
_DURATION_RE: Final = re.compile(r"[0-9]+(?:\.[0-9]+)?")

EXCERPT_LENGTH: Final = 200


def excerpt(text: str) -> str:
    """The beginning of a raw line, as stored for unparsed-line samples (ING-6)."""
    return text[:EXCERPT_LENGTH]


@dataclass(frozen=True, slots=True)
class ParsedTimestamp:
    value: datetime
    """Timezone-aware, in UTC."""
    assumed_utc: bool
    """True when the source had no offset and UTC was assumed (SPEC D-3)."""


def normalize_level(raw: str) -> Level | None:
    """Map a level name or alias, case-insensitively; ``None`` if unrecognized."""
    return _LEVEL_ALIASES.get(raw.strip().lower())


def parse_timestamp(raw: str) -> ParsedTimestamp | None:
    """Parse an ISO 8601 date-time and convert it to UTC.

    The shape is checked with :data:`TIMESTAMP_PATTERN` first, so that
    ``datetime.fromisoformat`` (which also accepts a bare date) only sees
    strings that already have a time part.
    """
    if _TIMESTAMP_RE.fullmatch(raw) is None:
        return None
    if raw[11:13] == "24":
        # Python 3.14 reads "24:00:00" as midnight of the next day; 3.12 and
        # 3.13 reject it. RFC 3339 allows hours 00-23 only: rejecting 24
        # everywhere keeps the result independent of the Python version.
        return None
    try:
        value = datetime.fromisoformat(raw)
        if value.tzinfo is None:
            return ParsedTimestamp(value.replace(tzinfo=UTC), assumed_utc=True)
        return ParsedTimestamp(value.astimezone(UTC), assumed_utc=False)
    except (ValueError, OverflowError):
        # ValueError: impossible date or offset. OverflowError: the conversion
        # to UTC leaves the representable range (e.g. year 1 with a positive offset).
        return None


def normalize_identifier(raw: str) -> str | None:
    """Trim an identifier; sentinel values and all-zero ids mean "absent"."""
    value = raw.strip()
    if value.lower() in _ID_SENTINELS or _ALL_ZEROS_RE.fullmatch(value):
        return None
    return value


def normalize_service(raw: str) -> str | None:
    """Trim a service name; an empty name means "absent"."""
    return raw.strip() or None


def parse_duration_ms(raw: str) -> float | None:
    """Parse a non-negative decimal number of milliseconds.

    A strict pattern is used instead of ``float()``, which would also accept
    ``nan``, ``inf``, exponents, underscores and surrounding whitespace.
    """
    if _DURATION_RE.fullmatch(raw) is None:
        return None
    return float(raw)


def normalize_duration_number(value: int | float) -> float | None:
    """Accept a JSON number of milliseconds if it is finite and non-negative."""
    try:
        duration = float(value)
    except OverflowError:  # an integer too large to be represented as a float
        return None
    if not math.isfinite(duration) or duration < 0:
        return None
    return duration + 0.0  # turns -0.0 into 0.0
