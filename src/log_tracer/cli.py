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

from log_tracer import __version__
from log_tracer.config import AnalysisConfig, load_config
from log_tracer.errors import (
    ConfigError,
    InputError,
    LogTracerError,
    NotFoundError,
    OutputError,
)
from log_tracer.ingestion.fields import normalize_identifier
from log_tracer.models import DataWarning, InputFormat, Report, Severity, WarningCode
from log_tracer.pipeline import run_analysis, run_inspect
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
    output: Annotated[
        Path | None,
        typer.Option(
            "--output",
            help="Also write the JSON report to this file, whatever --format is.",
            readable=False,
        ),
    ] = None,
    fail_on: Annotated[
        FailOn | None,
        typer.Option(
            "--fail-on", help="Exit with code 1 if a finding has this severity or higher."
        ),
    ] = None,
    config_path: ConfigOption = None,
) -> None:
    """Analyze log files and print a summary (or the JSON report)."""
    report = _run(paths, _load_config(config_path), input_format)
    report_json = json_report.dumps(report)
    if output is not None:
        # Written before stdout: if this fails, nothing partial is printed.
        with _exit_on_error():
            _write(output, report_json)
    if output_format is OutputFormat.JSON:
        typer.echo(report_json, nl=False)
    else:
        _stdout().print(render_report(report))
    if fail_on is not None:
        _exit_if_findings_reach(report, fail_on)


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
) -> None:
    """Show the most frequent error groups (ERROR and CRITICAL events)."""
    config = _load_config(config_path)
    if limit is not None:
        # Precedence: command-line flag, then configuration file, then default.
        config = dataclasses.replace(
            config, report=dataclasses.replace(config.report, top_errors=limit)
        )
    report = _run(paths, config, input_format)
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
    paths: Paths,
    output_format: FormatOption = OutputFormat.TEXT,
    input_format: InputFormatOption = InputFormatChoice.AUTO,
) -> None:
    """Show the timeline of the sequence that contains a request_id or trace_id."""
    query = normalize_identifier(identifier)
    if query is None:
        # Raised outside _exit_on_error: it is a usage error (exit 2), not a bug.
        raise typer.BadParameter(
            f"{identifier.strip()!r} means 'no identifier' in logs and cannot be searched for",
            param_hint="ID",
        )
    with _exit_on_error():
        inspection = run_inspect(
            paths,
            query,
            config=AnalysisConfig(),
            now=datetime.now(UTC),
            input_format=_input_format(input_format),
        )
    _print_warnings(inspection.ingestion.warnings)
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


def _load_config(path: Path | None) -> AnalysisConfig:
    with _exit_on_error():
        return AnalysisConfig() if path is None else load_config(path)


def _run(paths: list[Path], config: AnalysisConfig, input_format: InputFormatChoice) -> Report:
    with _exit_on_error():
        report = run_analysis(
            paths,
            config=config,
            now=datetime.now(UTC),
            input_format=_input_format(input_format),
        )
    _print_warnings(report.ingestion.warnings)
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
    except (InputError, OutputError) as error:
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
