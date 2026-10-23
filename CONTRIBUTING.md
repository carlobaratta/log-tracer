# Contributing to Log Tracer

Thank you for your interest in Log Tracer. Bug reports, ideas and pull requests are welcome. This guide explains how the project is developed, so that a change can be reviewed and merged without surprises.

By taking part you agree to follow the [Code of Conduct](CODE_OF_CONDUCT.md). Please report security problems privately, as described in the [security policy](SECURITY.md), and not in a public issue.

## Before you start

- **Bugs.** Open an issue with the bug report form. Include the output of `log-tracer --version`, the exact command, and a small log excerpt that reproduces the problem. Remove hostnames, IP addresses, user names, tokens and any other sensitive data from the excerpt first.
- **New features or changes in behavior.** Open an issue before writing code. Log Tracer's behavior is specified in [SPEC.md](SPEC.md), and a change in behavior starts as a change in the specification. Agreeing on it first saves you from writing code that cannot be merged.
- **Small fixes** to typos, documentation or tests can go straight to a pull request.

## Development setup

You need Python 3.12 or newer and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/carlobaratta/log-tracer.git
cd log-tracer
uv sync
uv run log-tracer --version
```

## How a change is made

The project is specification-driven and test-first:

1. **Specification.** Every observable behavior is a numbered requirement in [SPEC.md](SPEC.md), such as `CLI-4` or `JL-6`. A new behavior gets a new requirement, and a changed behavior updates the existing one. SPEC.md and ARCHITECTURE.md are written in Italian. You may propose a requirement in English in the issue or pull request, and it will be translated.
2. **Test.** Write a test that fails without the change. The test cites the requirement ID it verifies, in its name or docstring. Put it in `tests/unit`, `tests/integration` or `tests/e2e`, depending on how much of the program it exercises.
3. **Code.** Write the smallest change that makes the test pass, then refactor with the tests green.
4. **Design.** If the change affects module boundaries or the data model, update [ARCHITECTURE.md](ARCHITECTURE.md).

A few rules come from the specification and are checked by the tests or the linter:

- No network access, ever.
- The same input and configuration always produce the same report, apart from `generated_at`.
- stdout holds only the summary or one JSON document. Warnings and errors go to stderr.
- Text from the logs is shown literally, with control and bidirectional characters escaped.
- No blind `except Exception`. Catch the specific exceptions you expect.

## Checks

Run the same checks as the CI before opening a pull request:

```bash
uv run ruff check            # lint
uv run ruff format --check   # formatting (uv run ruff format to fix it)
uv run mypy                  # strict type checking of src/ and tests/
uv run pytest --cov          # all tests, with branch coverage (minimum 85%)
```

`uv run pytest -m "not slow"` skips the tests on large generated inputs while you iterate. The CI runs the whole suite on Linux with Python 3.12, 3.13 and 3.14, and on Windows and macOS with Python 3.14, so avoid assumptions about path separators and line endings.

### The golden report

`examples/expected_report.json` is the report of `log-tracer analyze examples/logs/`, and a test checks that the two match. If your change alters the report on purpose, regenerate the file as described in [docs/demo.md](docs/demo.md#regenerating-the-golden-report), and explain the difference in the pull request. If the report changes and you did not intend it to, the golden test has found a regression.

## Pull requests

- Keep a pull request focused on one change. Several small pull requests are easier to review than one large one.
- Write commit messages in the imperative mood, with a short first line: `Add the --limit option to inspect`, not `Added limit`.
- Add an entry under `## [Unreleased]` in [CHANGELOG.md](CHANGELOG.md) for any change a user can notice, following [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Create the section if it does not exist yet.
- Fill in the pull request template. It lists the points a review will check.

## License

By contributing, you agree that your contributions are licensed under the [MIT License](LICENSE) that covers the project.
