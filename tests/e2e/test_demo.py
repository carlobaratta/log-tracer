"""docs/demo.md runs as documented (SPEC A10, D-24).

Every line of a ```bash block in the demo is a ``log-tracer`` command. They run
in document order, from a copy of ``examples/``, so that ``--output`` and the
saved state stay out of the repository; each must exit with the code written
after it (``# exit 1``), or 0. The outputs in the document are excerpts kept
by hand: comparing them would tie the test to the exact rendering of Rich.
"""

import re
import shlex
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from log_tracer.cli import app

ROOT = Path(__file__).resolve().parents[2]
DEMO = ROOT / "docs" / "demo.md"
BASH_BLOCK = re.compile(r"^```bash\n(.*?)^```", re.MULTILINE | re.DOTALL)
EXPECTED_EXIT = re.compile(r"\s+#\s*exit\s+(\d+)\s*$")
SHELL_SYNTAX = re.compile(r"[|<>;&$`]")


def demo_commands() -> list[tuple[str, int]]:
    commands: list[tuple[str, int]] = []
    for block in BASH_BLOCK.findall(DEMO.read_text(encoding="utf-8")):
        for line in block.splitlines():
            if line.strip():
                match = EXPECTED_EXIT.search(line)
                command = line[: match.start()] if match else line
                commands.append((command.strip(), int(match.group(1)) if match else 0))
    return commands


def test_demo_has_commands() -> None:
    assert len(demo_commands()) >= 10


def test_demo_commands_are_plain_invocations() -> None:
    # Pipes and redirections would not run through CliRunner; a command that
    # needs them (like regenerating the golden file) belongs in a ```sh block.
    for command, _code in demo_commands():
        assert command.startswith("log-tracer "), command
        assert not SHELL_SYNTAX.search(command), command


def test_demo_commands_exit_as_documented(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:  # A10
    shutil.copytree(ROOT / "examples", tmp_path / "examples")
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    for command, expected in demo_commands():
        result = runner.invoke(app, shlex.split(command)[1:], env={"COLUMNS": "100"})
        assert result.exit_code == expected, f"{command}\n{result.stderr}"
