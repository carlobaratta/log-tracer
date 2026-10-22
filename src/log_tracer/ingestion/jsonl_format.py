"""Parser for JSON Lines: one JSON object per line (SPEC §2.3).

Field names are matched through aliases, in priority order, so that logs from
different libraries map onto the same model. Values are coerced field by field
(JL-3): an unacceptable value leaves the field empty and records an issue, but
never discards the event.
"""

import json
import re
from collections.abc import Mapping
from datetime import datetime
from types import MappingProxyType
from typing import Final

from log_tracer.ingestion.fields import (
    excerpt,
    normalize_duration_number,
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

ALIASES: Final[Mapping[str, tuple[str, ...]]] = {
    "timestamp": ("timestamp", "@timestamp", "ts", "time"),
    "level": ("level", "severity", "levelname", "lvl"),
    "message": ("message", "msg"),
    "service": ("service", "service_name", "app"),
    "request_id": ("request_id", "requestId", "req_id"),
    "trace_id": ("trace_id", "traceId"),
    "duration_ms": ("duration_ms", "durationMs"),
}
"""Field name → accepted keys, highest priority first (JL-2)."""

_ALIAS_KEYS: Final = frozenset(key for keys in ALIASES.values() for key in keys)
_ANCHORS: Final = ("timestamp", "level", "message")
"""An object needs at least one of these to be considered a log event."""
_LONE_SURROGATE_RE: Final = re.compile("[\ud800-\udfff]")


def parse_jsonl_line(text: str, ref: SourceRef) -> LineOutcome:
    """Parse one line (without its terminator). Never raises."""
    try:
        data: object = json.loads(text)
    except (ValueError, RecursionError):
        # ValueError: syntax errors and integers beyond Python's conversion
        # limit. RecursionError: nesting deeper than the interpreter allows.
        return UnparsedLine(ref, UnparsedReason.INVALID_JSON, excerpt(text))
    if not isinstance(data, dict):
        return UnparsedLine(ref, UnparsedReason.NOT_AN_OBJECT, excerpt(text))

    fields, extra = _split_fields(data)
    if not any(name in fields for name in _ANCHORS):
        return UnparsedLine(ref, UnparsedReason.NO_KNOWN_FIELDS, excerpt(text))
    return _Builder(ref).build(fields, extra)


def _split_fields(data: dict[str, object]) -> tuple[dict[str, object], dict[str, object]]:
    """Pick the first non-null alias of each field; everything else is extra.

    A null value counts as absent. Losing aliases are kept as attributes,
    unless they are null.
    """
    fields: dict[str, object] = {}
    used: set[str] = set()
    for name, keys in ALIASES.items():
        for key in keys:
            if data.get(key) is not None:
                fields[name] = data[key]
                used.add(key)
                break
    extra = {
        key: value
        for key, value in data.items()
        if key not in used and not (key in _ALIAS_KEYS and value is None)
    }
    return fields, extra


class _Builder:
    """Coerce the selected fields into an :class:`Event`, collecting issues in field order."""

    def __init__(self, ref: SourceRef) -> None:
        self._ref = ref
        self._issues: list[IssueCode] = []
        self._replaced_surrogates = False

    def build(self, fields: dict[str, object], extra: dict[str, object]) -> ParsedEvent:
        timestamp = self._timestamp(fields.get("timestamp"))
        level, raw_level = self._level(fields.get("level"))
        message = self._message(fields.get("message"))
        service = self._service(fields.get("service"))
        request_id = self._identifier(fields.get("request_id"), IssueCode.REQUEST_ID_INVALID)
        trace_id = self._identifier(fields.get("trace_id"), IssueCode.TRACE_ID_INVALID)
        duration_ms = self._duration(fields.get("duration_ms"))
        attributes = {
            self._clean(key): self._clean(value if isinstance(value, str) else _compact(value))
            for key, value in extra.items()
        }
        if self._replaced_surrogates:
            self._issues.append(IssueCode.ENCODING_INVALID)
        event = Event(
            ref=self._ref,
            timestamp=timestamp,
            level=level,
            raw_level=raw_level,
            message=message,
            service=service,
            request_id=request_id,
            trace_id=trace_id,
            duration_ms=duration_ms,
            attributes=MappingProxyType(attributes),
        )
        return ParsedEvent(event, tuple(self._issues))

    def _timestamp(self, value: object) -> datetime | None:
        if value is None:
            self._issues.append(IssueCode.TIMESTAMP_MISSING)
            return None
        parsed = parse_timestamp(value) if isinstance(value, str) else None
        if parsed is None:
            self._issues.append(IssueCode.TIMESTAMP_INVALID)
            return None
        if parsed.assumed_utc:
            self._issues.append(IssueCode.TIMESTAMP_ASSUMED_UTC)
        return parsed.value

    def _level(self, value: object) -> tuple[Level, str | None]:
        if value is None:
            self._issues.append(IssueCode.LEVEL_MISSING)
            return Level.UNKNOWN, None
        raw = self._clean(value if isinstance(value, str) else _compact(value))
        level = normalize_level(value) if isinstance(value, str) else None
        if level is None:
            # Numeric levels (pino, logging.levelno) are not mapped: SPEC §2.3.
            self._issues.append(IssueCode.LEVEL_UNKNOWN)
            return Level.UNKNOWN, raw
        return level, raw

    def _message(self, value: object) -> str:
        if value is None or value == "":
            self._issues.append(IssueCode.MESSAGE_MISSING)
            return ""
        if not isinstance(value, str):
            self._issues.append(IssueCode.MESSAGE_INVALID)
            return self._clean(_compact(value))
        return self._clean(value)

    def _service(self, value: object) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            self._issues.append(IssueCode.SERVICE_INVALID)
            return None
        return normalize_service(self._clean(value))

    def _identifier(self, value: object, invalid: IssueCode) -> str | None:
        if value is None:
            return None
        if isinstance(value, str):
            return normalize_identifier(self._clean(value))
        if isinstance(value, int) and not isinstance(value, bool):
            return normalize_identifier(str(value))
        self._issues.append(invalid)
        return None

    def _duration(self, value: object) -> float | None:
        if value is None:
            return None
        duration = None
        if isinstance(value, str):
            duration = parse_duration_ms(value)
        elif isinstance(value, int | float) and not isinstance(value, bool):
            duration = normalize_duration_number(value)
        if duration is None:
            self._issues.append(IssueCode.DURATION_INVALID)
        return duration

    def _clean(self, text: str) -> str:
        """Replace lone surrogates, which ``json.loads`` produces from ``"\\ud800"``.

        They are not valid Unicode scalar values and could not be written as
        UTF-8 later, when the report is printed.
        """
        cleaned, count = _LONE_SURROGATE_RE.subn("�", text)
        if count:
            self._replaced_surrogates = True
        return cleaned


def _compact(value: object) -> str:
    """Compact, key-sorted JSON text for a non-string value."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
