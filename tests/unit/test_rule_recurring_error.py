"""RUL-1 recurring_error."""

import pytest

from log_tracer.analysis import analyze
from log_tracer.analysis.context import AnalysisContext
from log_tracer.analysis.correlation import LARGE_GROUP
from log_tracer.analysis.rules import recurring_error
from log_tracer.analysis.statistics import group_errors
from log_tracer.config import AnalysisConfig, RecurringErrorConfig
from log_tracer.models import (
    DataWarning,
    Event,
    Finding,
    Level,
    RuleName,
    Severity,
    SourceRef,
    WarningCode,
)
from tests.factories import at, make_event

CONFIG = RecurringErrorConfig(min_occurrences=3, high_count=5)


def errors(
    count: int, *, service: str | None = "api", message: str = "Payment timeout after 2500 ms"
) -> list[Event]:
    return [
        make_event(level=Level.ERROR, service=service, message=message, line=i + 1, timestamp=at(i))
        for i in range(count)
    ]


def run(events: list[Event], config: RecurringErrorConfig = CONFIG) -> tuple[Finding, ...]:
    context = AnalysisContext(
        events=tuple(events),
        error_groups=group_errors(events, max_examples=2),
        max_examples=2,
    )
    outcome = recurring_error.run(context, config)
    assert outcome.rule is RuleName.RECURRING_ERROR
    assert outcome.status == "ok"
    assert outcome.reason is None
    return outcome.findings


def test_no_finding_below_min_occurrences() -> None:
    assert run(errors(2)) == ()


def test_finding_at_min_occurrences_is_medium() -> None:
    (finding,) = run(errors(3))
    assert finding.rule is RuleName.RECURRING_ERROR
    assert finding.severity is Severity.MEDIUM
    assert finding.event_count == 3


def test_finding_at_high_count_is_high() -> None:
    (finding,) = run(errors(5))
    assert finding.severity is Severity.HIGH


def test_a_critical_event_makes_the_finding_high() -> None:
    events = errors(2)
    events.append(make_event(level=Level.CRITICAL, message="Payment timeout after 1 ms", line=9))
    (finding,) = run(events)
    assert finding.event_count == 3
    assert finding.severity is Severity.HIGH


def test_finding_carries_evidence_and_a_capped_event_sample() -> None:
    (finding,) = run(errors(4, service="payments"))
    assert finding.evidence == {
        "service": "payments",
        "fingerprint": "Payment timeout after <num> ms",
        "count": 4,
        "min_occurrences": 3,
        "high_count": 5,
        "has_critical": False,
        "first_seen": at(0),
        "last_seen": at(3),
        "correlation_groups": 0,
    }
    assert finding.events == (SourceRef("app.log", 1), SourceRef("app.log", 2))
    assert finding.correlation_ids == ()
    assert "payments" in finding.title
    assert "Payment timeout after <num> ms" in finding.description


def test_missing_service_is_described_explicitly() -> None:
    (finding,) = run(errors(3, service=None))
    assert finding.evidence["service"] is None
    assert "without a service" in finding.description


def test_each_service_and_fingerprint_is_a_separate_finding() -> None:
    events = [
        *errors(3, service="payments"),
        *errors(3, service="orders"),
        *errors(3, service="payments", message="Card declined"),
    ]
    findings = run(events)
    assert len(findings) == 3
    assert {(f.evidence["service"], f.evidence["fingerprint"]) for f in findings} == {
        ("orders", "Payment timeout after <num> ms"),
        ("payments", "Card declined"),
        ("payments", "Payment timeout after <num> ms"),
    }


def test_finding_id_is_stable_and_distinct_per_group() -> None:
    first = run(errors(3, service="payments"))[0].id
    again = run(errors(4, service="payments"))[0].id
    other = run(errors(3, service="orders"))[0].id
    assert first == again
    assert first != other
    assert first.startswith("recurring_error:")
    assert len(first) == len("recurring_error:") + 12


# --- Through analyze() --------------------------------------------------------


def test_analyze_sorts_findings_by_severity_then_rule_then_id() -> None:
    config = AnalysisConfig(recurring_error=RecurringErrorConfig(min_occurrences=2, high_count=3))
    events = [*errors(2, service="a"), *errors(3, service="b"), *errors(2, service="c")]
    result = analyze(events, config)
    severities = [f.severity for f in result.findings]
    assert severities == [Severity.HIGH, Severity.MEDIUM, Severity.MEDIUM]
    medium_ids = [f.id for f in result.findings[1:]]
    assert medium_ids == sorted(medium_ids)


def test_analyze_reports_rule_status_and_top_errors() -> None:
    config = AnalysisConfig()
    events = [*errors(3, service="a"), make_event(level=Level.INFO)]
    result = analyze(events, config)
    assert [(o.rule, o.status) for o in result.rules] == [
        (RuleName.RECURRING_ERROR, "ok"),
        (RuleName.HIGH_LATENCY, "skipped"),  # no event has a duration
    ]
    assert [(g.service, g.count) for g in result.top_errors] == [("a", 3)]
    assert result.level_counts == {Level.ERROR: 3, Level.INFO: 1}


def test_finding_reports_the_sequences_it_appears_in() -> None:
    events = [
        make_event(level=Level.ERROR, message="timeout", line=i, timestamp=at(i), request_id=rid)
        for i, rid in enumerate(["r2", "r1", "r2", None], start=1)
    ]
    (finding,) = analyze(events, AnalysisConfig()).findings
    assert finding.evidence["correlation_groups"] == 2
    assert finding.correlation_ids == ("r2", "r1")


def test_analyze_counts_sequences_and_uncorrelated_events() -> None:
    events = [
        make_event(line=1, request_id="r1"),
        make_event(line=2, request_id="r1", trace_id="t1"),
        make_event(line=3, trace_id="t2"),
        make_event(line=4),
    ]
    result = analyze(events, AnalysisConfig())
    assert (result.correlation_groups, result.uncorrelated_events) == (2, 1)
    assert result.warnings == ()


@pytest.mark.parametrize(("size", "warned"), [(LARGE_GROUP, False), (LARGE_GROUP + 1, True)])
def test_very_large_sequences_produce_a_warning(size: int, warned: bool) -> None:  # COR-6
    events = [make_event(line=i, request_id="shared") for i in range(1, size + 1)]
    warnings = analyze(events, AnalysisConfig()).warnings
    if warned:
        assert warnings == (
            DataWarning(
                WarningCode.LARGE_CORRELATION_GROUP,
                None,
                f"sequence shared contains {size} events: "
                "an identifier may be reused by unrelated requests",
            ),
        )
    else:
        assert warnings == ()
