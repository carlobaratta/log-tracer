"""The scope of an analysis: --service, --since and --until (SPEC §5.2, D-9, D-18, D-19)."""

from datetime import datetime

import pytest

from log_tracer.models import DataWarning, Event, Filters, WarningCode
from log_tracer.pipeline import filter_events
from tests.factories import at, make_event

EVENTS = (
    make_event(line=1, service="api", timestamp=at(0)),
    make_event(line=2, service="payments", timestamp=at(10)),
    make_event(line=3, service="payments", timestamp=None),
    make_event(line=4, service=None, timestamp=at(20)),
    make_event(line=5, service="Payments", timestamp=at(30)),
)


def kept(filters: Filters) -> list[int]:
    events, _summary = filter_events(EVENTS, filters)
    return [event.ref.line for event in events]


def test_no_filters_keep_every_event() -> None:
    events, summary = filter_events(EVENTS, Filters())
    assert events == EVENTS
    assert (summary.excluded_events, summary.excluded_without_timestamp) == (0, 0)
    assert summary.warnings == ()


def test_service_names_are_matched_exactly() -> None:  # D-19
    assert kept(Filters(services=("payments",))) == [2, 3]


def test_several_services() -> None:
    assert kept(Filters(services=("api", "Payments"))) == [1, 5]


def test_events_without_a_service_are_excluded_by_a_service_filter() -> None:
    assert 4 not in kept(Filters(services=("api",)))


@pytest.mark.parametrize(
    ("since", "until", "lines"),
    [
        (at(10), None, [2, 4, 5]),  # since is inclusive
        (None, at(20), [1, 2]),  # until is exclusive
        (at(10), at(20), [2]),
        (at(31), None, []),
    ],
    ids=["since", "until", "both", "after-everything"],
)
def test_time_bounds_are_half_open(
    since: datetime | None, until: datetime | None, lines: list[int]
) -> None:  # D-18
    assert kept(Filters(since=since, until=until)) == lines


def test_events_without_a_timestamp_are_excluded_and_counted_with_time_bounds() -> None:
    _events, summary = filter_events(EVENTS, Filters(since=at(0)))
    assert (summary.excluded_events, summary.excluded_without_timestamp) == (1, 1)


def test_events_without_a_timestamp_are_not_counted_apart_without_time_bounds() -> None:
    _events, summary = filter_events(EVENTS, Filters(services=("api",)))
    assert (summary.excluded_events, summary.excluded_without_timestamp) == (4, 0)


def test_both_filters_apply_together() -> None:
    events, summary = filter_events(EVENTS, Filters(services=("payments",), since=at(5)))
    assert [e.ref.line for e in events] == [2]
    # Line 3 has no timestamp and the right service; line 1 has neither the service
    # nor the time: only events without a timestamp are counted apart.
    assert (summary.excluded_events, summary.excluded_without_timestamp) == (4, 1)


def test_services_are_deduplicated_and_sorted() -> None:  # NFR-2
    _events, summary = filter_events(EVENTS, Filters(services=("payments", "api", "payments")))
    assert summary.filters.services == ("api", "payments")


def test_unknown_service_is_a_warning() -> None:  # D-19
    _events, summary = filter_events(EVENTS, Filters(services=("paymnts", "api", "db")))
    assert summary.warnings == (
        DataWarning(
            WarningCode.UNMATCHED_SERVICE_FILTER,
            None,
            "no event has service 'db' (service names are matched exactly)",
        ),
        DataWarning(
            WarningCode.UNMATCHED_SERVICE_FILTER,
            None,
            "no event has service 'paymnts' (service names are matched exactly)",
        ),
    )


def test_a_service_outside_the_time_bounds_is_not_unknown() -> None:
    # The service exists in the logs: excluding all its events by time is not a typo.
    _events, summary = filter_events(EVENTS, Filters(services=("api",), since=at(5)))
    assert summary.warnings == ()


def test_order_of_kept_events_is_preserved() -> None:
    reversed_events: tuple[Event, ...] = EVENTS[::-1]
    events, _summary = filter_events(reversed_events, Filters(services=("payments",)))
    assert [e.ref.line for e in events] == [3, 2]
