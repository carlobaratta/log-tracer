"""RUL-4 error_spike: time windows with far more errors than usual.

Windows are fixed ("tumbling"), ``window_s`` seconds long and aligned to the
Unix epoch, so they do not depend on the data. The reference level is the
median number of errors over every window of the analyzed range, empty
windows included. That median is computed from the non-empty windows only:
a single stray timestamp years away would otherwise create millions of
empty windows in memory.
"""

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from types import MappingProxyType
from typing import Final

from log_tracer.analysis.context import AnalysisContext, finding_id
from log_tracer.config import ErrorSpikeConfig
from log_tracer.models import Event, Finding, Level, RuleName, RuleOutcome, Severity

RULE = RuleName.ERROR_SPIKE
MIN_WINDOWS: Final = 3
_EPOCH: Final = datetime(1970, 1, 1, tzinfo=UTC)
_SECOND: Final = timedelta(seconds=1)


def run(context: AnalysisContext, config: ErrorSpikeConfig) -> RuleOutcome:
    timed = [(e, e.timestamp) for e in context.events if e.timestamp is not None]
    if not timed:
        return _insufficient()
    first = _window(min(ts for _, ts in timed), config.window_s)
    last = _window(max(ts for _, ts in timed), config.window_s)
    total_windows = last - first + 1
    if total_windows < MIN_WINDOWS:
        return _insufficient()

    by_window: dict[int, list[Event]] = {}
    for event, timestamp in timed:  # timeline order is kept inside each window
        if event.level >= Level.ERROR:
            by_window.setdefault(_window(timestamp, config.window_s), []).append(event)
    excluded = sum(1 for e in context.events if e.level >= Level.ERROR and e.timestamp is None)

    baseline = window_median([len(events) for events in by_window.values()], total_windows)
    threshold = float(max(config.min_errors, config.factor * baseline))
    spikes = sorted(index for index, events in by_window.items() if len(events) >= threshold)
    findings = tuple(
        _finding(context, config, run_of_windows, by_window, baseline, threshold, excluded)
        for run_of_windows in _consecutive_runs(spikes)
    )
    return RuleOutcome(rule=RULE, status="ok", reason=None, findings=findings)


def window_median(counts: Sequence[int], total_windows: int) -> float:
    """Median over ``total_windows`` windows, given the counts of the non-empty ones.

    The ``total_windows - len(counts)`` empty windows are zeros that sort
    first, so the k-th value of the full list is known without building it.
    """
    ordered = sorted(counts)
    zeros = total_windows - len(ordered)

    def value_at(position: int) -> int:
        return 0 if position < zeros else ordered[position - zeros]

    middle = total_windows // 2
    if total_windows % 2:
        return float(value_at(middle))
    return (value_at(middle - 1) + value_at(middle)) / 2


def _window(timestamp: datetime, window_s: int) -> int:
    """Index of the window containing ``timestamp``; integer floor division, also before 1970."""
    return ((timestamp - _EPOCH) // _SECOND) // window_s


def _consecutive_runs(indexes: list[int]) -> list[list[int]]:
    runs: list[list[int]] = []
    for index in indexes:
        if runs and index == runs[-1][-1] + 1:
            runs[-1].append(index)
        else:
            runs.append([index])
    return runs


def _finding(
    context: AnalysisContext,
    config: ErrorSpikeConfig,
    windows: list[int],
    by_window: dict[int, list[Event]],
    baseline: float,
    threshold: float,
    excluded: int,
) -> Finding:
    start = _EPOCH + timedelta(seconds=windows[0] * config.window_s)
    end = _EPOCH + timedelta(seconds=(windows[-1] + 1) * config.window_s)
    events = [event for index in windows for event in by_window[index]]
    peak = max(len(by_window[index]) for index in windows)
    label_of = context.label_of
    labels = list(dict.fromkeys(label_of[e.ref] for e in events if e.ref in label_of))
    noun = "window" if len(windows) == 1 else "windows"
    return Finding(
        id=finding_id(RULE, _iso(start)),
        rule=RULE,
        severity=Severity.HIGH if peak >= 2 * threshold else Severity.MEDIUM,
        title=(
            f"Error spike from {_iso(start)}: {len(events)} errors in "
            f"{len(windows) * config.window_s} s"
        ),
        description=(
            f"{len(events)} ERROR or CRITICAL events between {_iso(start)} and {_iso(end)} "
            f"({len(windows)} {noun} of {config.window_s} s). The threshold is "
            f"{_number(threshold)} errors per window, against a median of {_number(baseline)}; "
            f"the busiest window had {peak}."
        ),
        evidence=MappingProxyType(
            {
                "window_s": config.window_s,
                "start": start,
                "end": end,
                "windows": len(windows),
                "errors": len(events),
                "peak_window_errors": peak,
                "baseline": baseline,
                "threshold": threshold,
                "excluded_without_timestamp": excluded,
            }
        ),
        event_count=len(events),
        events=tuple(e.ref for e in events[: context.max_examples]),
        correlation_ids=tuple(labels[: context.max_examples]),
    )


def _insufficient() -> RuleOutcome:
    return RuleOutcome(rule=RULE, status="skipped", reason="insufficient_data", findings=())


def _iso(value: datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


def _number(value: float) -> str:
    return f"{value:.3f}".rstrip("0").rstrip(".")
