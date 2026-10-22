"""Diagnostic engine: statistics and rules over normalized events (SPEC §3-4).

This package depends only on the domain models and the configuration: it does
not know about files, the CLI or output formats.
"""

from collections.abc import Sequence
from types import MappingProxyType

from log_tracer.analysis.context import AnalysisContext
from log_tracer.analysis.correlation import timeline_key
from log_tracer.analysis.rules import recurring_error
from log_tracer.analysis.statistics import count_levels, group_errors, time_range
from log_tracer.config import AnalysisConfig
from log_tracer.models import AnalysisResult, Event, Finding

__all__ = ["analyze"]


def analyze(events: Sequence[Event], config: AnalysisConfig) -> AnalysisResult:
    ordered = tuple(sorted(events, key=timeline_key))
    error_groups = group_errors(ordered, max_examples=config.report.max_examples)
    context = AnalysisContext(
        events=ordered,
        error_groups=error_groups,
        max_examples=config.report.max_examples,
    )
    outcomes = (recurring_error.run(context, config.recurring_error),)
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
    )


def _finding_order(finding: Finding) -> tuple[int, str, str]:
    """RUL-0.4: severity (descending), then rule, then id."""
    return (-finding.severity, finding.rule, finding.id)
