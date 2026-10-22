"""JSON encoding of the report (SPEC §6).

The encoding is canonical: keys have a fixed order, every enum member is
listed even when its count is zero, and timestamps are RFC 3339 in UTC. The
same report therefore always produces the same bytes (NFR-2).
"""

import dataclasses
import json
from collections import Counter
from datetime import UTC, datetime
from typing import Final

from log_tracer.models import (
    ErrorGroup,
    EvidenceValue,
    Finding,
    IngestionSummary,
    IngestionWarning,
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
        "config": dataclasses.asdict(report.config),
        "inputs": [_source(source) for source in ingestion.sources],
        "skipped_files": [_skipped(skipped) for skipped in ingestion.skipped],
        "summary": _summary(report),
        "data_quality": _data_quality(ingestion),
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
        "findings_by_severity": {
            severity.name.lower(): severities[severity] for severity in _SEVERITY_ORDER
        },
    }


def _data_quality(ingestion: IngestionSummary) -> JsonObject:
    return {
        "unparsed_by_reason": {
            reason.value: ingestion.unparsed_counts.get(reason, 0) for reason in UnparsedReason
        },
        "unparsed_samples": [_unparsed(sample) for sample in ingestion.unparsed_samples],
        "field_issues": {code.value: ingestion.issue_counts.get(code, 0) for code in IssueCode},
        "warnings": [_warning(warning) for warning in ingestion.warnings],
    }


def _skipped(skipped: SkippedFile) -> JsonObject:
    return {"path": skipped.path, "reason": skipped.reason.value}


def _warning(warning: IngestionWarning) -> JsonObject:
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


def _ref(ref: SourceRef) -> JsonObject:
    return {"path": ref.path, "line": ref.line}
