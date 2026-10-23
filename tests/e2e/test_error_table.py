"""SPEC §9, one case per row of the table (A6, D-29).

Each case states the situation, the command, the exit code and where the
outcome is visible: stdout, stderr or the JSON report. Some rows are also
covered in more detail elsewhere; here they are listed together, so that the
table and the tests can be compared line by line.
"""

import json
import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner, Result

from log_tracer import cli
from log_tracer.cli import app

TEXT = "2026-10-16T14:00:00Z INFO [api] fine request_id=r1\n"
ERROR = "2026-10-16T14:00:00Z ERROR [api] boom\n"

can_lock_files = pytest.mark.skipif(
    sys.platform == "win32" or (hasattr(os, "geteuid") and os.geteuid() == 0),
    reason="file permissions cannot make a file unreadable here",
)


@dataclass(frozen=True)
class Row:
    situation: str
    """The row of SPEC §9, as written there."""
    args: tuple[str, ...]
    code: int
    files: Mapping[str, str] = field(default_factory=dict)
    locked: tuple[str, ...] = ()
    """Files made unreadable before the command runs."""
    stderr: str | None = None
    """Text that stderr must contain; None means that stderr must be empty."""
    report: Mapping[str, object] = field(default_factory=dict)
    """Values of the JSON report on stdout, by dotted path."""
    stdout_empty: bool = False


ROWS = [
    Row(
        "Percorso inesistente",
        ("analyze", "nope.log"),
        3,
        stderr="Error: path not found: nope.log",
        stdout_empty=True,
    ),
    Row(
        "Directory senza file supportati",
        ("analyze", "logs"),
        3,
        files={"logs/notes.md": "# notes\n"},
        stderr="Hint: skipped logs/notes.md (unsupported_extension)",
        stdout_empty=True,
    ),
    Row(
        "File esplicito con estensione non riconosciuta",
        ("analyze", "app.csv"),
        3,
        files={"app.csv": TEXT},
        stderr="(or set the input format explicitly)",
        stdout_empty=True,
    ),
    Row(
        "Un file illeggibile tra diversi file",
        ("analyze", "ok.log", "locked.log", "--format", "json"),
        0,
        files={"ok.log": TEXT, "locked.log": TEXT},
        locked=("locked.log",),
        stderr="Warning: locked.log could not be read (Permission denied) and was skipped",
        report={"inputs.0.path": "locked.log", "inputs.0.events": 0, "inputs.1.events": 1},
    ),
    Row(
        "Nessuna sorgente leggibile",
        ("analyze", "locked.log"),
        3,
        files={"locked.log": TEXT},
        locked=("locked.log",),
        stderr="Error: none of the input files could be read (locked.log: Permission denied)",
        stdout_empty=True,
    ),
    Row(
        "File vuoto",
        ("analyze", "empty.log", "--format", "json"),
        0,
        files={"empty.log": ""},
        report={"summary.events": 0, "findings": []},
    ),
    Row(
        "Riga che non rispetta il formato",
        ("analyze", "app.log", "--format", "json"),
        0,
        # Most lines are valid, so that ING-9 (mostly unparsed) does not apply.
        files={"app.log": TEXT * 2 + "not a log line\n"},
        report={"data_quality.unparsed_by_reason.no_match": 1, "summary.events": 2},
    ),
    Row(
        "JSON malformato o troppo annidato",
        ("analyze", "app.jsonl", "--format", "json"),
        0,
        files={"app.jsonl": '{"msg": "ok"}\n' * 3 + "{broken\n" + "[" * 100_000 + "\n"},
        report={"data_quality.unparsed_by_reason.invalid_json": 2, "summary.events": 3},
    ),
    Row(
        "Timestamp assente o non valido (JSONL)",
        ("analyze", "app.jsonl", "--format", "json"),
        0,
        files={"app.jsonl": '{"msg": "a"}\n{"msg": "b", "ts": "yesterday"}\n'},
        report={
            "summary.events": 2,
            "data_quality.field_issues.timestamp.missing": 1,
            "data_quality.field_issues.timestamp.invalid": 1,
        },
    ),
    Row(
        "Timestamp senza fuso",
        ("analyze", "app.log", "--format", "json"),
        0,
        files={"app.log": "2026-10-16 14:00:00 INFO [api] local time\n"},
        report={
            "data_quality.field_issues.timestamp.assumed_utc": 1,
            "summary.time_range.start": "2026-10-16T14:00:00.000000Z",
        },
    ),
    Row(
        "Livello sconosciuto",
        ("analyze", "app.log", "--format", "json"),
        0,
        files={"app.log": "2026-10-16T14:00:00Z NOTICE [db] vacuum done\n"},
        report={"summary.levels.UNKNOWN": 1, "data_quality.field_issues.level.unknown": 1},
    ),
    Row(
        "Identificativi mancanti",
        ("analyze", "app.log", "--format", "json"),
        0,
        files={"app.log": TEXT + ERROR},
        report={"summary.uncorrelated_events": 1, "summary.correlation_groups": 1},
    ),
    Row(
        "`duration_ms` non valido",
        ("analyze", "app.jsonl", "--format", "json"),
        0,
        files={"app.jsonl": '{"msg": "slow", "duration_ms": "fast"}\n'},
        report={"summary.events": 1, "data_quality.field_issues.duration_ms.invalid": 1},
    ),
    Row(
        "Opzione o configurazione non valida",
        ("analyze", "app.log", "--config", "cfg.toml"),
        2,
        files={"app.log": TEXT, "cfg.toml": "[high_latency]\nwarn = 10\n"},
        stderr="Error: cfg.toml: unknown key 'high_latency.warn' (did you mean 'warn_ms'?)",
        stdout_empty=True,
    ),
    Row(
        "Nessuna analisi salvata",
        ("report",),
        3,
        stderr="Hint: run 'log-tracer analyze PATH' first",
        stdout_empty=True,
    ),
    Row(
        "ID non trovato in `inspect`",
        ("inspect", "r9", "app.log"),
        4,
        files={"app.log": TEXT},
        stderr="Error: no event has request_id or trace_id 'r9'",
        stdout_empty=True,
    ),
    Row(
        'ID di `inspect` che significa "assente" (`-`, `null`, `0`)',
        ("inspect", "null", "app.log"),
        2,
        files={"app.log": TEXT},
        stderr="means 'no identifier'",
        stdout_empty=True,
    ),
    Row(
        "File di `--output` non scrivibile",
        ("analyze", "app.log", "--output", "missing/report.json"),
        3,
        files={"app.log": TEXT},
        stderr="Error: cannot write the report to missing/report.json",
        stdout_empty=True,
    ),
    Row(
        "Soglia di `--fail-on` raggiunta",
        ("analyze", "app.log", "--fail-on", "medium", "--format", "json"),
        1,
        files={"app.log": ERROR * 3},
        stderr="Found 1 finding with severity medium or higher (--fail-on medium)",
        report={"findings.0.rule": "recurring_error"},
    ),
]

ROW_IDS = [
    "missing-path",
    "no-supported-files",
    "unknown-extension",
    "one-unreadable-file",
    "no-readable-source",
    "empty-file",
    "no-match-line",
    "malformed-json",
    "missing-timestamp",
    "naive-timestamp",
    "unknown-level",
    "missing-ids",
    "invalid-duration",
    "invalid-config",
    "no-saved-analysis",
    "id-not-found",
    "absent-id",
    "unwritable-output",
    "fail-on",
]


def value_at(data: Any, path: str) -> Any:
    """``inputs.0.path`` → data["inputs"][0]["path"]; keys may contain dots (field issues)."""
    for prefix in ("data_quality.field_issues.",):
        if path.startswith(prefix):
            return data["data_quality"]["field_issues"][path.removeprefix(prefix)]
    for part in path.split("."):
        data = data[int(part)] if isinstance(data, list) else data[part]
    return data


def run(row: Row, directory: Path) -> Result:
    for name, content in row.files.items():
        path = directory / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    for name in row.locked:
        (directory / name).chmod(0)
    try:
        return CliRunner().invoke(app, list(row.args), env={"COLUMNS": "200"})
    finally:
        for name in row.locked:
            (directory / name).chmod(0o644)


@pytest.mark.parametrize(
    "row",
    [
        pytest.param(row, id=row_id, marks=[can_lock_files] if row.locked else [])
        for row, row_id in zip(ROWS, ROW_IDS, strict=True)
    ],
)
def test_error_table_row(row: Row, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = run(row, tmp_path)
    assert result.exit_code == row.code, result.stderr
    if row.stderr is None:
        assert result.stderr == ""
    else:
        assert row.stderr in result.stderr
    if row.stdout_empty:
        assert result.stdout == ""
    if row.report:
        report = json.loads(result.stdout)
        for path, expected in row.report.items():
            assert value_at(report, path) == expected, path


def test_unexpected_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # "Errore inatteso": the last row, a bug, simulated by breaking the pipeline.
    def explode(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("kaboom")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "run_analysis", explode)
    (tmp_path / "app.log").write_text(TEXT, encoding="utf-8")
    result = CliRunner().invoke(app, ["analyze", "app.log"])
    assert result.exit_code == 70
    assert result.stdout == ""
    assert "Internal error: this is a bug in log-tracer" in result.stderr
    assert "RuntimeError: kaboom" in result.stderr


def test_every_row_of_the_table_has_a_case() -> None:
    spec = (Path(__file__).resolve().parents[2] / "SPEC.md").read_text(encoding="utf-8")
    table = spec.split("## 9. Gestione degli errori", 1)[1].split("\n\n", 2)[1]
    situations = [line.split(" | ")[0].removeprefix("| ") for line in table.splitlines()[2:]]
    assert [row.situation for row in ROWS] + ["Errore inatteso"] == situations
