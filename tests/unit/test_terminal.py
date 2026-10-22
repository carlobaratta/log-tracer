"""Terminal rendering with Rich (SPEC §5.3).

Renderables are printed on a console with a fixed width and no colors, and the
tests look for specific lines and table cells rather than comparing whole
snapshots, which would break on every Rich upgrade.
"""

import dataclasses
import io
import re
import unicodedata
from datetime import timedelta

import pytest
from hypothesis import given
from hypothesis import strategies as st
from rich.console import Console, RenderableType

from log_tracer.models import (
    IssueCode,
    Level,
    RuleName,
    RuleOutcome,
    SkippedFile,
    SkipReason,
    SourceSummary,
    TimeRange,
    UnparsedReason,
)
from log_tracer.reporting.terminal import render_report, render_top_errors, safe_text
from tests.factories import FINDING, SOURCE, TOP_ERROR, at, make_report

BIDI_AND_SEPARATORS = set(
    "\u061c\u200e\u200f\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069\u2028\u2029"
)


def plain(renderable: RenderableType, *, width: int = 140) -> str:
    buffer = io.StringIO()
    Console(file=buffer, width=width, color_system=None).print(renderable)
    return buffer.getvalue()


def lines(renderable: RenderableType) -> list[str]:
    return [line.strip() for line in plain(renderable).splitlines() if line.strip()]


def rows(renderable: RenderableType) -> list[list[str]]:
    """Table rows split into cells (cells are separated by two or more spaces)."""
    return [re.split(r" {2,}", line) for line in lines(renderable)]


# --- Escaping of log content (CLI-4) ------------------------------------------------------


@pytest.mark.parametrize(
    "value", ["[bold red]alert[/]", "disk :warning: full", "café ✓ \U0001f600 «x» 日本"]
)
def test_text_that_rich_would_interpret_is_kept_literally(value: str) -> None:
    # A default console interprets markup and emoji in plain strings.
    buffer = io.StringIO()
    Console(file=buffer, width=200, color_system=None).print(safe_text(value))
    assert buffer.getvalue() == value + "\n"


@pytest.mark.parametrize(
    ("value", "shown"),
    [
        ("\x1b[31mred", "\\x1b[31mred"),
        ("a\nb\tc\rd", "a\\nb\\tc\\rd"),
        ("nul\x00 del\x7f nel\x85", "nul\\x00 del\\x7f nel\\x85"),
        ("abc\u202eevil", "abc\\u202eevil"),
        ("x\u2066y\u200fz", "x\\u2066y\\u200fz"),
        ("line\u2028sep", "line\\u2028sep"),
    ],
)
def test_control_and_bidi_characters_are_shown_as_escapes(value: str, shown: str) -> None:
    assert safe_text(value).plain == shown


@given(st.text())
def test_escaped_text_never_contains_control_or_bidi_characters(value: str) -> None:
    for char in safe_text(value).plain:
        assert unicodedata.category(char) != "Cc"
        assert char not in BIDI_AND_SEPARATORS


# --- Analysis report ----------------------------------------------------------------------


def test_header_summarizes_files_events_and_unparsed_lines() -> None:
    assert lines(render_report(make_report()))[0] == (
        "Log Tracer 0.1.0 · 1 file · 7 events · 2 unparsed lines"
    )


def test_header_uses_singular_and_plural_forms() -> None:
    second = dataclasses.replace(SOURCE, path="logs/b.log", events=2, unparsed=0)
    report = make_report(sources=(SOURCE, second), unparsed_counts={UnparsedReason.NO_MATCH: 1})
    assert lines(render_report(report))[0] == (
        "Log Tracer 0.1.0 · 2 files · 9 events · 1 unparsed line"
    )


def test_time_range_is_shown_in_utc() -> None:  # CLI-5
    report = make_report(time_range=TimeRange(at(0), at(90)))
    assert "Time range (UTC): 2026-10-16T14:00:00.000Z → 2026-10-16T14:01:30.000Z" in lines(
        render_report(report)
    )


def test_missing_time_range_is_stated() -> None:
    assert "Time range: no timestamps" in lines(render_report(make_report(time_range=None)))


def test_findings_table() -> None:
    output = render_report(make_report())
    assert "Findings (1)" in lines(output)
    assert ["Severity", "Rule", "Title", "Events"] in rows(output)
    assert ["medium", "recurring_error", "Recurring error in payments: timeout", "3"] in rows(
        output
    )


def test_no_findings_is_stated() -> None:
    assert "No findings." in lines(render_report(make_report(findings=())))


def test_top_errors_show_time_only_when_the_analysis_spans_one_day() -> None:
    output = lines(render_report(make_report(time_range=TimeRange(at(0), at(90)))))
    assert "1. 3x payments: timeout" in output
    assert "first 14:00:01.000Z · last 14:00:09.000Z · example logs/app.log:4" in output


def test_top_errors_show_full_timestamps_across_days() -> None:
    report = make_report(time_range=TimeRange(at(0), at(0) + timedelta(days=1)))
    expected = (
        "first 2026-10-16T14:00:01.000Z · last 2026-10-16T14:00:09.000Z · example logs/app.log:4"
    )
    assert expected in lines(render_report(report))


def test_no_errors_is_stated() -> None:
    assert "No ERROR or CRITICAL events." in lines(render_report(make_report(top_errors=())))


def test_missing_service_and_timestamps_are_marked() -> None:
    group = dataclasses.replace(TOP_ERROR, service=None, first_seen=None, last_seen=None)
    output = lines(render_report(make_report(top_errors=(group,))))
    assert "1. 3x (none): timeout" in output
    assert "first - · last - · example logs/app.log:4" in output


def test_long_patterns_are_shortened() -> None:
    group = dataclasses.replace(TOP_ERROR, fingerprint="x" * 300)
    output = "".join(plain(render_report(make_report(top_errors=(group,)))).split())
    assert "x" * 119 + "…" in output
    assert "x" * 120 not in output


def test_levels_line_skips_zero_counts_and_follows_severity_order() -> None:
    report = make_report(level_counts={Level.INFO: 4, Level.UNKNOWN: 1, Level.CRITICAL: 2})
    assert "Levels: CRITICAL 2 · INFO 4 · UNKNOWN 1" in lines(render_report(report))


def test_inputs_and_unreadable_sources() -> None:
    failed = SourceSummary("locked.log", SOURCE.format, None, None, 0, 0, 0, 0, "Permission denied")
    output = lines(render_report(make_report(sources=(SOURCE, failed))))
    assert "logs/app.log (text): 7 events, 2 unparsed" in output
    assert "locked.log (text): not read (Permission denied)" in output


def test_skipped_files_are_listed_up_to_five() -> None:
    skipped = tuple(
        SkippedFile(f"logs/f{i}.csv", SkipReason.UNSUPPORTED_EXTENSION) for i in range(7)
    )
    output = lines(render_report(make_report(skipped=skipped)))
    joined = "\n".join(output)
    assert "logs/f4.csv (unsupported_extension)" in joined
    assert "logs/f5.csv" not in joined
    assert "and 2 more" in joined


def test_data_quality_lists_only_non_zero_counts() -> None:
    report = make_report(
        unparsed_counts={UnparsedReason.INVALID_JSON: 1, UnparsedReason.NO_MATCH: 2},
        issue_counts={IssueCode.TIMESTAMP_MISSING: 1, IssueCode.LEVEL_UNKNOWN: 0},
    )
    output = lines(render_report(report))
    assert "Unparsed lines: no_match 2 · invalid_json 1" in output
    assert "Field issues: timestamp.missing 1" in output


def test_clean_data_is_stated() -> None:
    report = make_report(unparsed_counts={}, issue_counts={}, skipped=())
    assert "Data quality: no unparsed lines or field issues" in lines(render_report(report))


def test_skipped_rules_are_listed() -> None:
    rules = (
        RuleOutcome(RuleName.RECURRING_ERROR, "ok", None, (FINDING,)),
        RuleOutcome(RuleName.RECURRING_ERROR, "skipped", "no_duration_data", ()),
    )
    assert "Skipped rules: recurring_error (no_duration_data)" in lines(
        render_report(make_report(rules=rules))
    )


def test_log_content_is_escaped_everywhere() -> None:  # CLI-4
    group = dataclasses.replace(TOP_ERROR, service="[red]svc", fingerprint="boom\x1b[2J")
    finding = dataclasses.replace(FINDING, title="Recurring error in [red]svc: boom\x1b[2J")
    source = dataclasses.replace(SOURCE, path="logs/[b]x.log")
    output = plain(
        render_report(make_report(sources=(source,), top_errors=(group,), findings=(finding,)))
    )
    assert "[red]svc" in output
    assert "boom\\x1b[2J" in output
    assert "logs/[b]x.log" in output
    assert "\x1b" not in output


# --- Top errors command -----------------------------------------------------------------------


def test_top_errors_view() -> None:
    report = make_report(time_range=TimeRange(at(0), at(90)))
    assert lines(render_top_errors(report)) == [
        "Top errors in 1 file (7 events)",
        "1. 3x payments: timeout",
        "first 14:00:01.000Z · last 14:00:09.000Z · example logs/app.log:4",
    ]


def test_top_errors_ranks_are_aligned() -> None:
    groups = tuple(dataclasses.replace(TOP_ERROR, fingerprint=f"error {i}") for i in range(10))
    output = plain(render_top_errors(make_report(top_errors=groups)))
    assert " 1. 3x payments: error 0" in output
    assert "10. 3x payments: error 9" in output


def test_top_errors_view_without_errors() -> None:
    assert lines(render_top_errors(make_report(top_errors=()))) == [
        "No ERROR or CRITICAL events found in 1 file (7 events)."
    ]


def test_shortened_text_does_not_end_with_a_space_before_the_ellipsis() -> None:
    finding = dataclasses.replace(FINDING, title="a" * 68 + " tail of the title")
    output = plain(render_report(make_report(findings=(finding,))))
    assert "a" * 68 + "…" in output


def test_narrow_terminals_wrap_the_list_instead_of_cutting_it() -> None:
    path = "logs/very/long/directory/structure/for/one/service/application.log"
    ref = dataclasses.replace(TOP_ERROR.events[0], path=path)
    group = dataclasses.replace(TOP_ERROR, events=(ref,))
    output = plain(render_top_errors(make_report(top_errors=(group,))), width=50)
    # One column only, so removing the line breaks rebuilds the text exactly.
    assert f"example{path}:4" in "".join(output.split())


def test_narrow_summary_keeps_every_part_of_an_error_group() -> None:
    # Full timestamps (24 characters each) used to squeeze a table to nothing.
    group = dataclasses.replace(TOP_ERROR, service="api-gateway", fingerprint="payment timeout")
    report = make_report(time_range=None, top_errors=(group,))
    output = "".join(plain(render_report(report), width=60).split())
    assert "1.3xapi-gateway:paymenttimeout" in output
    assert (
        "first2026-10-16T14:00:01.000Z·last2026-10-16T14:00:09.000Z·examplelogs/app.log:4" in output
    )


def test_group_without_an_event_sample_has_no_example() -> None:
    group = dataclasses.replace(TOP_ERROR, events=())
    output = lines(
        render_report(make_report(top_errors=(group,), time_range=TimeRange(at(0), at(90))))
    )
    assert "first 14:00:01.000Z · last 14:00:09.000Z" in output


@pytest.mark.parametrize(
    ("unparsed", "issues", "expected", "absent"),
    [
        ({UnparsedReason.NO_MATCH: 2}, {}, "Unparsed lines: no_match 2", "Field issues:"),
        ({}, {IssueCode.LEVEL_UNKNOWN: 1}, "Field issues: level.unknown 1", "Unparsed lines:"),
    ],
)
def test_data_quality_shows_only_what_is_present(
    unparsed: dict[UnparsedReason, int], issues: dict[IssueCode, int], expected: str, absent: str
) -> None:
    output = lines(render_report(make_report(unparsed_counts=unparsed, issue_counts=issues)))
    assert expected in output
    assert not any(line.startswith(absent) for line in output)
