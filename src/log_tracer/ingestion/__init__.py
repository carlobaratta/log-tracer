"""Ingestion: turn log files into normalized events (SPEC §2).

Content problems never stop ingestion: unparsable lines and field problems are
counted and sampled in the summary. An :class:`InputError` is raised only when
the inputs themselves are invalid, or when not a single file could be read.
"""

from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Final

from log_tracer.errors import InputError
from log_tracer.ingestion.discovery import Source, discover
from log_tracer.ingestion.jsonl_format import parse_jsonl_line
from log_tracer.ingestion.reader import DigestingReader
from log_tracer.ingestion.text_format import parse_text_line
from log_tracer.models import (
    Event,
    IngestionResult,
    IngestionSummary,
    IngestionWarning,
    InputFormat,
    IssueCode,
    LineOutcome,
    SourceRef,
    SourceSummary,
    UnparsedLine,
    UnparsedReason,
    WarningCode,
)

__all__ = ["ingest"]

_PARSERS: Final[Mapping[InputFormat, Callable[[str, SourceRef], LineOutcome]]] = {
    InputFormat.TEXT: parse_text_line,
    InputFormat.JSONL: parse_jsonl_line,
}


@dataclass(frozen=True, slots=True)
class _SourceResult:
    summary: SourceSummary
    events: tuple[Event, ...]
    unparsed_counts: Counter[UnparsedReason]
    unparsed_samples: tuple[UnparsedLine, ...]
    issue_counts: Counter[IssueCode]


def ingest(
    paths: Sequence[Path], *, input_format: InputFormat | None = None, max_samples: int
) -> IngestionResult:
    """Read, parse and normalize every input file.

    ``input_format`` forces the parser instead of choosing it by extension
    (ING-2). ``max_samples`` caps the unparsed lines kept as examples; all of
    them are counted regardless.
    """
    discovery = discover(paths, input_format=input_format)
    results = [_ingest_source(source, max_samples) for source in discovery.sources]
    if all(result.summary.error is not None for result in results):
        details = "; ".join(f"{r.summary.path}: {r.summary.error}" for r in results)
        raise InputError(f"none of the input files could be read ({details})")

    unparsed_counts: Counter[UnparsedReason] = Counter()
    issue_counts: Counter[IssueCode] = Counter()
    for result in results:
        unparsed_counts.update(result.unparsed_counts)
        issue_counts.update(result.issue_counts)
    samples = [sample for result in results for sample in result.unparsed_samples]

    return IngestionResult(
        events=tuple(event for result in results for event in result.events),
        summary=IngestionSummary(
            sources=tuple(result.summary for result in results),
            skipped=discovery.skipped,
            unparsed_counts=MappingProxyType(dict(unparsed_counts)),
            unparsed_samples=tuple(samples[:max_samples]),
            issue_counts=MappingProxyType(dict(issue_counts)),
            warnings=tuple(
                warning
                for result in results
                if (warning := _warning_for(result.summary)) is not None
            ),
        ),
    )


def _ingest_source(source: Source, max_samples: int) -> _SourceResult:
    parse = _PARSERS[source.format]
    events: list[Event] = []
    samples: list[UnparsedLine] = []
    unparsed_counts: Counter[UnparsedReason] = Counter()
    issue_counts: Counter[IssueCode] = Counter()
    lines = blank_lines = 0
    try:
        with source.path.open("rb") as stream:
            reader = DigestingReader(stream)
            for line in reader:
                lines += 1
                if not line.text.strip():
                    blank_lines += 1
                    continue
                outcome = parse(line.text, SourceRef(source.display, line.number))
                if isinstance(outcome, UnparsedLine):
                    unparsed_counts[outcome.reason] += 1
                    if len(samples) < max_samples:
                        samples.append(outcome)
                    continue
                events.append(outcome.event)
                # A set: each problem is counted at most once per event, even
                # when the reader and the parser both detect an encoding issue.
                issues = set(outcome.issues)
                if line.invalid_utf8:
                    issues.add(IssueCode.ENCODING_INVALID)
                issue_counts.update(issues)
    except OSError as exc:
        # Only this file is lost: the analysis continues on the others (ING-7).
        return _failed_source(source, exc.strerror or type(exc).__name__)

    summary = SourceSummary(
        path=source.display,
        format=source.format,
        size_bytes=reader.size_bytes,
        sha256=reader.sha256,
        lines=lines,
        blank_lines=blank_lines,
        events=len(events),
        unparsed=unparsed_counts.total(),
        error=None,
    )
    return _SourceResult(summary, tuple(events), unparsed_counts, tuple(samples), issue_counts)


def _failed_source(source: Source, error: str) -> _SourceResult:
    summary = SourceSummary(
        path=source.display,
        format=source.format,
        size_bytes=None,
        sha256=None,
        lines=0,
        blank_lines=0,
        events=0,
        unparsed=0,
        error=error,
    )
    return _SourceResult(summary, (), Counter(), (), Counter())


def _warning_for(source: SourceSummary) -> IngestionWarning | None:
    if source.error is not None:
        return IngestionWarning(
            WarningCode.UNREADABLE_FILE,
            source.path,
            f"{source.path} could not be read ({source.error}) and was skipped",
        )
    non_blank = source.events + source.unparsed
    if non_blank and 2 * source.unparsed >= non_blank:
        # ING-9: at least half of the lines failed, so the format is probably wrong.
        share = 100 * source.unparsed // non_blank
        return IngestionWarning(
            WarningCode.MOSTLY_UNPARSED,
            source.path,
            f"{share}% of the non-blank lines in {source.path} "
            f"could not be parsed as {source.format}",
        )
    return None
