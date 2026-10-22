"""Diagnostic engine: statistics and rules over normalized events (SPEC §3-4).

This package depends only on the domain models and the configuration: it does
not know about files, the CLI or output formats.
"""

from collections.abc import Sequence
from types import MappingProxyType

from log_tracer.analysis.context import AnalysisContext
from log_tracer.analysis.correlation import LARGE_GROUP, Correlation, correlate, timeline_key
from log_tracer.analysis.rules import (
    error_spike,
    high_latency,
    recurring_error,
    warning_before_error,
)
from log_tracer.analysis.statistics import count_levels, group_errors, time_range
from log_tracer.config import AnalysisConfig
from log_tracer.models import AnalysisResult, DataWarning, Event, Finding, WarningCode

__all__ = ["analyze"]


def analyze(events: Sequence[Event], config: AnalysisConfig) -> AnalysisResult:
    ordered = tuple(sorted(events, key=timeline_key))
    correlation = correlate(ordered)
    error_groups = group_errors(
        ordered, max_examples=config.report.max_examples, label_of=correlation.label_of
    )
    context = AnalysisContext(
        events=ordered,
        error_groups=error_groups,
        max_examples=config.report.max_examples,
        label_of=correlation.label_of,
        sequences=correlation.groups,
    )
    outcomes = (
        recurring_error.run(context, config.recurring_error),
        high_latency.run(context, config.high_latency),
        warning_before_error.run(context, config.warning_before_error),
        error_spike.run(context, config.error_spike),
    )
    findings = sorted(
        (finding for outcome in outcomes for finding in outcome.findings),
        key=_finding_order,
    )
    return AnalysisResult(
        level_counts=MappingProxyType(count_levels(ordered)),
        time_range=time_range(ordered),
        top_errors=error_groups[: config.report.top_errors],
        rules=outcomes,
        findings=tuple(findings),
        correlation_groups=len(correlation.groups),
        uncorrelated_events=correlation.uncorrelated,
        warnings=_large_group_warnings(correlation),
    )


def _finding_order(finding: Finding) -> tuple[int, str, str]:
    """RUL-0.4: severity (descending), then rule, then id."""
    return (-finding.severity, finding.rule, finding.id)


def _large_group_warnings(correlation: Correlation) -> tuple[DataWarning, ...]:
    """COR-6: a huge sequence usually means an identifier reused by unrelated requests."""
    return tuple(
        DataWarning(
            WarningCode.LARGE_CORRELATION_GROUP,
            None,
            f"sequence {group.label} contains {len(group.events)} events: "
            "an identifier may be reused by unrelated requests",
        )
        for group in correlation.groups
        if len(group.events) > LARGE_GROUP
    )
