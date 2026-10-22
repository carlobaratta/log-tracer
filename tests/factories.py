"""Builders for domain objects, so that tests state only what they care about."""

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from types import MappingProxyType

from log_tracer.models import Event, Level, SourceRef

T0 = datetime(2026, 10, 16, 14, 0, 0, tzinfo=UTC)


def at(seconds: float) -> datetime:
    """A timestamp ``seconds`` after :data:`T0`."""
    return T0 + timedelta(seconds=seconds)


def make_event(
    *,
    message: str = "something happened",
    level: Level = Level.INFO,
    timestamp: datetime | None = T0,
    service: str | None = "api",
    path: str = "app.log",
    line: int = 1,
    request_id: str | None = None,
    trace_id: str | None = None,
    duration_ms: float | None = None,
    raw_level: str | None = None,
    attributes: Mapping[str, str] | None = None,
) -> Event:
    return Event(
        ref=SourceRef(path, line),
        timestamp=timestamp,
        level=level,
        raw_level=raw_level if raw_level is not None else level.name,
        message=message,
        service=service,
        request_id=request_id,
        trace_id=trace_id,
        duration_ms=duration_ms,
        attributes=MappingProxyType(dict(attributes or {})),
    )
