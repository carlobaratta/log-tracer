"""JSON Lines parser (SPEC §2.3)."""

import json
from datetime import UTC, datetime

import pytest

from log_tracer.ingestion.jsonl_format import parse_jsonl_line
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

REF = SourceRef("logs/app.jsonl", 3)


def parse(line: str) -> LineOutcome:
    return parse_jsonl_line(line, REF)


def parse_obj(obj: dict[str, object]) -> tuple[Event, tuple[IssueCode, ...]]:
    outcome = parse(json.dumps(obj))
    assert isinstance(outcome, ParsedEvent), outcome
    return outcome.event, outcome.issues


def parse_raw(line: str) -> tuple[Event, tuple[IssueCode, ...]]:
    outcome = parse(line)
    assert isinstance(outcome, ParsedEvent), outcome
    return outcome.event, outcome.issues


def unparsed(line: str) -> UnparsedLine:
    outcome = parse(line)
    assert isinstance(outcome, UnparsedLine), outcome
    return outcome


BASE: dict[str, object] = {"timestamp": "2026-10-16T14:03:22Z", "level": "info", "message": "hi"}


# --- Complete objects and aliases (JL-2) ---------------------------------------


def test_object_with_canonical_keys() -> None:
    event, issues = parse_obj(
        {
            "timestamp": "2026-10-16T14:03:22.120Z",
            "level": "error",
            "message": "Payment gateway timeout",
            "service": "payments",
            "request_id": "req-184",
            "trace_id": "4bf92f35",
            "duration_ms": 2500,
        }
    )
    assert event.ref == REF
    assert event.timestamp == datetime(2026, 10, 16, 14, 3, 22, 120_000, tzinfo=UTC)
    assert event.level is Level.ERROR
    assert event.raw_level == "error"
    assert event.message == "Payment gateway timeout"
    assert event.service == "payments"
    assert event.request_id == "req-184"
    assert event.trace_id == "4bf92f35"
    assert event.duration_ms == 2500.0
    assert dict(event.attributes) == {}
    assert issues == ()


def test_every_alternative_alias_is_recognized() -> None:
    event, issues = parse_obj(
        {
            "ts": "2026-10-16T14:03:22Z",
            "severity": "WARNING",
            "msg": "slow",
            "app": "inventory",
            "requestId": "r1",
            "traceId": "t1",
            "durationMs": 12.5,
        }
    )
    assert (event.level, event.message, event.service) == (Level.WARNING, "slow", "inventory")
    assert (event.request_id, event.trace_id, event.duration_ms) == ("r1", "t1", 12.5)
    assert issues == ()


@pytest.mark.parametrize(
    ("aliases", "winner"),
    [
        (["timestamp", "@timestamp", "ts", "time"], "timestamp"),
        (["@timestamp", "ts", "time"], "@timestamp"),
        (["ts", "time"], "ts"),
    ],
)
def test_first_alias_in_priority_order_wins(aliases: list[str], winner: str) -> None:
    obj: dict[str, object] = {"message": "m"}
    for minute, alias in enumerate(aliases):
        obj[alias] = f"2026-10-16T14:0{minute}:00Z"
    event, _ = parse_obj(obj)
    assert event.timestamp is not None
    assert event.timestamp.minute == aliases.index(winner)
    assert set(event.attributes) == set(aliases) - {winner}


def test_losing_alias_is_kept_as_an_attribute() -> None:
    event, _ = parse_obj({**BASE, "msg": "other", "lvl": "debug"})
    assert event.message == "hi"
    assert event.level is Level.INFO
    assert dict(event.attributes) == {"msg": "other", "lvl": "debug"}


def test_null_counts_as_absent_and_the_next_alias_is_used() -> None:
    event, _ = parse_obj({**BASE, "message": None, "msg": "from msg"})
    assert event.message == "from msg"
    assert dict(event.attributes) == {}  # a null alias is not kept as an attribute


def test_duplicate_keys_keep_the_last_value() -> None:
    event, _ = parse_raw(
        '{"ts": "2026-10-16T14:03:22Z", "level": "info", "message": "first", "message": "second"}'
    )
    assert event.message == "second"


# --- Unparsable lines (JL-1) ---------------------------------------------------------


@pytest.mark.parametrize(
    "line",
    [
        "{bad json",
        '{"message": "truncated"',
        "not json at all",
        "{'single': 'quotes'}",
        "[" * 100_000,  # deep nesting raises RecursionError, not ValueError
        "1" * 5_000,  # beyond Python's int conversion limit: ValueError
    ],
)
def test_invalid_json(line: str) -> None:
    assert unparsed(line).reason is UnparsedReason.INVALID_JSON


@pytest.mark.parametrize("line", ['["a", "b"]', '"text"', "42", "null", "true"])
def test_valid_json_that_is_not_an_object(line: str) -> None:
    assert unparsed(line).reason is UnparsedReason.NOT_AN_OBJECT


@pytest.mark.parametrize(
    "obj",
    [
        {},
        {"user": "bob"},
        {"service": "api", "request_id": "r1", "duration_ms": 5},
        {"timestamp": None, "level": None, "message": None},
    ],
)
def test_object_without_timestamp_level_or_message(obj: dict[str, object]) -> None:
    assert unparsed(json.dumps(obj)).reason is UnparsedReason.NO_KNOWN_FIELDS


def test_unparsed_excerpt_is_truncated_and_keeps_the_reference() -> None:
    line = "{" + "x" * 500
    result = unparsed(line)
    assert result.ref == REF
    assert result.excerpt == line[:200]


# --- Timestamps (JL-3, JL-5) -----------------------------------------------------------


def test_missing_timestamp_keeps_the_event() -> None:
    event, issues = parse_obj({"level": "error", "message": "boom"})
    assert event.timestamp is None
    assert issues == (IssueCode.TIMESTAMP_MISSING,)


@pytest.mark.parametrize(
    "value", ["yesterday", "2026-10-16", "2026-02-30T00:00:00Z", 1760623402, True]
)
def test_invalid_timestamp_keeps_the_event(value: object) -> None:
    event, issues = parse_obj({**BASE, "timestamp": value})
    assert event.timestamp is None
    assert issues == (IssueCode.TIMESTAMP_INVALID,)


def test_timestamp_without_offset_is_assumed_utc() -> None:
    event, issues = parse_obj({**BASE, "timestamp": "2026-10-16 14:03:22,250"})
    assert event.timestamp == datetime(2026, 10, 16, 14, 3, 22, 250_000, tzinfo=UTC)
    assert issues == (IssueCode.TIMESTAMP_ASSUMED_UTC,)


# --- Levels -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "level", "raw"),
    [
        ("WARN", Level.WARNING, "WARN"),
        ("Error", Level.ERROR, "Error"),
        ("fatal", Level.CRITICAL, "fatal"),
    ],
)
def test_level_is_case_insensitive(value: str, level: Level, raw: str) -> None:
    event, issues = parse_obj({**BASE, "level": value})
    assert (event.level, event.raw_level, issues) == (level, raw, ())


@pytest.mark.parametrize(
    ("value", "raw"),
    [("notice", "notice"), (30, "30"), (True, "true"), ({"name": "info"}, '{"name":"info"}')],
)
def test_unrecognized_level_is_unknown_with_its_original_value(value: object, raw: str) -> None:
    event, issues = parse_obj({**BASE, "level": value})
    assert event.level is Level.UNKNOWN
    assert event.raw_level == raw
    assert issues == (IssueCode.LEVEL_UNKNOWN,)


def test_missing_level_is_unknown() -> None:
    event, issues = parse_obj({"timestamp": "2026-10-16T14:03:22Z", "message": "hi"})
    assert event.level is Level.UNKNOWN
    assert event.raw_level is None
    assert issues == (IssueCode.LEVEL_MISSING,)


# --- Message ------------------------------------------------------------------------------


def test_message_is_kept_exactly() -> None:
    event, _ = parse_obj({**BASE, "message": "  Pagamento «rifiutato»\n  at line 3 "})
    assert event.message == "  Pagamento «rifiutato»\n  at line 3 "


@pytest.mark.parametrize(
    "obj", [{"timestamp": "2026-10-16T14:03:22Z", "level": "info"}, {**BASE, "message": ""}]
)
def test_missing_or_empty_message(obj: dict[str, object]) -> None:
    event, issues = parse_obj(obj)
    assert event.message == ""
    assert issues == (IssueCode.MESSAGE_MISSING,)


@pytest.mark.parametrize(
    ("value", "text"), [(42, "42"), ({"b": 1, "a": [True]}, '{"a":[true],"b":1}')]
)
def test_non_string_message_is_serialized_and_reported(value: object, text: str) -> None:
    event, issues = parse_obj({**BASE, "message": value})
    assert event.message == text
    assert issues == (IssueCode.MESSAGE_INVALID,)


# --- Service and identifiers ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"), [(" billing ", "billing"), ("", None), ("  ", None)]
)
def test_service_is_trimmed(value: str, expected: str | None) -> None:
    event, issues = parse_obj({**BASE, "service": value})
    assert (event.service, issues) == (expected, ())


@pytest.mark.parametrize("value", [5, True, ["api"]])
def test_non_string_service_is_invalid(value: object) -> None:
    event, issues = parse_obj({**BASE, "service": value})
    assert (event.service, issues) == (None, (IssueCode.SERVICE_INVALID,))


@pytest.mark.parametrize(
    ("value", "expected"),
    [("req-184", "req-184"), ("  r1 ", "r1"), (184, "184"), ("-", None), ("null", None), (0, None)],
)
def test_identifiers_accept_strings_and_integers(value: object, expected: str | None) -> None:
    event, issues = parse_obj({**BASE, "request_id": value, "trace_id": value})
    assert (event.request_id, event.trace_id, issues) == (expected, expected, ())


@pytest.mark.parametrize("value", [1.5, True, False, {"id": 1}, ["r1"]])
def test_other_identifier_types_are_invalid(value: object) -> None:
    event, issues = parse_obj({**BASE, "request_id": value, "trace_id": value})
    assert (event.request_id, event.trace_id) == (None, None)
    assert issues == (IssueCode.REQUEST_ID_INVALID, IssueCode.TRACE_ID_INVALID)


# --- Durations ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"), [(0, 0.0), (12, 12.0), (12.5, 12.5), ("340", 340.0), ("0.75", 0.75)]
)
def test_valid_durations(value: object, expected: float) -> None:
    event, issues = parse_obj({**BASE, "duration_ms": value})
    assert (event.duration_ms, issues) == (expected, ())


@pytest.mark.parametrize("value", [-1, -0.5, True, "abc", "1e3", "-5", [1], {"ms": 1}])
def test_invalid_durations(value: object) -> None:
    event, issues = parse_obj({**BASE, "duration_ms": value})
    assert (event.duration_ms, issues) == (None, (IssueCode.DURATION_INVALID,))


@pytest.mark.parametrize("literal", ["NaN", "Infinity", "-Infinity", "1e400", "1" * 400])
def test_non_finite_or_huge_durations_are_invalid(literal: str) -> None:
    # json.loads accepts NaN and Infinity; 1e400 overflows to inf; a 400-digit
    # integer cannot be converted to a float at all.
    event, issues = parse_raw(
        '{"ts": "2026-10-16T14:03:22Z", "level": "info", "message": "m", '
        f'"duration_ms": {literal}}}'
    )
    assert (event.duration_ms, issues) == (None, (IssueCode.DURATION_INVALID,))


def test_negative_zero_duration_is_normalized() -> None:
    event, _ = parse_raw(
        '{"ts": "2026-10-16T14:03:22Z", "level": "info", "message": "m", "duration_ms": -0.0}'
    )
    assert event.duration_ms == 0.0
    assert str(event.duration_ms) == "0.0"


# --- Attributes (JL-4) ------------------------------------------------------------------


def test_unrecognized_fields_become_string_attributes() -> None:
    event, _ = parse_obj(
        {
            **BASE,
            "user": "bob",
            "count": 3,
            "ratio": 0.5,
            "flag": False,
            "none": None,
            "tags": ["b", "a"],
            "meta": {"z": 1, "a": {"y": 2, "b": 3}},
        }
    )
    assert dict(event.attributes) == {
        "user": "bob",
        "count": "3",
        "ratio": "0.5",
        "flag": "false",
        "none": "null",
        "tags": '["b","a"]',
        "meta": '{"a":{"b":3,"y":2},"z":1}',
    }


def test_attributes_are_read_only() -> None:
    event, _ = parse_obj({**BASE, "user": "bob"})
    with pytest.raises(TypeError):
        event.attributes["user"] = "eve"  # type: ignore[index]


# --- Encoding -------------------------------------------------------------------------------------


def test_lone_surrogates_are_replaced_and_reported() -> None:
    # json.loads turns "\ud800" into a lone surrogate, which cannot be written
    # as UTF-8: it would make the report fail later.
    event, issues = parse_raw(
        '{"ts": "2026-10-16T14:03:22Z", "level": "info", "message": "bad \\ud800 text", '
        '"user": "x\\udfff", "k\\udc00": "v"}'
    )
    assert event.message == "bad � text"
    assert dict(event.attributes) == {"user": "x�", "k�": "v"}
    assert issues == (IssueCode.ENCODING_INVALID,)
    event.message.encode("utf-8")


def test_valid_surrogate_pairs_are_kept() -> None:
    event, issues = parse_raw(
        '{"ts": "2026-10-16T14:03:22Z", "level": "info", "message": "ok \\ud83d\\ude00"}'
    )
    assert event.message == "ok \U0001f600"
    assert issues == ()


def test_issues_are_reported_in_a_stable_order() -> None:
    _, issues = parse_obj(
        {
            "timestamp": 1,
            "level": 30,
            "message": 2,
            "service": 3,
            "request_id": 1.5,
            "trace_id": 2.5,
            "duration_ms": -1,
            "user": "\ud800",
        }
    )
    assert issues == (
        IssueCode.TIMESTAMP_INVALID,
        IssueCode.LEVEL_UNKNOWN,
        IssueCode.MESSAGE_INVALID,
        IssueCode.SERVICE_INVALID,
        IssueCode.REQUEST_ID_INVALID,
        IssueCode.TRACE_ID_INVALID,
        IssueCode.DURATION_INVALID,
        IssueCode.ENCODING_INVALID,
    )
