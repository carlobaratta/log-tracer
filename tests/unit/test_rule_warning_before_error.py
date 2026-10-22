"""RUL-3 warning_before_error."""

from datetime import datetime

import pytest

from log_tracer.analysis.context import AnalysisContext
from log_tracer.analysis.correlation import correlate, timeline_key
from log_tracer.analysis.rules import warning_before_error
from log_tracer.config import WarningBeforeErrorConfig
from log_tracer.models import Event, Finding, Level, RuleName, RuleOutcome, Severity, SourceRef
from tests.factories import at, make_event

CONFIG = WarningBeforeErrorConfig(medium_min_groups=3)
_DEFAULT_TIME = at(-1)  # sentinel meaning "use the line number as seconds"


def warn(
    line: int,
    *,
    rid: str | None = "r1",
    ts: datetime | None = _DEFAULT_TIME,
    message: str = "disk slow",
    path: str = "app.log",
) -> Event:
    return make_event(
        line=line,
        level=Level.WARNING,
        message=message,
        request_id=rid,
        path=path,
        timestamp=at(line) if ts is _DEFAULT_TIME else ts,
    )


def error(
    line: int,
    *,
    rid: str | None = "r1",
    ts: datetime | None = _DEFAULT_TIME,
    message: str = "disk failed",
    level: Level = Level.ERROR,
    path: str = "app.log",
) -> Event:
    return make_event(
        line=line,
        level=level,
        message=message,
        request_id=rid,
        path=path,
        timestamp=at(line) if ts is _DEFAULT_TIME else ts,
    )


def run(
    events: list[Event], config: WarningBeforeErrorConfig = CONFIG, max_examples: int = 10
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
    outcome = warning_before_error.run(context, config)
    assert outcome.rule is RuleName.WARNING_BEFORE_ERROR
    return outcome


def single(events: list[Event]) -> Finding:
    outcome = run(events)
    assert outcome.status == "ok"
    (finding,) = outcome.findings
    return finding


# --- Applicability ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "events",
    [
        [warn(1)],  # no error at all
        [warn(1, rid=None), error(2, rid=None)],  # not correlated (COR-2)
        [warn(1), error(2, ts=None)],  # the only error has no timestamp
    ],
    ids=["no-error", "uncorrelated", "untimed-error"],
)
def test_skipped_without_a_correlated_timed_error(events: list[Event]) -> None:
    outcome = run(events)
    assert (outcome.status, outcome.reason, outcome.findings) == (
        "skipped",
        "no_correlated_errors",
        (),
    )


def test_ok_when_errors_have_no_preceding_warning() -> None:
    outcome = run([error(1), warn(2)])
    assert (outcome.status, outcome.findings) == ("ok", ())


# --- Basic finding ----------------------------------------------------------------------------


def test_warning_followed_by_error_in_one_sequence() -> None:
    finding = single([warn(1), error(4)])
    assert finding.severity is Severity.LOW
    assert finding.title == "Warning before error in 1 sequence: disk slow → disk failed"
    assert finding.description == (
        'In 1 correlated sequence the warning "disk slow" (api) precedes the error '
        '"disk failed" (api). This is a temporal correlation, not proven causation.'
    )
    assert finding.evidence == {
        "warning_service": "api",
        "warning_fingerprint": "disk slow",
        "error_service": "api",
        "error_fingerprint": "disk failed",
        "groups": 1,
        "median_gap_ms": 3000.0,
        "undetermined_groups": 0,
    }
    assert finding.events == (SourceRef("app.log", 1), SourceRef("app.log", 4))
    assert finding.event_count == 2
    assert finding.correlation_ids == ("r1",)


def test_medium_from_medium_min_groups_sequences() -> None:
    events = [e for rid in ("r1", "r2", "r3") for e in (warn(1, rid=rid), error(2, rid=rid))]
    # Same lines in three sequences would collide: give each sequence its own file.
    events = [
        make_event(
            line=e.ref.line,
            path=f"{e.request_id}.log",
            level=e.level,
            message=e.message,
            request_id=e.request_id,
            timestamp=e.timestamp,
        )
        for e in events
    ]
    finding = single(events)
    assert finding.evidence["groups"] == 3
    assert finding.severity is Severity.MEDIUM


# --- Which events count -------------------------------------------------------------------


def test_only_the_first_error_of_a_sequence_counts() -> None:
    events = [
        warn(1, message="cache cold"),
        error(2, message="first error"),
        warn(3, message="retrying"),
        error(4, message="second error"),
    ]
    finding = single(events)
    assert (finding.evidence["warning_fingerprint"], finding.evidence["error_fingerprint"]) == (
        "cache cold",
        "first error",
    )


def test_critical_counts_as_an_error_and_unknown_is_not_a_warning() -> None:
    events = [
        make_event(line=1, level=Level.UNKNOWN, message="odd", request_id="r1", timestamp=at(1)),
        warn(2),
        error(3, level=Level.CRITICAL, message="crash"),
    ]
    finding = single(events)
    assert finding.evidence["warning_fingerprint"] == "disk slow"
    assert finding.evidence["error_fingerprint"] == "crash"


def test_repeated_warning_counts_once_and_the_closest_sets_the_gap() -> None:
    finding = single([warn(1), warn(2), error(5)])
    assert finding.evidence["groups"] == 1
    assert finding.evidence["median_gap_ms"] == 3000.0
    assert finding.events == (SourceRef("app.log", 2), SourceRef("app.log", 5))


def test_median_gap_of_an_even_number_of_sequences() -> None:
    events = [
        warn(1, rid="r1", path="a.log"),
        error(2, rid="r1", path="a.log"),
        warn(1, rid="r2", path="b.log"),
        error(3, rid="r2", path="b.log"),
    ]
    assert single(events).evidence["median_gap_ms"] == 1500.0


def test_fingerprints_group_messages_with_different_numbers() -> None:
    events = [warn(1, message="retry attempt 1"), warn(2, message="retry attempt 2"), error(3)]
    assert single(events).evidence["warning_fingerprint"] == "retry attempt <num>"


# --- Precedence (COR-4) -------------------------------------------------------------------


def test_same_timestamp_in_the_same_file_follows_the_line_order() -> None:
    finding = single([warn(1, ts=at(5)), error(2, ts=at(5))])
    assert finding.evidence["median_gap_ms"] == 0.0


@pytest.mark.parametrize(
    "events",
    [
        [warn(1, ts=at(5), path="a.log"), error(1, ts=at(5), path="b.log")],
        [warn(1, ts=None), error(2)],
    ],
    ids=["same-time-other-file", "untimed-warning"],
)
def test_undetermined_order_alone_gives_no_finding(events: list[Event]) -> None:
    assert run(events).findings == ()


def test_undetermined_sequences_are_counted_beside_certain_ones() -> None:
    events = [
        warn(1, rid="r1", path="a.log"),
        error(2, rid="r1", path="a.log"),
        warn(1, rid="r2", path="b.log", ts=None),
        error(2, rid="r2", path="b.log"),
    ]
    finding = single(events)
    assert (finding.evidence["groups"], finding.evidence["undetermined_groups"]) == (1, 1)


def test_a_warning_after_the_error_is_neither_counted_nor_undetermined() -> None:
    outcome = run([error(1), warn(2)])
    assert outcome.findings == ()


# --- Several findings ---------------------------------------------------------------------


def test_each_pair_of_patterns_is_a_separate_finding() -> None:
    events = [
        warn(1, rid="r1", path="a.log", message="cache cold"),
        warn(2, rid="r1", path="a.log", message="disk slow"),
        error(3, rid="r1", path="a.log"),
        warn(1, rid="r2", path="b.log"),
        error(2, rid="r2", path="b.log", message="other failure"),
    ]
    pairs = {
        (f.evidence["warning_fingerprint"], f.evidence["error_fingerprint"])
        for f in run(events).findings
    }
    assert pairs == {
        ("cache cold", "disk failed"),
        ("disk slow", "disk failed"),
        ("disk slow", "other failure"),
    }


def test_events_and_sequences_follow_the_errors_and_are_capped() -> None:
    events = [
        e
        for i, rid in enumerate(["r5", "r3", "r1"])
        for e in (
            warn(2 * i, rid=rid, path=f"{rid}.log"),
            error(2 * i + 1, rid=rid, path=f"{rid}.log"),
        )
    ]
    (finding,) = run(events, max_examples=4).findings
    assert finding.correlation_ids == ("r5", "r3", "r1")[:4]
    assert finding.events == (
        SourceRef("r5.log", 0),
        SourceRef("r5.log", 1),
        SourceRef("r3.log", 2),
        SourceRef("r3.log", 3),
    )
    assert finding.event_count == 6


def test_finding_id_is_stable_and_distinct_per_pair() -> None:
    first = single([warn(1), error(2)]).id
    again = single([warn(1), warn(3), error(7)]).id
    other = single([warn(1, message="cpu hot"), error(2)]).id
    assert first == again
    assert first != other
    assert first.startswith("warning_before_error:")
