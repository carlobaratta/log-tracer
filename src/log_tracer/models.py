"""Domain data types shared by every layer.

These classes hold data only: parsing, analysis and formatting live in their
own modules. Every class is a frozen dataclass whose collections are tuples or
read-only ``Mapping`` views, so that events cannot change after normalization.
"""

import enum
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from log_tracer.config import AnalysisConfig

# --- Ingestion -------------------------------------------------------------


class Level(enum.IntEnum):
    """Severity declared by a log line, ranked like the stdlib ``logging`` levels.

    ``UNKNOWN`` ranks below every other level, so it never satisfies a threshold.
    """

    UNKNOWN = 0
    TRACE = 5
    DEBUG = 10
    INFO = 20
    WARNING = 30
    ERROR = 40
    CRITICAL = 50


class InputFormat(enum.StrEnum):
    TEXT = "text"


@dataclass(frozen=True, slots=True, order=True)
class SourceRef:
    """Where an event comes from: source path (POSIX, as given) and 1-based line."""

    path: str
    line: int


@dataclass(frozen=True, slots=True)
class Event:
    """A log line normalized into the common model, independent of its format."""

    ref: SourceRef
    timestamp: datetime | None
    """Always timezone-aware and in UTC when present."""
    level: Level
    raw_level: str | None
    """The level as written in the source, kept for ``UNKNOWN`` levels."""
    message: str
    service: str | None
    request_id: str | None
    trace_id: str | None
    duration_ms: float | None
    attributes: Mapping[str, str] = field(hash=False)
    """Extra ``key=value`` fields that are not part of the model."""


class IssueCode(enum.StrEnum):
    """A field problem: the event is kept, but a value was missing or assumed."""

    TIMESTAMP_ASSUMED_UTC = "timestamp.assumed_utc"
    LEVEL_UNKNOWN = "level.unknown"
    MESSAGE_MISSING = "message.missing"
    DURATION_INVALID = "duration_ms.invalid"
    ENCODING_INVALID = "encoding.invalid"


class UnparsedReason(enum.StrEnum):
    """Why a line could not become an event."""

    NO_MATCH = "no_match"
    INVALID_TIMESTAMP = "invalid_timestamp"


@dataclass(frozen=True, slots=True)
class ParsedEvent:
    event: Event
    issues: tuple[IssueCode, ...]


@dataclass(frozen=True, slots=True)
class UnparsedLine:
    ref: SourceRef
    reason: UnparsedReason
    excerpt: str
    """The beginning of the raw line, truncated to 200 characters."""


LineOutcome = ParsedEvent | UnparsedLine
"""Result of parsing one line. Parsers return it for any input and never raise."""


@dataclass(frozen=True, slots=True)
class SourceSummary:
    path: str
    format: InputFormat
    size_bytes: int | None
    sha256: str | None
    lines: int
    blank_lines: int
    events: int
    unparsed: int
    error: str | None
    """Set when the file could not be read; the counters are then zero (ING-7)."""


@dataclass(frozen=True, slots=True)
class IngestionSummary:
    sources: tuple[SourceSummary, ...]
    unparsed_counts: Mapping[UnparsedReason, int]
    unparsed_samples: tuple[UnparsedLine, ...]
    issue_counts: Mapping[IssueCode, int]


@dataclass(frozen=True, slots=True)
class IngestionResult:
    events: tuple[Event, ...]
    summary: IngestionSummary


# --- Analysis --------------------------------------------------------------


class Severity(enum.IntEnum):
    """Severity of a finding. Distinct from :class:`Level`, the severity of a log line."""

    LOW = 1
    MEDIUM = 2
    HIGH = 3


class RuleName(enum.StrEnum):
    RECURRING_ERROR = "recurring_error"


@dataclass(frozen=True, slots=True)
class TimeRange:
    start: datetime
    end: datetime


@dataclass(frozen=True, slots=True)
class ErrorGroup:
    """Error events that share a service and a message fingerprint."""

    service: str | None
    fingerprint: str
    count: int
    has_critical: bool
    first_seen: datetime | None
    last_seen: datetime | None
    events: tuple[SourceRef, ...]
    """A sample in timeline order, capped at ``report.max_examples``."""


EvidenceValue = str | int | float | bool | datetime | None


@dataclass(frozen=True, slots=True)
class Finding:
    id: str
    """Stable across identical runs: ``<rule>:<12 hex chars of a SHA-256>``."""
    rule: RuleName
    severity: Severity
    title: str
    description: str
    evidence: Mapping[str, EvidenceValue]
    event_count: int
    events: tuple[SourceRef, ...]
    correlation_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RuleOutcome:
    rule: RuleName
    status: Literal["ok", "skipped"]
    reason: str | None
    findings: tuple[Finding, ...]


@dataclass(frozen=True, slots=True)
class AnalysisResult:
    level_counts: Mapping[Level, int]
    time_range: TimeRange | None
    top_errors: tuple[ErrorGroup, ...]
    rules: tuple[RuleOutcome, ...]
    findings: tuple[Finding, ...]
    """Sorted by severity (descending), then rule, then id (RUL-0.4)."""


# --- Report ----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Report:
    """Everything a report needs. It references events but never embeds them (REP-2)."""

    tool_version: str
    generated_at: datetime
    config: AnalysisConfig
    ingestion: IngestionSummary
    analysis: AnalysisResult
