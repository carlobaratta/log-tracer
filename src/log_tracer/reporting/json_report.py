"""JSON encoding of the report (SPEC §6).

The encoding is canonical: keys have a fixed order, every enum member is
listed even when its count is zero, and timestamps are RFC 3339 in UTC. The
same report therefore always produces the same bytes (NFR-2).
"""

import json
from collections import Counter
from datetime import UTC, datetime
from typing import Final

from log_tracer.config import AnalysisConfig
from log_tracer.models import (
    CorrelationGroup,
    DataWarning,
    ErrorGroup,
    Event,
    EvidenceValue,
    Finding,
    Inspection,
    IssueCode,
    Level,
    Report,
    RuleOutcome,
    Severity,
    SkippedFile,
    SourceRef,
    SourceSummary,
    UnparsedLine,
    UnparsedReason,
)

SCHEMA_VERSION: Final = "1.0"
TOOL_NAME: Final = "log-tracer"

type JsonValue = str | int | float | bool | list[JsonValue] | dict[str, JsonValue] | None
type JsonObject = dict[str, JsonValue]

_LEVEL_ORDER: Final = (
    Level.CRITICAL,
    Level.ERROR,
    Level.WARNING,
    Level.INFO,
    Level.DEBUG,
    Level.TRACE,
    Level.UNKNOWN,
)
_SEVERITY_ORDER: Final = (Severity.HIGH, Severity.MEDIUM, Severity.LOW)


def dumps(report: Report) -> str:
    """Serialize ``report`` as a single JSON document followed by a newline (CLI-1)."""
    return _dumps(report_to_dict(report))


def dumps_top_errors(report: Report) -> str:
    """Serialize the ``top-errors`` document (see :func:`top_errors_to_dict`)."""
    return _dumps(top_errors_to_dict(report))


def _dumps(data: JsonObject) -> str:
    # allow_nan=False turns a NaN or infinity that slipped into the data into
    # an error, instead of emitting ``NaN``, which is not valid JSON.
    return json.dumps(data, ensure_ascii=False, allow_nan=False, indent=2) + "\n"


def dumps_inspection(inspection: Inspection) -> str:
    """Serialize the ``inspect`` document (see :func:`inspection_to_dict`)."""
    return _dumps(inspection_to_dict(inspection))


def inspection_to_dict(inspection: Inspection) -> JsonObject:
    """The ``inspect`` output (REP-6): the matching sequences with complete events.

    Unlike the report, which only references events (REP-2), this document
    contains their content: showing it is the purpose of ``inspect``.
    """
    ingestion = inspection.ingestion
    return {
        "schema_version": SCHEMA_VERSION,
        "tool": {"name": TOOL_NAME, "version": inspection.tool_version},
        "generated_at": format_timestamp(inspection.generated_at),
        "query": inspection.query,
        "inputs": [_source(source) for source in ingestion.sources],
        "skipped_files": [_skipped(skipped) for skipped in ingestion.skipped],
        "sequences": [_sequence(group) for group in inspection.groups],
    }


def top_errors_to_dict(report: Report) -> JsonObject:
    """The ``top-errors`` output: report metadata, inputs and error groups only.

    Every part has the same shape as in the full report, so a consumer can
    parse both with the same code.
    """
    full = report_to_dict(report)
    keys = ("schema_version", "tool", "generated_at", "inputs", "skipped_files", "top_errors")
    return {key: full[key] for key in keys}


def report_to_dict(report: Report) -> JsonObject:
    ingestion, analysis = report.ingestion, report.analysis
    return {
        "schema_version": SCHEMA_VERSION,
        "tool": {"name": TOOL_NAME, "version": report.tool_version},
        "generated_at": format_timestamp(report.generated_at),
        "config": _config(report.config),
        "inputs": [_source(source) for source in ingestion.sources],
        "skipped_files": [_skipped(skipped) for skipped in ingestion.skipped],
        "summary": _summary(report),
        "data_quality": _data_quality(report),
        "rules": [_rule(outcome) for outcome in analysis.rules],
        "top_errors": [_error_group(group) for group in analysis.top_errors],
        "findings": [_finding(finding) for finding in analysis.findings],
    }


def format_timestamp(value: datetime) -> str:
    """RFC 3339 in UTC with microseconds, e.g. ``2026-10-16T14:03:22.120000Z``."""
    if value.tzinfo is None:
        # Converting a naive datetime would silently use the local time zone.
        raise ValueError(f"expected a timezone-aware datetime, got {value!r}")
    return value.astimezone(UTC).isoformat(timespec="microseconds").removesuffix("+00:00") + "Z"


def _optional_timestamp(value: datetime | None) -> str | None:
    return None if value is None else format_timestamp(value)


def _summary(report: Report) -> JsonObject:
    ingestion, analysis = report.ingestion, report.analysis
    time_range = analysis.time_range
    severities = Counter(finding.severity for finding in analysis.findings)
    return {
        "events": sum(source.events for source in ingestion.sources),
        "unparsed_lines": sum(ingestion.unparsed_counts.values()),
        "time_range": {
            "start": _optional_timestamp(time_range.start if time_range else None),
            "end": _optional_timestamp(time_range.end if time_range else None),
        },
        "levels": {level.name: analysis.level_counts.get(level, 0) for level in _LEVEL_ORDER},
        "correlation_groups": analysis.correlation_groups,
        "uncorrelated_events": analysis.uncorrelated_events,
        "findings_by_severity": {
            severity.name.lower(): severities[severity] for severity in _SEVERITY_ORDER
        },
    }


def _data_quality(report: Report) -> JsonObject:
    ingestion = report.ingestion
    warnings = (*ingestion.warnings, *report.analysis.warnings)
    return {
        "unparsed_by_reason": {
            reason.value: ingestion.unparsed_counts.get(reason, 0) for reason in UnparsedReason
        },
        "unparsed_samples": [_unparsed(sample) for sample in ingestion.unparsed_samples],
        "field_issues": {code.value: ingestion.issue_counts.get(code, 0) for code in IssueCode},
        "warnings": [_warning(warning) for warning in warnings],
    }


def _skipped(skipped: SkippedFile) -> JsonObject:
    return {"path": skipped.path, "reason": skipped.reason.value}


def _warning(warning: DataWarning) -> JsonObject:
    return {"code": warning.code.value, "path": warning.path, "message": warning.message}


def _source(source: SourceSummary) -> JsonObject:
    return {
        "path": source.path,
        "format": source.format.value,
        "size_bytes": source.size_bytes,
        "sha256": source.sha256,
        "lines": source.lines,
        "blank_lines": source.blank_lines,
        "events": source.events,
        "unparsed": source.unparsed,
        "error": source.error,
    }


def _unparsed(sample: UnparsedLine) -> JsonObject:
    return {
        "path": sample.ref.path,
        "line": sample.ref.line,
        "reason": sample.reason.value,
        "excerpt": sample.excerpt,
    }


def _config(config: AnalysisConfig) -> JsonObject:
    """The effective configuration, written explicitly rather than with
    ``dataclasses.asdict``: that fails on read-only mappings, and an explicit
    encoder keeps the service order stable (alphabetical)."""
    latency = config.high_latency
    return {
        "report": {
            "max_examples": config.report.max_examples,
            "top_errors": config.report.top_errors,
        },
        "recurring_error": {
            "min_occurrences": config.recurring_error.min_occurrences,
            "high_count": config.recurring_error.high_count,
        },
        "high_latency": {
            "warn_ms": latency.warn_ms,
            "critical_ms": latency.critical_ms,
            "services": {
                name: {"warn_ms": thresholds.warn_ms, "critical_ms": thresholds.critical_ms}
                for name, thresholds in sorted(latency.services.items())
            },
        },
        "warning_before_error": {
            "medium_min_groups": config.warning_before_error.medium_min_groups,
        },
        "error_spike": {
            "window_s": config.error_spike.window_s,
            "factor": config.error_spike.factor,
            "min_errors": config.error_spike.min_errors,
        },
    }


def _rule(outcome: RuleOutcome) -> JsonObject:
    return {"rule": outcome.rule.value, "status": outcome.status, "reason": outcome.reason}


def _error_group(group: ErrorGroup) -> JsonObject:
    return {
        "service": group.service,
        "fingerprint": group.fingerprint,
        "count": group.count,
        "first_seen": _optional_timestamp(group.first_seen),
        "last_seen": _optional_timestamp(group.last_seen),
        "events": [_ref(ref) for ref in group.events],
        "correlation_groups": group.correlation_groups,
        "correlation_ids": list(group.correlation_ids),
    }


def _finding(finding: Finding) -> JsonObject:
    return {
        "id": finding.id,
        "rule": finding.rule.value,
        "severity": finding.severity.name.lower(),
        "title": finding.title,
        "description": finding.description,
        "evidence": {key: _evidence(value) for key, value in finding.evidence.items()},
        "event_count": finding.event_count,
        "events": [_ref(ref) for ref in finding.events],
        "correlation_ids": list(finding.correlation_ids),
    }


def _evidence(value: EvidenceValue) -> JsonValue:
    return format_timestamp(value) if isinstance(value, datetime) else value


def _sequence(group: CorrelationGroup) -> JsonObject:
    timestamps = [event.timestamp for event in group.events if event.timestamp is not None]
    names = sorted({e.service for e in group.events if e.service is not None})
    services: list[JsonValue] = list(names)  # annotated: list is invariant
    return {
        "label": group.label,
        "request_ids": list(group.request_ids),
        "trace_ids": list(group.trace_ids),
        "services": services,
        "event_count": len(group.events),
        "time_range": {
            "start": _optional_timestamp(min(timestamps, default=None)),
            "end": _optional_timestamp(max(timestamps, default=None)),
        },
        "events": [_event(event) for event in group.events],
    }


def _event(event: Event) -> JsonObject:
    return {
        "path": event.ref.path,
        "line": event.ref.line,
        "timestamp": _optional_timestamp(event.timestamp),
        "level": event.level.name,
        "raw_level": event.raw_level,
        "service": event.service,
        "message": event.message,
        "request_id": event.request_id,
        "trace_id": event.trace_id,
        "duration_ms": event.duration_ms,
        "attributes": dict(event.attributes),
    }


def _ref(ref: SourceRef) -> JsonObject:
    return {"path": ref.path, "line": ref.line}
