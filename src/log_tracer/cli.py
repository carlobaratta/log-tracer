"""Command-line interface (SPEC §5).

This module parses arguments, calls the pipeline and writes the output. It is
the only module that imports Typer, and the only place where exceptions are
translated into exit codes (SPEC §5.4).
"""

import enum
import sys
import traceback
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import typer

from log_tracer import __version__
from log_tracer.config import AnalysisConfig
from log_tracer.errors import InputError, LogTracerError
from log_tracer.pipeline import run_analysis
from log_tracer.reporting import json_report


class ExitCode(enum.IntEnum):
    OK = 0
    USAGE = 2  # also used by Typer for invalid arguments and options
    INPUT = 3
    INTERNAL = 70


class OutputFormat(enum.StrEnum):
    JSON = "json"


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
        typer.Argument(help="Log files to analyze (.log, .txt).", show_default=False),
    ],
    output_format: Annotated[
        OutputFormat, typer.Option("--format", help="Output format.")
    ] = OutputFormat.JSON,
) -> None:
    """Analyze log files and print the report."""
    with _exit_on_error():
        report = run_analysis(paths, config=AnalysisConfig(), now=datetime.now(UTC))
        output = json_report.dumps(report)
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


def _print_error(error: LogTracerError) -> None:
    typer.echo(f"Error: {error}", err=True)
    if error.hint:
        typer.echo(f"Hint: {error.hint}", err=True)


def main() -> None:
    """Console-script entry point."""
    app(prog_name="log-tracer")
