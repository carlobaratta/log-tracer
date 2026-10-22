"""Terminal rendering of reports with Rich (SPEC §5.3).

The functions here build Rich renderables and never print: the CLI decides
where they go. Text that comes from the logs (messages, services, paths) is
always wrapped by :func:`safe_text`, because Rich would otherwise interpret
markup and emoji codes in plain strings, and would pass terminal control
sequences through unchanged (CLI-4).
"""

import re
from collections.abc import Callable, Iterable, Mapping
from datetime import UTC, datetime
from typing import Final

from rich import box
from rich.console import Group, RenderableType
from rich.table import Table
from rich.text import Text

from log_tracer.models import (
    ErrorGroup,
    Finding,
    IngestionSummary,
    IssueCode,
    Level,
    Report,
    RuleOutcome,
    Severity,
    TimeRange,
    UnparsedReason,
)

_UNSAFE_RE: Final = re.compile(
    # C0 controls, DEL and C1 controls; bidirectional overrides and isolates,
    # which can make text display in a different order than it is stored
    # ("Trojan Source"); line and paragraph separators.
    "[\x00-\x1f\x7f-\x9f\u061c\u200e\u200f\u202a-\u202e\u2066-\u2069\u2028\u2029]"
)
_NAMED_ESCAPES: Final = {"\n": "\\n", "\t": "\\t", "\r": "\\r"}

_TITLE_LIMIT: Final = 70
_PATTERN_LIMIT: Final = 120
_SKIPPED_LIMIT: Final = 5
_LEVEL_ORDER: Final = (
    Level.CRITICAL,
    Level.ERROR,
    Level.WARNING,
    Level.INFO,
    Level.DEBUG,
    Level.TRACE,
    Level.UNKNOWN,
)
_SEVERITY_STYLES: Final = {
    Severity.HIGH: "bold red",
    Severity.MEDIUM: "yellow",
    Severity.LOW: "cyan",
}


def safe_text(value: str, style: str = "") -> Text:
    """Literal text with control and bidi characters shown as escapes (``\\x1b``)."""
    return Text(_escape(value), style=style)


def render_report(report: Report) -> RenderableType:
    """The summary printed by ``analyze``: findings first, details last."""
    ingestion, analysis = report.ingestion, report.analysis
    time_format = _time_formatter(analysis.time_range)
    parts: list[RenderableType] = [
        Text(_headline(report), style="bold"),
        _time_range(analysis.time_range),
        Text(),
        *_findings(analysis.findings),
        Text(),
        *_error_groups(analysis.top_errors, time_format),
        Text(),
        _levels(analysis.level_counts),
        *_inputs(ingestion),
        *_data_quality(ingestion),
        *_skipped_rules(analysis.rules),
    ]
    return Group(*parts)


def render_top_errors(report: Report) -> RenderableType:
    """The list printed by ``top-errors``."""
    scope = (
        f"{_plural(len(report.ingestion.sources), 'file')} ({_plural(_events(report), 'event')})"
    )
    groups = report.analysis.top_errors
    if not groups:
        return Text(f"No ERROR or CRITICAL events found in {scope}.")
    time_format = _time_formatter(report.analysis.time_range)
    return Group(
        Text(f"Top errors in {scope}", style="bold"), Text(), *_error_list(groups, time_format)
    )


# --- Sections ---------------------------------------------------------------------------


def _headline(report: Report) -> str:
    unparsed = sum(report.ingestion.unparsed_counts.values())
    return " · ".join(
        [
            f"Log Tracer {report.tool_version}",
            _plural(len(report.ingestion.sources), "file"),
            _plural(_events(report), "event"),
            _plural(unparsed, "unparsed line"),
        ]
    )


def _time_range(time_range: TimeRange | None) -> Text:
    if time_range is None:
        return Text("Time range: no timestamps")
    start, end = _iso(time_range.start), _iso(time_range.end)
    return Text(f"Time range (UTC): {start} → {end}")


def _findings(findings: tuple[Finding, ...]) -> list[RenderableType]:
    if not findings:
        return [Text("No findings.")]
    table = _table("Severity", "Rule", "Title", "Events")
    for finding in findings:
        table.add_row(
            Text(finding.severity.name.lower(), style=_SEVERITY_STYLES[finding.severity]),
            Text(finding.rule.value),
            _cell(finding.title, _TITLE_LIMIT),
            Text(str(finding.event_count)),
        )
    return [Text(f"Findings ({len(findings)})", style="bold"), table]


def _error_groups(
    groups: tuple[ErrorGroup, ...], time_format: Callable[[datetime | None], str]
) -> list[RenderableType]:
    if not groups:
        return [Text("No ERROR or CRITICAL events.")]
    return [Text("Top errors", style="bold"), *_error_list(groups, time_format)]


def _error_list(
    groups: tuple[ErrorGroup, ...], time_format: Callable[[datetime | None], str]
) -> list[RenderableType]:
    """Two lines per error group.

    A list rather than a table: with up to seven attributes per group, and
    timestamps that can be 24 characters wide, a table on a narrow terminal
    squeezes the pattern column to nothing. A list wraps like ordinary text.
    """
    width = len(str(len(groups)))
    lines: list[RenderableType] = []
    for rank, group in enumerate(groups, start=1):
        service = "(none)" if group.service is None else _escape(group.service)
        pattern = _shorten(_escape(group.fingerprint), _PATTERN_LIMIT)
        lines.append(
            Text.assemble(
                f"{rank:>{width}}. ",
                (f"{group.count}x ", "bold"),
                (service, "cyan"),
                f": {pattern}",
            )
        )
        details = [f"first {time_format(group.first_seen)}", f"last {time_format(group.last_seen)}"]
        if group.events:
            details.append(f"example {_escape(group.events[0].path)}:{group.events[0].line}")
        lines.append(Text(" " * (width + 2) + " · ".join(details), style="dim"))
    return lines


def _levels(counts: Mapping[Level, int]) -> Text:
    present = [f"{level.name} {counts[level]}" for level in _LEVEL_ORDER if counts.get(level)]
    return Text("Levels: " + (" · ".join(present) if present else "no events"))


def _inputs(ingestion: IngestionSummary) -> list[RenderableType]:
    parts: list[RenderableType] = [Text("Inputs:", style="bold")]
    for source in ingestion.sources:
        if source.error is None:
            status = f"{_plural(source.events, 'event')}, {source.unparsed} unparsed"
        else:
            status = f"not read ({source.error})"
        parts.append(safe_text(f"  {source.path} ({source.format}): {status}"))
    if ingestion.skipped:
        parts.append(Text("Skipped files:", style="bold"))
        for skipped in ingestion.skipped[:_SKIPPED_LIMIT]:
            parts.append(safe_text(f"  {skipped.path} ({skipped.reason})"))
        if len(ingestion.skipped) > _SKIPPED_LIMIT:
            parts.append(Text(f"  and {len(ingestion.skipped) - _SKIPPED_LIMIT} more"))
    return parts


def _data_quality(ingestion: IngestionSummary) -> list[RenderableType]:
    unparsed = _non_zero(ingestion.unparsed_counts, UnparsedReason)
    issues = _non_zero(ingestion.issue_counts, IssueCode)
    if not unparsed and not issues:
        return [Text("Data quality: no unparsed lines or field issues")]
    parts: list[RenderableType] = []
    if unparsed:
        parts.append(Text(f"Unparsed lines: {unparsed}"))
    if issues:
        parts.append(Text(f"Field issues: {issues}"))
    return parts


def _skipped_rules(outcomes: tuple[RuleOutcome, ...]) -> list[RenderableType]:
    skipped = [f"{o.rule.value} ({o.reason})" for o in outcomes if o.status == "skipped"]
    return [Text("Skipped rules: " + ", ".join(skipped))] if skipped else []


# --- Helpers ----------------------------------------------------------------------------


def _table(*headers: str) -> Table:
    table = Table(box=box.SIMPLE_HEAD, show_edge=False, pad_edge=False, header_style="bold")
    for header in headers:
        if header == "Events":
            table.add_column(header, justify="right", no_wrap=True)
        else:
            # On narrow terminals, long cells wrap instead of being cut with "…".
            table.add_column(header, overflow="fold")
    return table


def _cell(value: str, limit: int | None = None) -> Text:
    """A table cell holding log content: escaped, then shortened to ``limit`` characters."""
    escaped = _escape(value)
    return Text(escaped if limit is None else _shorten(escaped, limit))


def _shorten(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _escape(value: str) -> str:
    return _UNSAFE_RE.sub(_escape_char, value)


def _escape_char(match: re.Match[str]) -> str:
    char = match.group()
    if char in _NAMED_ESCAPES:
        return _NAMED_ESCAPES[char]
    code = ord(char)
    return f"\\x{code:02x}" if code <= 0xFF else f"\\u{code:04x}"


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _time_formatter(time_range: TimeRange | None) -> Callable[[datetime | None], str]:
    """Show only the time of day when the whole analysis falls on one UTC day."""
    same_day = time_range is not None and time_range.start.date() == time_range.end.date()

    def format_time(value: datetime | None) -> str:
        if value is None:
            return "-"
        iso = _iso(value)
        return iso.partition("T")[2] if same_day else iso

    return format_time


def _non_zero[K: (UnparsedReason, IssueCode)](counts: Mapping[K, int], order: Iterable[K]) -> str:
    return " · ".join(f"{key.value} {counts[key]}" for key in order if counts.get(key))


def _events(report: Report) -> int:
    return sum(source.events for source in report.ingestion.sources)


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"
