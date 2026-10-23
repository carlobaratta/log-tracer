## What and why

<!-- What does this change do, and which problem does it solve? Link the issue: "Closes #123". -->

## Requirements

<!-- The SPEC.md requirement IDs this adds, changes or verifies, e.g. CLI-4. Write "none" for a change with no observable behavior. -->

## Checklist

- [ ] Tests cover the change and cite the requirement IDs they verify.
- [ ] `uv run ruff check`, `uv run ruff format --check`, `uv run mypy` and `uv run pytest --cov` pass locally.
- [ ] SPEC.md is updated if the behavior changes, and ARCHITECTURE.md if the design changes.
- [ ] `examples/expected_report.json` is regenerated only if the report changes on purpose, and the difference is explained above.
- [ ] CHANGELOG.md has an entry under `[Unreleased]` for a change users can notice.
- [ ] No network access and no blind `except Exception` are introduced.
