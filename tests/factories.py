"""Builders for domain objects, so that tests state only what they care about."""

import dataclasses
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from types import MappingProxyType

from log_tracer.config import AnalysisConfig
from log_tracer.models import (
    AnalysisResult,
    CorrelationGroup,
    DataWarning,
    ErrorGroup,
    Event,
    EvidenceValue,
    Finding,
    IngestionSummary,
    InputFormat,
    Inspection,
    IssueCode,
    Level,
    Report,
    RuleName,
    RuleOutcome,
    Severity,
    SkippedFile,
    SkipReason,
    SourceRef,
    SourceSummary,
    TimeRange,
    UnparsedLine,
    UnparsedReason,
    WarningCode,
)

T0 = datetime(2026, 10, 16, 14, 0, 0, tzinfo=UTC)


def at(seconds: float) -> datetime:
    """A timestamp ``seconds`` after :data:`T0`."""
    return T0 + timedelta(seconds=seconds)


def make_event(
    *,
    message: str = "something happened",
    level: Level = Level.INFO,
    timestamp: datetime | None = T0,
    service: str | None = "api",
    path: str = "app.log",
    line: int = 1,
    request_id: str | None = None,
    trace_id: str | None = None,
    duration_ms: float | None = None,
    raw_level: str | None = None,
    attributes: Mapping[str, str] | None = None,
) -> Event:
    return Event(
        ref=SourceRef(path, line),
        timestamp=timestamp,
        level=level,
        raw_level=raw_level if raw_level is not None else level.name,
        message=message,
        service=service,
        request_id=request_id,
        trace_id=trace_id,
        duration_ms=duration_ms,
        attributes=MappingProxyType(dict(attributes or {})),
    )


# --- Reports ----------------------------------------------------------------------

SOURCE = SourceSummary(
    path="logs/app.log",
    format=InputFormat.TEXT,
    size_bytes=1234,
    sha256="ab" * 32,
    lines=10,
    blank_lines=1,
    events=7,
    unparsed=2,
    error=None,
)

SKIPPED = SkippedFile("logs/notes.md", SkipReason.UNSUPPORTED_EXTENSION)

WARNING = DataWarning(
    WarningCode.MOSTLY_UNPARSED,
    "logs/app.log",
    "66% of the non-blank lines in logs/app.log could not be parsed as text",
)

FINDING = Finding(
    id="recurring_error:0123456789ab",
    rule=RuleName.RECURRING_ERROR,
    severity=Severity.MEDIUM,
    title="Recurring error in payments: timeout",
    description="3 error events from service 'payments' match the pattern \"timeout\".",
    evidence=MappingProxyType({"count": 3, "has_critical": False, "first_seen": at(1)}),
    event_count=3,
    events=(SourceRef("logs/app.log", 4),),
    correlation_ids=(),
)

TOP_ERROR = ErrorGroup(
    service="payments",
    fingerprint="timeout",
    count=3,
    has_critical=False,
    first_seen=at(1),
    last_seen=at(9),
    events=(SourceRef("logs/app.log", 4),),
    correlation_groups=0,
    correlation_ids=(),
)


def make_report(
    *,
    sources: tuple[SourceSummary, ...] = (SOURCE,),
    skipped: tuple[SkippedFile, ...] = (SKIPPED,),
    unparsed_counts: Mapping[UnparsedReason, int] | None = None,
    issue_counts: Mapping[IssueCode, int] | None = None,
    warnings: tuple[DataWarning, ...] = (WARNING,),
    level_counts: Mapping[Level, int] | None = None,
    time_range: TimeRange | None = None,
    top_errors: tuple[ErrorGroup, ...] = (TOP_ERROR,),
    findings: tuple[Finding, ...] = (FINDING,),
    rules: tuple[RuleOutcome, ...] | None = None,
    evidence: dict[str, EvidenceValue] | None = None,
    correlation_groups: int = 0,
    uncorrelated_events: int = 0,
    analysis_warnings: tuple[DataWarning, ...] = (),
) -> Report:
    """A small but complete report; every part can be replaced by a test."""
    if evidence is not None:
        findings = tuple(
            dataclasses.replace(f, evidence=MappingProxyType(evidence)) for f in findings
        )
    return Report(
        tool_version="0.1.0",
        generated_at=T0,
        config=AnalysisConfig(),
        ingestion=IngestionSummary(
            sources=sources,
            skipped=skipped,
            unparsed_counts=MappingProxyType(
                dict({UnparsedReason.NO_MATCH: 2} if unparsed_counts is None else unparsed_counts)
            ),
            unparsed_samples=(
                UnparsedLine(SourceRef("logs/app.log", 5), UnparsedReason.NO_MATCH, "garbage"),
            ),
            issue_counts=MappingProxyType(
                dict({IssueCode.LEVEL_UNKNOWN: 1} if issue_counts is None else issue_counts)
            ),
            warnings=warnings,
        ),
        analysis=AnalysisResult(
            level_counts=MappingProxyType(
                dict({Level.INFO: 4, Level.ERROR: 3} if level_counts is None else level_counts)
            ),
            time_range=time_range,
            top_errors=top_errors,
            rules=(
                (RuleOutcome(RuleName.RECURRING_ERROR, "ok", None, findings),)
                if rules is None
                else rules
            ),
            findings=findings,
            correlation_groups=correlation_groups,
            uncorrelated_events=uncorrelated_events,
            warnings=analysis_warnings,
        ),
    )


def make_inspection(
    *, query: str = "req-184", groups: tuple[CorrelationGroup, ...] | None = None
) -> Inspection:
    """An inspection that reuses the inputs of :func:`make_report`."""
    if groups is None:
        events = (
            make_event(
                line=2,
                timestamp=at(0),
                service="api-gateway",
                message="POST /checkout",
                request_id="req-184",
                trace_id="t-1",
            ),
            make_event(
                line=5,
                timestamp=at(2.5),
                level=Level.ERROR,
                service="payments",
                message="timeout",
                request_id="req-184",
                trace_id="t-1",
                duration_ms=2500.0,
                attributes={"attempt": "2"},
            ),
        )
        groups = (CorrelationGroup("t-1", ("req-184",), ("t-1",), events),)
    report = make_report()
    return Inspection(
        tool_version="0.1.0",
        generated_at=T0,
        query=query,
        groups=groups,
        ingestion=report.ingestion,
    )
