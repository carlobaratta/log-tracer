"""The whole pipeline on real files, without the CLI."""

from pathlib import Path

import pytest

from log_tracer import __version__
from log_tracer.config import AnalysisConfig, RecurringErrorConfig
from log_tracer.errors import StateError
from log_tracer.models import Filters, InputFormat, RuleName, Severity
from log_tracer.pipeline import changed_sources, run_analysis, run_inspect_saved
from log_tracer.reporting.json_report import dumps
from tests.factories import T0

API_LOG = (
    "2026-10-16T14:00:00Z INFO [api] request accepted request_id=r1\n"
    "2026-10-16T14:00:01Z ERROR [payments] Payment timeout after 2500 ms request_id=r1\n"
    "2026-10-16T14:00:05Z ERROR [payments] Payment timeout after 2610 ms request_id=r2\n"
)
WORKER_LOG = (
    "2026-10-16T13:59:58Z ERROR [payments] Payment timeout after 2400 ms\n"
    "2026-10-16T14:00:09Z CRITICAL [db] Connection pool exhausted\n"
    "garbage line\n"
)


@pytest.fixture
def logs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list[Path]:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "api.log").write_text(API_LOG, encoding="utf-8")
    (tmp_path / "worker.log").write_text(WORKER_LOG, encoding="utf-8")
    return [Path("api.log"), Path("worker.log")]


def test_report_combines_ingestion_and_analysis(logs: list[Path]) -> None:
    report = run_analysis(logs, config=AnalysisConfig(), now=T0)

    assert report.tool_version == __version__
    assert report.generated_at == T0
    assert report.config == AnalysisConfig()
    assert [s.path for s in report.ingestion.sources] == ["api.log", "worker.log"]
    assert report.ingestion.unparsed_samples[0].ref.path == "worker.log"

    (finding,) = report.analysis.findings
    assert finding.rule is RuleName.RECURRING_ERROR
    assert finding.event_count == 3  # across both files
    assert finding.severity is Severity.MEDIUM
    # The sample is in timeline order, across files.
    assert [(ref.path, ref.line) for ref in finding.events] == [
        ("worker.log", 1),
        ("api.log", 2),
        ("api.log", 3),
    ]
    assert [(g.service, g.count) for g in report.analysis.top_errors] == [
        ("payments", 3),
        ("db", 1),
    ]


def test_configuration_changes_the_outcome(logs: list[Path]) -> None:
    strict = AnalysisConfig(recurring_error=RecurringErrorConfig(min_occurrences=4))
    assert run_analysis(logs, config=strict, now=T0).analysis.findings == ()


def test_report_is_identical_across_runs_and_argument_orders(logs: list[Path]) -> None:  # NFR-2
    first = dumps(run_analysis(logs, config=AnalysisConfig(), now=T0))
    again = dumps(run_analysis(logs, config=AnalysisConfig(), now=T0))
    reversed_order = dumps(run_analysis(logs[::-1], config=AnalysisConfig(), now=T0))
    assert first == again == reversed_order


def test_filters_restrict_what_the_rules_see(logs: list[Path]) -> None:  # D-9
    everything = run_analysis(logs, config=AnalysisConfig(), now=T0)
    only_db = run_analysis(logs, config=AnalysisConfig(), now=T0, filters=Filters(services=("db",)))
    assert [f.rule for f in everything.analysis.findings] == [RuleName.RECURRING_ERROR]
    assert only_db.analysis.findings == ()
    assert [g.service for g in only_db.analysis.top_errors] == ["db"]
    assert only_db.filters.excluded_events == 4  # 5 events, 1 from db
    assert only_db.ingestion == everything.ingestion  # the inputs are the same


# --- inspect without paths (STA-3) ------------------------------------------------------


def test_saved_inputs_are_read_again(logs: list[Path]) -> None:  # STA-3
    saved = run_analysis(logs, config=AnalysisConfig(), now=T0)
    inspection = run_inspect_saved(saved, "r1", now=T0)
    (group,) = inspection.groups
    assert [(e.ref.path, e.ref.line) for e in group.events] == [("api.log", 1), ("api.log", 2)]
    assert changed_sources(saved.ingestion, inspection.ingestion) == ()


def test_saved_format_is_used_unless_overridden(tmp_path: Path, logs: list[Path]) -> None:
    # D-16: a .log file analyzed as JSON Lines is read again as JSON Lines.
    (tmp_path / "events.log").write_text('{"msg": "hello", "request_id": "j1"}\n', "utf-8")
    saved = run_analysis(
        [Path("events.log")], config=AnalysisConfig(), now=T0, input_format=InputFormat.JSONL
    )
    assert len(run_inspect_saved(saved, "j1", now=T0).groups) == 1
    forced = run_inspect_saved(saved, "j1", now=T0, input_format=InputFormat.TEXT)
    assert forced.groups == ()


def test_changed_sources_are_listed(logs: list[Path]) -> None:  # STA-3
    saved = run_analysis(logs, config=AnalysisConfig(), now=T0)
    with Path("worker.log").open("a", encoding="utf-8") as stream:
        stream.write("2026-10-16T14:00:10Z INFO [db] recovered\n")
    inspection = run_inspect_saved(saved, "r1", now=T0)
    assert changed_sources(saved.ingestion, inspection.ingestion) == ("worker.log",)


def test_missing_saved_source_is_a_state_error(logs: list[Path]) -> None:  # STA-3
    saved = run_analysis(logs, config=AnalysisConfig(), now=T0)
    Path("worker.log").unlink()
    with pytest.raises(StateError) as excinfo:
        run_inspect_saved(saved, "r1", now=T0)
    assert str(excinfo.value) == "worker.log, listed in the saved analysis, no longer exists"
    assert excinfo.value.hint == (
        "pass the log paths to inspect, or run 'log-tracer analyze PATH' again"
    )
