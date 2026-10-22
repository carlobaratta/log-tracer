"""Descriptive statistics: level distribution, time range and error groups."""

from collections import Counter
from collections.abc import Iterable, Sequence

from log_tracer.analysis.correlation import timeline_key
from log_tracer.analysis.fingerprint import fingerprint
from log_tracer.models import ErrorGroup, Event, Level, TimeRange


def count_levels(events: Iterable[Event]) -> dict[Level, int]:
    return dict(Counter(event.level for event in events))


def time_range(events: Iterable[Event]) -> TimeRange | None:
    timestamps = [event.timestamp for event in events if event.timestamp is not None]
    if not timestamps:
        return None
    return TimeRange(start=min(timestamps), end=max(timestamps))


def group_errors(events: Sequence[Event], *, max_examples: int) -> tuple[ErrorGroup, ...]:
    """Group ERROR and CRITICAL events by (service, fingerprint).

    Groups are sorted by count (descending), then service (missing last), then
    fingerprint. Each group keeps at most ``max_examples`` event references,
    in timeline order.
    """
    buckets: dict[tuple[str | None, str], list[Event]] = {}
    for event in sorted(events, key=timeline_key):
        if event.level >= Level.ERROR:
            key = (event.service, fingerprint(event.message))
            buckets.setdefault(key, []).append(event)

    groups = [
        _make_group(service, pattern, members, max_examples)
        for (service, pattern), members in buckets.items()
    ]
    groups.sort(key=lambda g: (-g.count, g.service is None, g.service or "", g.fingerprint))
    return tuple(groups)


def _make_group(
    service: str | None, pattern: str, members: list[Event], max_examples: int
) -> ErrorGroup:
    timestamps = [event.timestamp for event in members if event.timestamp is not None]
    return ErrorGroup(
        service=service,
        fingerprint=pattern,
        count=len(members),
        has_critical=any(event.level is Level.CRITICAL for event in members),
        first_seen=min(timestamps, default=None),
        last_seen=max(timestamps, default=None),
        events=tuple(event.ref for event in members[:max_examples]),
    )
