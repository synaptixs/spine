"""Eval-gated vetting (persona+skill Phase 1): native trusted, imports must earn it."""

from __future__ import annotations

from orchestrator.catalog.skills import Skill, SkillEval, SkillOrigin, SkillProvenance, default_skills
from orchestrator.catalog.vetting import (
    VettingStatus,
    approved_skills,
    evaluate_vetting,
    is_approved,
)


def _imported(skill_id: str, evals: tuple[SkillEval, ...] = ()) -> Skill:
    return Skill(
        skill_id,
        "guidance",
        provenance=SkillProvenance(origin=SkillOrigin.CLAUDE_SKILL, source="x", pin="v1"),
        evals=evals,
    )


def test_native_skills_are_approved_without_evals() -> None:
    for s in default_skills():
        assert evaluate_vetting(s) is VettingStatus.APPROVED


def test_imported_without_evals_is_unvetted() -> None:
    # Supply-chain discipline: an import with no declared bar is never auto-trusted.
    assert evaluate_vetting(_imported("test-strategy")) is VettingStatus.UNVETTED


def test_imported_with_unmeasured_evals_is_unvetted() -> None:
    s = _imported("test-strategy", (SkillEval("e1", 0.7),))
    assert evaluate_vetting(s, {}) is VettingStatus.UNVETTED  # no score yet


def test_imported_below_bar_fails() -> None:
    s = _imported("test-strategy", (SkillEval("e1", 0.7),))
    assert evaluate_vetting(s, {"e1": 0.5}) is VettingStatus.FAILED


def test_imported_meeting_bar_is_approved() -> None:
    s = _imported("test-strategy", (SkillEval("e1", 0.7),))
    assert evaluate_vetting(s, {"e1": 0.7}) is VettingStatus.APPROVED
    assert is_approved(s, {"e1": 0.9})


def test_multiple_gates_all_must_pass() -> None:
    s = _imported("sec", (SkillEval("e1", 0.7), SkillEval("e2", 0.8)))
    assert evaluate_vetting(s, {"e1": 0.9, "e2": 0.75}) is VettingStatus.FAILED
    assert evaluate_vetting(s, {"e1": 0.9, "e2": 0.85}) is VettingStatus.APPROVED


def test_approved_skills_filters_out_unvetted_imports() -> None:
    natives = list(default_skills())
    unvetted = _imported("unproven")
    proven = _imported("proven", (SkillEval("e1", 0.6),))
    out = approved_skills([*natives, unvetted, proven], {"e1": 0.6})
    ids = {s.id for s in out}
    assert "proven" in ids and "unproven" not in ids
    assert {n.id for n in natives} <= ids  # natives always pass
