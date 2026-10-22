# Log Tracer

Log Tracer is a local, offline command-line tool for analyzing log files. It reads them, normalizes every line into a common event model, and reports recurring problems with deterministic, documented rules. The same input and configuration always produce the same report. It needs no network access, credentials, or external services.

> **Status: early development (increment 6 of 10).** Log Tracer reads text and JSON Lines logs, from single files or whole directories, correlates events by `request_id` and `trace_id`, shows the timeline of a request, and applies four diagnostic rules, in the terminal or as JSON, with thresholds you can configure. Saved analyses (`report`) and filters come in later increments. See the [plan](ARCHITECTURE.md#10-piano-incrementale).

## Requirements

- Python 3.12 or newer
- [uv](https://docs.astral.sh/uv/) for development (optional for a plain install)

## Installation

Install the package from a clone of the repository:

```bash
pip install .
```

Or set up the development environment with uv:

```bash
uv sync
uv run log-tracer --version
```

## Usage

```bash
log-tracer analyze examples/logs/                         # summary in the terminal
log-tracer analyze examples/logs/ --format json           # full JSON report on stdout
log-tracer analyze examples/logs/ --output report.json    # summary, and the JSON report in a file
log-tracer analyze app.log worker.jsonl                   # explicit files
log-tracer analyze service.log --input-format jsonl       # JSON Lines in a .log file
log-tracer analyze logs/ --fail-on high                   # exit code 1 if a finding is high
log-tracer analyze logs/ --config log-tracer.toml         # custom thresholds
log-tracer top-errors examples/logs/ --limit 5            # the most frequent errors only
log-tracer inspect req-184 examples/logs/                 # timeline of one request or trace
```

```
Log Tracer 0.1.0 · 2 files · 34 events · 3 unparsed lines
Time range (UTC): 2026-10-16T14:03:21.004Z → 2026-10-16T14:06:02.011Z

Findings (3)
Severity   Rule              Title                                                       Events
───────────────────────────────────────────────────────────────────────────────────────────────
medium     recurring_error   Recurring error in api-gateway: POST /checkout failed            3
...

Top errors
1. 3x api-gateway: POST /checkout failed
   first 14:03:24.910Z · last 14:05:23.897Z · example examples/logs/sample.log:6
...
```

Output on stdout is either the summary or a single JSON document, so you can pipe it to other programs; errors, warnings and hints go to stderr. Colors are used only on a terminal, and `NO_COLOR` turns them off. Text taken from the logs is always shown literally: terminal control sequences and bidirectional-override characters are displayed as escapes such as `\x1b`. Log Tracer also warns when at least half of a file's lines cannot be parsed, because this usually means the file uses another format. The JSON report contains:

- the identity of each input (path, size, SHA-256);
- line counts: events, blank lines, and unparsed lines with the reason for each;
- the distribution of log levels;
- the most frequent error groups;
- findings, each with the evidence that justifies it.

Unparsable lines are counted and reported, never silently dropped.

### Diagnostic rules

Every rule is deterministic and documented in [SPEC.md §4](SPEC.md#4-regole-diagnostiche); its findings carry the evidence that justifies them.

| Rule | Finds | Severity |
|---|---|---|
| `recurring_error` | The same error pattern (message with ids and numbers masked) seen at least 3 times in a service | medium; high from 20 occurrences or with a CRITICAL event |
| `high_latency` | Events of a service whose `duration_ms` is above 1000 ms | medium; high above 5000 ms |
| `warning_before_error` | A warning that precedes the first error of a correlated sequence | low; medium from 3 sequences |
| `error_spike` | Fixed 60 s windows with at least max(5, 3 × median) errors | medium; high at twice the threshold |

A warning that precedes an error is reported as a temporal correlation, never as proven causation. All thresholds can be changed with `--config` (see below).

### Request timelines

Events that share a `request_id` or a `trace_id` (directly, or through other events) form a *sequence*, even across files and formats. `inspect` shows one in time order:

```
$ log-tracer inspect req-184 examples/logs/
Sequence 4bf92f3577b34da6 · 6 events · 4 services · 2.790 s
  request_id req-184 · trace_id 4bf92f3577b34da6
  Order follows each service's own timestamps; clocks on different hosts may differ.

1. 14:03:22.120Z      +0 ms  INFO     api-gateway: POST /checkout
   examples/logs/sample.log:2
...
5. 14:03:24.902Z   +2.782 s  ERROR    payments: Payment gateway timeout after 2500 ms
   examples/logs/sample.log:5 · duration 2500 ms
6. 14:03:24.910Z   +2.790 s  ERROR    api-gateway: POST /checkout failed
   examples/logs/sample.log:6 · duration 2790 ms · status=502
```

Identifiers are matched exactly, including upper and lower case. Events are only ever linked through identifiers, never because they are close in time. The order across services follows the timestamps each service wrote, so it is a diagnostic hint, not proof of causality.

### Configuration

Thresholds come from a TOML file passed with `--config`. Nothing is read implicitly. Every key is optional and defaults to the value in [SPEC.md §7](SPEC.md#7-configurazione):

```toml
[report]
max_examples = 10          # event references per finding
top_errors = 10            # error groups in the report (top-errors --limit overrides it)

[recurring_error]
min_occurrences = 3        # an error pattern seen this often is a finding
high_count = 20            # ...and a high-severity one from this count

[high_latency]
warn_ms = 1000             # durations strictly above this are slow
critical_ms = 5000         # strictly above this, the finding is high

[high_latency.services.payments]
warn_ms = 3000             # per-service thresholds; missing ones are inherited

[warning_before_error]
medium_min_groups = 3      # sequences needed for a medium finding

[error_spike]
window_s = 60              # window length in seconds, aligned to the Unix epoch
factor = 3.0               # a spike has at least factor x median errors...
min_errors = 5             # ...and never fewer than this
```

The file is validated strictly, before any log is read. An unknown key, a value of the wrong type or an inconsistent pair (such as `critical_ms` not above `warn_ms`) stops the command with exit code 2 and names the key:

```
Error: log-tracer.toml: unknown key 'high_latency.warn' (did you mean 'warn_ms'?)
```

`examples/log-tracer.toml` shows per-service thresholds on the bundled logs.

### Exit codes

| Code | Meaning |
|---|---|
| 0 | Analysis completed, even if there are findings or unparsable lines |
| 1 | `--fail-on` was given and a finding reached that severity |
| 2 | Invalid command-line usage, or invalid `--config` file |
| 3 | Input or output error: missing path, unsupported file type, directory without supported files, no readable file, `--output` file not writable |
| 4 | `inspect`: no event has that `request_id` or `trace_id` |
| 70 | Internal error, which is a bug. A traceback is printed on stderr |

### Input formats

The format of each file is chosen by its extension:

| Extension | Format |
|---|---|
| `.log`, `.txt` | text (see below) |
| `.jsonl`, `.ndjson` | JSON Lines: one JSON object per line |

`--input-format text|jsonl` sets the parser for every file instead. Inside directories, files are still selected by extension, so `--input-format` never makes Log Tracer read a `README.md` as a log. Files in a directory that are not analyzed are listed in the report's `skipped_files`, with the reason. Hidden files are ignored.

#### Text

Text logs follow one documented line format (see [SPEC.md §2.2](SPEC.md#22-formato-testuale-v1-text)):

```
<timestamp> <LEVEL> [<service>] <message> [<key>=<value> ...]
```

```
2026-10-16T14:03:24.902Z ERROR [payments] Payment gateway timeout after 2500 ms request_id=req-184 duration_ms=2500
```

- **Timestamp:** ISO 8601. A timestamp without an offset is assumed to be UTC, and the report records this assumption.
- **Trailing fields:** `request_id`, `trace_id` and `duration_ms` are mapped to the event model. Other `key=value` fields are kept as attributes.

#### JSON Lines

Common field names are recognized: for example `timestamp`, `ts` or `@timestamp` for the time, and `message` or `msg` for the message. The full list of aliases and type rules is in [SPEC.md §2.3](SPEC.md#23-formato-json-lines-jsonl). Other fields are kept as attributes. A line that is not valid JSON, or is not an object, is counted as unparsed. An object without a timestamp is still analyzed as an event without a time.

## Development

```bash
uv sync                      # create .venv with runtime and dev dependencies
uv run pytest                # all tests
uv run pytest --cov          # with branch coverage (minimum 85%)
uv run ruff check            # lint
uv run ruff format --check   # formatting
uv run mypy                  # strict type checking of src/ and tests/
```

Tests are organized as unit, integration and end-to-end tests. A test cites the requirement ID from SPEC.md that it verifies.

## Documentation

- [SPEC.md](SPEC.md) is the functional specification: formats, rules, CLI, report, error handling and acceptance criteria.
- [ARCHITECTURE.md](ARCHITECTURE.md) covers modules, dependency rules, the data model, the test strategy and CI.

Both documents are written in Italian; code, CLI messages and JSON keys are in English.
