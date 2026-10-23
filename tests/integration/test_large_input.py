"""100 000 generated lines through the whole pipeline (NFR-2, NFR-3, ARCHITECTURE §8.3).

Expected values come from the generator's own account of what it wrote
(D-27). Timings are not checked: thresholds on time make CI unstable, so
NFR-3 is measured by hand and documented in docs/demo.md.
"""

from collections import Counter
from pathlib import Path

import pytest

from log_tracer.config import AnalysisConfig
from log_tracer.models import Report, RuleName, Severity, WarningCode
from log_tracer.pipeline import run_analysis
from log_tracer.reporting.json_report import dumps
from tests.factories import T0
from tests.large_logs import BURST_START, HEALTH_ID, JSONL_FILE, TEXT_FILE, Expected, generate

pytestmark = pytest.mark.slow

LINES = 100_000


@pytest.fixture(scope="module")
def generated(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Expected]:
    directory = tmp_path_factory.mktemp("large")
    return directory, generate(directory, LINES)


@pytest.fixture(scope="module")
def report(generated: tuple[Path, Expected]) -> Report:
    directory, _expected = generated
    return run_analysis([directory], config=AnalysisConfig(), now=T0)


def test_generator_wrote_what_the_test_relies_on(generated: tuple[Path, Expected]) -> None:
    _directory, expected = generated
    assert sum(expected.lines.values()) == LINES
    assert expected.health_events > 10_000  # enough for COR-6
    assert expected.burst_errors > 1000


def test_every_line_is_accounted_for(generated: tuple[Path, Expected], report: Report) -> None:
    _directory, expected = generated
    by_name = {Path(source.path).name: source for source in report.ingestion.sources}
    assert sorted(by_name) == [TEXT_FILE, JSONL_FILE]
    for name, source in by_name.items():
        assert source.error is None
        assert source.lines == expected.lines[name]
        assert source.events == expected.events[name]
        assert source.blank_lines == 0
        assert source.events + source.unparsed == source.lines
    unparsed = {reason.value: n for reason, n in report.ingestion.unparsed_counts.items() if n}
    assert unparsed == dict(expected.unparsed)
    issues = {code.value: n for code, n in report.ingestion.issue_counts.items() if n}
    assert issues == dict(expected.issues)


def test_levels_and_correlation(generated: tuple[Path, Expected], report: Report) -> None:
    _directory, expected = generated
    levels = {level.name: n for level, n in report.analysis.level_counts.items() if n}
    assert levels == dict(expected.levels)
    assert report.analysis.correlation_groups == len(expected.request_ids)
    assert report.analysis.uncorrelated_events == expected.uncorrelated


def test_reused_identifier_is_reported(generated: tuple[Path, Expected], report: Report) -> None:
    _directory, expected = generated
    (warning,) = report.analysis.warnings  # COR-6
    assert warning.code is WarningCode.LARGE_CORRELATION_GROUP
    assert warning.message.startswith(
        f"sequence {HEALTH_ID} contains {expected.health_events} events"
    )


def test_recurring_errors(generated: tuple[Path, Expected], report: Report) -> None:  # RUL-1
    _directory, expected = generated
    found = {
        (f.evidence["service"], f.evidence["fingerprint"]): (f.event_count, f.severity)
        for f in report.analysis.findings
        if f.rule is RuleName.RECURRING_ERROR
    }
    # Every group is far above high_count (20), so every finding is high.
    assert found == {group: (n, Severity.HIGH) for group, n in expected.error_groups.items()}


def test_high_latency(generated: tuple[Path, Expected], report: Report) -> None:  # RUL-2
    _directory, expected = generated
    found = {
        f.evidence["service"]: (
            f.evidence["slow_events"],
            f.evidence["measured_events"],
            f.evidence["max_ms"],
            f.severity,
        )
        for f in report.analysis.findings
        if f.rule is RuleName.HIGH_LATENCY
    }
    assert found == {
        service: (latency.slow, latency.measured, latency.max_ms, Severity.MEDIUM)
        for service, latency in expected.latency.items()
    }


def test_the_burst_is_the_only_spike(generated: tuple[Path, Expected], report: Report) -> None:
    _directory, expected = generated  # RUL-4
    (spike,) = [f for f in report.analysis.findings if f.rule is RuleName.ERROR_SPIKE]
    assert spike.evidence["start"] == BURST_START
    assert spike.evidence["windows"] == 1
    # The burst, plus the JSON Lines errors written in the same minute.
    assert spike.event_count >= expected.burst_errors
    assert spike.severity is Severity.HIGH


def test_warning_before_error(generated: tuple[Path, Expected], report: Report) -> None:
    _directory, expected = generated  # RUL-3
    (finding,) = [f for f in report.analysis.findings if f.rule is RuleName.WARNING_BEFORE_ERROR]
    assert finding.evidence["warning_fingerprint"] == "Payment provider slow, retrying"
    assert finding.evidence["error_fingerprint"] == "Payment timeout after <num> ms"
    assert finding.evidence["groups"] == expected.precursors
    assert finding.evidence["median_gap_ms"] == 500  # 10 lines of 50 ms
    assert finding.severity is Severity.MEDIUM


def test_every_rule_ran(report: Report) -> None:
    assert [(o.rule, o.status) for o in report.analysis.rules] == [
        (RuleName.RECURRING_ERROR, "ok"),
        (RuleName.HIGH_LATENCY, "ok"),
        (RuleName.WARNING_BEFORE_ERROR, "ok"),
        (RuleName.ERROR_SPIKE, "ok"),
    ]
    assert Counter(f.rule for f in report.analysis.findings)[RuleName.ERROR_SPIKE] == 1


def test_large_report_is_deterministic(generated: tuple[Path, Expected], report: Report) -> None:
    directory, _expected = generated  # NFR-2
    files = sorted(directory.iterdir(), reverse=True)
    again = run_analysis(files, config=AnalysisConfig(), now=T0)
    # Explicit files instead of the directory: the same sources, given another way.
    assert [s.path for s in again.ingestion.sources] == [s.path for s in report.ingestion.sources]
    assert dumps(again) == dumps(report)
