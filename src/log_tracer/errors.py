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


class NotFoundError(LogTracerError):
    """The identifier given to ``inspect`` does not appear in the logs."""


class ConfigError(LogTracerError):
    """The configuration file cannot be read or contains an invalid value."""


class StateError(LogTracerError):
    """The saved analysis is missing, unreadable or incompatible, or cannot be written."""
