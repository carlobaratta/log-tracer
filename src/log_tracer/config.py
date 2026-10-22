"""Analysis configuration: defaults, loading and validation (SPEC §7).

The configuration only comes from a TOML file given explicitly with
``--config`` (CFG-1). Validation is strict (CFG-2): an unknown key or an
invalid value is an error that names the key, because a typo silently
ignored would make the analysis run with thresholds nobody chose.
"""

import difflib
import json
import math
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Final, NoReturn

from log_tracer.errors import ConfigError

# --- Model ------------------------------------------------------------------------------


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
class LatencyThresholds:
    warn_ms: float = 1000
    critical_ms: float = 5000


_NO_SERVICES: Final[Mapping[str, LatencyThresholds]] = MappingProxyType({})


@dataclass(frozen=True, slots=True)
class HighLatencyConfig:
    warn_ms: float = 1000
    critical_ms: float = 5000
    services: Mapping[str, LatencyThresholds] = _NO_SERVICES
    """Per-service thresholds, already merged with the global ones."""


@dataclass(frozen=True, slots=True)
class AnalysisConfig:
    report: ReportConfig = field(default_factory=ReportConfig)
    recurring_error: RecurringErrorConfig = field(default_factory=RecurringErrorConfig)
    high_latency: HighLatencyConfig = field(default_factory=HighLatencyConfig)


# --- Loading ----------------------------------------------------------------------------

_SECTIONS: Final = ("report", "recurring_error", "high_latency")
_LATENCY_KEYS: Final = ("warn_ms", "critical_ms")


def load_config(path: Path) -> AnalysisConfig:
    """Read and validate a TOML configuration file."""
    shown = path.as_posix()
    try:
        text = path.read_bytes().decode("utf-8")
    except FileNotFoundError as error:
        raise ConfigError(f"configuration file not found: {shown}") from error
    except OSError as error:
        reason = error.strerror or type(error).__name__
        raise ConfigError(f"cannot read configuration file {shown} ({reason})") from error
    except UnicodeDecodeError as error:
        raise ConfigError(f"configuration file {shown} is not valid UTF-8") from error
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise ConfigError(f"{shown}: invalid TOML: {error}") from error
    return parse_config(data, source=shown)


def parse_config(data: Mapping[str, object], *, source: str) -> AnalysisConfig:
    """Validate already-parsed TOML data; ``source`` prefixes every error message."""
    check = _Checker(source)
    check.known_keys(data, _SECTIONS, prefix="")
    report = check.table(data, "report")
    recurring = check.table(data, "recurring_error")
    latency = check.table(data, "high_latency")
    check.known_keys(report, ("max_examples", "top_errors"), prefix="report.")
    check.known_keys(recurring, ("min_occurrences", "high_count"), prefix="recurring_error.")
    check.known_keys(latency, (*_LATENCY_KEYS, "services"), prefix="high_latency.")

    defaults = AnalysisConfig()
    report_config = ReportConfig(
        max_examples=check.positive_int(
            report, "report.max_examples", defaults.report.max_examples
        ),
        top_errors=check.positive_int(report, "report.top_errors", defaults.report.top_errors),
    )
    recurring_config = RecurringErrorConfig(
        min_occurrences=check.positive_int(
            recurring, "recurring_error.min_occurrences", defaults.recurring_error.min_occurrences
        ),
        high_count=check.positive_int(
            recurring, "recurring_error.high_count", defaults.recurring_error.high_count
        ),
    )
    if recurring_config.high_count < recurring_config.min_occurrences:
        check.fail(
            f"recurring_error.high_count ({_show(recurring_config.high_count)}) must be greater "
            f"than or equal to recurring_error.min_occurrences "
            f"({_show(recurring_config.min_occurrences)})"
        )
    return AnalysisConfig(
        report=report_config,
        recurring_error=recurring_config,
        high_latency=_latency(check, latency, defaults.high_latency),
    )


def _latency(
    check: "_Checker", table: Mapping[str, object], defaults: HighLatencyConfig
) -> HighLatencyConfig:
    warn = check.positive_number(table, "high_latency.warn_ms", defaults.warn_ms)
    critical = check.positive_number(table, "high_latency.critical_ms", defaults.critical_ms)
    if critical <= warn:
        check.fail(
            f"high_latency.critical_ms ({_show(critical)}) must be greater than "
            f"high_latency.warn_ms ({_show(warn)})"
        )
    services: dict[str, LatencyThresholds] = {}
    for name, value in check.table(table, "services", prefix="high_latency.").items():
        key = f"high_latency.services.{name}"
        entry = check.as_table(value, key)
        check.known_keys(entry, _LATENCY_KEYS, prefix=f"{key}.")
        service_warn = check.positive_number(entry, f"{key}.warn_ms", warn)
        service_critical = check.positive_number(entry, f"{key}.critical_ms", critical)
        if service_critical <= service_warn:
            inherited = {k: "" if k in entry else ", inherited" for k in _LATENCY_KEYS}
            check.fail(
                f"{key}: critical_ms ({_show(service_critical)}{inherited['critical_ms']}) must be "
                f"greater than warn_ms ({_show(service_warn)}{inherited['warn_ms']})"
            )
        services[name] = LatencyThresholds(service_warn, service_critical)
    return HighLatencyConfig(warn, critical, MappingProxyType(services))


class _Checker:
    """Type and range checks that report the offending key and value."""

    def __init__(self, source: str) -> None:
        self._source = source

    def fail(self, message: str) -> NoReturn:
        raise ConfigError(f"{self._source}: {message}")

    def known_keys(
        self, table: Mapping[str, object], allowed: tuple[str, ...], prefix: str
    ) -> None:
        for key in sorted(set(table) - set(allowed)):
            match = difflib.get_close_matches(key, allowed, n=1)
            hint = f" (did you mean '{match[0]}'?)" if match else ""
            self.fail(f"unknown key '{prefix}{key}'{hint}")

    def table(
        self, data: Mapping[str, object], name: str, prefix: str = ""
    ) -> Mapping[str, object]:
        return self.as_table(data.get(name, {}), f"{prefix}{name}")

    def as_table(self, value: object, key: str) -> Mapping[str, object]:
        if not isinstance(value, dict):
            self.fail(f"{key} must be a table, got {_show(value)}")
        return value

    def positive_int(self, table: Mapping[str, object], key: str, default: int) -> int:
        value = table.get(key.rpartition(".")[2], default)
        # bool is a subclass of int: ``true`` must not count as 1.
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            self.fail(f"{key} must be a positive integer, got {_show(value)}")
        return value

    def positive_number(self, table: Mapping[str, object], key: str, default: float) -> float:
        value = table.get(key.rpartition(".")[2], default)
        if (
            isinstance(value, bool)
            or not isinstance(value, int | float)
            or not math.isfinite(value)
            or value <= 0
        ):
            self.fail(f"{key} must be a positive number, got {_show(value)}")
        return value


def _show(value: object) -> str:
    """A value as it would be written in TOML or JSON: true, "fast", 2.0, Infinity."""
    return json.dumps(value, default=str)
