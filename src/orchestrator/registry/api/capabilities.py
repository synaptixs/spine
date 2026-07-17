"""Capability API layer (Phase 0) — the CLI-only capabilities over HTTP.

The CLI is a thin shell over decoupled services (``orchestrator.knowledge`` /
``.pkg`` / ``.catalog``); these endpoints call the same services so the web UI
reaches repo intelligence without shelling out. Two shapes:

* **Fast + synchronous** — ``profile`` / ``catalog`` / ``catalog plan`` are
  filesystem-light and return their result inline.
* **Long-running → a job** — ``understand`` / ``state`` / ``pkg extract|export``
  can re-extract a whole repo, so they start a background capability job
  (``jobs.py``) and return ``202 {job_id}``; the client streams progress on
  ``/v1/stream?run_id=<job_id>`` and downloads the deliverable from ``/v1/jobs``.

A repo is named by a **local path** (under the configured workspace root) **or a
git URL** (github/bitbucket/gitlab/enterprise, host-allow-listed, cloned on
demand — ``workspace.resolve_repo_source``). URL clones happen off the event loop
(a job thread, or ``asyncio.to_thread`` for the sync endpoints) and are cleaned up.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field

from orchestrator.registry.api.deps import PrincipalDep
from orchestrator.registry.api.jobs import CapabilityResult, ProgressLog, start_capability_job
from orchestrator.registry.api.workspace import (
    RepoPathError,
    RepoSource,
    RepoSourceError,
    materialize_repo_source,
    resolve_repo_source,
)

router = APIRouter(prefix="/v1/capabilities", tags=["capabilities"])

_REPO_FIELD = Field(default=".", description="Local path (under the workspace root) or a git URL to clone.")


def _source(request: Request, repo: str | None) -> RepoSource:
    """Validate a repo spec into a RepoSource (no clone yet). 400 on a bad path
    or a disallowed URL host."""
    try:
        return resolve_repo_source(repo, request.app.state.settings)
    except (RepoPathError, RepoSourceError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


async def _in_repo(source: RepoSource, work: Any) -> Any:
    """Run ``work(path)`` against the materialised repo off the event loop (so a
    URL clone doesn't block). Maps a clone failure to 502."""

    def _run() -> Any:
        with materialize_repo_source(source) as path:
            return work(path)

    try:
        return await asyncio.to_thread(_run)
    except RepoSourceError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


# --------------------------------------------------------------------------- #
# Fast, synchronous capabilities
# --------------------------------------------------------------------------- #
class ProfileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repo: str = _REPO_FIELD
    intent: str | None = Field(default=None, description="Optional intent title for task-type inference.")


class ProfileResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile: dict[str, Any]


@router.post("/profile", response_model=ProfileResponse)
async def profile(body: ProfileRequest, request: Request, _principal: PrincipalDep) -> ProfileResponse:
    from orchestrator.catalog.profile import ProjectProfile

    source = _source(request, body.repo)
    result = await _in_repo(
        source, lambda repo: dict(ProjectProfile.from_repo(repo, intent_title=body.intent).to_dict())
    )
    return ProfileResponse(profile=result)


class CapabilityInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    kind: str
    summary: str


class CatalogResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[CapabilityInfo]


@router.get("/catalog", response_model=CatalogResponse)
async def catalog(_principal: PrincipalDep) -> CatalogResponse:
    """The static capability catalog — what Spine can do (independent of a repo)."""
    from orchestrator.catalog.catalog import default_catalog

    items = [
        CapabilityInfo(id=cap.id, kind=cap.kind.value, summary=cap.summary) for cap in default_catalog().all()
    ]
    return CatalogResponse(items=items)


class PlanResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan: dict[str, Any]
    summary_lines: list[str]


@router.post("/plan", response_model=PlanResponse)
async def plan(body: ProfileRequest, request: Request, _principal: PrincipalDep) -> PlanResponse:
    """Plan the capabilities Spine would apply to this repo (``catalog plan``)."""
    from orchestrator.catalog.planner import plan_capabilities
    from orchestrator.catalog.profile import ProjectProfile

    source = _source(request, body.repo)

    def work(repo: Path) -> tuple[dict[str, Any], list[str]]:
        prof = ProjectProfile.from_repo(repo, intent_title=body.intent)
        cap_plan = plan_capabilities(prof)
        return dict(cap_plan.to_dict()), cap_plan.summary_lines()

    plan_dict, lines = await _in_repo(source, work)
    return PlanResponse(plan=plan_dict, summary_lines=lines)


class MemoryBankFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    markdown: str


class MemoryBankResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    exists: bool  # False when the repo has no memory-bank/ yet (run understand first)
    files: list[MemoryBankFile]


@router.get("/memory-bank", response_model=MemoryBankResponse)
async def memory_bank(request: Request, _principal: PrincipalDep, repo: str = ".") -> MemoryBankResponse:
    """Read a repo's committed knowledge base (``episteme/*.md``, what ``understand`` writes).

    Read-only and scoped to the bank dir under the resolved repo. Returns
    ``exists=False`` (not 404) when the repo hasn't been analysed yet."""
    source = _source(request, repo)

    def work(root: Path) -> list[tuple[str, str]] | None:
        from orchestrator.knowledge.understand import existing_bank_dir

        mb_dir = existing_bank_dir(root)
        if not mb_dir.is_dir():
            return None
        return [(p.name, p.read_text(encoding="utf-8")) for p in sorted(mb_dir.glob("*.md"))]

    files = await _in_repo(source, work)
    if files is None:
        return MemoryBankResponse(exists=False, files=[])
    return MemoryBankResponse(exists=True, files=[MemoryBankFile(name=n, markdown=m) for n, m in files])


# --------------------------------------------------------------------------- #
# Long-running capabilities → background jobs
# --------------------------------------------------------------------------- #
class JobStartResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: str
    stream: str  # /v1/stream?run_id=<job_id>


def _started(job_id: str) -> JobStartResponse:
    return JobStartResponse(job_id=job_id, stream=f"/v1/stream?run_id={job_id}")


class UnderstandRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repo: str = _REPO_FIELD
    refresh: bool = Field(default=False, description="Bypass the commit-keyed extraction cache.")
    dialect: str | None = Field(default=None, description="Pinned SQL dialect (skips the cache).")


@router.post("/understand", response_model=JobStartResponse, status_code=status.HTTP_202_ACCEPTED)
async def understand(body: UnderstandRequest, request: Request, principal: PrincipalDep) -> JobStartResponse:
    """Build the code-true memory bank for a repo (``understand``), as a job."""
    source = _source(request, body.repo)

    def adapter(log: ProgressLog) -> CapabilityResult:
        from orchestrator.knowledge import build_memory_bank

        with materialize_repo_source(source, log=log) as repo:
            result = build_memory_bank(repo, refresh=body.refresh, sql_dialect=body.dialect, log=log)
            summary = {k: result.get(k) for k in ("files", "greenfield", "summary") if k in result}
            body_bytes = json.dumps(result, default=str, ensure_ascii=False).encode("utf-8")
            return CapabilityResult(body_bytes, "application/json", "understand.json", summary)

    job_id = await start_capability_job(
        app=request.app,
        tenant=principal.tenant_id,
        actor=principal.id,
        kind="understand",
        adapter=adapter,
        params={"repo": source.display, "refresh": body.refresh, "dialect": body.dialect},
    )
    return _started(job_id)


class StateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repo: str = _REPO_FIELD
    lens: str = Field(default="developer", description="developer | stakeholder")
    refresh: bool = Field(default=False, description="Bypass the commit-keyed extraction cache.")
    dialect: str | None = Field(default=None, description="Pinned SQL dialect (skips the cache).")


@router.post("/state", response_model=JobStartResponse, status_code=status.HTTP_202_ACCEPTED)
async def state(body: StateRequest, request: Request, principal: PrincipalDep) -> JobStartResponse:
    """Render the current-state report for a repo (``state``), as a job."""
    if body.lens not in ("developer", "stakeholder"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="lens must be developer or stakeholder"
        )
    source = _source(request, body.repo)

    def adapter(log: ProgressLog) -> CapabilityResult:
        from orchestrator.knowledge.current_state import build_current_state

        with materialize_repo_source(source, log=log) as repo:
            log(f"rendering current-state ({body.lens} lens)")
            markdown = build_current_state(
                repo, lens=body.lens, refresh=body.refresh, sql_dialect=body.dialect
            )
            summary = {"lens": body.lens, "bytes": len(markdown.encode("utf-8"))}
            return CapabilityResult(markdown.encode("utf-8"), "text/markdown", "current-state.md", summary)

    job_id = await start_capability_job(
        app=request.app,
        tenant=principal.tenant_id,
        actor=principal.id,
        kind="state",
        adapter=adapter,
        params={"repo": source.display, "lens": body.lens, "refresh": body.refresh, "dialect": body.dialect},
    )
    return _started(job_id)


class PkgRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repo: str = _REPO_FIELD
    dialect: str | None = Field(default=None, description="Pinned SQL dialect.")


@router.post("/pkg/extract", response_model=JobStartResponse, status_code=status.HTTP_202_ACCEPTED)
async def pkg_extract(body: PkgRequest, request: Request, principal: PrincipalDep) -> JobStartResponse:
    """Extract the Product Knowledge Graph and return its overview (``pkg extract``)."""
    source = _source(request, body.repo)

    def adapter(log: ProgressLog) -> CapabilityResult:
        from orchestrator.pkg import RepoCodeExtractor
        from orchestrator.pkg.overview import build_overview

        with materialize_repo_source(source, log=log) as repo:
            log("extracting product knowledge graph")
            batch = RepoCodeExtractor(sql_dialect=body.dialect).extract(repo)
            log("aggregating a module-level overview")
            overview = build_overview(batch)
            body_bytes = json.dumps(overview, default=str, ensure_ascii=False).encode("utf-8")
            # The audit summary carries the headline counts; the artifact carries
            # the full (bounded) module graph the B4 explorer renders.
            return CapabilityResult(body_bytes, "application/json", "pkg-overview.json", overview["summary"])

    job_id = await start_capability_job(
        app=request.app,
        tenant=principal.tenant_id,
        actor=principal.id,
        kind="pkg-extract",
        adapter=adapter,
        params={"repo": source.display, "dialect": body.dialect},
    )
    return _started(job_id)


@router.post("/pkg/export", response_model=JobStartResponse, status_code=status.HTTP_202_ACCEPTED)
async def pkg_export(body: PkgRequest, request: Request, principal: PrincipalDep) -> JobStartResponse:
    """Extract the PKG and export it to a downloadable SQLite file (``pkg export``)."""
    source = _source(request, body.repo)

    def adapter(log: ProgressLog) -> CapabilityResult:
        from orchestrator.pkg import RepoCodeExtractor, export_sqlite

        with materialize_repo_source(source, log=log) as repo:
            log("extracting product knowledge graph")
            batch = RepoCodeExtractor(sql_dialect=body.dialect).extract(repo)
            with tempfile.TemporaryDirectory() as tmp:
                db_path = Path(tmp) / "pkg-facts.db"
                log("exporting facts to sqlite")
                counts = export_sqlite(batch, db_path)
                data = db_path.read_bytes()
            return CapabilityResult(data, "application/vnd.sqlite3", "pkg-facts.db", {"tables": dict(counts)})

    job_id = await start_capability_job(
        app=request.app,
        tenant=principal.tenant_id,
        actor=principal.id,
        kind="pkg-export",
        adapter=adapter,
        params={"repo": source.display, "dialect": body.dialect},
    )
    return _started(job_id)
