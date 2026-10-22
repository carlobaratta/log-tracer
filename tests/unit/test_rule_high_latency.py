"""RUL-2 high_latency."""

from collections.abc import Mapping

import pytest
from hypothesis import given
from hypothesis import strategies as st

from log_tracer.analysis import analyze
from log_tracer.analysis.context import AnalysisContext
from log_tracer.analysis.correlation import timeline_key
from log_tracer.analysis.rules import high_latency
from log_tracer.analysis.rules.high_latency import nearest_rank_p95
from log_tracer.config import AnalysisConfig, HighLatencyConfig, LatencyThresholds
from log_tracer.models import Event, Finding, RuleName, RuleOutcome, Severity, SourceRef
from tests.factories import at, make_event

DEFAULT = HighLatencyConfig(warn_ms=1000, critical_ms=5000)


def timed(duration: float | None, *, service: str | None = "api", line: int = 1) -> Event:
    return make_event(duration_ms=duration, service=service, line=line, timestamp=at(line))


def durations(*values: float, service: str | None = "api") -> list[Event]:
    return [timed(v, service=service, line=i) for i, v in enumerate(values, start=1)]


def run(
    events: list[Event],
    config: HighLatencyConfig = DEFAULT,
    *,
    max_examples: int = 10,
    label_of: Mapping[SourceRef, str] | None = None,
) -> RuleOutcome:
    context = AnalysisContext(
        events=tuple(sorted(events, key=timeline_key)),
        error_groups=(),
        max_examples=max_examples,
        label_of=label_of or {},
    )
    outcome = high_latency.run(context, config)
    assert outcome.rule is RuleName.HIGH_LATENCY
    return outcome


def single(events: list[Event], config: HighLatencyConfig = DEFAULT) -> Finding:
    (finding,) = run(events, config).findings
    return finding


# --- Applicability -------------------------------------------------------------------------


def test_skipped_without_durations() -> None:
    outcome = run([timed(None), timed(None, line=2)])
    assert (outcome.status, outcome.reason, outcome.findings) == ("skipped", "no_duration_data", ())


def test_ok_without_slow_events() -> None:
    outcome = run(durations(10, 999.9))
    assert (outcome.status, outcome.reason, outcome.findings) == ("ok", None, ())


# --- Thresholds (strictly greater) -------------------------------------------------------------


@pytest.mark.parametrize(
    ("duration", "severity"),
    [(1000, None), (1000.5, Severity.MEDIUM), (5000, Severity.MEDIUM), (5000.1, Severity.HIGH)],
)
def test_thresholds_are_exceeded_only_by_greater_durations(
    duration: float, severity: Severity | None
) -> None:
    findings = run(durations(duration)).findings
    assert [f.severity for f in findings] == ([] if severity is None else [severity])


def test_service_thresholds_replace_the_global_ones() -> None:
    config = HighLatencyConfig(
        warn_ms=1000, critical_ms=5000, services={"payments": LatencyThresholds(3000, 5000)}
    )
    events = [*durations(2500, service="payments"), *durations(2500, service="api")]
    assert [f.evidence["service"] for f in run(events, config).findings] == ["api"]


def test_events_without_a_service_use_the_global_thresholds() -> None:
    config = HighLatencyConfig(warn_ms=1000, critical_ms=5000, services={})
    finding = single(durations(1200, service=None), config)
    assert finding.evidence["service"] is None
    assert finding.title == "High latency in unknown service: 1 of 1 measured events over 1000 ms"
    assert finding.description.startswith("1 event without a service took longer than 1000 ms")


# --- Finding content --------------------------------------------------------------------------


def test_finding_text_and_evidence() -> None:
    finding = single(durations(2790, 419, 1910, 2741, 2797))
    assert finding.rule is RuleName.HIGH_LATENCY
    assert finding.severity is Severity.MEDIUM
    assert finding.title == "High latency in api: 4 of 5 measured events over 1000 ms"
    assert finding.description == (
        "4 events from service 'api' took longer than 1000 ms (slowest 2797 ms, p95 2797 ms)."
    )
    assert finding.evidence == {
        "service": "api",
        "warn_ms": 1000,
        "critical_ms": 5000,
        "slow_events": 4,
        "measured_events": 5,
        "max_ms": 2797.0,
        "p95_ms": 2797.0,
    }
    assert finding.event_count == 4


def test_events_are_slowest_first_then_in_timeline_order_and_capped() -> None:
    events = durations(1500, 3000, 1500, 2000)
    (finding,) = run(events, max_examples=3).findings
    assert finding.events == (
        SourceRef("app.log", 2),
        SourceRef("app.log", 4),
        SourceRef("app.log", 1),
    )


def test_correlation_ids_follow_the_slowest_events() -> None:
    events = durations(1500, 3000, 2000, 2500)
    label_of = {
        SourceRef("app.log", 1): "r1",
        SourceRef("app.log", 2): "r2",
        SourceRef("app.log", 3): "r2",
    }
    (finding,) = run(events, label_of=label_of, max_examples=10).findings
    assert finding.correlation_ids == ("r2", "r1")


def test_finding_id_is_stable_per_service() -> None:
    first = single(durations(1500, service="api")).id
    again = single(durations(2500, 3500, service="api")).id
    other = single(durations(1500, service="db")).id
    assert first == again
    assert first != other
    assert first.startswith("high_latency:")


# --- p95 (nearest rank) --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        ([7.0], 7.0),
        ([float(v) for v in range(1, 21)], 19.0),
        ([float(v) for v in range(1, 22)], 20.0),
    ],
)
def test_p95_uses_the_nearest_rank(values: list[float], expected: float) -> None:
    assert nearest_rank_p95(values) == expected


@given(st.lists(st.floats(0, 1e9, allow_nan=False), min_size=1, max_size=200))
def test_p95_satisfies_the_nearest_rank_definition(values: list[float]) -> None:
    # Definition: the smallest observed value with at least 95% of values <= it.
    p95 = nearest_rank_p95(values)
    n = len(values)
    assert p95 in values
    assert 100 * sum(v <= p95 for v in values) >= 95 * n
    for smaller in {v for v in values if v < p95}:
        assert 100 * sum(v <= smaller for v in values) < 95 * n


# --- Through analyze() -------------------------------------------------------------------------


def test_analyze_runs_both_rules_in_a_fixed_order() -> None:
    result = analyze(durations(1500, 2500), AnalysisConfig())
    assert [o.rule for o in result.rules] == [RuleName.RECURRING_ERROR, RuleName.HIGH_LATENCY]
    assert [f.rule for f in result.findings] == [RuleName.HIGH_LATENCY]
