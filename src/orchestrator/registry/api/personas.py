"""Personas + skills read API (unified UI — P1a).

Surfaces the persona/skill system over the consolidated ``/v1`` API:

- ``GET /v1/personas`` → the published personas (role, skills, workflow slot, model).
- ``GET /v1/skills``   → the native skill catalog (guidance, provenance, vetting).

Read-only and authenticated (session cookie or X-API-Key). Typed response models
keep the OpenAPI contract accurate for a future generated TS client (spec §6).
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict

from orchestrator.catalog.catalog import default_catalog
from orchestrator.catalog.models import CapabilityKind
from orchestrator.catalog.skills import default_skills
from orchestrator.catalog.vetting import evaluate_vetting
from orchestrator.personas.registry import ALL_PERSONAS
from orchestrator.registry.api.deps import PrincipalDep

router = APIRouter(prefix="/v1", tags=["personas"])


class PersonaSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    version: str
    role: str
    skills: list[str]
    workflow_slot: str
    model: str


class SkillSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    guidance: str
    origin: str  # native | claude-skill | claude-subagent | codex-agent
    pin: str
    vetting: str  # approved | unvetted | failed
    phases: list[str]  # codegen phase(s) this skill conditions (implement/author_tests/refine)
    status: str  # active = planner-selectable (catalog-wired); candidate = pending measurement
    score: float | None = None  # measured held-out acceptance, when promoted with evidence


class PersonaListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[PersonaSummary]


class SkillListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[SkillSummary]


@router.get("/personas", response_model=PersonaListResponse)
async def list_personas(_principal: PrincipalDep) -> PersonaListResponse:
    return PersonaListResponse(
        items=[
            PersonaSummary(
                id=p.metadata.id,
                version=p.metadata.version,
                role=p.spec.role,
                skills=list(p.spec.skills),
                workflow_slot=p.spec.workflow_slot,
                model=p.spec.model,
            )
            for p in ALL_PERSONAS
        ]
    )


@router.get("/skills", response_model=SkillListResponse)
async def list_skills(_principal: PrincipalDep) -> SkillListResponse:
    # A skill is "active" when it's wired into the catalog as a SKILL capability the
    # planner can select; otherwise it's a "candidate" — defined and available but
    # inert until the persona-skill measurement promotes it. Promoted entries carry
    # their measured held-out acceptance in payload["eval"] (the evidence record).
    skill_caps = {c.id: c for c in default_catalog().all() if c.kind is CapabilityKind.SKILL}
    items: list[SkillSummary] = []
    for s in default_skills():
        cap = skill_caps.get(s.id)
        eval_ev = (cap.payload.get("eval") if cap else None) or {}
        items.append(
            SkillSummary(
                id=s.id,
                guidance=s.guidance,
                origin=s.provenance.origin.value,
                pin=s.provenance.pin,
                vetting=evaluate_vetting(s).value,
                phases=list(s.phases),
                status="active" if cap is not None else "candidate",
                score=eval_ev.get("achieved"),
            )
        )
    return SkillListResponse(items=items)
