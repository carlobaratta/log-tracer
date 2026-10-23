# Changelog

All notable changes to Log Tracer are listed here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-10-23

The first release: a local, offline command-line tool that analyzes log files with deterministic, documented rules. The behavior is specified in [SPEC.md](SPEC.md) and the design in [ARCHITECTURE.md](ARCHITECTURE.md).

### Added

- **Inputs.** Text logs in one documented format (`<timestamp> <LEVEL> [<service>] <message> [key=value ...]`) and JSON Lines with common field aliases (`ts`, `msg`, `severity`, `requestId`, …), from single files or whole directories. The format is chosen by extension, or forced with `--input-format`. Files are read as UTF-8, with or without BOM and CRLF.
- **Data quality.** Lines that cannot be parsed are counted by reason and sampled, never dropped; field problems (a timestamp without an offset, an unknown level, an invalid duration…) are counted; warnings when most of a file cannot be parsed, when a file cannot be read, and when one identifier links more than 10 000 events.
- **Correlation.** Events that share a `request_id` or a `trace_id`, directly or through other events, form one sequence across files and formats.
- **Diagnostic rules**, each with the evidence behind its findings and configurable thresholds:
  - `recurring_error`: the same error pattern repeated in a service;
  - `high_latency`: durations above a threshold, per service;
  - `warning_before_error`: a warning that precedes the first error of a sequence, reported as a temporal correlation, not as proof of causation;
  - `error_spike`: windows with far more errors than the median.
- **Commands.**
  - `analyze`: a terminal summary, or the complete JSON report with `--format json`; `--output` also writes the report to a file; `--fail-on low|medium|high` exits with code 1 for CI.
  - `top-errors`: the most frequent error groups.
  - `inspect ID`: the timeline of the sequence that contains a request or trace id.
  - `report`: the last analysis again, without reading the logs.
- **Saved analysis.** `analyze` saves its report atomically in `.log-tracer/` (`--no-save`, `--state-dir`, `LOG_TRACER_STATE_DIR`); `inspect` without paths reads the inputs of that analysis again and warns if a file has changed.
- **Filters.** `--service`, `--since` and `--until` restrict the events the rules see; the report records them and the number of events left out.
- **Configuration.** Thresholds from an explicit TOML file (`--config`), with per-service latency thresholds, validated strictly before any log is read.
- **Output contract.** stdout holds only the summary or one JSON document; warnings and errors go to stderr; text from the logs is shown literally, with control and bidirectional characters escaped; documented exit codes (0, 1, 2, 3, 4, 70).
- **Guarantees.** No network access. The same input and configuration always give the same report, apart from `generated_at`. 100 000 lines are analyzed in about 1.2 s on a recent laptop, with memory linear in the number of events.
- **Examples and documentation.** Bundled example logs, the golden report `examples/expected_report.json`, and a guided demo (`docs/demo.md`) whose commands are run by the test suite.

[0.1.0]: https://github.com/carlobaratta/log-tracer/releases/tag/v0.1.0
