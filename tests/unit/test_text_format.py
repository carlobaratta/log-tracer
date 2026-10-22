"""Text format v1 parser (SPEC §2.2)."""

from datetime import UTC, datetime

import pytest

from log_tracer.ingestion.text_format import parse_text_line
from log_tracer.models import (
    Event,
    IssueCode,
    Level,
    LineOutcome,
    ParsedEvent,
    SourceRef,
    UnparsedLine,
    UnparsedReason,
)

REF = SourceRef("logs/app.log", 7)


def parse(line: str) -> LineOutcome:
    return parse_text_line(line, REF)


def parse_event(line: str) -> tuple[Event, tuple[IssueCode, ...]]:
    outcome = parse(line)
    assert isinstance(outcome, ParsedEvent), outcome
    return outcome.event, outcome.issues


def parse_unparsed(line: str) -> UnparsedLine:
    outcome = parse(line)
    assert isinstance(outcome, UnparsedLine), outcome
    return outcome


# --- Examples from SPEC §2.2 -------------------------------------------------


def test_full_line_with_service_and_trailing_fields() -> None:
    event, issues = parse_event(
        "2026-10-16T14:03:22.120Z INFO [api-gateway] POST /checkout "
        "request_id=req-184 trace_id=4bf92f35"
    )
    assert event.ref == REF
    assert event.timestamp == datetime(2026, 10, 16, 14, 3, 22, 120_000, tzinfo=UTC)
    assert event.level is Level.INFO
    assert event.raw_level == "INFO"
    assert event.service == "api-gateway"
    assert event.message == "POST /checkout"
    assert event.request_id == "req-184"
    assert event.trace_id == "4bf92f35"
    assert event.duration_ms is None
    assert dict(event.attributes) == {}
    assert issues == ()


def test_python_logging_style_timestamp_is_assumed_utc() -> None:
    event, issues = parse_event(
        "2026-10-16 14:03:24,902 ERROR [payments] Gateway timeout duration_ms=2500"
    )
    assert event.timestamp == datetime(2026, 10, 16, 14, 3, 24, 902_000, tzinfo=UTC)
    assert event.level is Level.ERROR
    assert event.message == "Gateway timeout"
    assert event.duration_ms == 2500.0
    assert event.request_id is None
    assert event.trace_id is None
    assert issues == (IssueCode.TIMESTAMP_ASSUMED_UTC,)


def test_key_value_followed_by_words_stays_in_message() -> None:
    event, issues = parse_event("2026-10-16T14:03:25Z WARN retry attempt=2 scheduled")
    assert event.level is Level.WARNING
    assert event.raw_level == "WARN"
    assert event.service is None
    assert event.message == "retry attempt=2 scheduled"
    assert dict(event.attributes) == {}
    assert issues == ()


def test_unknown_level_is_kept_with_its_original_value() -> None:
    event, issues = parse_event("2026-10-16T14:03:25Z NOTICE [db] vacuum done")
    assert event.level is Level.UNKNOWN
    assert event.raw_level == "NOTICE"
    assert event.message == "vacuum done"
    assert issues == (IssueCode.LEVEL_UNKNOWN,)


@pytest.mark.parametrize(
    ("line", "reason"),
    [
        ("14:03:25 ERROR something", UnparsedReason.NO_MATCH),
        ("2026-02-30T10:00:00Z ERROR [x] y", UnparsedReason.INVALID_TIMESTAMP),
        ("    at com.example.Foo.bar(Foo.java:42)", UnparsedReason.NO_MATCH),
    ],
)
def test_spec_examples_of_unparsable_lines(line: str, reason: UnparsedReason) -> None:
    unparsed = parse_unparsed(line)
    assert unparsed.ref == REF
    assert unparsed.reason is reason
    assert unparsed.excerpt == line


# --- Header ------------------------------------------------------------------


def test_offset_timestamp_is_converted_to_utc() -> None:
    event, issues = parse_event("2026-10-16T16:03:22+02:00 INFO [api] hello")
    assert event.timestamp == datetime(2026, 10, 16, 14, 3, 22, tzinfo=UTC)
    assert issues == ()


def test_tabs_and_repeated_spaces_are_separators() -> None:
    event, _ = parse_event("2026-10-16T14:03:22Z\tERROR  \t[payments]   card   declined")
    assert event.level is Level.ERROR
    assert event.service == "payments"
    assert event.message == "card   declined"


def test_empty_brackets_mean_no_service() -> None:
    event, _ = parse_event("2026-10-16T14:03:22Z INFO [] hello")
    assert event.service is None
    assert event.message == "hello"


def test_service_name_is_trimmed() -> None:
    event, _ = parse_event("2026-10-16T14:03:22Z INFO [ billing ] hello")
    assert event.service == "billing"


def test_brackets_later_in_the_message_are_not_a_service() -> None:
    event, _ = parse_event("2026-10-16T14:03:22Z INFO cache [warm] done")
    assert event.service is None
    assert event.message == "cache [warm] done"


def test_trailing_whitespace_is_ignored() -> None:
    event, _ = parse_event("2026-10-16T14:03:22Z INFO [api] hello request_id=r1   ")
    assert event.message == "hello"
    assert event.request_id == "r1"


def test_unicode_message_is_preserved() -> None:
    event, _ = parse_event("2026-10-16T14:03:22Z INFO [api] Pagamento rifiutato: «carta» ✓")
    assert event.message == "Pagamento rifiutato: «carta» ✓"


@pytest.mark.parametrize(
    "line",
    [
        "",
        "hello world",
        "2026-10-16T14:03:22Z error [api] lowercase level",  # text format: uppercase only
        "2026-10-16T14:03:22Z Error [api] mixed-case level",
        "2026-10-16T14:03:22Z ERROR[api] no space before bracket",
        "2026-10-16T14:03:22Z [api] missing level",
        "2026-10-16 ERROR [api] date without time",
        " 2026-10-16T14:03:22Z INFO [api] leading space",
    ],
)
def test_lines_not_following_the_grammar_are_unparsed(line: str) -> None:
    assert parse_unparsed(line).reason is UnparsedReason.NO_MATCH


def test_unclosed_bracket_is_part_of_the_message() -> None:
    event, _ = parse_event("2026-10-16T14:03:22Z INFO [unclosed bracket")
    assert event.service is None
    assert event.message == "[unclosed bracket"


def test_excerpt_is_truncated_to_200_characters() -> None:
    line = "x" * 500
    assert parse_unparsed(line).excerpt == "x" * 200


# --- Message and trailing fields --------------------------------------------


def test_line_with_only_fields_has_empty_message() -> None:
    event, issues = parse_event("2026-10-16T14:03:22Z INFO [api] request_id=r1")
    assert event.message == ""
    assert event.request_id == "r1"
    assert issues == (IssueCode.MESSAGE_MISSING,)


def test_line_with_nothing_after_level_has_empty_message() -> None:
    event, issues = parse_event("2026-10-16T14:03:22Z INFO")
    assert event.message == ""
    assert event.service is None
    assert issues == (IssueCode.MESSAGE_MISSING,)


def test_unrecognized_trailing_fields_become_attributes() -> None:
    event, _ = parse_event(
        "2026-10-16T14:03:22Z ERROR [api] POST /checkout failed status=502 region=eu-west.1"
    )
    assert event.message == "POST /checkout failed"
    assert dict(event.attributes) == {"status": "502", "region": "eu-west.1"}


def test_duplicate_keys_keep_the_last_value() -> None:
    event, _ = parse_event("2026-10-16T14:03:22Z INFO [api] hi request_id=a status=1 request_id=b")
    assert event.request_id == "b"
    assert dict(event.attributes) == {"status": "1"}


@pytest.mark.parametrize(
    "token",
    ["Key=value", "1key=value", "key=", "=value", "key-name=value"],
)
def test_tokens_that_are_not_valid_fields_stay_in_message(token: str) -> None:
    event, _ = parse_event(f"2026-10-16T14:03:22Z INFO [api] hello {token}")
    assert event.message == f"hello {token}"
    assert dict(event.attributes) == {}


def test_sentinel_identifiers_are_absent() -> None:
    event, issues = parse_event("2026-10-16T14:03:22Z INFO [api] hi request_id=- trace_id=0000")
    assert event.request_id is None
    assert event.trace_id is None
    assert issues == ()


@pytest.mark.parametrize("value", ["abc", "-5", "1e3", "nan"])
def test_invalid_duration_is_dropped_and_reported(value: str) -> None:
    event, issues = parse_event(f"2026-10-16T14:03:22Z INFO [api] done duration_ms={value}")
    assert event.duration_ms is None
    assert issues == (IssueCode.DURATION_INVALID,)


def test_issues_are_reported_in_a_stable_order() -> None:
    _, issues = parse_event("2026-10-16 14:03:22 NOTICE [api] duration_ms=x")
    assert issues == (
        IssueCode.TIMESTAMP_ASSUMED_UTC,
        IssueCode.LEVEL_UNKNOWN,
        IssueCode.MESSAGE_MISSING,
        IssueCode.DURATION_INVALID,
    )


def test_attributes_are_read_only() -> None:
    event, _ = parse_event("2026-10-16T14:03:22Z INFO [api] hi status=200")
    with pytest.raises(TypeError):
        event.attributes["status"] = "500"  # type: ignore[index]


def test_many_trailing_fields_are_parsed_in_linear_time() -> None:
    fields = " ".join(f"k{i}=v{i}" for i in range(20_000))
    event, _ = parse_event(f"2026-10-16T14:03:22Z INFO [api] bulk {fields}")
    assert event.message == "bulk"
    assert len(event.attributes) == 20_000
