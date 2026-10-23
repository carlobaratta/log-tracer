"""Golden test: the report of the bundled examples (SPEC A5, ARCHITECTURE §8.2).

``examples/expected_report.json`` is the complete report of ``analyze
examples/logs/``. Any change to parsing, rules or encoding that alters it
fails here, and the diff shows exactly what changed. Regenerating the file is
a deliberate step, described in docs/demo.md, never done by a test.
"""

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from log_tracer.cli import app
from log_tracer.reporting.json_report import report_from_dict, report_to_dict

ROOT = Path(__file__).resolve().parents[2]
GOLDEN = ROOT / "examples" / "expected_report.json"
REGENERATE = (
    "if the change is intended, regenerate the file as described in docs/demo.md "
    "('Regenerating the golden report') and review the diff"
)


def without_generated_at(report: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in report.items() if key != "generated_at"}


@pytest.fixture
def golden() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(GOLDEN.read_text(encoding="utf-8"))
    return data


def test_report_of_the_examples_matches_the_golden_file(
    golden: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:  # A5, REP-4
    monkeypatch.chdir(ROOT)
    result = CliRunner().invoke(app, ["analyze", "examples/logs/", "--format", "json"])
    assert result.exit_code == 0, result.stderr
    actual = without_generated_at(json.loads(result.stdout))
    assert actual == without_generated_at(golden), REGENERATE


def test_golden_file_is_a_report_this_version_can_read(golden: dict[str, Any]) -> None:
    # Guards against a hand-edited golden file: it must be a canonical report.
    assert report_to_dict(report_from_dict(golden)) == golden


def test_examples_exercise_every_rule(golden: dict[str, Any]) -> None:  # D-23
    assert {finding["rule"] for finding in golden["findings"]} == {
        "recurring_error",
        "high_latency",
        "warning_before_error",
        "error_spike",
    }
    assert {finding["severity"] for finding in golden["findings"]} == {"high", "medium"}


def test_example_logs_have_unix_line_endings() -> None:
    # .gitattributes keeps their bytes as committed; CRLF would change size and SHA-256.
    for path in sorted((ROOT / "examples" / "logs").iterdir()):
        assert b"\r\n" not in path.read_bytes(), path.name
