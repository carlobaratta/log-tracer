"""Field normalization shared by every input format (SPEC §2.4)."""

from datetime import UTC, datetime, timedelta

import pytest

from log_tracer.ingestion.fields import (
    normalize_identifier,
    normalize_level,
    normalize_service,
    parse_duration_ms,
    parse_time_bound,
    parse_timestamp,
)
from log_tracer.models import Level

# --- Levels ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("TRACE", Level.TRACE),
        ("DEBUG", Level.DEBUG),
        ("INFO", Level.INFO),
        ("WARNING", Level.WARNING),
        ("WARN", Level.WARNING),
        ("ERROR", Level.ERROR),
        ("ERR", Level.ERROR),
        ("CRITICAL", Level.CRITICAL),
        ("CRIT", Level.CRITICAL),
        ("FATAL", Level.CRITICAL),
        ("error", Level.ERROR),
        ("Warn", Level.WARNING),
        ("  info ", Level.INFO),
    ],
)
def test_known_levels_and_aliases_are_recognized(raw: str, expected: Level) -> None:
    assert normalize_level(raw) is expected


@pytest.mark.parametrize("raw", ["NOTICE", "SEVERE", "30", "", "INFORMATION"])
def test_unrecognized_levels_return_none(raw: str) -> None:
    assert normalize_level(raw) is None


def test_unknown_level_ranks_below_every_threshold() -> None:
    assert all(level > Level.UNKNOWN for level in Level if level is not Level.UNKNOWN)


# --- Timestamps --------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2026-10-16T14:03:22Z", datetime(2026, 10, 16, 14, 3, 22, tzinfo=UTC)),
        ("2026-10-16T14:03:22.120Z", datetime(2026, 10, 16, 14, 3, 22, 120_000, tzinfo=UTC)),
        ("2026-10-16T16:03:22+02:00", datetime(2026, 10, 16, 14, 3, 22, tzinfo=UTC)),
        ("2026-10-16T16:03:22+0200", datetime(2026, 10, 16, 14, 3, 22, tzinfo=UTC)),
        ("2026-10-16T09:03:22-05:00", datetime(2026, 10, 16, 14, 3, 22, tzinfo=UTC)),
        ("2026-10-16T14:03:22.123456789Z", datetime(2026, 10, 16, 14, 3, 22, 123456, tzinfo=UTC)),
    ],
)
def test_timestamps_with_offset_are_converted_to_utc(raw: str, expected: datetime) -> None:
    parsed = parse_timestamp(raw)
    assert parsed is not None
    assert parsed.value == expected
    assert parsed.value.utcoffset() == timedelta(0)
    assert parsed.assumed_utc is False


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2026-10-16T14:03:22", datetime(2026, 10, 16, 14, 3, 22, tzinfo=UTC)),
        ("2026-10-16 14:03:22,250", datetime(2026, 10, 16, 14, 3, 22, 250_000, tzinfo=UTC)),
        ("2026-10-16 14:03:22.250", datetime(2026, 10, 16, 14, 3, 22, 250_000, tzinfo=UTC)),
    ],
)
def test_timestamps_without_offset_are_assumed_utc(raw: str, expected: datetime) -> None:
    parsed = parse_timestamp(raw)
    assert parsed is not None
    assert parsed.value == expected
    assert parsed.value.tzinfo is UTC
    assert parsed.assumed_utc is True


@pytest.mark.parametrize(
    "raw",
    [
        "2026-02-30T10:00:00Z",  # day does not exist
        "2026-10-16T24:00:00Z",  # hour 24: accepted by Python 3.14 only, rejected everywhere
        "2026-10-16T25:00:00Z",  # hour out of range
        "2026-10-16",  # time part is required
        "14:03:22",
        "2026/10/16 14:03:22",
        "2026-10-16T14:03:22+25:00",  # offset out of range
        "0001-01-01T00:00:00+05:00",  # conversion to UTC overflows
        "\u0662\u0660\u0662\u0666-\u0661\u0660-\u0661\u0666T14:03:22Z",  # Arabic-Indic digits
        "",
    ],
)
def test_invalid_timestamps_return_none(raw: str) -> None:
    assert parse_timestamp(raw) is None


def test_parsed_timestamps_are_always_utc_aware() -> None:
    parsed = parse_timestamp("2026-10-16T14:03:22+05:30")
    assert parsed is not None
    assert parsed.value.tzinfo is UTC
    assert parsed.value == datetime(2026, 10, 16, 8, 33, 22, tzinfo=UTC)


# --- Identifiers -------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("req-184", "req-184"),
        ("  req-184  ", "req-184"),
        ("4bf92f3577b34da6a3ce929d0e0e4736", "4bf92f3577b34da6a3ce929d0e0e4736"),
        ("100", "100"),
        ("", None),
        ("   ", None),
        ("-", None),
        ("null", None),
        ("NULL", None),
        ("None", None),
        ("n/a", None),
        ("N/A", None),
        ("0", None),
        ("00000000000000000000000000000000", None),
    ],
)
def test_identifiers_are_trimmed_and_sentinels_mean_absent(raw: str, expected: str | None) -> None:
    assert normalize_identifier(raw) == expected


# --- Service -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("payments", "payments"), ("  payments ", "payments"), ("", None), ("   ", None)],
)
def test_service_is_trimmed_and_empty_means_absent(raw: str, expected: str | None) -> None:
    assert normalize_service(raw) == expected


# --- Durations ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("0", 0.0), ("12", 12.0), ("2500", 2500.0), ("12.5", 12.5), ("0.75", 0.75)],
)
def test_valid_durations_are_parsed(raw: str, expected: float) -> None:
    assert parse_duration_ms(raw) == expected


@pytest.mark.parametrize(
    "raw",
    ["", "-1", "abc", "12ms", "1e3", "nan", "inf", "1_000", " 12", "12.", ".5", "\uff11\uff12"],
)
def test_invalid_durations_return_none(raw: str) -> None:
    assert parse_duration_ms(raw) is None


# --- Time bounds of --since and --until (D-18, D-20) ----------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2026-10-16T14:03:22Z", datetime(2026, 10, 16, 14, 3, 22, tzinfo=UTC)),
        ("2026-10-16T16:03:22+02:00", datetime(2026, 10, 16, 14, 3, 22, tzinfo=UTC)),
        ("2026-10-16 14:03:22", datetime(2026, 10, 16, 14, 3, 22, tzinfo=UTC)),  # assumed UTC
        ("2026-10-16", datetime(2026, 10, 16, tzinfo=UTC)),  # a date alone: midnight UTC
        ("  2026-10-16  ", datetime(2026, 10, 16, tzinfo=UTC)),
    ],
)
def test_time_bounds(raw: str, expected: datetime) -> None:
    assert parse_time_bound(raw) == expected


@pytest.mark.parametrize(
    "raw", ["", "yesterday", "2026-02-30", "2026-10-16T25:00:00Z", "20261016", "2026-10"]
)
def test_invalid_time_bounds(raw: str) -> None:
    assert parse_time_bound(raw) is None
