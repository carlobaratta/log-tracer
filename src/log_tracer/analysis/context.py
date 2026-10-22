"""Data shared by every rule, and helpers for building findings."""

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final

from log_tracer.models import ErrorGroup, Event, RuleName, SourceRef

_NO_LABELS: Final[Mapping[SourceRef, str]] = MappingProxyType({})


@dataclass(frozen=True, slots=True)
class AnalysisContext:
    """Inputs computed once and shared by all rules, so that no rule recomputes them."""

    events: tuple[Event, ...]
    """All analyzed events, in timeline order."""
    error_groups: tuple[ErrorGroup, ...]
    max_examples: int
    label_of: Mapping[SourceRef, str] = _NO_LABELS
    """The sequence label of every correlated event (COR-1)."""


def finding_id(rule: RuleName, *key: str | None) -> str:
    """A finding id that is stable across runs (``hash()`` is randomized per process).

    ``key`` holds the values that identify the finding within its rule; they
    are encoded as a JSON array so that, for example, ``None`` and ``""`` stay
    distinct and no separator can be confused with the content.
    """
    canonical = json.dumps([rule.value, *key], ensure_ascii=False, separators=(",", ":"))
    return f"{rule.value}:{hashlib.sha256(canonical.encode()).hexdigest()[:12]}"
