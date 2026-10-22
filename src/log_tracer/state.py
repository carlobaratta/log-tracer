"""The saved state: the report of the last ``analyze`` (SPEC §8, D-7).

The state is a single file, ``<state-dir>/last-report.json``, in the same
format as ``--format json``. Only ``analyze`` writes it (STA-4); ``report``
and ``inspect`` without paths read it.
"""

import os
import tempfile
from pathlib import Path
from typing import Final

from log_tracer.errors import StateError
from log_tracer.models import Report
from log_tracer.reporting import json_report

DEFAULT_DIRECTORY: Final = Path(".log-tracer")
REPORT_FILE: Final = "last-report.json"

_SAVE_HINT: Final = "use --no-save, or choose another directory with --state-dir"
_REPLACE_HINT: Final = "run 'log-tracer analyze PATH' again to replace it"


def save(report: Report, directory: Path) -> None:
    """Write ``report`` atomically, creating the directory if needed (STA-1).

    A reader never sees a half-written file: the report is written to a
    temporary file in the same directory, then renamed over the old one.
    """
    try:
        directory.mkdir(parents=True, exist_ok=True)
        gitignore = directory / ".gitignore"
        if not gitignore.exists():
            # The state is local: keep it out of version control.
            gitignore.write_text("*\n", encoding="utf-8")
        _write_atomically(directory / REPORT_FILE, json_report.dumps(report))
    except OSError as error:
        reason = error.strerror or type(error).__name__
        raise StateError(
            f"cannot save the analysis in {_shown(directory)} ({reason})", hint=_SAVE_HINT
        ) from error


def load(directory: Path) -> Report:
    """Read the saved report (STA-2); a missing or unreadable one is a :class:`StateError`."""
    path = directory / REPORT_FILE
    try:
        content = path.read_bytes()
    except FileNotFoundError as error:
        raise StateError(
            f"no saved analysis in {_shown(directory)}",
            hint="run 'log-tracer analyze PATH' first",
        ) from error
    except OSError as error:
        reason = error.strerror or type(error).__name__
        raise StateError(
            f"cannot read the saved analysis {path.as_posix()} ({reason})", hint=_REPLACE_HINT
        ) from error
    try:
        return json_report.loads(content.decode("utf-8"))
    except UnicodeDecodeError as error:
        problem = "not valid UTF-8"
        raise StateError(_unreadable(path, problem), hint=_REPLACE_HINT) from error
    except json_report.ReportDecodeError as error:
        raise StateError(_unreadable(path, str(error)), hint=_REPLACE_HINT) from error


def _write_atomically(target: Path, text: str) -> None:
    descriptor, temporary = tempfile.mkstemp(
        dir=target.parent, prefix=f".{target.name}.", suffix=".tmp"
    )
    replaced = False
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(text.encode("utf-8"))
            stream.flush()
            os.fsync(stream.fileno())
        Path(temporary).replace(target)
        replaced = True
    finally:
        if not replaced:
            Path(temporary).unlink(missing_ok=True)


def _unreadable(path: Path, problem: str) -> str:
    return f"cannot read the saved analysis {path.as_posix()}: {problem}"


def _shown(directory: Path) -> str:
    return directory.as_posix().rstrip("/") + "/"
