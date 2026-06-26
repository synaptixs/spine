"""Models and helpers for the manager-with-specialists pattern.

The protocol is small on purpose. Specialists do real work and write full
outputs to the artifact store; only ``SpecialistReturn`` payloads cross
back to the manager, keeping the manager's prompt context bounded even
when specialists produce large outputs.

``Handoff`` describes what flows *between* specialists (artifact ids,
glossary slices, narrative context) when the manager chains them rather
than running them in parallel.

``ContextBudget`` is the per-node truncation policy: glossary and
explicit handoffs are pinned first, recent claims next, oldest content
truncated when over budget.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class CompletionStatus(str, Enum):
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"


class ClaimSummary(BaseModel):
    """A claim slimmed down for the manager's view.

    Carries the artifact reference and the headline metric; full
    supporting_artifacts and detailed metric_values stay in the
    artifact-store-backed full output.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    statement: str = Field(min_length=1, max_length=1024)
    artifact_id: str
    confidence: float = Field(ge=0.0, le=1.0)


class SpecialistReturn(BaseModel):
    """What a specialist passes back to its manager. Small by design."""

    model_config = ConfigDict(extra="forbid")

    specialist_id: str
    artifact_id: str = Field(description="Object-store key for the specialist's full output.")
    summary: str = Field(min_length=1, max_length=2048, description="Concise narrative for the manager.")
    key_claims: list[ClaimSummary] = Field(
        default_factory=list, max_length=8, description="Top claims; cap of 8 keeps context bounded."
    )
    confidence: float = Field(ge=0.0, le=1.0)
    caveats: list[str] = Field(default_factory=list)
    completion_status: CompletionStatus = CompletionStatus.SUCCESS


class Handoff(BaseModel):
    """A specialist-to-specialist handoff."""

    model_config = ConfigDict(extra="forbid")

    from_specialist_id: str
    to_specialist_id: str
    artifact_ids: list[str] = Field(default_factory=list)
    glossary_terms: dict[str, str] = Field(default_factory=dict)
    narrative_context: str = Field(default="", max_length=4096)


class ContextBudget(BaseModel):
    """Per-node prompt-construction budget.

    Token counts are heuristic (chars-divided-by-4) until a real tokenizer
    lands; the policy ordering is what matters for now.
    """

    model_config = ConfigDict(extra="forbid")

    max_tokens: int = Field(default=8000, ge=1)
    pin_glossary: bool = True
    pin_handoffs: bool = True
    keep_recent_claims: int = Field(default=10, ge=0)
    chars_per_token: int = Field(default=4, ge=1)


def estimate_tokens(text: str, *, chars_per_token: int = 4) -> int:
    """Cheap-but-deterministic token estimate. Swap for tiktoken when needed."""
    return max(1, len(text) // chars_per_token)


def fit_to_budget(
    sections: list[tuple[str, str, int]],
    budget: ContextBudget,
) -> tuple[list[tuple[str, str]], dict[str, Any]]:
    """Greedy fit of named text sections to a token budget.

    ``sections`` is a list of ``(name, text, priority)``. Lower priority
    numbers are pinned first; ties are kept in input order. Returns the
    fit sections plus a stats dict (``total_tokens``, ``dropped``).
    """
    ordered = sorted(enumerate(sections), key=lambda pair: (pair[1][2], pair[0]))
    kept: list[tuple[int, str, str]] = []
    used = 0
    dropped: list[str] = []
    for original_idx, (name, text, _priority) in ordered:
        cost = estimate_tokens(text, chars_per_token=budget.chars_per_token)
        if used + cost <= budget.max_tokens:
            kept.append((original_idx, name, text))
            used += cost
        else:
            dropped.append(name)
    kept.sort(key=lambda triple: triple[0])
    return (
        [(name, text) for _idx, name, text in kept],
        {"total_tokens": used, "dropped_count": len(dropped), "dropped_names": dropped},
    )
