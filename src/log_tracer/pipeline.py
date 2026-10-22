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
from log_tracer.ingestion import ingest
from log_tracer.models import InputFormat, Inspection, Report


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
    groups = find_groups(correlate(ingestion.events).groups, query)
    return Inspection(
        tool_version=__version__,
        generated_at=now,
        query=query,
        groups=groups,
        ingestion=ingestion.summary,
    )
