from __future__ import annotations

import pytest

from orchestrator.runtime.specialist import (
    ClaimSummary,
    CompletionStatus,
    ContextBudget,
    Handoff,
    SpecialistReturn,
    estimate_tokens,
    fit_to_budget,
)


def test_specialist_return_minimal_valid() -> None:
    sr = SpecialistReturn(
        specialist_id="n_analyst",
        artifact_id="art_1",
        summary="ARR grew 12%.",
        confidence=0.85,
    )
    assert sr.completion_status is CompletionStatus.SUCCESS
    assert sr.key_claims == []


def test_specialist_return_caps_key_claims() -> None:
    claims = [
        ClaimSummary(id=f"c_{i}", statement=f"claim {i}", artifact_id=f"art_{i}", confidence=0.5)
        for i in range(8)
    ]
    sr = SpecialistReturn(specialist_id="x", artifact_id="a", summary="s", confidence=0.5, key_claims=claims)
    assert len(sr.key_claims) == 8

    too_many = claims + [ClaimSummary(id="c_9", statement="extra", artifact_id="art_9", confidence=0.5)]
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        SpecialistReturn(
            specialist_id="x",
            artifact_id="a",
            summary="s",
            confidence=0.5,
            key_claims=too_many,
        )


def test_handoff_defaults() -> None:
    h = Handoff(from_specialist_id="a", to_specialist_id="b")
    assert h.artifact_ids == []
    assert h.glossary_terms == {}
    assert h.narrative_context == ""


def test_estimate_tokens_is_deterministic_and_nonzero() -> None:
    assert estimate_tokens("") == 1
    assert estimate_tokens("hello") == max(1, len("hello") // 4)
    assert estimate_tokens("a" * 400) == 100


def test_fit_to_budget_pins_by_priority() -> None:
    budget = ContextBudget(max_tokens=20)
    sections = [
        ("preamble", "x" * 80, 5),  # 20 tokens
        ("glossary", "g" * 40, 0),  # 10 tokens — pinned first
        ("handoffs", "h" * 40, 1),  # 10 tokens — fits after glossary
        ("trailing", "z" * 40, 9),  # 10 tokens — over budget, dropped
    ]
    kept, stats = fit_to_budget(sections, budget)
    names = [name for name, _ in kept]
    assert "glossary" in names
    assert "handoffs" in names
    assert "trailing" not in names
    assert stats["dropped_count"] >= 1
    assert stats["total_tokens"] <= budget.max_tokens


def test_fit_to_budget_preserves_input_order_after_priority_fit() -> None:
    budget = ContextBudget(max_tokens=100)
    sections = [
        ("first", "a" * 40, 2),
        ("second", "b" * 40, 1),  # higher priority
        ("third", "c" * 40, 3),
    ]
    kept, _ = fit_to_budget(sections, budget)
    assert [n for n, _ in kept] == ["first", "second", "third"]
