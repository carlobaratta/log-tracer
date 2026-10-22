"""Data shared by every rule, and helpers for building findings."""

import hashlib
import json
from dataclasses import dataclass

from log_tracer.models import ErrorGroup, Event, RuleName


@dataclass(frozen=True, slots=True)
class AnalysisContext:
    """Inputs computed once and shared by all rules, so that no rule recomputes them."""

    events: tuple[Event, ...]
    """All analyzed events, in timeline order."""
    error_groups: tuple[ErrorGroup, ...]
    max_examples: int


def finding_id(rule: RuleName, *key: str | None) -> str:
    """A finding id that is stable across runs (``hash()`` is randomized per process).

    ``key`` holds the values that identify the finding within its rule; they
    are encoded as a JSON array so that, for example, ``None`` and ``""`` stay
    distinct and no separator can be confused with the content.
    """
    canonical = json.dumps([rule.value, *key], ensure_ascii=False, separators=(",", ":"))
    return f"{rule.value}:{hashlib.sha256(canonical.encode()).hexdigest()[:12]}"
