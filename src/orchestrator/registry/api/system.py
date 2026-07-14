"""System readiness API (Phase A3): the `doctor` env checks + DB probe over HTTP.

``GET /v1/system/readiness`` runs the same environment checks as
``orchestrator doctor`` (variable **presence** only — never values, so no secret
leaks) plus a live database probe (the `/readyz` check), and reports an overall
``ok``. The `/app/system` page renders it as a status dashboard.
"""

from __future__ import annotations

import os

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict
from sqlalchemy import text

from orchestrator.registry.api.deps import PrincipalDep

router = APIRouter(prefix="/v1/system", tags=["system"])


class CheckOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    passed: bool
    optional: bool  # an unset optional group: non-blocking, shown as "skipped"
    detail: str


class ReadinessResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: bool  # every required check passes AND the database is reachable
    db_ready: bool
    db_detail: str
    checks: list[CheckOut]


async def _db_probe(request: Request) -> tuple[bool, str]:
    engine = getattr(request.app.state, "engine", None)
    if engine is None:
        return False, "database engine not initialised"
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001 — any failure means not-ready, surface it
        return False, f"unreachable: {exc}"
    return True, "reachable"


@router.get("/readiness", response_model=ReadinessResponse)
async def readiness(request: Request, _principal: PrincipalDep) -> ReadinessResponse:
    from orchestrator.doctor import run_env_checks

    checks = [
        CheckOut(name=c.name, passed=c.passed, optional=c.optional, detail=c.detail) for c in run_env_checks()
    ]
    db_ready, db_detail = await _db_probe(request)
    ok = db_ready and all(c.passed for c in checks)
    return ReadinessResponse(ok=ok, db_ready=db_ready, db_detail=db_detail, checks=checks)


# --------------------------------------------------------------------------- #
# Advanced / experimental capability flags (Phase E3)
# --------------------------------------------------------------------------- #
class FeatureFlag(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str  # the env var that gates it
    name: str
    enabled: bool
    kind: str  # loop | governance | memory | spine
    detail: str


class AdvancedResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    features: list[FeatureFlag]


# (env var, display name, kind, truthy-values?, detail). ``truthy`` flags are on
# when the value is 1/true/yes; ``present`` flags are on when the var is set at
# all (a URL/topology). We report on/off only — never the value — so no secret
# or endpoint leaks.
_FLAGS: tuple[tuple[str, str, str, str, str], ...] = (
    (
        "SDLC_AGENTIC_CODEGEN",
        "Agentic codegen loop (ReAct)",
        "loop",
        "truthy",
        "Multi-step tool-using codegen. Off by default; the single-shot path is the default until "
        "it's proven at scale. Its step trace is OpenTelemetry-only, not stored.",
    ),
    (
        "SDLC_AGENTIC_POLICY",
        "Per-tool-call policy",
        "governance",
        "truthy",
        "Allow/deny/require-approval on each agentic tool call. Traced (OTel), not persisted to the "
        "audit log.",
    ),
    (
        "ORCHESTRATOR_SEMANTIC_MEMORY",
        "Cross-run memory writes",
        "memory",
        "truthy",
        "When on, runs consolidate conventions/pitfalls into the queryable memory store (see the "
        "Cross-run memory page). Off by default.",
    ),
    (
        "SPINE_ONTOMESH_URL",
        "Ontomesh grounding (Seam 1)",
        "spine",
        "present",
        "Grounds codegen against an external ontomesh service. Experimental; inactive unless the "
        "service URL is configured.",
    ),
    (
        "SPINE_INFODRIFT_URL",
        "Infodrift → drift (Seam 2)",
        "spine",
        "present",
        "Registers shipped units with an external infodrift service for drift detection. Experimental; "
        "also needs a deploy topology.",
    ),
)


@router.get("/advanced", response_model=AdvancedResponse)
async def advanced(_principal: PrincipalDep) -> AdvancedResponse:
    """Which gated/experimental subsystems are enabled (by env-var presence only,
    never values). These have no stored history to browse — this reports whether
    each is wired, and how it's triggered/traced."""
    features: list[FeatureFlag] = []
    for key, name, kind, mode, detail in _FLAGS:
        raw = os.getenv(key, "")
        enabled = bool(raw) if mode == "present" else raw.lower() in ("1", "true", "yes")
        features.append(FeatureFlag(key=key, name=name, enabled=enabled, kind=kind, detail=detail))
    return AdvancedResponse(features=features)
