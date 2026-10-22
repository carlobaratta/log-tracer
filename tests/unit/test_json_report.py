"""JSON encoding of the report (SPEC §6)."""

import json
from datetime import UTC, datetime, timedelta, timezone

import pytest

from log_tracer.models import (
    InputFormat,
    SourceSummary,
    TimeRange,
)
from log_tracer.reporting.json_report import (
    dumps,
    dumps_top_errors,
    format_timestamp,
    report_to_dict,
    top_errors_to_dict,
)
from tests.factories import at, make_report


def test_top_level_keys_are_present_in_a_fixed_order() -> None:  # REP-1
    assert list(report_to_dict(make_report())) == [
        "schema_version",
        "tool",
        "generated_at",
        "config",
        "inputs",
        "skipped_files",
        "summary",
        "data_quality",
        "rules",
        "top_errors",
        "findings",
    ]


def test_metadata_and_configuration() -> None:
    data = report_to_dict(make_report())
    assert data["schema_version"] == "1.0"
    assert data["tool"] == {"name": "log-tracer", "version": "0.1.0"}
    assert data["generated_at"] == "2026-10-16T14:00:00.000000Z"
    assert data["config"] == {
        "report": {"max_examples": 10, "top_errors": 10},
        "recurring_error": {"min_occurrences": 3, "high_count": 20},
    }


def test_inputs_identify_each_analyzed_file() -> None:
    assert report_to_dict(make_report())["inputs"] == [
        {
            "path": "logs/app.log",
            "format": "text",
            "size_bytes": 1234,
            "sha256": "ab" * 32,
            "lines": 10,
            "blank_lines": 1,
            "events": 7,
            "unparsed": 2,
            "error": None,
        }
    ]


def test_summary_lists_every_level_and_severity_including_zeros() -> None:
    summary = report_to_dict(make_report())["summary"]
    assert summary == {
        "events": 7,
        "unparsed_lines": 2,
        "time_range": {"start": None, "end": None},
        "levels": {
            "CRITICAL": 0,
            "ERROR": 3,
            "WARNING": 0,
            "INFO": 4,
            "DEBUG": 0,
            "TRACE": 0,
            "UNKNOWN": 0,
        },
        "findings_by_severity": {"high": 0, "medium": 1, "low": 0},
    }


def test_summary_time_range_is_encoded_in_utc() -> None:
    summary = report_to_dict(make_report(time_range=TimeRange(at(0), at(90))))["summary"]
    assert isinstance(summary, dict)
    assert summary["time_range"] == {
        "start": "2026-10-16T14:00:00.000000Z",
        "end": "2026-10-16T14:01:30.000000Z",
    }


def test_data_quality_lists_every_reason_and_issue_including_zeros() -> None:
    data_quality = report_to_dict(make_report())["data_quality"]
    assert data_quality == {
        "unparsed_by_reason": {
            "no_match": 2,
            "invalid_timestamp": 0,
            "invalid_json": 0,
            "not_an_object": 0,
            "no_known_fields": 0,
        },
        "unparsed_samples": [
            {"path": "logs/app.log", "line": 5, "reason": "no_match", "excerpt": "garbage"}
        ],
        "field_issues": {
            "timestamp.missing": 0,
            "timestamp.invalid": 0,
            "timestamp.assumed_utc": 0,
            "level.missing": 0,
            "level.unknown": 1,
            "message.missing": 0,
            "message.invalid": 0,
            "service.invalid": 0,
            "request_id.invalid": 0,
            "trace_id.invalid": 0,
            "duration_ms.invalid": 0,
            "encoding.invalid": 0,
        },
        "warnings": [
            {
                "code": "mostly_unparsed",
                "path": "logs/app.log",
                "message": "66% of the non-blank lines in logs/app.log could not be parsed as text",
            }
        ],
    }


def test_skipped_files_are_listed_with_their_reason() -> None:
    assert report_to_dict(make_report())["skipped_files"] == [
        {"path": "logs/notes.md", "reason": "unsupported_extension"}
    ]


def test_rules_top_errors_and_findings() -> None:
    data = report_to_dict(make_report())
    assert data["rules"] == [{"rule": "recurring_error", "status": "ok", "reason": None}]
    assert data["top_errors"] == [
        {
            "service": "payments",
            "fingerprint": "timeout",
            "count": 3,
            "first_seen": "2026-10-16T14:00:01.000000Z",
            "last_seen": "2026-10-16T14:00:09.000000Z",
            "events": [{"path": "logs/app.log", "line": 4}],
        }
    ]
    assert data["findings"] == [
        {
            "id": "recurring_error:0123456789ab",
            "rule": "recurring_error",
            "severity": "medium",
            "title": "Recurring error in payments: timeout",
            "description": "3 error events from service 'payments' match the pattern \"timeout\".",
            "evidence": {
                "count": 3,
                "has_critical": False,
                "first_seen": "2026-10-16T14:00:01.000000Z",
            },
            "event_count": 3,
            "events": [{"path": "logs/app.log", "line": 4}],
            "correlation_ids": [],
        }
    ]


def test_unreadable_source_is_encoded_with_its_error() -> None:
    failed = SourceSummary(
        "locked.log", InputFormat.TEXT, None, None, 0, 0, 0, 0, "Permission denied"
    )
    assert report_to_dict(make_report(sources=(failed,)))["inputs"] == [
        {
            "path": "locked.log",
            "format": "text",
            "size_bytes": None,
            "sha256": None,
            "lines": 0,
            "blank_lines": 0,
            "events": 0,
            "unparsed": 0,
            "error": "Permission denied",
        }
    ]


# --- Serialization --------------------------------------------------------------


def test_dumps_produces_one_json_document_ending_with_newline() -> None:  # CLI-1
    text = dumps(make_report())
    assert text.endswith("}\n")
    assert json.loads(text) == report_to_dict(make_report())


def test_dumps_is_deterministic() -> None:  # NFR-2
    assert dumps(make_report()) == dumps(make_report())


def test_non_ascii_text_is_kept_readable() -> None:
    text = dumps(make_report(evidence={"fingerprint": "Pagamento rifiutato «carta»"}))
    assert "Pagamento rifiutato «carta»" in text


def test_nan_is_rejected_instead_of_producing_invalid_json() -> None:
    with pytest.raises(ValueError, match="not JSON compliant"):
        dumps(make_report(evidence={"p95_ms": float("nan")}))


# --- Timestamps -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (datetime(2026, 10, 16, 14, 3, 22, tzinfo=UTC), "2026-10-16T14:03:22.000000Z"),
        (datetime(2026, 10, 16, 14, 3, 22, 120, tzinfo=UTC), "2026-10-16T14:03:22.000120Z"),
        (
            datetime(2026, 10, 16, 16, 3, 22, tzinfo=timezone(timedelta(hours=2))),
            "2026-10-16T14:03:22.000000Z",
        ),
    ],
)
def test_timestamps_are_rfc3339_utc_with_microseconds(value: datetime, expected: str) -> None:
    assert format_timestamp(value) == expected


def test_naive_timestamps_are_rejected() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        format_timestamp(datetime(2026, 10, 16, 14, 3, 22))


# --- top-errors document (SPEC §6) -------------------------------------------------


def test_top_errors_document_has_metadata_inputs_and_groups() -> None:
    report = make_report()
    data = top_errors_to_dict(report)
    assert list(data) == [
        "schema_version",
        "tool",
        "generated_at",
        "inputs",
        "skipped_files",
        "top_errors",
    ]
    full = report_to_dict(report)
    for key in ("schema_version", "tool", "generated_at", "inputs", "skipped_files", "top_errors"):
        assert data[key] == full[key]


def test_top_errors_document_is_one_json_document() -> None:
    text = dumps_top_errors(make_report())
    assert text.endswith("}\n")
    assert json.loads(text) == top_errors_to_dict(make_report())
