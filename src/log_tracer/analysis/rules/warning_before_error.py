"""RUL-3 warning_before_error: warnings that precede the first error of a sequence.

The result is a hint, not proof: a warning that happens before an error in
the same request is not necessarily its cause. The finding text says so.
"""

import statistics
from dataclasses import dataclass
from datetime import datetime, timedelta
from types import MappingProxyType
from typing import Final

from log_tracer.analysis.context import AnalysisContext, finding_id
from log_tracer.analysis.correlation import precedes, timeline_key
from log_tracer.analysis.fingerprint import fingerprint
from log_tracer.config import WarningBeforeErrorConfig
from log_tracer.models import (
    CorrelationGroup,
    Event,
    Finding,
    Level,
    RuleName,
    RuleOutcome,
    Severity,
)

RULE = RuleName.WARNING_BEFORE_ERROR
_MICROSECOND: Final = timedelta(microseconds=1)

type _Pattern = tuple[str | None, str]
"""(service, message fingerprint)."""
type _Pair = tuple[_Pattern, _Pattern]
"""(warning pattern, error pattern)."""


@dataclass(frozen=True, slots=True)
class _Observation:
    """One sequence in which the warning certainly precedes the first error."""

    label: str
    warning: Event
    """The closest preceding warning with this pattern."""
    error: Event
    gap_ms: float


def run(context: AnalysisContext, config: WarningBeforeErrorConfig) -> RuleOutcome:
    observations: dict[_Pair, list[_Observation]] = {}
    undetermined: dict[_Pair, int] = {}
    applicable = False
    for sequence in context.sequences:
        timed_error = _first_timed_error(sequence)
        if timed_error is None:
            continue
        applicable = True
        first_error, error_time = timed_error
        error_pattern = _pattern(first_error)
        closest, unclear = _warnings_before(sequence, first_error)
        for pattern, (warning, warning_time) in closest.items():
            gap = _milliseconds(error_time - warning_time)
            observation = _Observation(sequence.label, warning, first_error, gap)
            observations.setdefault((pattern, error_pattern), []).append(observation)
        for pattern in unclear - closest.keys():
            pair = (pattern, error_pattern)
            undetermined[pair] = undetermined.get(pair, 0) + 1

    if not applicable:
        return RuleOutcome(rule=RULE, status="skipped", reason="no_correlated_errors", findings=())
    findings = tuple(
        _finding(pair, found, undetermined.get(pair, 0), config, context.max_examples)
        for pair, found in observations.items()
    )
    return RuleOutcome(rule=RULE, status="ok", reason=None, findings=findings)


def _first_timed_error(sequence: CorrelationGroup) -> tuple[Event, datetime] | None:
    """The first ERROR or CRITICAL event with a timestamp (events are in timeline order)."""
    for event in sequence.events:
        if event.level >= Level.ERROR and event.timestamp is not None:
            return event, event.timestamp
    return None


def _warnings_before(
    sequence: CorrelationGroup, first_error: Event
) -> tuple[dict[_Pattern, tuple[Event, datetime]], set[_Pattern]]:
    """Warnings that certainly precede the error, and patterns whose order is unknown.

    For each pattern the closest preceding warning is kept: events are in
    timeline order, so a later match replaces an earlier one. A warning is of
    unknown order when neither event certainly precedes the other (COR-4): it
    has no timestamp, or the same timestamp as the error in another file.
    """
    closest: dict[_Pattern, tuple[Event, datetime]] = {}
    unclear: set[_Pattern] = set()
    for event in sequence.events:
        if event.level is not Level.WARNING:
            continue
        if event.timestamp is not None and precedes(event, first_error):
            closest[_pattern(event)] = (event, event.timestamp)
        elif not precedes(first_error, event):
            unclear.add(_pattern(event))
    return closest, unclear


def _finding(
    pair: _Pair,
    found: list[_Observation],
    undetermined: int,
    config: WarningBeforeErrorConfig,
    max_examples: int,
) -> Finding:
    (warning_service, warning_print), (error_service, error_print) = pair
    found = sorted(found, key=lambda o: timeline_key(o.error))
    groups = len(found)
    sequences = "sequence" if groups == 1 else "sequences"
    gap = statistics.median(o.gap_ms for o in found)
    events = [ref for o in found for ref in (o.warning.ref, o.error.ref)]
    return Finding(
        id=finding_id(RULE, warning_service, warning_print, error_service, error_print),
        rule=RULE,
        severity=Severity.MEDIUM if groups >= config.medium_min_groups else Severity.LOW,
        title=f"Warning before error in {groups} {sequences}: {warning_print} → {error_print}",
        description=(
            f'In {groups} correlated {sequences} the warning "{warning_print}" '
            f'({warning_service or "no service"}) precedes the error "{error_print}" '
            f"({error_service or 'no service'}). "
            "This is a temporal correlation, not proven causation."
        ),
        evidence=MappingProxyType(
            {
                "warning_service": warning_service,
                "warning_fingerprint": warning_print,
                "error_service": error_service,
                "error_fingerprint": error_print,
                "groups": groups,
                "median_gap_ms": gap,
                "undetermined_groups": undetermined,
            }
        ),
        event_count=len(events),
        events=tuple(events[:max_examples]),
        correlation_ids=tuple(o.label for o in found[:max_examples]),
    )


def _pattern(event: Event) -> _Pattern:
    return (event.service, fingerprint(event.message))


def _milliseconds(delta: timedelta) -> float:
    """Computed from whole microseconds, so 2.492 s gives exactly 2492.0."""
    return (delta // _MICROSECOND) / 1000
