"""Command-line interface (SPEC §5).

This module parses arguments, calls the pipeline and writes the output. It is
the only module that imports Typer, and the only place where exceptions are
translated into exit codes (SPEC §5.4).
"""

import dataclasses
import enum
import io
import sys
import traceback
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.text import Text

from log_tracer import __version__, state
from log_tracer.config import AnalysisConfig, load_config
from log_tracer.errors import (
    ConfigError,
    InputError,
    LogTracerError,
    NotFoundError,
    OutputError,
    StateError,
)
from log_tracer.ingestion.fields import normalize_identifier, normalize_service, parse_time_bound
from log_tracer.models import (
    DataWarning,
    Filters,
    InputFormat,
    Inspection,
    Report,
    Severity,
    WarningCode,
)
from log_tracer.pipeline import changed_sources, run_analysis, run_inspect, run_inspect_saved
from log_tracer.reporting import json_report
from log_tracer.reporting.terminal import (
    render_report,
    render_timeline,
    render_top_errors,
    safe_text,
)


class ExitCode(enum.IntEnum):
    OK = 0
    FINDINGS = 1  # --fail-on threshold reached
    USAGE = 2  # also used by Typer for invalid arguments and options
    INPUT = 3  # input, output or saved-state problems
    NOT_FOUND = 4  # inspect: identifier not found
    INTERNAL = 70


class OutputFormat(enum.StrEnum):
    TEXT = "text"
    JSON = "json"


class InputFormatChoice(enum.StrEnum):
    AUTO = "auto"
    TEXT = "text"
    JSONL = "jsonl"


class FailOn(enum.StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


Paths = Annotated[
    list[Path],
    typer.Argument(
        help="Log files or directories (.log, .txt, .jsonl, .ndjson).",
        show_default=False,
        # Click would reject an unreadable file as a usage error (exit 2).
        # Ingestion handles it as SPEC §9 requires: a warning when other
        # files remain, an input error (exit 3) when none can be read.
        readable=False,
    ),
]
OptionalPaths = Annotated[
    list[Path] | None,
    typer.Argument(
        help="Log files or directories; without them, the inputs of the saved analysis.",
        show_default=False,
        readable=False,
    ),
]
FormatOption = Annotated[
    OutputFormat,
    typer.Option("--format", help="'text' for a terminal summary, 'json' for the full report."),
]
ConfigOption = Annotated[
    Path | None,
    typer.Option(
        "--config",
        help="TOML file with analysis thresholds; it is checked before the logs are read.",
        readable=False,
    ),
]
InputFormatOption = Annotated[
    InputFormatChoice,
    typer.Option(
        "--input-format", help="Parser to use for every file; 'auto' chooses it by file extension."
    ),
]
OutputOption = Annotated[
    Path | None,
    typer.Option(
        "--output",
        help="Also write the JSON report to this file, whatever --format is.",
        readable=False,
    ),
]
ServiceOption = Annotated[
    list[str] | None,
    typer.Option(
        "--service",
        metavar="NAME",
        help="Analyze only the events of this service (exact name); repeat it for several.",
        show_default=False,
    ),
]
SinceOption = Annotated[
    str | None,
    typer.Option(
        "--since",
        metavar="TS",
        help="Analyze only events at or after this time (ISO 8601; UTC without an offset).",
        show_default=False,
    ),
]
UntilOption = Annotated[
    str | None,
    typer.Option(
        "--until",
        metavar="TS",
        help="Analyze only events before this time (ISO 8601; UTC without an offset).",
        show_default=False,
    ),
]
StateDirOption = Annotated[
    Path,
    typer.Option(
        "--state-dir", envvar="LOG_TRACER_STATE_DIR", help="Directory of the saved analysis."
    ),
]

app = typer.Typer(
    name="log-tracer",
    help="Analyze log files offline and report recurring problems.",
    no_args_is_help=True,
    add_completion=False,
    pretty_exceptions_enable=False,
)


def _print_version(value: bool) -> None:
    if value:
        typer.echo(f"log-tracer {__version__}")
        raise typer.Exit(ExitCode.OK)


@app.callback()
def _root(
    version: Annotated[
        bool,
        typer.Option(
            "--version", help="Show the version and exit.", callback=_print_version, is_eager=True
        ),
    ] = False,
) -> None:
    """Analyze log files offline and report recurring problems."""


@app.command()
def analyze(
    paths: Paths,
    output_format: FormatOption = OutputFormat.TEXT,
    input_format: InputFormatOption = InputFormatChoice.AUTO,
    output: OutputOption = None,
    fail_on: Annotated[
        FailOn | None,
        typer.Option(
            "--fail-on", help="Exit with code 1 if a finding has this severity or higher."
        ),
    ] = None,
    config_path: ConfigOption = None,
    services: ServiceOption = None,
    since: SinceOption = None,
    until: UntilOption = None,
    no_save: Annotated[
        bool, typer.Option("--no-save", help="Do not save the analysis for 'report' and 'inspect'.")
    ] = False,
    state_dir: StateDirOption = state.DEFAULT_DIRECTORY,
) -> None:
    """Analyze log files and print a summary (or the JSON report)."""
    filters = _filters(services, since, until)
    report = _run(paths, _load_config(config_path), input_format, filters)
    if not no_save:
        # Saved before stdout, even if --fail-on is reached later (D-17).
        with _exit_on_error():
            state.save(report, state_dir)
    _print_report(report, output_format, output)
    if fail_on is not None:
        _exit_if_findings_reach(report, fail_on)


@app.command("report")
def report_command(
    output_format: FormatOption = OutputFormat.TEXT,
    output: OutputOption = None,
    state_dir: StateDirOption = state.DEFAULT_DIRECTORY,
) -> None:
    """Show the saved analysis again, without reading the logs."""
    with _exit_on_error():
        report = state.load(state_dir)
    _print_report(report, output_format, output)


@app.command("top-errors")
def top_errors(
    paths: Paths,
    limit: Annotated[
        int | None,
        typer.Option(
            "--limit",
            min=1,
            help="Number of error groups to show [default: report.top_errors, 10].",
            show_default=False,
        ),
    ] = None,
    output_format: FormatOption = OutputFormat.TEXT,
    input_format: InputFormatOption = InputFormatChoice.AUTO,
    config_path: ConfigOption = None,
    services: ServiceOption = None,
    since: SinceOption = None,
    until: UntilOption = None,
) -> None:
    """Show the most frequent error groups (ERROR and CRITICAL events)."""
    filters = _filters(services, since, until)
    config = _load_config(config_path)
    if limit is not None:
        # Precedence: command-line flag, then configuration file, then default.
        config = dataclasses.replace(
            config, report=dataclasses.replace(config.report, top_errors=limit)
        )
    report = _run(paths, config, input_format, filters)
    if output_format is OutputFormat.JSON:
        typer.echo(json_report.dumps_top_errors(report), nl=False)
    else:
        _stdout().print(render_top_errors(report))


@app.command("inspect")
def inspect_command(
    identifier: Annotated[
        str,
        typer.Argument(
            metavar="ID",
            help="A request_id or trace_id, matched exactly (upper and lower case differ).",
            show_default=False,
        ),
    ],
    paths: OptionalPaths = None,
    output_format: FormatOption = OutputFormat.TEXT,
    input_format: InputFormatOption = InputFormatChoice.AUTO,
    state_dir: StateDirOption = state.DEFAULT_DIRECTORY,
) -> None:
    """Show the timeline of the sequence that contains a request_id or trace_id."""
    query = normalize_identifier(identifier)
    if query is None:
        # Raised outside _exit_on_error: it is a usage error (exit 2), not a bug.
        raise typer.BadParameter(
            f"{identifier.strip()!r} means 'no identifier' in logs and cannot be searched for",
            param_hint="ID",
        )
    inspection = _inspect(query, paths, _input_format(input_format), state_dir)
    with _exit_on_error():
        if not inspection.groups:
            raise NotFoundError(
                f"no event has request_id or trace_id '{query}'",
                hint="identifiers are matched exactly, including upper and lower case",
            )
    if output_format is OutputFormat.JSON:
        typer.echo(json_report.dumps_inspection(inspection), nl=False)
    else:
        _stdout().print(render_timeline(inspection))


# --- Helpers ---------------------------------------------------------------------------


def _inspect(
    query: str, paths: list[Path] | None, input_format: InputFormat | None, state_dir: Path
) -> Inspection:
    now = datetime.now(UTC)
    if paths:
        with _exit_on_error():
            inspection = run_inspect(
                paths, query, config=AnalysisConfig(), now=now, input_format=input_format
            )
        _print_warnings(inspection.ingestion.warnings)
        return inspection
    # Without paths: the inputs of the saved analysis, read again (STA-3).
    with _exit_on_error():
        saved = state.load(state_dir)
        inspection = run_inspect_saved(saved, query, now=now, input_format=input_format)
    _print_warnings(inspection.ingestion.warnings)
    for path in changed_sources(saved.ingestion, inspection.ingestion):
        _say(
            "Warning",
            "yellow",
            f"{path} has changed since the saved analysis; the timeline shows its current content",
        )
    return inspection


def _print_report(report: Report, output_format: OutputFormat, output: Path | None) -> None:
    report_json = json_report.dumps(report)
    if output is not None:
        # Written before stdout: if this fails, nothing partial is printed.
        with _exit_on_error():
            _write(output, report_json)
    if output_format is OutputFormat.JSON:
        typer.echo(report_json, nl=False)
    else:
        _stdout().print(render_report(report))


def _load_config(path: Path | None) -> AnalysisConfig:
    with _exit_on_error():
        return AnalysisConfig() if path is None else load_config(path)


def _filters(services: list[str] | None, since: str | None, until: str | None) -> Filters:
    """Check the filter options before any log is read (D-20); errors exit with code 2."""
    names: list[str] = []
    for raw in services or ():
        name = normalize_service(raw)
        if name is None:
            raise typer.BadParameter("a service name cannot be empty", param_hint="'--service'")
        names.append(name)
    lower, upper = _time_bound(since, "--since"), _time_bound(until, "--until")
    if lower is not None and upper is not None and lower >= upper:
        raise typer.BadParameter("--since must be earlier than --until", param_hint="'--until'")
    return Filters(services=tuple(names), since=lower, until=upper)


def _time_bound(raw: str | None, option: str) -> datetime | None:
    if raw is None:
        return None
    value = parse_time_bound(raw)
    if value is None:
        raise typer.BadParameter(
            f"{raw!r} is not an ISO 8601 date or date-time", param_hint=f"'{option}'"
        )
    return value


def _run(
    paths: list[Path], config: AnalysisConfig, input_format: InputFormatChoice, filters: Filters
) -> Report:
    with _exit_on_error():
        report = run_analysis(
            paths,
            config=config,
            now=datetime.now(UTC),
            input_format=_input_format(input_format),
            filters=filters,
        )
    _print_warnings((*report.ingestion.warnings, *report.filters.warnings))
    return report


def _input_format(choice: InputFormatChoice) -> InputFormat | None:
    return None if choice is InputFormatChoice.AUTO else InputFormat(choice)


def _write(path: Path, text: str) -> None:
    try:
        path.write_text(text, encoding="utf-8")
    except OSError as error:
        reason = error.strerror or type(error).__name__
        raise OutputError(f"cannot write the report to {path.as_posix()} ({reason})") from error


def _exit_if_findings_reach(report: Report, fail_on: FailOn) -> None:
    threshold = Severity[fail_on.name]
    count = sum(1 for finding in report.analysis.findings if finding.severity >= threshold)
    if count:
        noun = "finding" if count == 1 else "findings"
        _stderr().print(
            Text(f"Found {count} {noun} with severity {fail_on} or higher (--fail-on {fail_on})")
        )
        raise typer.Exit(ExitCode.FINDINGS)


@contextmanager
def _exit_on_error() -> Iterator[None]:
    """Translate exceptions raised inside the block into messages and exit codes."""
    try:
        yield
    except typer.Exit:
        raise
    except ConfigError as error:
        _print_error(error)
        raise typer.Exit(ExitCode.USAGE) from error
    except (InputError, OutputError, StateError) as error:
        _print_error(error)
        raise typer.Exit(ExitCode.INPUT) from error
    except NotFoundError as error:
        _print_error(error)
        raise typer.Exit(ExitCode.NOT_FOUND) from error
    except Exception as error:  # last-resort guard: anything else is a bug
        _stderr().print(Text("Internal error: this is a bug in log-tracer. Details follow."))
        traceback.print_exception(error, file=sys.stderr)
        raise typer.Exit(ExitCode.INTERNAL) from error


def _print_warnings(warnings: Iterable[DataWarning]) -> None:
    """Warnings go to stderr, so that stdout stays a single document (CLI-1, CLI-2)."""
    for warning in warnings:
        _say("Warning", "yellow", warning.message)
        if warning.code is WarningCode.MOSTLY_UNPARSED:
            _say("Hint", "dim", "if the file uses another format, choose it with --input-format")


def _print_error(error: LogTracerError) -> None:
    _say("Error", "bold red", str(error))
    if error.hint:
        _say("Hint", "dim", error.hint)


def _say(label: str, style: str, message: str) -> None:
    # The message may contain paths or log content: it is escaped (CLI-4).
    _stderr().print(Text.assemble((f"{label}: ", style), safe_text(message)))


def _stdout() -> Console:
    # Created per call, so that NO_COLOR, COLUMNS and redirections are read
    # at that moment. No markup, emoji or automatic highlighting: the output
    # must not depend on the content of the logs (CLI-3, CLI-4).
    return Console(markup=False, emoji=False, highlight=False)


def _stderr() -> Console:
    # soft_wrap: messages are never broken into several lines, so that they
    # can be searched for and compared as they are.
    return Console(stderr=True, markup=False, emoji=False, highlight=False, soft_wrap=True)


def _use_utf8_streams() -> None:
    """Write UTF-8 even where the locale says otherwise (CLI-1).

    On Windows, redirected output uses the ANSI code page (e.g. cp1252), which
    cannot encode many characters found in logs or used in the summary.
    """
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8")


def main() -> None:
    """Console-script entry point."""
    _use_utf8_streams()
    app(prog_name="log-tracer")
