"""Persona → skills binding (persona + skill system — Phase 2).

A persona (an ``AgentTemplate``) lists the skill ids it wants. Resolving those to
actual guidance goes **through the vetting gate**: only approved skills contribute.
Native skills are approved by construction; imported skills the persona references
are included only once they've cleared their eval bar (``catalog.vetting``). This is
the seam that lets measured imported skills slot into a persona without editing the
persona — and that the run-driver (Phase 2b) calls to assemble the prompt.
"""

from __future__ import annotations

from collections.abc import Mapping

from orchestrator.catalog.skills import Skill, default_skills
from orchestrator.catalog.vetting import approved_skills
from orchestrator.registry.agent_template import AgentTemplate


def _resolve(
    skill_ids: list[str],
    available: list[Skill] | None,
    scores: Mapping[str, float] | None,
) -> list[Skill]:
    """Skill ids → Skill artifacts that resolve and pass vetting, in listed order."""
    by_id = {s.id: s for s in (available if available is not None else list(default_skills()))}
    wanted = [by_id[sid] for sid in skill_ids if sid in by_id]
    return approved_skills(wanted, scores)


def resolve_persona_skills(
    persona: AgentTemplate,
    *,
    available: list[Skill] | None = None,
    scores: Mapping[str, float] | None = None,
) -> list[Skill]:
    """The persona's referenced skills that resolve and pass vetting, in listed order.

    ``available`` defaults to the native skill set; ``scores`` (``{eval_id: score}``)
    gate imported skills. Unknown ids and unvetted imports are dropped.
    """
    return _resolve(persona.spec.skills, available, scores)


def persona_skill_guidance(
    persona: AgentTemplate,
    *,
    available: list[Skill] | None = None,
    scores: Mapping[str, float] | None = None,
) -> list[str]:
    """The guidance fragments a persona contributes (vetting-gated, in listed order)."""
    return [s.guidance for s in resolve_persona_skills(persona, available=available, scores=scores)]


def persona_guidance_for_selection(
    persona: AgentTemplate,
    selected_ids: list[str],
    *,
    available: list[Skill] | None = None,
    scores: Mapping[str, float] | None = None,
) -> list[str]:
    """Guidance for the persona's skills that were *also* selected for this run.

    The run-driver seam (Phase 2b): the capability plan selects skills by project
    profile (``selected_ids``); this narrows them to the persona's endorsed set and
    gates the result through vetting — so a run gets exactly the persona-endorsed,
    profile-relevant, approved skills, in the persona's listed order.
    """
    selected = set(selected_ids)
    scoped = [sid for sid in persona.spec.skills if sid in selected]
    return [s.guidance for s in _resolve(scoped, available, scores)]


__all__ = [
    "persona_guidance_for_selection",
    "persona_skill_guidance",
    "resolve_persona_skills",
]
