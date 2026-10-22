"""Level distribution, time range and error groups."""

import random

from log_tracer.analysis.correlation import timeline_key
from log_tracer.analysis.statistics import count_levels, group_errors, time_range
from log_tracer.models import Level, SourceRef, TimeRange
from tests.factories import at, make_event


def test_levels_are_counted() -> None:
    events = [
        make_event(level=Level.INFO),
        make_event(level=Level.ERROR),
        make_event(level=Level.INFO),
        make_event(level=Level.UNKNOWN),
    ]
    assert count_levels(events) == {Level.INFO: 2, Level.ERROR: 1, Level.UNKNOWN: 1}


def test_time_range_ignores_events_without_timestamp() -> None:
    events = [
        make_event(timestamp=at(30)),
        make_event(timestamp=None),
        make_event(timestamp=at(-10)),
        make_event(timestamp=at(5)),
    ]
    assert time_range(events) == TimeRange(start=at(-10), end=at(30))


def test_time_range_is_none_without_timestamps() -> None:
    assert time_range([make_event(timestamp=None)]) is None
    assert time_range([]) is None


# --- Timeline order (COR-3) -------------------------------------------------


def test_timeline_orders_by_time_then_path_then_line_with_untimed_last() -> None:
    a = make_event(timestamp=at(1), path="b.log", line=9)
    b = make_event(timestamp=at(1), path="a.log", line=5)
    c = make_event(timestamp=at(1), path="a.log", line=2)
    d = make_event(timestamp=at(0), path="z.log", line=1)
    e = make_event(timestamp=None, path="a.log", line=1)
    shuffled = [a, b, c, d, e]
    random.Random(4).shuffle(shuffled)
    assert sorted(shuffled, key=timeline_key) == [d, c, b, a, e]


# --- Error groups -------------------------------------------------------------


def test_only_error_and_critical_events_are_grouped() -> None:
    events = [
        make_event(level=Level.WARNING, message="disk almost full"),
        make_event(level=Level.ERROR, message="disk full"),
        make_event(level=Level.CRITICAL, message="disk full"),
        make_event(level=Level.UNKNOWN, message="disk full"),
    ]
    (group,) = group_errors(events, max_examples=10)
    assert group.fingerprint == "disk full"
    assert group.count == 2
    assert group.has_critical is True


def test_groups_are_keyed_by_service_and_fingerprint() -> None:
    events = [
        make_event(level=Level.ERROR, service="payments", message="timeout after 10 ms"),
        make_event(level=Level.ERROR, service="payments", message="timeout after 99 ms"),
        make_event(level=Level.ERROR, service="orders", message="timeout after 10 ms"),
        make_event(level=Level.ERROR, service=None, message="timeout after 10 ms"),
    ]
    groups = group_errors(events, max_examples=10)
    assert [(g.service, g.fingerprint, g.count) for g in groups] == [
        ("payments", "timeout after <num> ms", 2),
        ("orders", "timeout after <num> ms", 1),
        (None, "timeout after <num> ms", 1),
    ]


def test_groups_are_sorted_by_count_then_service_then_fingerprint() -> None:
    events = [
        make_event(level=Level.ERROR, service="b", message="y"),
        make_event(level=Level.ERROR, service="b", message="x"),
        make_event(level=Level.ERROR, service="a", message="z"),
        make_event(level=Level.ERROR, service="c", message="w"),
        make_event(level=Level.ERROR, service="c", message="w"),
    ]
    groups = group_errors(events, max_examples=10)
    assert [(g.service, g.fingerprint) for g in groups] == [
        ("c", "w"),
        ("a", "z"),
        ("b", "x"),
        ("b", "y"),
    ]


def test_group_records_first_and_last_seen_and_a_capped_ordered_sample() -> None:
    events = [
        make_event(level=Level.ERROR, message="boom", timestamp=at(30), line=4),
        make_event(level=Level.ERROR, message="boom", timestamp=None, line=9),
        make_event(level=Level.ERROR, message="boom", timestamp=at(10), line=2),
        make_event(level=Level.ERROR, message="boom", timestamp=at(20), line=3),
    ]
    (group,) = group_errors(events, max_examples=2)
    assert group.count == 4
    assert group.has_critical is False
    assert group.first_seen == at(10)
    assert group.last_seen == at(30)
    assert group.events == (SourceRef("app.log", 2), SourceRef("app.log", 3))


def test_group_without_timestamps_has_no_first_or_last_seen() -> None:
    (group,) = group_errors([make_event(level=Level.ERROR, timestamp=None)], max_examples=10)
    assert group.first_seen is None
    assert group.last_seen is None


def test_grouping_does_not_depend_on_input_order() -> None:
    events = [
        make_event(level=Level.ERROR, service=s, message=m, line=i)
        for i, (s, m) in enumerate([("a", "x 1"), ("b", "y"), ("a", "x 2"), ("b", "y")] * 3)
    ]
    expected = group_errors(events, max_examples=3)
    for seed in range(5):
        shuffled = list(events)
        random.Random(seed).shuffle(shuffled)
        assert group_errors(shuffled, max_examples=3) == expected


# --- Sequences of each error group (RUL-1, COR-1) -------------------------------------------


def test_error_groups_count_their_sequences_in_order_of_first_occurrence() -> None:
    events = [
        make_event(level=Level.ERROR, message="boom", line=1, timestamp=at(3), request_id="r3"),
        make_event(level=Level.ERROR, message="boom", line=2, timestamp=at(1), request_id="r1"),
        make_event(level=Level.ERROR, message="boom", line=3, timestamp=at(2), request_id="r3"),
        make_event(level=Level.ERROR, message="boom", line=4, timestamp=at(4)),
        make_event(level=Level.ERROR, message="boom", line=5, timestamp=at(5), request_id="r2"),
    ]
    label_of = {
        SourceRef("app.log", 1): "r3",
        SourceRef("app.log", 2): "r1",
        SourceRef("app.log", 3): "r3",
        SourceRef("app.log", 5): "r2",
    }
    (group,) = group_errors(events, max_examples=2, label_of=label_of)
    assert group.correlation_groups == 3
    assert group.correlation_ids == ("r1", "r3")  # timeline order, capped at max_examples


def test_error_groups_without_identifiers_have_no_sequences() -> None:
    (group,) = group_errors([make_event(level=Level.ERROR)], max_examples=10)
    assert (group.correlation_groups, group.correlation_ids) == (0, ())
