"""Analysis configuration and its defaults (SPEC §7)."""

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class ReportConfig:
    max_examples: int = 10
    """Maximum event references per finding and unparsed-line samples per report."""
    top_errors: int = 10
    """Number of error groups listed in the ``top_errors`` section."""


@dataclass(frozen=True, slots=True)
class RecurringErrorConfig:
    min_occurrences: int = 3
    high_count: int = 20


@dataclass(frozen=True, slots=True)
class AnalysisConfig:
    report: ReportConfig = field(default_factory=ReportConfig)
    recurring_error: RecurringErrorConfig = field(default_factory=RecurringErrorConfig)
