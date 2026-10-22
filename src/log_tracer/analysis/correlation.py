"""Correlation of events into sequences (SPEC §3).

Two events belong to the same sequence when they share a ``request_id`` or a
``trace_id``, directly or through other events (COR-1): a sequence is a
connected component of the graph that links events to their identifiers. The
components are found with a union-find structure over the identifiers, in
time close to linear in the number of events. Nothing else, and in particular
no closeness in time, ever links two events (COR-7).
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Final

from log_tracer.models import CorrelationGroup, Event, SourceRef

LARGE_GROUP: Final = 10_000
"""Above this size a sequence probably comes from a reused identifier (COR-6)."""

_NO_TIME: Final = datetime.min.replace(tzinfo=UTC)

type _Key = tuple[str, str]
"""A node of the identifier graph: ("request", id) or ("trace", id).

The namespace keeps a request_id and a trace_id with the same value apart.
"""


@dataclass(frozen=True, slots=True)
class Correlation:
    groups: tuple[CorrelationGroup, ...]
    """Sorted by label; on equal labels, a sequence with a trace_id comes first."""
    uncorrelated: int
    """Events with neither a request_id nor a trace_id (COR-2)."""
    label_of: Mapping[SourceRef, str]
    """The sequence label of every correlated event."""


def timeline_key(event: Event) -> tuple[bool, datetime, str, int]:
    """Sort key of the timeline: time, then source path, then line (COR-3).

    The key is total, so sorting never depends on the input order. Events
    without a timestamp come last, in source order.
    """
    return (event.timestamp is None, event.timestamp or _NO_TIME, event.ref.path, event.ref.line)


def precedes(first: Event, second: Event) -> bool:
    """Whether ``first`` certainly happened before ``second`` (COR-4).

    Equal timestamps from different files say nothing about the order: the
    line numbers of two files cannot be compared.
    """
    if first.timestamp is None or second.timestamp is None:
        return False
    if first.timestamp != second.timestamp:
        return first.timestamp < second.timestamp
    return first.ref.path == second.ref.path and first.ref.line < second.ref.line


def correlate(events: Iterable[Event]) -> Correlation:
    events = list(events)
    forest = _UnionFind()
    for event in events:
        keys = _keys(event)
        for key in keys:
            forest.add(key)
        if len(keys) == 2:
            forest.union(keys[0], keys[1])

    members: dict[_Key, list[Event]] = {}
    uncorrelated = 0
    for event in events:
        keys = _keys(event)
        if keys:
            members.setdefault(forest.find(keys[0]), []).append(event)
        else:
            uncorrelated += 1

    groups = sorted(
        (_group(group_events) for group_events in members.values()),
        key=lambda g: (g.label, not g.trace_ids, g.request_ids, g.trace_ids),
    )
    label_of = {event.ref: group.label for group in groups for event in group.events}
    return Correlation(tuple(groups), uncorrelated, MappingProxyType(label_of))


def find_groups(groups: Sequence[CorrelationGroup], query: str) -> tuple[CorrelationGroup, ...]:
    """Sequences where ``query`` is a request_id or a trace_id (exact, case-sensitive)."""
    return tuple(g for g in groups if query in g.request_ids or query in g.trace_ids)


def _keys(event: Event) -> list[_Key]:
    keys: list[_Key] = []
    if event.request_id is not None:
        keys.append(("request", event.request_id))
    if event.trace_id is not None:
        keys.append(("trace", event.trace_id))
    return keys


def _group(events: list[Event]) -> CorrelationGroup:
    request_ids = tuple(sorted({e.request_id for e in events if e.request_id is not None}))
    trace_ids = tuple(sorted({e.trace_id for e in events if e.trace_id is not None}))
    return CorrelationGroup(
        label=trace_ids[0] if trace_ids else request_ids[0],
        request_ids=request_ids,
        trace_ids=trace_ids,
        events=tuple(sorted(events, key=timeline_key)),
    )


class _UnionFind:
    """Disjoint sets with union by size and path halving."""

    def __init__(self) -> None:
        self._parent: dict[_Key, _Key] = {}
        self._size: dict[_Key, int] = {}

    def add(self, key: _Key) -> None:
        if key not in self._parent:
            self._parent[key] = key
            self._size[key] = 1

    def find(self, key: _Key) -> _Key:
        parent = self._parent
        while parent[key] != key:
            parent[key] = parent[parent[key]]
            key = parent[key]
        return key

    def union(self, a: _Key, b: _Key) -> None:
        root_a, root_b = self.find(a), self.find(b)
        if root_a == root_b:
            return
        if self._size[root_a] < self._size[root_b]:
            root_a, root_b = root_b, root_a
        self._parent[root_b] = root_a
        self._size[root_a] += self._size[root_b]
