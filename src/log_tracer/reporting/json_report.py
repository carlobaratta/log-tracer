"""JSON encoding and decoding of the report (SPEC §6, §8).

The encoding is canonical: keys have a fixed order, every enum member is
listed even when its count is zero, and timestamps are RFC 3339 in UTC. The
same report therefore always produces the same bytes (NFR-2).

Decoding reads a saved report back (STA-2). It is checked against the
encoder: a decoded report encodes to exactly the document it came from.
"""

import json
from collections import Counter
from collections.abc import Mapping
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Final, Literal, NoReturn

from log_tracer.config import AnalysisConfig, parse_config
from log_tracer.errors import ConfigError
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


# --- Decoding --------------------------------------------------------------------------


class ReportDecodeError(ValueError):
    """The document is not a report that this version can read (STA-2)."""


_SCHEMA_MAJOR: Final = SCHEMA_VERSION.partition(".")[0]
_SEVERITIES: Final = {severity.name.lower(): severity for severity in Severity}
_LEVELS: Final = {level.name: level for level in Level}
_STATUSES: Final[Mapping[str, Literal["ok", "skipped"]]] = {"ok": "ok", "skipped": "skipped"}
_ANALYSIS_WARNINGS: Final = frozenset({WarningCode.LARGE_CORRELATION_GROUP})


def loads(text: str) -> Report:
    """Parse and decode a saved report (see :func:`report_from_dict`)."""
    try:
        data = json.loads(text, parse_constant=_reject_constant)
    except json.JSONDecodeError as error:
        raise ReportDecodeError(
            f"not valid JSON: {error.msg} (line {error.lineno}, column {error.colno})"
        ) from error
    except RecursionError as error:
        raise ReportDecodeError("not valid JSON: nested too deeply") from error
    if not isinstance(data, dict):
        raise ReportDecodeError(f"the document must be a JSON object, got {_kind(data)}")
    return report_from_dict(data)


def report_from_dict(data: Mapping[str, object]) -> Report:
    """Rebuild a :class:`Report` from its encoding.

    Every minor version of the current major schema is accepted, and keys
    this version does not know are ignored. Values derived from others in the
    encoding (``summary.events``, ``findings_by_severity``) are not read.
    Two values are not stored as such: timestamps in a finding's evidence
    stay strings, and ``has_critical`` of an error group comes from its
    ``recurring_error`` finding, or is false without one (D-15).
    """
    root = _Node(data, "")
    version = root.text("schema_version")
    major, dot, _minor = version.partition(".")
    if major != _SCHEMA_MAJOR or not dot:
        raise ReportDecodeError(
            f"unsupported schema version {version!r} "
            f"(this version of log-tracer reads {_SCHEMA_MAJOR}.x)"
        )
    try:
        config = parse_config(root.mapping("config"), source="config")
    except ConfigError as error:
        raise ReportDecodeError(str(error)) from error

    quality = root.obj("data_quality")
    warnings = tuple(_decode_warning(node) for node in quality.objects("warnings"))
    summary = root.obj("summary")
    findings = tuple(_decode_finding(node) for node in root.objects("findings"))
    critical = {
        (finding.evidence.get("service"), finding.evidence.get("fingerprint")): (
            finding.evidence.get("has_critical") is True
        )
        for finding in findings
        if finding.rule is RuleName.RECURRING_ERROR
    }
    return Report(
        tool_version=root.obj("tool").text("version"),
        generated_at=root.timestamp("generated_at"),
        config=config,
        ingestion=IngestionSummary(
            sources=tuple(_decode_source(node) for node in root.objects("inputs")),
            skipped=tuple(
                SkippedFile(node.text("path"), node.choice("reason", _values(SkipReason)))
                for node in root.objects("skipped_files")
            ),
            unparsed_counts=quality.counts("unparsed_by_reason", _values(UnparsedReason)),
            unparsed_samples=tuple(
                UnparsedLine(
                    SourceRef(node.text("path"), node.integer("line")),
                    node.choice("reason", _values(UnparsedReason)),
                    node.text("excerpt"),
                )
                for node in quality.objects("unparsed_samples")
            ),
            issue_counts=quality.counts("field_issues", _values(IssueCode)),
            # The report lists both kinds of warnings together, ingestion first.
            warnings=tuple(w for w in warnings if w.code not in _ANALYSIS_WARNINGS),
        ),
        analysis=AnalysisResult(
            level_counts=summary.counts("levels", _LEVELS),
            time_range=_decode_time_range(summary.obj("time_range")),
            top_errors=tuple(
                _decode_error_group(node, critical) for node in root.objects("top_errors")
            ),
            rules=tuple(
                RuleOutcome(
                    rule=(rule := node.choice("rule", _values(RuleName))),
                    status=node.choice("status", _STATUSES),
                    reason=node.optional_text("reason"),
                    findings=tuple(finding for finding in findings if finding.rule is rule),
                )
                for node in root.objects("rules")
            ),
            findings=findings,
            correlation_groups=summary.integer("correlation_groups"),
            uncorrelated_events=summary.integer("uncorrelated_events"),
            warnings=tuple(w for w in warnings if w.code in _ANALYSIS_WARNINGS),
        ),
    )


def _decode_source(node: "_Node") -> SourceSummary:
    return SourceSummary(
        path=node.text("path"),
        format=node.choice("format", _values(InputFormat)),
        size_bytes=node.optional_integer("size_bytes"),
        sha256=node.optional_text("sha256"),
        lines=node.integer("lines"),
        blank_lines=node.integer("blank_lines"),
        events=node.integer("events"),
        unparsed=node.integer("unparsed"),
        error=node.optional_text("error"),
    )


def _decode_warning(node: "_Node") -> DataWarning:
    return DataWarning(
        code=node.choice("code", _values(WarningCode)),
        path=node.optional_text("path"),
        message=node.text("message"),
    )


def _decode_time_range(node: "_Node") -> TimeRange | None:
    start, end = node.optional_timestamp("start"), node.optional_timestamp("end")
    if start is None and end is None:
        return None
    if start is None or end is None:
        node.fail("must have both start and end, or neither", key="")
    return TimeRange(start, end)


def _decode_error_group(
    node: "_Node", critical: Mapping[tuple[EvidenceValue, EvidenceValue], bool]
) -> ErrorGroup:
    service, fingerprint = node.optional_text("service"), node.text("fingerprint")
    return ErrorGroup(
        service=service,
        fingerprint=fingerprint,
        count=node.integer("count"),
        has_critical=critical.get((service, fingerprint), False),
        first_seen=node.optional_timestamp("first_seen"),
        last_seen=node.optional_timestamp("last_seen"),
        events=node.refs("events"),
        correlation_groups=node.integer("correlation_groups"),
        correlation_ids=node.strs("correlation_ids"),
    )


def _decode_finding(node: "_Node") -> Finding:
    return Finding(
        id=node.text("id"),
        rule=node.choice("rule", _values(RuleName)),
        severity=node.choice("severity", _SEVERITIES),
        title=node.text("title"),
        description=node.text("description"),
        evidence=node.evidence("evidence"),
        event_count=node.integer("event_count"),
        events=node.refs("events"),
        correlation_ids=node.strs("correlation_ids"),
    )


class _Node:
    """A JSON object being decoded; errors name the full location of the value."""

    def __init__(self, data: Mapping[str, object], location: str) -> None:
        self._data = data
        self._location = location

    def fail(self, problem: str, *, key: str) -> NoReturn:
        raise ReportDecodeError(f"{self._name(key)} {problem}")

    def _name(self, key: str) -> str:
        if not key:
            return self._location
        return f"{self._location}.{key}" if self._location else key

    def _get(self, key: str) -> object:
        if key not in self._data:
            raise ReportDecodeError(f"missing key '{self._name(key)}'")
        return self._data[key]

    def _expect(self, key: str, value: object, expected: str) -> NoReturn:
        self.fail(f"must be {expected}, got {_show(value)}", key=key)

    def text(self, key: str) -> str:
        value = self._get(key)
        if not isinstance(value, str):
            self._expect(key, value, "a string")
        return value

    def optional_text(self, key: str) -> str | None:
        value = self._get(key)
        return None if value is None else self.text(key)

    def integer(self, key: str) -> int:
        value = self._get(key)
        # bool is a subclass of int: ``true`` must not count as 1.
        if isinstance(value, bool) or not isinstance(value, int):
            self._expect(key, value, "an integer")
        return value

    def optional_integer(self, key: str) -> int | None:
        value = self._get(key)
        return None if value is None else self.integer(key)

    def timestamp(self, key: str) -> datetime:
        text = self.text(key)
        try:
            value = datetime.fromisoformat(text)
        except ValueError:
            self.fail(f"is not an RFC 3339 timestamp: {_show(text)}", key=key)
        if value.tzinfo is None:
            self.fail(f"has no time zone: {_show(text)}", key=key)
        return value.astimezone(UTC)

    def optional_timestamp(self, key: str) -> datetime | None:
        value = self._get(key)
        return None if value is None else self.timestamp(key)

    def choice[T](self, key: str, options: Mapping[str, T]) -> T:
        value = self.text(key)
        if value not in options:
            self.fail(f"has an unknown value {_show(value)}", key=key)
        return options[value]

    def mapping(self, key: str) -> Mapping[str, object]:
        value = self._get(key)
        if not isinstance(value, dict):
            self._expect(key, value, "an object")
        return value

    def obj(self, key: str) -> "_Node":
        return _Node(self.mapping(key), self._name(key))

    def objects(self, key: str) -> list["_Node"]:
        name = self._name(key)
        nodes: list[_Node] = []
        for index, item in enumerate(self._list(key)):
            if not isinstance(item, dict):
                raise ReportDecodeError(f"{name}[{index}] must be an object, got {_show(item)}")
            nodes.append(_Node(item, f"{name}[{index}]"))
        return nodes

    def strs(self, key: str) -> tuple[str, ...]:
        items = self._list(key)
        for index, item in enumerate(items):
            if not isinstance(item, str):
                self._expect(f"{key}[{index}]", item, "a string")
        return tuple(item for item in items if isinstance(item, str))

    def refs(self, key: str) -> tuple[SourceRef, ...]:
        return tuple(SourceRef(n.text("path"), n.integer("line")) for n in self.objects(key))

    def counts[K](self, key: str, options: Mapping[str, K]) -> Mapping[K, int]:
        node = self.obj(key)
        for name in node._data:
            if name not in options:
                node.fail(f"has an unknown key {_show(name)}", key="")
        return MappingProxyType({options[name]: node.integer(name) for name in node._data})

    def evidence(self, key: str) -> Mapping[str, EvidenceValue]:
        node = self.obj(key)
        values: dict[str, EvidenceValue] = {}
        for name, value in node._data.items():
            if not isinstance(value, str | int | float | bool | None):
                node._expect(name, value, "a string, a number, a boolean or null")
            values[name] = value
        return MappingProxyType(values)

    def _list(self, key: str) -> list[object]:
        value = self._get(key)
        if not isinstance(value, list):
            self._expect(key, value, "a list")
        return value


def _values[E: StrEnum](kind: type[E]) -> Mapping[str, E]:
    return {member.value: member for member in kind}


def _reject_constant(name: str) -> NoReturn:
    raise ReportDecodeError(f"not valid JSON: {name} is not allowed")


def _kind(value: object) -> str:
    kinds = {list: "a list", str: "a string", bool: "a boolean", int: "a number", float: "a number"}
    return "null" if value is None else kinds.get(type(value), type(value).__name__)


def _show(value: object) -> str:
    """A value as it is written in JSON: true, "x", 2.0."""
    return json.dumps(value, ensure_ascii=False)
