"""Resolution of input paths into sources with a known format (ING-1…3)."""

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from log_tracer.errors import InputError
from log_tracer.models import InputFormat

EXTENSION_FORMATS: Final = {
    ".log": InputFormat.TEXT,
    ".txt": InputFormat.TEXT,
}


@dataclass(frozen=True, slots=True)
class Source:
    path: Path
    """The path used to open the file."""
    display: str
    """The path as given by the user, in POSIX form: used in reports (REP-3)."""
    format: InputFormat


def discover(paths: Sequence[Path]) -> tuple[Source, ...]:
    """Validate the input paths and return one source per distinct file.

    Sources are sorted by display path. When the same file is reached through
    different spellings, the lexicographically smallest one is kept, so the
    result never depends on the order of the arguments (NFR-2).
    """
    candidates = sorted((_to_source(path) for path in paths), key=lambda s: s.display)
    seen: set[Path] = set()
    sources: list[Source] = []
    for source in candidates:
        identity = source.path.resolve()
        if identity not in seen:
            seen.add(identity)
            sources.append(source)
    return tuple(sources)


def _to_source(path: Path) -> Source:
    display = path.as_posix()
    if not path.exists():
        raise InputError(f"path not found: {display}")
    if path.is_dir():
        raise InputError(
            f"{display} is a directory: directory input is not supported yet",
            hint=f"pass the log files explicitly, for example {display}/*.log",
        )
    suffix = path.suffix.lower()
    input_format = EXTENSION_FORMATS.get(suffix)
    if input_format is None:
        problem = f"unsupported extension '{suffix}'" if suffix else "the file has no extension"
        raise InputError(
            f"cannot determine the format of {display}: {problem}",
            hint="supported extensions: " + ", ".join(EXTENSION_FORMATS),
        )
    return Source(path=path, display=display, format=input_format)
