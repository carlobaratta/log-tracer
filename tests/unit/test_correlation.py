"""Correlation of events into sequences (SPEC §3, COR-1…7)."""

import random
from collections import deque
from datetime import datetime

import pytest
from hypothesis import given
from hypothesis import strategies as st

from log_tracer.analysis.correlation import correlate, find_groups, precedes
from log_tracer.models import Event, SourceRef
from tests.factories import at, make_event


def event(
    line: int,
    *,
    request_id: str | None = None,
    trace_id: str | None = None,
    timestamp: datetime | None = None,
    path: str = "app.log",
) -> Event:
    return make_event(
        line=line,
        path=path,
        request_id=request_id,
        trace_id=trace_id,
        timestamp=at(line) if timestamp is None else timestamp,
    )


def lines_of(events: tuple[Event, ...]) -> list[int]:
    return [e.ref.line for e in events]


# --- Grouping (COR-1, COR-2) ------------------------------------------------------------


def test_events_sharing_a_request_id_form_one_sequence() -> None:
    result = correlate(
        [event(1, request_id="r1"), event(2, request_id="r2"), event(3, request_id="r1")]
    )
    assert [(g.label, lines_of(g.events)) for g in result.groups] == [("r1", [1, 3]), ("r2", [2])]


def test_events_sharing_a_trace_id_form_one_sequence() -> None:
    result = correlate([event(1, trace_id="t1"), event(2, trace_id="t1")])
    assert [lines_of(g.events) for g in result.groups] == [[1, 2]]


def test_sequences_are_transitive() -> None:
    # SPEC COR-1: A (req=1, trace=T), B (req=1) and C (trace=T) form one sequence.
    a = event(1, request_id="1", trace_id="T")
    b = event(2, request_id="1")
    c = event(3, trace_id="T")
    d = event(4, request_id="2")
    result = correlate([c, d, b, a])
    assert [(g.label, g.request_ids, g.trace_ids, lines_of(g.events)) for g in result.groups] == [
        ("2", ("2",), (), [4]),
        ("T", ("1",), ("T",), [1, 2, 3]),
    ]


def test_a_chain_of_identifiers_is_one_sequence() -> None:
    events = [
        event(1, request_id="r1", trace_id="t1"),
        event(2, request_id="r2", trace_id="t1"),
        event(3, request_id="r2", trace_id="t2"),
        event(4, request_id="r3", trace_id="t2"),
    ]
    (group,) = correlate(events).groups
    assert group.request_ids == ("r1", "r2", "r3")
    assert group.trace_ids == ("t1", "t2")
    assert group.label == "t1"


def test_request_and_trace_identifiers_are_separate_namespaces() -> None:
    result = correlate([event(1, request_id="abc"), event(2, trace_id="abc")])
    assert len(result.groups) == 2
    # Same label: the sequence with a trace_id comes first.
    assert [(g.label, g.trace_ids) for g in result.groups] == [("abc", ("abc",)), ("abc", ())]


def test_events_without_identifiers_are_not_correlated() -> None:  # COR-2
    events = [event(1), event(2, request_id="r1"), event(3), event(4, request_id="r1")]
    result = correlate(events)
    assert result.uncorrelated == 2
    assert [lines_of(g.events) for g in result.groups] == [[2, 4]]


def test_events_close_in_time_are_not_correlated() -> None:  # COR-7
    result = correlate([event(1, timestamp=at(0)), event(2, timestamp=at(0))])
    assert result.groups == ()
    assert result.uncorrelated == 2


# --- Labels and order (COR-3, COR-5) -------------------------------------------------------


def test_label_is_the_smallest_trace_id_else_the_smallest_request_id() -> None:
    result = correlate(
        [
            event(1, request_id="r0", trace_id="t2"),
            event(2, request_id="r0", trace_id="t1"),
            event(3, request_id="r9"),
            event(4, request_id="r9", trace_id=None),
            event(5, request_id="r3"),
        ]
    )
    assert [g.label for g in result.groups] == ["r3", "r9", "t1"]


def test_events_follow_the_timeline_with_untimed_events_last() -> None:
    events = [
        make_event(line=1, request_id="r", timestamp=None),
        make_event(line=2, request_id="r", timestamp=at(5)),
        make_event(line=3, request_id="r", timestamp=at(1)),
        make_event(line=4, request_id="r", timestamp=at(1), path="a.log"),
    ]
    (group,) = correlate(events).groups
    assert [(e.ref.path, e.ref.line) for e in group.events] == [
        ("a.log", 4),
        ("app.log", 3),
        ("app.log", 2),
        ("app.log", 1),
    ]


def test_every_correlated_event_knows_its_sequence_label() -> None:
    result = correlate([event(1, trace_id="t"), event(2), event(3, request_id="r")])
    assert result.label_of == {SourceRef("app.log", 1): "t", SourceRef("app.log", 3): "r"}


# --- Precedence (COR-4) ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("first", "second", "expected"),
    [
        (event(1, timestamp=at(0)), event(2, timestamp=at(1)), True),
        (event(2, timestamp=at(1)), event(1, timestamp=at(0)), False),
        (event(1, timestamp=at(0)), event(2, timestamp=at(0)), True),  # same file: line order
        (event(2, timestamp=at(0)), event(1, timestamp=at(0)), False),
        (event(1, timestamp=at(0), path="a.log"), event(2, timestamp=at(0), path="b.log"), False),
        (event(2, timestamp=at(0), path="b.log"), event(1, timestamp=at(0), path="a.log"), False),
        (make_event(line=1, timestamp=None), event(2, timestamp=at(0)), False),
        (event(1, timestamp=at(0)), make_event(line=2, timestamp=None), False),
    ],
    ids=[
        "earlier",
        "later",
        "same-time-same-file",
        "same-time-same-file-reversed",
        "same-time-other-file",
        "same-time-other-file-reversed",
        "first-untimed",
        "second-untimed",
    ],
)
def test_precedes(first: Event, second: Event, expected: bool) -> None:
    assert precedes(first, second) is expected


# --- Lookup ----------------------------------------------------------------------------------


def test_find_by_request_id_or_trace_id() -> None:
    groups = correlate(
        [event(1, request_id="req-184", trace_id="t-1"), event(2, request_id="req-185")]
    ).groups
    assert [g.label for g in find_groups(groups, "req-184")] == ["t-1"]
    assert [g.label for g in find_groups(groups, "t-1")] == ["t-1"]
    assert [g.label for g in find_groups(groups, "req-185")] == ["req-185"]


def test_find_is_exact_and_case_sensitive() -> None:
    groups = correlate([event(1, request_id="req-184")]).groups
    assert find_groups(groups, "REQ-184") == ()
    assert find_groups(groups, "req-18") == ()


def test_find_returns_both_sequences_on_a_namespace_collision() -> None:
    groups = correlate([event(1, request_id="abc"), event(2, trace_id="abc")]).groups
    assert len(find_groups(groups, "abc")) == 2


# --- Properties ----------------------------------------------------------------------------------

identifiers = st.none() | st.sampled_from(["a", "b", "c", "d", "e", "f"])


def build(specs: list[tuple[str | None, str | None]]) -> list[Event]:
    return [
        event(i + 1, request_id=request, trace_id=trace, timestamp=at(i % 3))
        for i, (request, trace) in enumerate(specs)
    ]


def components_by_search(events: list[Event]) -> set[frozenset[SourceRef]]:
    """Independent oracle: breadth-first search over 'shares an identifier' edges."""

    def linked(a: Event, b: Event) -> bool:
        same_request = a.request_id is not None and a.request_id == b.request_id
        same_trace = a.trace_id is not None and a.trace_id == b.trace_id
        return same_request or same_trace

    seen: set[int] = set()
    components: set[frozenset[SourceRef]] = set()
    for start, first in enumerate(events):
        if start in seen or (first.request_id is None and first.trace_id is None):
            continue
        queue, members = deque([start]), set()
        seen.add(start)
        while queue:
            current = queue.popleft()
            members.add(events[current].ref)
            for other in range(len(events)):
                if other not in seen and linked(events[current], events[other]):
                    seen.add(other)
                    queue.append(other)
        components.add(frozenset(members))
    return components


@given(st.lists(st.tuples(identifiers, identifiers), max_size=30))
def test_sequences_match_an_independent_graph_search(
    specs: list[tuple[str | None, str | None]],
) -> None:
    events = build(specs)
    result = correlate(events)
    assert {frozenset(e.ref for e in g.events) for g in result.groups} == components_by_search(
        events
    )
    assert result.uncorrelated == sum(
        1 for e in events if e.request_id is None and e.trace_id is None
    )


@given(
    st.lists(st.tuples(identifiers, identifiers), max_size=30), st.randoms(use_true_random=False)
)
def test_sequences_do_not_depend_on_input_order(
    specs: list[tuple[str | None, str | None]], rng: random.Random
) -> None:
    events = build(specs)
    shuffled = list(events)
    rng.shuffle(shuffled)
    assert correlate(shuffled) == correlate(events)
