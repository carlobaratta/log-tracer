"""Errors caused by the environment: paths, configuration, saved state.

Problems in the *content* of a log (malformed lines, missing fields) are never
raised: they are returned as data and counted in the report. The exceptions
below are reserved for situations where there is nothing sensible to analyze.
The CLI translates each class into an exit code (SPEC §5.4).
"""


class LogTracerError(Exception):
    """Base class for expected, user-facing errors."""

    def __init__(self, message: str, *, hint: str | None = None) -> None:
        super().__init__(message)
        self.hint = hint


class InputError(LogTracerError):
    """The input paths cannot be analyzed (missing, unsupported or unreadable)."""


class OutputError(LogTracerError):
    """An output file (``--output``) cannot be written."""
