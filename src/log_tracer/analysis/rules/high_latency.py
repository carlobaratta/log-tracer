"""RUL-2 high_latency: events whose declared duration exceeds a threshold.

Durations come only from ``duration_ms``. They are never derived from the
difference between two timestamps, because the clocks of different services
can drift apart (SPEC D-6).
"""

from collections.abc import Sequence
from types import MappingProxyType

from log_tracer.analysis.context import AnalysisContext, finding_id
from log_tracer.analysis.correlation import timeline_key
from log_tracer.config import HighLatencyConfig, LatencyThresholds
from log_tracer.models import Event, Finding, RuleName, RuleOutcome, Severity

RULE = RuleName.HIGH_LATENCY


def run(context: AnalysisContext, config: HighLatencyConfig) -> RuleOutcome:
    measured: dict[str | None, list[tuple[Event, float]]] = {}
    for event in context.events:  # timeline order
        if event.duration_ms is not None:
            measured.setdefault(event.service, []).append((event, event.duration_ms))
    if not measured:
        return RuleOutcome(rule=RULE, status="skipped", reason="no_duration_data", findings=())

    findings: list[Finding] = []
    for service, timed in measured.items():
        thresholds = _thresholds(config, service)
        # Strictly greater: a duration equal to the threshold is not slow.
        slow = [(event, ms) for event, ms in timed if ms > thresholds.warn_ms]
        if slow:
            findings.append(_finding(context, service, timed, slow, thresholds))
    return RuleOutcome(rule=RULE, status="ok", reason=None, findings=tuple(findings))


def nearest_rank_p95(values: Sequence[float]) -> float:
    """The 95th percentile by nearest rank: the value at rank ⌈0.95·n⌉ (1-based).

    It is always an observed value. The rank is computed with integers to
    avoid floating-point surprises in ``0.95 * n``.
    """
    ordered = sorted(values)
    rank = (95 * len(ordered) + 99) // 100
    return ordered[rank - 1]


def _finding(
    context: AnalysisContext,
    service: str | None,
    timed: list[tuple[Event, float]],
    slow: list[tuple[Event, float]],
    thresholds: LatencyThresholds,
) -> Finding:
    slowest = [e for e, _ in sorted(slow, key=lambda pair: (-pair[1], timeline_key(pair[0])))]
    longest = max(ms for _, ms in slow)
    p95 = nearest_rank_p95([ms for _, ms in timed])
    label_of = context.label_of
    labels = list(dict.fromkeys(label_of[e.ref] for e in slowest if e.ref in label_of))
    noun = "event" if len(slow) == 1 else "events"
    origin = f"from service '{service}'" if service else "without a service"
    return Finding(
        id=finding_id(RULE, service),
        rule=RULE,
        severity=Severity.HIGH if longest > thresholds.critical_ms else Severity.MEDIUM,
        title=(
            f"High latency in {service or 'unknown service'}: {len(slow)} of {len(timed)} "
            f"measured events over {_ms(thresholds.warn_ms)} ms"
        ),
        description=(
            f"{len(slow)} {noun} {origin} took longer than {_ms(thresholds.warn_ms)} ms "
            f"(slowest {_ms(longest)} ms, p95 {_ms(p95)} ms)."
        ),
        evidence=MappingProxyType(
            {
                "service": service,
                "warn_ms": thresholds.warn_ms,
                "critical_ms": thresholds.critical_ms,
                "slow_events": len(slow),
                "measured_events": len(timed),
                "max_ms": longest,
                "p95_ms": p95,
            }
        ),
        event_count=len(slow),
        events=tuple(e.ref for e in slowest[: context.max_examples]),
        correlation_ids=tuple(labels[: context.max_examples]),
    )


def _thresholds(config: HighLatencyConfig, service: str | None) -> LatencyThresholds:
    default = LatencyThresholds(config.warn_ms, config.critical_ms)
    return default if service is None else config.services.get(service, default)


def _ms(value: float) -> str:
    """2500.0 -> "2500", 12.5 -> "12.5": readable numbers in titles."""
    return f"{value:.3f}".rstrip("0").rstrip(".")
