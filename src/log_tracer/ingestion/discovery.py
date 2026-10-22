"""Resolution of input paths into sources with a known format (ING-1…3).

Explicit files are always analyzed (with ``input_format`` when their extension
is not recognized). Directories are walked recursively: files are *selected*
by extension, and ``input_format`` only changes how the selected files are
parsed. Every file that is found but not analyzed is listed with a reason,
except hidden files, which are ignored by design.
"""

import os
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from log_tracer.errors import InputError
from log_tracer.models import InputFormat, SkippedFile, SkipReason

EXTENSION_FORMATS: Final = {
    ".log": InputFormat.TEXT,
    ".txt": InputFormat.TEXT,
    ".jsonl": InputFormat.JSONL,
    ".ndjson": InputFormat.JSONL,
}
_SUPPORTED: Final = ", ".join(EXTENSION_FORMATS)
_HINT_LIMIT: Final = 5


@dataclass(frozen=True, slots=True)
class Source:
    path: Path
    """The path used to open the file."""
    display: str
    """The path as given by the user, in POSIX form: used in reports (REP-3)."""
    format: InputFormat


@dataclass(frozen=True, slots=True)
class Discovery:
    sources: tuple[Source, ...]
    """Distinct files to analyze, sorted by display path."""
    skipped: tuple[SkippedFile, ...]
    """Files found in directories but not analyzed, sorted by path."""


def discover(paths: Sequence[Path], *, input_format: InputFormat | None = None) -> Discovery:
    """Validate the input paths and return one source per distinct file.

    When the same file is reached through different spellings, the
    lexicographically smallest one is kept, so the result never depends on
    the order of the arguments or of the directory listing (NFR-2).
    """
    if not paths:
        raise InputError("no input files given")
    candidates: list[Source] = []
    skipped: list[SkippedFile] = []
    directories: list[str] = []
    for path in paths:
        if not path.exists():
            raise InputError(f"path not found: {path.as_posix()}")
        if path.is_dir():
            directories.append(path.as_posix())
            _walk(path, input_format, candidates, skipped)
        else:
            candidates.append(_explicit_file(path, input_format))

    sources = _deduplicate(candidates)
    if not sources:
        # Only directories were given, and none contained a supported file.
        raise InputError(
            f"no supported log files found in {', '.join(directories)}",
            hint=_skipped_hint(skipped),
        )
    return Discovery(sources, tuple(sorted(set(skipped), key=lambda s: (s.path, s.reason))))


def _explicit_file(path: Path, input_format: InputFormat | None) -> Source:
    display = path.as_posix()
    if not path.is_file():
        # A FIFO or a device would block or never end when read.
        raise InputError(f"not a regular file: {display}")
    suffix = path.suffix.lower()
    chosen = input_format or EXTENSION_FORMATS.get(suffix)
    if chosen is None:
        problem = f"unsupported extension '{suffix}'" if suffix else "the file has no extension"
        raise InputError(
            f"cannot determine the format of {display}: {problem}",
            hint=f"supported extensions: {_SUPPORTED} (or set the input format explicitly)",
        )
    return Source(path=path, display=display, format=chosen)


def _walk(
    root: Path,
    input_format: InputFormat | None,
    sources: list[Source],
    skipped: list[SkippedFile],
) -> None:
    def on_error(error: OSError) -> None:
        # Without this callback os.walk silently skips directories it cannot list.
        location = Path(error.filename).as_posix() if error.filename else root.as_posix()
        skipped.append(SkippedFile(location, SkipReason.UNREADABLE_DIRECTORY))

    for current, dirnames, filenames in os.walk(root, onerror=on_error):
        directory = Path(current)
        subdirectories = [name for name in dirnames if not name.startswith(".")]
        dirnames.clear()
        for name in subdirectories:
            if (directory / name).is_symlink():
                # Not followed: a link can point outside the tree or form a cycle.
                skipped.append(
                    SkippedFile((directory / name).as_posix(), SkipReason.SYMLINKED_DIRECTORY)
                )
            else:
                dirnames.append(name)

        for name in filenames:
            if name.startswith("."):
                continue
            path = directory / name
            detected = EXTENSION_FORMATS.get(path.suffix.lower())
            if detected is None:
                skipped.append(SkippedFile(path.as_posix(), SkipReason.UNSUPPORTED_EXTENSION))
            elif not path.is_file():
                skipped.append(SkippedFile(path.as_posix(), SkipReason.NOT_A_REGULAR_FILE))
            else:
                sources.append(Source(path, path.as_posix(), input_format or detected))


def _deduplicate(candidates: list[Source]) -> tuple[Source, ...]:
    seen: set[Path] = set()
    sources: list[Source] = []
    for source in sorted(candidates, key=lambda s: s.display):
        identity = source.path.resolve()
        if identity not in seen:
            seen.add(identity)
            sources.append(source)
    return tuple(sources)


def _skipped_hint(skipped: list[SkippedFile]) -> str:
    if not skipped:
        return f"supported extensions: {_SUPPORTED}"
    ordered = sorted(set(skipped), key=lambda s: (s.path, s.reason))
    listed = ", ".join(f"{s.path} ({s.reason})" for s in ordered[:_HINT_LIMIT])
    more = f" and {len(ordered) - _HINT_LIMIT} more" if len(ordered) > _HINT_LIMIT else ""
    return f"skipped {listed}{more}; supported extensions: {_SUPPORTED}"
