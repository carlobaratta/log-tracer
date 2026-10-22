"""RUL-4 error_spike."""

import statistics
from datetime import UTC, datetime, timedelta

import pytest
from hypothesis import given
from hypothesis import strategies as st

from log_tracer.analysis.context import AnalysisContext
from log_tracer.analysis.correlation import correlate, timeline_key
from log_tracer.analysis.rules import error_spike
from log_tracer.analysis.rules.error_spike import window_median
from log_tracer.config import ErrorSpikeConfig
from log_tracer.models import Event, Finding, Level, RuleName, RuleOutcome, Severity, SourceRef
from tests.factories import make_event

START = datetime(2026, 10, 16, 14, 0, 0, tzinfo=UTC)  # a whole minute: aligned to windows
CONFIG = ErrorSpikeConfig(window_s=60, factor=3.0, min_errors=5)


class Log:
    """Builds events minute by minute, with unique line numbers."""

    def __init__(self) -> None:
        self.events: list[Event] = []

    def add(self, minute: int, count: int, *, level: Level = Level.ERROR, second: int = 0) -> None:
        for i in range(count):
            timestamp = START + timedelta(minutes=minute, seconds=second + i % 30)
            self.event(level, timestamp)

    def event(
        self, level: Level, timestamp: datetime | None, request_id: str | None = None
    ) -> None:
        line = len(self.events) + 1
        self.events.append(
            make_event(level=level, timestamp=timestamp, line=line, request_id=request_id)
        )

    def counts(self, *per_minute: int) -> "Log":
        """Error counts for consecutive minutes; INFO events mark the first and last minute."""
        self.add(0, 1, level=Level.INFO)
        self.add(len(per_minute) - 1, 1, level=Level.INFO, second=59)
        for minute, count in enumerate(per_minute):
            self.add(minute, count)
        return self


def run(
    events: list[Event], config: ErrorSpikeConfig = CONFIG, max_examples: int = 10
) -> RuleOutcome:
    ordered = tuple(sorted(events, key=timeline_key))
    correlation = correlate(ordered)
    context = AnalysisContext(
        events=ordered,
        error_groups=(),
        max_examples=max_examples,
        label_of=correlation.label_of,
        sequences=correlation.groups,
    )
    outcome = error_spike.run(context, config)
    assert outcome.rule is RuleName.ERROR_SPIKE
    return outcome


def findings(*per_minute: int) -> tuple[Finding, ...]:
    return run(Log().counts(*per_minute).events).findings


# --- Applicability ---------------------------------------------------------------------------


def test_skipped_without_timestamps() -> None:
    log = Log()
    log.event(Level.ERROR, None)
    outcome = run(log.events)
    assert (outcome.status, outcome.reason) == ("skipped", "insufficient_data")


def test_skipped_with_fewer_than_three_windows() -> None:
    outcome = run(Log().counts(9, 9).events)
    assert (outcome.status, outcome.reason, outcome.findings) == (
        "skipped",
        "insufficient_data",
        (),
    )


def test_no_spike_in_the_example_data() -> None:
    # Errors per minute in examples/logs: 2, 5, 2, 1 -> median 2, threshold max(5, 6) = 6.
    outcome = run(Log().counts(2, 5, 2, 1).events)
    assert (outcome.status, outcome.findings) == ("ok", ())


# --- Threshold ---------------------------------------------------------------------------------


def test_windows_without_errors_count_in_the_baseline() -> None:
    (finding,) = findings(0, 0, 10, 0, 0)
    assert finding.evidence["baseline"] == 0.0
    assert finding.evidence["threshold"] == 5.0  # min_errors
    assert finding.severity is Severity.HIGH  # 10 >= 2 * 5


def test_factor_times_baseline_can_exceed_min_errors() -> None:
    (finding,) = findings(4, 4, 4, 13, 4)
    assert (finding.evidence["baseline"], finding.evidence["threshold"]) == (4.0, 12.0)
    assert finding.severity is Severity.MEDIUM


def test_reaching_the_threshold_exactly_is_a_spike() -> None:
    assert len(findings(4, 4, 4, 12, 4)) == 1
    assert findings(4, 4, 4, 11, 4) == ()


def test_high_from_twice_the_threshold() -> None:
    (finding,) = findings(4, 4, 4, 24, 4)
    assert finding.severity is Severity.HIGH


# --- Windows -------------------------------------------------------------------------------------


def test_consecutive_spike_windows_form_one_finding() -> None:
    (finding,) = findings(0, 6, 7, 0, 0)
    assert finding.evidence == {
        "window_s": 60,
        "start": START + timedelta(minutes=1),
        "end": START + timedelta(minutes=3),
        "windows": 2,
        "errors": 13,
        "peak_window_errors": 7,
        "baseline": 0.0,
        "threshold": 5.0,
        "excluded_without_timestamp": 0,
    }
    assert finding.title == "Error spike from 2026-10-16T14:01:00Z: 13 errors in 120 s"
    assert finding.description == (
        "13 ERROR or CRITICAL events between 2026-10-16T14:01:00Z and 2026-10-16T14:03:00Z "
        "(2 windows of 60 s). The threshold is 5 errors per window, against a median of 0; "
        "the busiest window had 7."
    )


def test_separate_spikes_are_separate_findings() -> None:
    starts = [f.evidence["start"] for f in findings(6, 0, 6, 0, 0)]
    assert starts == [START, START + timedelta(minutes=2)]


def test_windows_are_aligned_to_the_unix_epoch() -> None:
    log = Log()
    log.add(0, 1, level=Level.INFO)
    log.add(4, 1, level=Level.INFO)
    for second in (55, 56, 57, 58, 59):  # 14:00:55-59
        log.event(Level.ERROR, START + timedelta(seconds=second))
    for second in (60, 61, 62, 63, 64):  # 14:01:00-04
        log.event(Level.ERROR, START + timedelta(seconds=second))
    (finding,) = run(log.events).findings
    assert (finding.evidence["windows"], finding.evidence["peak_window_errors"]) == (2, 5)


def test_window_size_comes_from_the_configuration() -> None:
    config = ErrorSpikeConfig(window_s=300, factor=3.0, min_errors=5)
    log = Log().counts(*([0] * 20))
    log.add(1, 3)
    log.add(3, 3)  # 6 errors in the 14:00-14:05 window, none elsewhere
    (finding,) = run(log.events, config).findings
    assert (finding.evidence["window_s"], finding.evidence["errors"]) == (300, 6)


# --- Robustness ----------------------------------------------------------------------------------


def test_errors_without_timestamp_are_excluded_and_counted() -> None:
    log = Log().counts(0, 0, 10, 0, 0)
    log.event(Level.ERROR, None)
    log.event(Level.CRITICAL, None)
    (finding,) = run(log.events).findings
    assert finding.evidence["excluded_without_timestamp"] == 2
    assert finding.evidence["errors"] == 10


def test_a_stray_old_timestamp_does_not_create_millions_of_windows() -> None:
    log = Log().counts(0, 0, 10, 0, 0)
    log.event(Level.INFO, datetime(1970, 1, 1, tzinfo=UTC))  # about 29.6 million windows earlier
    (finding,) = run(log.events).findings
    assert finding.evidence["baseline"] == 0.0
    assert finding.evidence["errors"] == 10


# --- Finding content -----------------------------------------------------------------------------


def test_events_and_sequences_are_capped_in_timeline_order() -> None:
    log = Log()
    log.add(0, 1, level=Level.INFO)
    log.add(4, 1, level=Level.INFO)
    for i in range(6):
        log.event(Level.ERROR, START + timedelta(minutes=2, seconds=i), request_id=f"r{5 - i}")
    (finding,) = run(log.events, max_examples=3).findings
    assert finding.event_count == 6
    assert finding.events == (
        SourceRef("app.log", 3),
        SourceRef("app.log", 4),
        SourceRef("app.log", 5),
    )
    assert finding.correlation_ids == ("r5", "r4", "r3")


def test_finding_id_depends_on_the_start_of_the_spike() -> None:
    first = findings(0, 0, 10, 0, 0)[0].id
    again = findings(0, 0, 12, 0, 0)[0].id
    other = findings(0, 10, 0, 0, 0)[0].id
    assert first == again
    assert first != other
    assert first.startswith("error_spike:")


# --- Median of windows ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("counts", "total", "expected"),
    [([10], 5, 0.0), ([5, 5, 5], 4, 5.0), ([2, 5, 2, 1], 4, 2.0), ([4, 6], 2, 5.0)],
)
def test_window_median(counts: list[int], total: int, expected: float) -> None:
    assert window_median(counts, total) == expected


@given(st.lists(st.integers(1, 50), max_size=30), st.integers(0, 30))
def test_window_median_equals_the_median_of_all_windows(counts: list[int], empty: int) -> None:
    # Independent oracle: build every window, empty ones included.
    total = len(counts) + empty
    if total == 0:
        return
    assert window_median(counts, total) == statistics.median(counts + [0] * empty)
