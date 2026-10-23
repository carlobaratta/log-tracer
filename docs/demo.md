# Log Tracer demo

A guided tour of Log Tracer on the logs bundled in `examples/logs/`. Every command runs offline from the root of the repository, and every one of them is executed by the test suite (`tests/e2e/test_demo.py`), which checks its exit code. The outputs below are excerpts, shown with a 100-column terminal.

## The example logs

Three files describe a few minutes of a small shop:

| File | Format | What it contains |
|---|---|---|
| `sample.log` | text | Checkout requests through `api-gateway`, `cart` and `payments`, where the payment provider is slow and sometimes times out; a slow database query; a failing `scheduler` job; a stack-trace line that is not a log line. |
| `sample.jsonl` | JSON Lines | The `inventory` service, with different field names (`ts`, `severity`, `app`, `msg`, `requestId`), an event without a timestamp, a truncated line and an array instead of an object. |
| `batch.log` | text | A nightly export job whose nine workers crash within one minute, the last one with a `CRITICAL` event. |

The checkout requests share a `request_id` and a `trace_id` across the text and the JSON Lines file, so they can be followed from one service to the next.

## Analyze

```bash
log-tracer analyze examples/logs/
```

```
Log Tracer 0.1.0 · 3 files · 45 events · 3 unparsed lines
Time range (UTC): 2026-10-16T14:03:21.004Z → 2026-10-16T14:07:38.900Z

Findings (10)
Severity   Rule                   Title                                                       Events
────────────────────────────────────────────────────────────────────────────────────────────────────
high       recurring_error        Recurring error in scheduler: Job nightly-export failed:         9
                                  worker <num>…
medium     error_spike            Error spike from 2026-10-16T14:07:00Z: 9 errors in 60 s          9
medium     high_latency           High latency in orders-db: 1 of 1 measured events over           1
                                  1000 ms
...
medium     warning_before_error   Warning before error in 3 sequences: Payment provider            6
                                  slow, retrying…

Top errors
1. 9x scheduler: Job nightly-export failed: worker <num> crashed
   first 14:07:03.120Z · last 14:07:38.415Z · example examples/logs/batch.log:2
2. 3x api-gateway: POST /checkout failed
   first 14:03:24.910Z · last 14:05:23.897Z · 3 sequences · example examples/logs/sample.log:6
...

Levels: CRITICAL 1 · ERROR 19 · WARNING 6 · INFO 16 · DEBUG 2 · UNKNOWN 1
Correlation: 5 sequences · 20 uncorrelated events
Inputs:
  examples/logs/batch.log (text): 11 events, 0 unparsed
  examples/logs/sample.jsonl (jsonl): 9 events, 2 unparsed
  examples/logs/sample.log (text): 25 events, 1 unparsed
Unparsed lines: no_match 1 · invalid_json 1 · not_an_object 1
Field issues: timestamp.missing 1 · timestamp.assumed_utc 1 · level.unknown 1
```

All four rules find something:

- **`recurring_error`**: the same error pattern seen at least 3 times in a service. Numbers and addresses are masked, so `worker 1 crashed` and `worker 9 crashed` are one pattern. The scheduler group is `high` because one of its events is `CRITICAL`.
- **`error_spike`**: the minute starting at 14:07 has 9 errors. The busiest earlier minute had 5, the median minute 2, so the threshold is max(5, 3 × 2) = 6.
- **`high_latency`**: events whose `duration_ms` is above 1000 ms, per service.
- **`warning_before_error`**: in 3 checkout sequences, the "provider slow" warning comes before the payment timeout. This is a temporal correlation, not proof of causation.

Unparsable lines are counted, never dropped: the stack-trace line in `sample.log`, and the truncated and non-object lines in `sample.jsonl`.

The same analysis as JSON, on stdout or in a file:

```bash
log-tracer analyze examples/logs/ --format json
```

```bash
log-tracer analyze examples/logs/ --output report.json
```

`--output` writes the JSON report and still prints the summary. The report lists each input with its size and SHA-256, the configuration and filters used, the counts, and every finding with the evidence behind it and references (`path`, `line`) to the events. `examples/expected_report.json` is the complete report of this analysis.

## The most frequent errors

```bash
log-tracer top-errors examples/logs/ --limit 3
```

```
Top errors in 3 files (45 events)

1. 9x scheduler: Job nightly-export failed: worker <num> crashed
   first 14:07:03.120Z · last 14:07:38.415Z · example examples/logs/batch.log:2
2. 3x api-gateway: POST /checkout failed
   first 14:03:24.910Z · last 14:05:23.897Z · 3 sequences · example examples/logs/sample.log:6
3. 3x inventory: Stock sync with warehouse failed: HTTP <num>
   first 14:04:20.000Z · last 14:04:50.000Z · example examples/logs/sample.jsonl:5
```

## Follow one request

```bash
log-tracer inspect req-184 examples/logs/
```

```
Sequence 4bf92f3577b34da6 · 6 events · 4 services · 2.790 s
  request_id req-184 · trace_id 4bf92f3577b34da6
  Order follows each service's own timestamps; clocks on different hosts may differ.

1. 14:03:22.120Z      +0 ms  INFO     api-gateway: POST /checkout
   examples/logs/sample.log:2
2. 14:03:22.135Z     +15 ms  DEBUG    cart: Cart loaded with 3 items
   examples/logs/sample.log:3
3. 14:03:22.180Z     +60 ms  INFO     inventory: Reserving stock for 3 items
   examples/logs/sample.jsonl:1 · duration 41 ms
4. 14:03:22.410Z    +290 ms  WARNING  payments: Payment provider slow, retrying
   examples/logs/sample.log:4 · attempt=1
5. 14:03:24.902Z   +2.782 s  ERROR    payments: Payment gateway timeout after 2500 ms
   examples/logs/sample.log:5 · duration 2500 ms
6. 14:03:24.910Z   +2.790 s  ERROR    api-gateway: POST /checkout failed
   examples/logs/sample.log:6 · duration 2790 ms · status=502
```

The sequence mixes the text file and the JSON Lines file. The trace id finds the same sequence:

```bash
log-tracer inspect 4bf92f3577b34da6 examples/logs/
```

## The saved analysis

Every `analyze` saves its report in `.log-tracer/last-report.json` (or in the directory given with `--state-dir` or `LOG_TRACER_STATE_DIR`). `report` shows it again without reading the logs:

```bash
log-tracer report
```

```bash
log-tracer report --format json
```

and `inspect` without paths reads again the files of that analysis, warning if one of them has changed since:

```bash
log-tracer inspect req-187
```

## Narrow the analysis

Filters define what the rules see. Here, only the `payments` service from 14:04 on:

```bash
log-tracer analyze examples/logs/ --service payments --since 2026-10-16T14:04:00Z
```

```
Log Tracer 0.1.0 · 3 files · 5 events · 3 unparsed lines
Time range (UTC): 2026-10-16T14:04:16.101Z → 2026-10-16T14:05:23.891Z
Filters: service payments · since 2026-10-16T14:04:00.000Z · 40 events excluded

Findings (2)
Severity   Rule                   Title                                                       Events
────────────────────────────────────────────────────────────────────────────────────────────────────
medium     high_latency           High latency in payments: 2 of 2 measured events over            2
                                  1000 ms
low        warning_before_error   Warning before error in 2 sequences: Payment provider            4
                                  slow, retrying…
...
Skipped rules: error_spike (insufficient_data)
```

With only two sequences left, the warning-before-error finding drops to `low`; with errors in two minutes only, there is not enough data to look for spikes.

## Configure the thresholds

`examples/log-tracer.toml` raises the latency threshold of `payments` (a slow provider is normal there) and sets a strict budget for `api-gateway`. With `--fail-on high`, the command exits with code 1 when a finding is `high`, which is how a CI job would use it:

```bash
log-tracer analyze examples/logs/ --config examples/log-tracer.toml --fail-on high  # exit 1
```

```
...
Findings (9)
Severity   Rule                   Title                                                       Events
────────────────────────────────────────────────────────────────────────────────────────────────────
high       high_latency           High latency in api-gateway: 4 of 5 measured events over         4
                                  1000 ms
high       recurring_error        Recurring error in scheduler: Job nightly-export failed:         9
                                  worker <num>…
...
Found 2 findings with severity high or higher (--fail-on high)
```

The output is printed in full; the last line goes to stderr.

## Errors

Problems with the environment stop the command with a message on stderr and a documented exit code:

```bash
log-tracer inspect req-999 examples/logs/  # exit 4
```

```
Error: no event has request_id or trace_id 'req-999'
Hint: identifiers are matched exactly, including upper and lower case
```

```bash
log-tracer analyze missing.log  # exit 3
```

```
Error: path not found: missing.log
```

```bash
log-tracer analyze examples/logs/ --since yesterday  # exit 2
```

## Performance

NFR-3 asks for 100 000 lines (about 15 MB) in less than 10 seconds, with memory linear in the number of events. Measured on an Apple M4 laptop (16 GB, macOS 27.0.1) with Python 3.12.13, as the median of three runs of `log-tracer analyze DIR --no-save`; peak memory is the maximum resident set size reported by `/usr/bin/time -l`:

| Lines | Size | Events | Time | Peak memory |
|---:|---:|---:|---:|---:|
| 100 000 | 14.3 MB | 99 800 | 1.24 s | 164 MB |
| 200 000 | 28.6 MB | 199 600 | 2.47 s | 294 MB |
| 400 000 | 57.3 MB | 399 200 | 4.89 s | 553 MB |

Time and memory grow linearly: about 12 µs and 1.3 KB per event, on top of about 30 MB for the interpreter. Of the 400 000 lines, the 320 000 text lines take 3.75 s and the 80 000 JSON Lines take 1.04 s when analyzed on their own. All events are kept in memory (ARCHITECTURE §11), so a few million lines is the practical limit.

The inputs come from a deterministic generator, the same one the large-input tests use: the same arguments always write the same bytes, with unparsable lines, unknown levels, events without a timestamp, a burst of errors and an identifier reused by more than 10 000 events. To repeat the measurement:

```sh
uv run python -m tests.large_logs /tmp/large --lines 100000
/usr/bin/time -l log-tracer analyze /tmp/large --no-save > /dev/null
```

On Linux, use `/usr/bin/time -v`. This block is not run by the tests, since it writes large files. The tests check counts and results on 100 000 lines (`tests/integration/test_large_input.py`, marked `slow`), never timings, which would make CI unstable.

## Regenerating the golden report

`tests/e2e/test_golden.py` compares the report of `analyze examples/logs/` with `examples/expected_report.json`, ignoring `generated_at`. When a change to the parsers, the rules or the report is intended, regenerate the file and review the diff before committing it:

```sh
log-tracer analyze examples/logs/ --format json --no-save > examples/expected_report.json
git diff examples/expected_report.json
```

This block is not run by the tests, since it rewrites the golden file.
