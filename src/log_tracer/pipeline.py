"""Composition of the stages: ingestion, analysis and report assembly.

Keeping this outside the CLI lets integration tests (and any other caller)
run the whole analysis without going through command-line parsing.
"""

from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from log_tracer import __version__
from log_tracer.analysis import analyze
from log_tracer.analysis.correlation import correlate, find_groups
from log_tracer.config import AnalysisConfig
from log_tracer.errors import StateError
from log_tracer.ingestion import ingest, ingest_files
from log_tracer.models import IngestionResult, IngestionSummary, InputFormat, Inspection, Report


def run_analysis(
    paths: Sequence[Path],
    *,
    config: AnalysisConfig,
    now: datetime,
    input_format: InputFormat | None = None,
) -> Report:
    """Analyze ``paths`` and assemble the report.

    ``now`` becomes ``generated_at``. It is a parameter rather than a call to
    the clock, so that the result depends on the inputs alone (NFR-2).
    ``input_format`` forces the parser for every file (ING-2).
    """
    ingestion = ingest(paths, input_format=input_format, max_samples=config.report.max_examples)
    analysis = analyze(ingestion.events, config)
    return Report(
        tool_version=__version__,
        generated_at=now,
        config=config,
        ingestion=ingestion.summary,
        analysis=analysis,
    )


def run_inspect(
    paths: Sequence[Path],
    query: str,
    *,
    config: AnalysisConfig,
    now: datetime,
    input_format: InputFormat | None = None,
) -> Inspection:
    """Find the sequences that contain ``query`` as a request_id or trace_id.

    Only ingestion and correlation run: the diagnostic rules are not needed
    to show a timeline.
    """
    ingestion = ingest(paths, input_format=input_format, max_samples=config.report.max_examples)
    return _inspection(ingestion, query, now)


def run_inspect_saved(
    saved: Report,
    query: str,
    *,
    now: datetime,
    input_format: InputFormat | None = None,
) -> Inspection:
    """Like :func:`run_inspect`, on the inputs of a saved analysis (STA-3).

    Each source is read again with the format recorded in the report, unless
    ``input_format`` forces another one (D-16). Paths are resolved against the
    current directory, as they were given to ``analyze``.
    """
    files: list[tuple[Path, InputFormat]] = []
    for source in saved.ingestion.sources:
        path = Path(source.path)
        if not path.exists():
            raise StateError(
                f"{source.path}, listed in the saved analysis, no longer exists",
                hint="pass the log paths to inspect, or run 'log-tracer analyze PATH' again",
            )
        files.append((path, input_format or source.format))
    ingestion = ingest_files(files, max_samples=saved.config.report.max_examples)
    return _inspection(ingestion, query, now)


def changed_sources(saved: IngestionSummary, current: IngestionSummary) -> tuple[str, ...]:
    """Paths whose content differs from the saved analysis, by SHA-256 (STA-3).

    A source that could not be read, then or now, has no digest to compare:
    reading it now already produces its own warning.
    """
    before = {source.path: source.sha256 for source in saved.sources}
    return tuple(
        source.path
        for source in current.sources
        if None not in (source.sha256, before.get(source.path))
        and source.sha256 != before[source.path]
    )


def _inspection(ingestion: IngestionResult, query: str, now: datetime) -> Inspection:
    groups = find_groups(correlate(ingestion.events).groups, query)
    return Inspection(
        tool_version=__version__,
        generated_at=now,
        query=query,
        groups=groups,
        ingestion=ingestion.summary,
    )
