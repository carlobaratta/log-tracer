"""Configuration loading and validation (SPEC §7, CFG-1…3)."""

from pathlib import Path

import pytest

from log_tracer.config import (
    AnalysisConfig,
    HighLatencyConfig,
    LatencyThresholds,
    RecurringErrorConfig,
    ReportConfig,
    load_config,
    parse_config,
)
from log_tracer.errors import ConfigError


def write(tmp_path: Path, text: str, name: str = "log-tracer.toml") -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def error_of(data: dict[str, object]) -> str:
    with pytest.raises(ConfigError) as excinfo:
        parse_config(data, source="cfg.toml")
    return str(excinfo.value)


# --- Defaults and merging ---------------------------------------------------------------


def test_defaults_match_the_spec() -> None:
    config = AnalysisConfig()
    assert config.report == ReportConfig(max_examples=10, top_errors=10)
    assert config.recurring_error == RecurringErrorConfig(min_occurrences=3, high_count=20)
    assert (config.high_latency.warn_ms, config.high_latency.critical_ms) == (1000, 5000)
    assert dict(config.high_latency.services) == {}


def test_empty_file_gives_the_defaults(tmp_path: Path) -> None:
    assert load_config(write(tmp_path, "")) == AnalysisConfig()


def test_values_from_the_file_override_the_defaults(tmp_path: Path) -> None:
    config = load_config(
        write(
            tmp_path,
            "[report]\nmax_examples = 5\n\n"
            "[recurring_error]\nmin_occurrences = 2\n\n"
            "[high_latency]\nwarn_ms = 800\ncritical_ms = 2500.5\n",
        )
    )
    assert config.report == ReportConfig(max_examples=5, top_errors=10)
    assert config.recurring_error == RecurringErrorConfig(min_occurrences=2, high_count=20)
    assert (config.high_latency.warn_ms, config.high_latency.critical_ms) == (800, 2500.5)


def test_service_thresholds_inherit_missing_values(tmp_path: Path) -> None:
    config = load_config(
        write(
            tmp_path,
            "[high_latency]\nwarn_ms = 1000\ncritical_ms = 5000\n\n"
            "[high_latency.services.payments]\nwarn_ms = 3000\n\n"
            "[high_latency.services.api-gateway]\ncritical_ms = 2700\n",
        )
    )
    assert dict(config.high_latency.services) == {
        "payments": LatencyThresholds(warn_ms=3000, critical_ms=5000),
        "api-gateway": LatencyThresholds(warn_ms=1000, critical_ms=2700),
    }


def test_loaded_configuration_is_read_only(tmp_path: Path) -> None:
    config = load_config(write(tmp_path, "[high_latency.services.payments]\nwarn_ms = 3000\n"))
    with pytest.raises(TypeError):
        config.high_latency.services["x"] = LatencyThresholds()  # type: ignore[index]


def test_configurations_compare_by_value() -> None:
    services = {"a": LatencyThresholds(10, 20)}
    first = HighLatencyConfig(services=services)
    assert first == HighLatencyConfig(services=dict(services))


# --- Unknown keys (CFG-2) ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("data", "message"),
    [
        ({"reports": {}}, "cfg.toml: unknown key 'reports' (did you mean 'report'?)"),
        ({"error_spike": {"window_s": 60}}, "cfg.toml: unknown key 'error_spike'"),
        (
            {"high_latency": {"warn": 10}},
            "cfg.toml: unknown key 'high_latency.warn' (did you mean 'warn_ms'?)",
        ),
        (
            {"high_latency": {"services": {"payments": {"warn": 1}}}},
            "cfg.toml: unknown key 'high_latency.services.payments.warn' (did you mean 'warn_ms'?)",
        ),
    ],
)
def test_unknown_keys_are_rejected(data: dict[str, object], message: str) -> None:
    assert error_of(data) == message


# --- Invalid values (CFG-2) -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (
            {"report": {"max_examples": 0}},
            "cfg.toml: report.max_examples must be a positive integer, got 0",
        ),
        (
            {"report": {"top_errors": 2.0}},
            "cfg.toml: report.top_errors must be a positive integer, got 2.0",
        ),
        (
            {"recurring_error": {"min_occurrences": True}},
            "cfg.toml: recurring_error.min_occurrences must be a positive integer, got true",
        ),
        (
            {"high_latency": {"warn_ms": "fast"}},
            'cfg.toml: high_latency.warn_ms must be a positive number, got "fast"',
        ),
        (
            {"high_latency": {"warn_ms": -5}},
            "cfg.toml: high_latency.warn_ms must be a positive number, got -5",
        ),
        (
            {"high_latency": {"critical_ms": float("inf")}},
            "cfg.toml: high_latency.critical_ms must be a positive number, got Infinity",
        ),
        (
            {"high_latency": {"services": {"payments": {"warn_ms": False}}}},
            "cfg.toml: high_latency.services.payments.warn_ms must be a positive number, got false",
        ),
        ({"report": 5}, "cfg.toml: report must be a table, got 5"),
        (
            {"high_latency": {"services": []}},
            "cfg.toml: high_latency.services must be a table, got []",
        ),
        (
            {"high_latency": {"services": {"payments": 3000}}},
            "cfg.toml: high_latency.services.payments must be a table, got 3000",
        ),
    ],
)
def test_invalid_values_are_rejected_with_their_key(data: dict[str, object], message: str) -> None:
    assert error_of(data) == message


# --- Consistency between keys -----------------------------------------------------------------


def test_critical_threshold_must_exceed_the_warning_threshold() -> None:
    assert error_of({"high_latency": {"warn_ms": 5000, "critical_ms": 5000}}) == (
        "cfg.toml: high_latency.critical_ms (5000) must be greater than high_latency.warn_ms (5000)"
    )


def test_service_thresholds_are_checked_after_inheriting() -> None:
    assert error_of({"high_latency": {"services": {"slow": {"warn_ms": 6000}}}}) == (
        "cfg.toml: high_latency.services.slow: critical_ms (5000, inherited) must be "
        "greater than warn_ms (6000)"
    )


def test_high_count_must_not_be_below_min_occurrences() -> None:
    assert error_of({"recurring_error": {"min_occurrences": 5, "high_count": 4}}) == (
        "cfg.toml: recurring_error.high_count (4) must be greater than or equal to "
        "recurring_error.min_occurrences (5)"
    )


# --- Files -------------------------------------------------------------------------------------


def test_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match=r"^configuration file not found: .*missing\.toml$"):
        load_config(tmp_path / "missing.toml")


def test_directory_instead_of_a_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match=r"^cannot read configuration file "):
        load_config(tmp_path)


def test_invalid_toml_reports_line_and_column(tmp_path: Path) -> None:
    path = write(tmp_path, "[high_latency]\nwarn_ms = \n")
    with pytest.raises(ConfigError) as excinfo:
        load_config(path)
    assert str(excinfo.value) == (
        f"{path.as_posix()}: invalid TOML: Invalid value (at line 2, column 11)"
    )


def test_file_that_is_not_utf8(tmp_path: Path) -> None:
    path = tmp_path / "latin1.toml"
    path.write_bytes(b"# caf\xe9\n")
    with pytest.raises(ConfigError, match="is not valid UTF-8"):
        load_config(path)


def test_errors_name_the_file(tmp_path: Path) -> None:
    path = write(tmp_path, "[report]\nmax_examples = -1\n")
    with pytest.raises(ConfigError) as excinfo:
        load_config(path)
    assert str(excinfo.value).startswith(f"{path.as_posix()}: report.max_examples")
