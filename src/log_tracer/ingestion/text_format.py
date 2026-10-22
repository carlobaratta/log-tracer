"""Parser for the Log Tracer text format v1 (SPEC §2.2).

    <timestamp> <LEVEL> [<service>] <message> [<key>=<value> ...]

Parsing happens in two steps: a regular expression recognizes the header
(timestamp, level, optional service), then the trailing ``key=value`` tokens
are peeled off the end of the remainder. Only *trailing* tokens are fields, so
``retry attempt=2 scheduled`` keeps ``attempt=2`` in the message.
"""

import re
from types import MappingProxyType
from typing import Final

from log_tracer.ingestion.fields import (
    TIMESTAMP_PATTERN,
    excerpt,
    normalize_identifier,
    normalize_level,
    normalize_service,
    parse_duration_ms,
    parse_timestamp,
)
from log_tracer.models import (
    Event,
    IssueCode,
    Level,
    LineOutcome,
    ParsedEvent,
    SourceRef,
    UnparsedLine,
    UnparsedReason,
)

_LINE_RE: Final = re.compile(
    rf"(?P<timestamp>{TIMESTAMP_PATTERN})"
    r"[ \t]+(?P<level>[A-Z]+)"
    r"(?:[ \t]+\[(?P<service>[^\]]*)\])?"
    r"(?:[ \t]+(?P<rest>.*))?"
)
_BLANKS: Final = " \t"
_BLANK_RUN_RE: Final = re.compile(r"([ \t]+)")
_FIELD_RE: Final = re.compile(r"(?P<key>[a-z_][a-z0-9_.]*)=(?P<value>[^ \t]+)")


def parse_text_line(text: str, ref: SourceRef) -> LineOutcome:
    """Parse one line (without its terminator). Never raises."""
    header = _LINE_RE.fullmatch(text)
    if header is None:
        return UnparsedLine(ref, UnparsedReason.NO_MATCH, excerpt(text))

    timestamp = parse_timestamp(header["timestamp"])
    if timestamp is None:
        # The shape matched but the date does not exist: in the text format the
        # timestamp anchors the grammar, so the line is not trusted (SPEC D-4).
        return UnparsedLine(ref, UnparsedReason.INVALID_TIMESTAMP, excerpt(text))

    issues: list[IssueCode] = []
    if timestamp.assumed_utc:
        issues.append(IssueCode.TIMESTAMP_ASSUMED_UTC)

    raw_level = header["level"]
    level = normalize_level(raw_level)
    if level is None:
        level = Level.UNKNOWN
        issues.append(IssueCode.LEVEL_UNKNOWN)

    message, fields = _split_trailing_fields(header["rest"] or "")
    if not message:
        issues.append(IssueCode.MESSAGE_MISSING)

    request_id = normalize_identifier(fields.pop("request_id", ""))
    trace_id = normalize_identifier(fields.pop("trace_id", ""))
    duration_ms = None
    if (raw_duration := fields.pop("duration_ms", None)) is not None:
        duration_ms = parse_duration_ms(raw_duration)
        if duration_ms is None:
            issues.append(IssueCode.DURATION_INVALID)

    event = Event(
        ref=ref,
        timestamp=timestamp.value,
        level=level,
        raw_level=raw_level,
        message=message,
        service=normalize_service(header["service"] or ""),
        request_id=request_id,
        trace_id=trace_id,
        duration_ms=duration_ms,
        attributes=MappingProxyType(fields),
    )
    return ParsedEvent(event, tuple(issues))


def _split_trailing_fields(rest: str) -> tuple[str, dict[str, str]]:
    """Split ``rest`` into the message and its trailing ``key=value`` fields.

    The text is tokenized once, keeping the separators, and tokens are popped
    from the end while they are valid fields: the cost is linear in the line
    length. Duplicate keys keep the last value.
    """
    parts = _BLANK_RUN_RE.split(rest.rstrip(_BLANKS))  # token, separator, token, ...
    trailing: list[tuple[str, str]] = []
    while parts and (field := _FIELD_RE.fullmatch(parts[-1])) is not None:
        trailing.append((field["key"], field["value"]))
        del parts[-2:]  # the token and the separator before it
    trailing.reverse()
    return "".join(parts), dict(trailing)
