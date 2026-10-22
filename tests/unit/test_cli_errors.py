"""Translation of exceptions into exit codes (SPEC §5.4)."""

import pytest
import typer

from log_tracer.cli import ExitCode, _exit_on_error
from log_tracer.errors import InputError


def test_typer_exit_passes_through_unchanged() -> None:
    # typer.Exit is a RuntimeError: the internal-error guard must not swallow it.
    with pytest.raises(typer.Exit) as excinfo, _exit_on_error():
        raise typer.Exit(5)
    assert excinfo.value.exit_code == 5


def test_input_error_becomes_exit_code_3(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(typer.Exit) as excinfo, _exit_on_error():
        raise InputError("path not found: x.log", hint="check the path")
    assert excinfo.value.exit_code == ExitCode.INPUT
    assert capsys.readouterr().err == "Error: path not found: x.log\nHint: check the path\n"
