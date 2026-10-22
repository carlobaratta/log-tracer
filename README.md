# Log Tracer

Log Tracer is a local, offline command-line tool for analyzing log files. It reads them, normalizes every line into a common event model, and reports recurring problems with deterministic, documented rules. The same input and configuration always produce the same report. It needs no network access, credentials, or external services.

> **Status: early development (increment 1 of 10).** Today Log Tracer reads the text format and reports recurring errors as JSON. JSON Lines input, terminal tables, request timelines (`inspect`) and the remaining rules come in later increments. See the [plan](ARCHITECTURE.md#10-piano-incrementale).

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
log-tracer analyze examples/sample.log --format json
```

The command writes a single JSON report to stdout, so you can pipe it to other programs. Errors and hints go to stderr. On the bundled example, the report contains:

- the identity of each input (path, size, SHA-256);
- line counts: events, blank lines, and unparsed lines with the reason for each;
- the distribution of log levels;
- the most frequent error groups;
- findings, each with the evidence that justifies it.

Unparsable lines are counted and reported, never silently dropped.

### Exit codes

| Code | Meaning |
|---|---|
| 0 | Analysis completed, even if there are findings or unparsable lines |
| 2 | Invalid command-line usage |
| 3 | Input error: missing path, unsupported file type, no readable file |
| 70 | Internal error, which is a bug. A traceback is printed on stderr |

### Input format

Text logs follow one documented line format (see [SPEC.md §2.2](SPEC.md#22-formato-testuale-v1-text)):

```
<timestamp> <LEVEL> [<service>] <message> [<key>=<value> ...]
```

```
2026-10-16T14:03:24.902Z ERROR [payments] Payment gateway timeout after 2500 ms request_id=req-184 duration_ms=2500
```

- **Timestamp:** ISO 8601. A timestamp without an offset is assumed to be UTC, and the report records this assumption.
- **Trailing fields:** `request_id`, `trace_id` and `duration_ms` are mapped to the event model. Other `key=value` fields are kept as attributes.
- **Accepted files:** `.log` and `.txt`.

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
