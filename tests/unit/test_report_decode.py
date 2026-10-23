"""Decoding of a saved report (STA-2, D-14, D-15).

The decoder is checked against the encoder: a report that goes through
``dumps`` and ``loads`` must encode to exactly the same document again.
"""

import dataclasses
import json
import tempfile
from pathlib import Path
from types import MappingProxyType
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from log_tracer.config import AnalysisConfig, ErrorSpikeConfig, HighLatencyConfig, LatencyThresholds
from log_tracer.models import (
    DataWarning,
    ErrorGroup,
    Filters,
    FilterSummary,
    Finding,
    Level,
    RuleName,
    RuleOutcome,
    Severity,
    SourceRef,
    TimeRange,
    WarningCode,
)
from log_tracer.pipeline import run_analysis
from log_tracer.reporting.json_report import (
    ReportDecodeError,
    dumps,
    loads,
    report_from_dict,
    report_to_dict,
)
from log_tracer.reporting.terminal import render_report
from tests.factories import FINDING, SOURCE, T0, TOP_ERROR, at, make_report
from tests.unit.test_terminal import plain

LATENCY_FINDING = Finding(
    id="high_latency:abcdefabcdef",
    rule=RuleName.HIGH_LATENCY,
    severity=Severity.LOW,
    title="High latency in api",
    description="2 events above 1000 ms.",
    evidence=MappingProxyType({"service": "api", "max_ms": 1500.5, "p95_ms": 1500.5}),
    event_count=2,
    events=(SourceRef("logs/app.log", 2), SourceRef("logs/app.log", 3)),
    correlation_ids=("t-1", "t-2"),
)
COR6 = DataWarning(WarningCode.LARGE_CORRELATION_GROUP, None, "sequence r1 has 12000 events")
UNMATCHED = DataWarning(WarningCode.UNMATCHED_SERVICE_FILTER, None, "no event has service 'x'")
FILTERS = FilterSummary(
    Filters(services=("payments", "x"), since=at(0), until=at(90)),
    excluded_events=2,
    excluded_without_timestamp=1,
    warnings=(UNMATCHED,),
)


def round_trip(report_json: str) -> str:
    return dumps(loads(report_json))


# --- Round trip ------------------------------------------------------------------------


def test_default_report_round_trips() -> None:  # STA-2
    original = dumps(make_report())
    assert round_trip(original) == original


def test_rich_report_round_trips() -> None:  # STA-2
    config = AnalysisConfig(
        high_latency=HighLatencyConfig(
            warn_ms=800,
            critical_ms=4000.5,
            services=MappingProxyType({"db": LatencyThresholds(100, 200)}),
        ),
        error_spike=ErrorSpikeConfig(window_s=30, factor=2.5, min_errors=7),
    )
    rules = (
        RuleOutcome(RuleName.RECURRING_ERROR, "ok", None, (FINDING,)),
        RuleOutcome(RuleName.HIGH_LATENCY, "ok", None, (LATENCY_FINDING,)),
        RuleOutcome(RuleName.ERROR_SPIKE, "skipped", "no events with a timestamp", ()),
    )
    report = make_report(
        config=config,
        time_range=TimeRange(at(0), at(3600.25)),
        findings=(FINDING, LATENCY_FINDING),
        rules=rules,
        correlation_groups=4,
        uncorrelated_events=2,
        analysis_warnings=(COR6,),
        top_errors=(TOP_ERROR, dataclasses.replace(TOP_ERROR, service=None, fingerprint="x")),
        sources=(SOURCE, dataclasses.replace(SOURCE, path="b.log", sha256=None, error="denied")),
        filters=FILTERS,
    )
    original = dumps(report)
    assert round_trip(original) == original


def test_decoded_report_renders_like_the_original() -> None:  # STA-2
    report = make_report(time_range=TimeRange(at(0), at(10)))
    decoded = loads(dumps(report))
    assert plain(render_report(decoded)) == plain(render_report(report))


log_lines = st.lists(
    st.sampled_from(
        [
            "2026-10-16T14:00:00Z INFO [api] request accepted request_id=r1",
            "2026-10-16T14:00:01Z ERROR [payments] Payment timeout after 2500 ms request_id=r1",
            "2026-10-16T14:00:02Z WARN [payments] slow provider trace_id=t1 request_id=r1",
            "2026-10-16T14:00:03Z CRITICAL [db] Connection pool exhausted duration_ms=7000",
            "2026-10-16T14:00:04 NOTICE [db] naive timestamp and unknown level",
            "2026-10-16T15:00:00Z ERROR Payment timeout after 99 ms duration_ms=1200",
            "garbage line",
            "",
        ]
    ),
    max_size=40,
)


filters = st.builds(
    Filters,
    services=st.lists(st.sampled_from(["api", "payments", "db", "nope"]), unique=True).map(
        lambda names: tuple(sorted(names))
    ),
    since=st.none() | st.just(at(1)),
    until=st.none() | st.just(at(3)),
)


@given(log_lines, filters)
def test_any_analysis_round_trips(lines: list[str], scope: Filters) -> None:  # STA-2, NFR-2
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "app.log"
        path.write_text("\n".join(lines), encoding="utf-8")
        config = AnalysisConfig(error_spike=ErrorSpikeConfig(min_errors=1))
        original = dumps(run_analysis([path], config=config, now=T0, filters=scope))
    assert round_trip(original) == original


# --- Reconstructed model -----------------------------------------------------------------


def test_decoded_model_values() -> None:
    report = make_report(
        time_range=TimeRange(at(0), at(10)),
        analysis_warnings=(COR6,),
        findings=(FINDING, LATENCY_FINDING),
        rules=(
            RuleOutcome(RuleName.RECURRING_ERROR, "ok", None, (FINDING,)),
            RuleOutcome(RuleName.HIGH_LATENCY, "ok", None, (LATENCY_FINDING,)),
        ),
    )
    decoded = loads(dumps(dataclasses.replace(report, filters=FILTERS)))
    assert decoded.filters == FILTERS
    assert decoded.generated_at == T0
    assert decoded.config == report.config
    assert decoded.ingestion.sources == report.ingestion.sources
    assert decoded.ingestion.skipped == report.ingestion.skipped
    assert decoded.ingestion.unparsed_samples == report.ingestion.unparsed_samples
    assert decoded.analysis.time_range == report.analysis.time_range
    assert decoded.analysis.level_counts[Level.ERROR] == 3
    # The two lists of warnings are merged in the report and split again by code.
    assert decoded.ingestion.warnings == report.ingestion.warnings
    assert decoded.analysis.warnings == (COR6,)
    # Each rule outcome gets back its own findings.
    assert [[f.id for f in o.findings] for o in decoded.analysis.rules] == [
        [FINDING.id],
        [LATENCY_FINDING.id],
    ]
    assert decoded.analysis.findings[1] == LATENCY_FINDING
    assert decoded.analysis.findings[1].evidence["max_ms"] == 1500.5


def test_finding_timestamps_in_evidence_stay_strings() -> None:
    decoded = loads(dumps(make_report()))
    assert decoded.analysis.findings[0].evidence["first_seen"] == "2026-10-16T14:00:01.000000Z"


@pytest.mark.parametrize("has_critical", [True, False])
def test_has_critical_comes_from_the_recurring_error_finding(has_critical: bool) -> None:  # D-15
    finding = dataclasses.replace(
        FINDING,
        evidence=MappingProxyType(
            {"service": "payments", "fingerprint": "timeout", "has_critical": has_critical}
        ),
    )
    other = ErrorGroup("api", "boom", 1, True, None, None, (), 0, ())
    report = make_report(findings=(finding,), top_errors=(TOP_ERROR, other))
    decoded = loads(dumps(report))
    # The second group has no finding (below min_occurrences): it cannot be known.
    assert [g.has_critical for g in decoded.analysis.top_errors] == [has_critical, False]


# --- Invalid documents ---------------------------------------------------------------------


ENCODED: dict[str, Any] = report_to_dict(make_report())
TOP_ERROR_JSON: dict[str, Any] = ENCODED["top_errors"][0]
SUMMARY_JSON: dict[str, Any] = ENCODED["summary"]
FINDING_JSON: dict[str, Any] = ENCODED["findings"][0]
T0_JSON = "2026-10-16T14:00:00.000000Z"


def saved(**changes: Any) -> dict[str, Any]:
    data: dict[str, Any] = report_to_dict(make_report())
    data.update(changes)
    return data


@pytest.mark.parametrize("version", ["1.0", "1.7"])
def test_any_minor_version_of_schema_1_is_accepted(version: str) -> None:  # STA-2
    assert report_from_dict(saved(schema_version=version)).tool_version == "0.1.0"


@pytest.mark.parametrize(
    ("version", "message"),
    [
        ("2.0", "unsupported schema version '2.0' (this version of log-tracer reads 1.x)"),
        ("0.9", "unsupported schema version '0.9' (this version of log-tracer reads 1.x)"),
        ("one", "unsupported schema version 'one' (this version of log-tracer reads 1.x)"),
        (1, "schema_version must be a string, got 1"),
    ],
)
def test_incompatible_schema_version_is_rejected(version: object, message: str) -> None:  # STA-2
    with pytest.raises(ReportDecodeError) as excinfo:
        report_from_dict(saved(schema_version=version))
    assert str(excinfo.value) == message


def test_unknown_keys_are_ignored() -> None:  # a later 1.x may add keys
    assert report_from_dict(saved(extra={"anything": 1})).tool_version == "0.1.0"


def without(data: dict[str, Any], *path: str) -> dict[str, Any]:
    target = data
    for key in path[:-1]:
        target = target[key]
    del target[path[-1]]
    return data


def broken_input(key: str, value: object) -> dict[str, Any]:
    data = saved()
    data["inputs"][0][key] = value
    return data


def missing_input_key(key: str) -> dict[str, Any]:
    data = saved()
    del data["inputs"][0][key]
    return data


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (without(saved(), "summary"), "missing key 'summary'"),
        (missing_input_key("sha256"), "missing key 'inputs[0].sha256'"),
        (saved(inputs="x"), 'inputs must be a list, got "x"'),
        (broken_input("lines", True), "inputs[0].lines must be an integer, got true"),
        (broken_input("format", "xml"), 'inputs[0].format has an unknown value "xml"'),
        (saved(generated_at="later"), 'generated_at is not an RFC 3339 timestamp: "later"'),
        (
            saved(generated_at="2026-10-16T14:00:00"),
            'generated_at has no time zone: "2026-10-16T14:00:00"',
        ),
        (
            saved(rules=[{"rule": "magic", "status": "ok", "reason": None}]),
            'rules[0].rule has an unknown value "magic"',
        ),
        (saved(inputs=[1]), "inputs[0] must be an object, got 1"),
        (saved(config=[]), "config must be an object, got []"),
        (
            saved(top_errors=[{**TOP_ERROR_JSON, "correlation_ids": [7]}]),
            "top_errors[0].correlation_ids[0] must be a string, got 7",
        ),
        (
            saved(summary={**SUMMARY_JSON, "levels": {"LOUD": 1}}),
            'summary.levels has an unknown key "LOUD"',
        ),
        (
            saved(summary={**SUMMARY_JSON, "time_range": {"start": None, "end": T0_JSON}}),
            "summary.time_range must have both start and end, or neither",
        ),
        (
            saved(findings=[{**FINDING_JSON, "evidence": {"items": [1]}}]),
            "findings[0].evidence.items must be a string, a number, a boolean or null, got [1]",
        ),
        (
            saved(config={"report": {"top_errors": 0}}),
            "config: report.top_errors must be a positive integer, got 0",
        ),
    ],
    ids=lambda value: value if isinstance(value, str) and len(value) < 40 else None,
)
def test_malformed_document_is_rejected(data: dict[str, Any], message: str) -> None:
    with pytest.raises(ReportDecodeError) as excinfo:
        report_from_dict(data)
    assert str(excinfo.value) == message


@pytest.mark.parametrize(
    ("text", "message"),
    [
        (
            "{",
            "not valid JSON: Expecting property name enclosed in double quotes (line 1, column 2)",
        ),
        ("[]", "the document must be a JSON object, got a list"),
        ("[" * 100_000, "not valid JSON: nested too deeply"),
        ('{"a": NaN}', "not valid JSON: NaN is not allowed"),
    ],
    # Short ids: pytest puts the id in PYTEST_CURRENT_TEST, and Windows limits
    # an environment variable to 32 767 characters.
    ids=["syntax", "not-an-object", "nested", "nan"],
)
def test_invalid_json_is_rejected(text: str, message: str) -> None:
    with pytest.raises(ReportDecodeError) as excinfo:
        loads(text)
    assert str(excinfo.value) == message


def test_round_trip_survives_json_reparsing() -> None:
    # The saved document is plain JSON: nothing depends on Python-only values.
    data = json.loads(dumps(make_report()))
    assert dumps(report_from_dict(data)) == dumps(make_report())
