"""Command-line interface (SPEC §5).

This module parses arguments, calls the pipeline and writes the output. It is
the only module that imports Typer, and the only place where exceptions are
translated into exit codes (SPEC §5.4).
"""

import enum
import sys
import traceback
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import typer

from log_tracer import __version__
from log_tracer.config import AnalysisConfig
from log_tracer.errors import InputError, LogTracerError
from log_tracer.models import IngestionWarning, InputFormat, WarningCode
from log_tracer.pipeline import run_analysis
from log_tracer.reporting import json_report


class ExitCode(enum.IntEnum):
    OK = 0
    USAGE = 2  # also used by Typer for invalid arguments and options
    INPUT = 3
    INTERNAL = 70


class OutputFormat(enum.StrEnum):
    JSON = "json"


class InputFormatChoice(enum.StrEnum):
    AUTO = "auto"
    TEXT = "text"
    JSONL = "jsonl"


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
    paths: Annotated[
        list[Path],
        typer.Argument(
            help="Log files or directories (.log, .txt, .jsonl, .ndjson).",
            show_default=False,
            # Click would reject an unreadable file as a usage error (exit 2).
            # Ingestion handles it as SPEC §9 requires: a warning when other
            # files remain, an input error (exit 3) when none can be read.
            readable=False,
        ),
    ],
    output_format: Annotated[
        OutputFormat, typer.Option("--format", help="Output format.")
    ] = OutputFormat.JSON,
    input_format: Annotated[
        InputFormatChoice,
        typer.Option(
            "--input-format",
            help="Parser to use for every file; 'auto' chooses it by file extension.",
        ),
    ] = InputFormatChoice.AUTO,
) -> None:
    """Analyze log files and print the report."""
    with _exit_on_error():
        report = run_analysis(
            paths,
            config=AnalysisConfig(),
            now=datetime.now(UTC),
            input_format=None
            if input_format is InputFormatChoice.AUTO
            else InputFormat(input_format),
        )
        output = json_report.dumps(report)
    _print_warnings(report.ingestion.warnings)
    typer.echo(output, nl=False)


@contextmanager
def _exit_on_error() -> Iterator[None]:
    """Translate exceptions raised inside the block into messages and exit codes."""
    try:
        yield
    except typer.Exit:
        raise
    except InputError as error:
        _print_error(error)
        raise typer.Exit(ExitCode.INPUT) from error
    except Exception as error:  # last-resort guard: anything else is a bug
        typer.echo("Internal error: this is a bug in log-tracer. Details follow.", err=True)
        traceback.print_exception(error, file=sys.stderr)
        raise typer.Exit(ExitCode.INTERNAL) from error


def _print_warnings(warnings: Iterable[IngestionWarning]) -> None:
    """Warnings go to stderr, so that stdout stays a single JSON document (CLI-1, CLI-2)."""
    for warning in warnings:
        typer.echo(f"Warning: {warning.message}", err=True)
        if warning.code is WarningCode.MOSTLY_UNPARSED:
            typer.echo(
                "Hint: if the file uses another format, choose it with --input-format", err=True
            )


def _print_error(error: LogTracerError) -> None:
    typer.echo(f"Error: {error}", err=True)
    if error.hint:
        typer.echo(f"Hint: {error.hint}", err=True)


def main() -> None:
    """Console-script entry point."""
    app(prog_name="log-tracer")
