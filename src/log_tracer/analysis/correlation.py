"""Correlation of events: timeline ordering (COR-3)."""

from datetime import UTC, datetime
from typing import Final

from log_tracer.models import Event

_NO_TIME: Final = datetime.min.replace(tzinfo=UTC)


def timeline_key(event: Event) -> tuple[bool, datetime, str, int]:
    """Sort key of the timeline: time, then source path, then line.

    The key is total, so sorting never depends on the input order. Events
    without a timestamp come last, in source order.
    """
    return (event.timestamp is None, event.timestamp or _NO_TIME, event.ref.path, event.ref.line)
