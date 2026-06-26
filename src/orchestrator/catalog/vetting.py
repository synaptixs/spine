"""Eval-gated vetting for skills (persona + skill system — Phase 1).

The discipline that keeps imported skills from becoming ungoverned prompt-bloat:
a skill is **selectable only when vetted**. Native (in-repo) skills are trusted by
construction; an *imported* skill must declare an eval bar (``SkillEval`` gates)
and clear it before it can be selected.

The gate is a pure function over a ``{eval_id: score}`` map — it does not run evals
itself, keeping the catalog decoupled from the eval harness. Callers build the map
from a ``Scorecard`` (``metrics()["acceptance_rate"]`` is the natural score) and
pass it in. ``score`` is in ``[0, 1]`` and compared against each gate's ``min_score``.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import Enum

from orchestrator.catalog.skills import Skill, SkillOrigin


class VettingStatus(str, Enum):
    """Whether a skill may be selected."""

    APPROVED = "approved"  # native, or imported + cleared all its eval gates
    UNVETTED = "unvetted"  # no evidence yet — imported with no gates, or gates unmeasured
    FAILED = "failed"  # measured, but fell short of a gate's min_score


def evaluate_vetting(skill: Skill, scores: Mapping[str, float] | None = None) -> VettingStatus:
    """Vetting status of ``skill`` given measured eval ``scores`` (``{eval_id: score}``)."""
    scores = scores or {}
    if skill.provenance.origin is SkillOrigin.NATIVE:
        return VettingStatus.APPROVED
    if not skill.evals:
        return VettingStatus.UNVETTED  # imported skills must declare a bar to be trusted
    fell_short = False
    for gate in skill.evals:
        if gate.id not in scores:
            return VettingStatus.UNVETTED  # not measured yet
        if scores[gate.id] < gate.min_score:
            fell_short = True
    return VettingStatus.FAILED if fell_short else VettingStatus.APPROVED


def is_approved(skill: Skill, scores: Mapping[str, float] | None = None) -> bool:
    return evaluate_vetting(skill, scores) is VettingStatus.APPROVED


def approved_skills(skills: list[Skill], scores: Mapping[str, float] | None = None) -> list[Skill]:
    """The subset of ``skills`` that are selectable (vetting passed)."""
    return [s for s in skills if is_approved(s, scores)]


__all__ = [
    "VettingStatus",
    "approved_skills",
    "evaluate_vetting",
    "is_approved",
]
