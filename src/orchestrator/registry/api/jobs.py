"""In-process capability job runner + the ``/v1/jobs`` read surface (Phase 0).

Comprehension capabilities (``understand`` / ``state`` / ``pkg``) run seconds to
minutes on a cache miss, so a blocking request is wrong. But they're
deterministic, no-LLM, read-only analysis — they don't need Temporal's
durability. So they run **in the API process**: a background asyncio task drives
the (CPU-bound) work in a worker thread and emits progress the same way SDLC
runs do — as append-only ``audit_log`` rows. That means ``/v1/stream`` tails them
unchanged (``?run_id=<job_id>``); this module adds the matching list / status /
artifact-download surface, deriving state from the audit log exactly like
``/v1/runs`` derives run state.

The deliverable (markdown / sqlite / json) is stored via the shared
``ArtifactStore`` under a ``job/<job_id>/`` key and streamed back by
``GET /v1/jobs/{id}/artifact``. Local ``up`` uses the in-memory store, so the
runner and the download route — one process — share it with no MinIO/S3.
"""

from __future__ import annotations

import asyncio
import contextlib
import queue
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

from orchestrator.registry.api.deps import PrincipalDep, SessionDep
from orchestrator.registry.db.models import AuditLogRow
from orchestrator.registry.repositories import AuditLogRepo
from orchestrator.runtime import artifact_store_from_env, make_job_artifact_id

router = APIRouter(prefix="/v1/jobs", tags=["jobs"])

RESOURCE_TYPE = "capability"
# Terminal audit actions → job state (first match on a newest-first scan wins).
_TERMINAL_STATE = {
    "capability_completed": "completed",
    "capability_failed": "failed",
}
_MAX_SCAN = 4000

# A capability adapter is a *sync* callable that does the work and returns the
# deliverable. It's handed a ``log`` callback for coarse progress (each call
# becomes one streamed ``capability_progress`` event). Runs in a worker thread.
ProgressLog = Callable[[str], None]
CapabilityAdapter = Callable[[ProgressLog], "CapabilityResult"]


@dataclass(frozen=True)
class CapabilityResult:
    """What a capability adapter returns: the deliverable bytes + how to serve it."""

    body: bytes
    content_type: str
    filename: str
    summary: dict[str, Any] = field(default_factory=dict)


# Strong refs to in-flight background tasks so the loop doesn't GC them mid-run.
_RUNNING: set[asyncio.Task[None]] = set()


def _artifact_store(app: Any) -> Any:
    """The process-shared artifact store (set once in the app lifespan)."""
    return getattr(app.state, "artifact_store", None) or artifact_store_from_env()


async def _audit(
    factory: Any, *, tenant: str, actor: str, job_id: str, action: str, after: dict[str, Any]
) -> None:
    async with factory() as session:
        await AuditLogRepo(session).write(
            actor=actor,
            action=action,
            resource_type=RESOURCE_TYPE,
            resource_id=job_id,
            after=after,
            trace_id=job_id,
            tenant_id=tenant,
        )
        await session.commit()


async def start_capability_job(
    *, app: Any, tenant: str, actor: str, kind: str, adapter: CapabilityAdapter, params: dict[str, Any]
) -> str:
    """Mint a job id, record ``capability_started``, and run the adapter in the
    background. Returns immediately with the job id; the caller streams progress
    via ``/v1/stream?run_id=<job_id>`` and fetches the result from ``/v1/jobs``."""
    job_id = uuid4().hex[:16]
    factory = app.state.session_factory
    await _audit(
        factory,
        tenant=tenant,
        actor=actor,
        job_id=job_id,
        action="capability_started",
        after={"kind": kind, "params": params},
    )
    store = _artifact_store(app)
    task = asyncio.create_task(_run(factory, store, tenant, actor, job_id, kind, adapter))
    _RUNNING.add(task)
    task.add_done_callback(_RUNNING.discard)
    return job_id


async def _run(
    factory: Any, store: Any, tenant: str, actor: str, job_id: str, kind: str, adapter: CapabilityAdapter
) -> None:
    """Drive one capability to completion: stream progress, store the artifact,
    write the terminal row. Any failure becomes a ``capability_failed`` event —
    a background job must never crash the event loop."""
    q: queue.Queue[str | None] = queue.Queue()

    def log(message: str) -> None:
        q.put(str(message))

    async def drain() -> None:
        while True:
            message = await asyncio.to_thread(q.get)
            if message is None:
                return
            await _audit(
                factory,
                tenant=tenant,
                actor=actor,
                job_id=job_id,
                action="capability_progress",
                after={"kind": kind, "message": message},
            )

    drain_task = asyncio.create_task(drain())
    try:
        result = await asyncio.to_thread(adapter, log)
    except Exception as exc:  # noqa: BLE001 — surface as a failed job, never propagate
        q.put(None)
        with contextlib.suppress(Exception):
            await drain_task
        await _audit(
            factory,
            tenant=tenant,
            actor=actor,
            job_id=job_id,
            action="capability_failed",
            after={"kind": kind, "error": str(exc)},
        )
        return

    q.put(None)
    with contextlib.suppress(Exception):
        await drain_task

    artifact_id = make_job_artifact_id(job_id=job_id, filename=result.filename)
    try:
        await store.put_bytes(artifact_id, result.body, result.content_type)
    except Exception as exc:  # noqa: BLE001 — a storage failure is still a job failure
        await _audit(
            factory,
            tenant=tenant,
            actor=actor,
            job_id=job_id,
            action="capability_failed",
            after={"kind": kind, "error": f"could not store artifact: {exc}"},
        )
        return

    await _audit(
        factory,
        tenant=tenant,
        actor=actor,
        job_id=job_id,
        action="capability_completed",
        after={
            "kind": kind,
            "artifact_id": artifact_id,
            "content_type": result.content_type,
            "filename": result.filename,
            "summary": result.summary,
        },
    )


# --------------------------------------------------------------------------- #
# Read surface
# --------------------------------------------------------------------------- #
class JobSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: str
    kind: str
    state: str  # running | completed | failed
    started_at: str
    updated_at: str
    events: int
    summary: dict[str, Any] | None = None
    error: str | None = None
    has_artifact: bool = False


class JobListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[JobSummary]


def _summarize(job_id: str, rows_newest_first: list[AuditLogRow]) -> JobSummary:
    newest = rows_newest_first[0]
    oldest = rows_newest_first[-1]
    state = "running"
    terminal: AuditLogRow | None = None
    for row in rows_newest_first:  # newest-first → first terminal action wins
        mapped = _TERMINAL_STATE.get(row.action)
        if mapped is not None:
            state = mapped
            terminal = row
            break
    after = (terminal.after_json if terminal else None) or {}
    started = oldest.after_json or {}
    return JobSummary(
        job_id=job_id,
        kind=str(after.get("kind") or started.get("kind") or ""),
        state=state,
        started_at=_iso(oldest),
        updated_at=_iso(newest),
        events=len(rows_newest_first),
        summary=after.get("summary") if state == "completed" else None,
        error=after.get("error") if state == "failed" else None,
        has_artifact=bool(after.get("artifact_id")) if state == "completed" else False,
    )


async def _fetch_job_rows(session: SessionDep, tenant: str, job_id: str) -> list[AuditLogRow]:
    stmt = (
        select(AuditLogRow)
        .where(
            AuditLogRow.resource_type == RESOURCE_TYPE,
            AuditLogRow.tenant_id == tenant,
            AuditLogRow.resource_id == job_id,
        )
        .order_by(AuditLogRow.timestamp.desc())
        .limit(_MAX_SCAN)
    )
    return list((await session.execute(stmt)).scalars().all())


@router.get("", response_model=JobListResponse)
async def list_jobs(session: SessionDep, principal: PrincipalDep, limit: int = 50) -> JobListResponse:
    """List recent capability jobs for the caller's tenant (most-recent first)."""
    limit = min(max(limit, 1), 200)
    stmt = (
        select(AuditLogRow)
        .where(AuditLogRow.resource_type == RESOURCE_TYPE, AuditLogRow.tenant_id == principal.tenant_id)
        .order_by(AuditLogRow.timestamp.desc())
        .limit(_MAX_SCAN)
    )
    rows = list((await session.execute(stmt)).scalars().all())
    by_job: dict[str, list[AuditLogRow]] = {}
    for row in rows:
        by_job.setdefault(row.resource_id, []).append(row)
    summaries = [_summarize(job_id, events) for job_id, events in by_job.items()]
    summaries.sort(key=lambda s: s.updated_at, reverse=True)
    return JobListResponse(items=summaries[:limit])


@router.get("/{job_id}", response_model=JobSummary)
async def get_job(job_id: str, session: SessionDep, principal: PrincipalDep) -> JobSummary:
    rows = await _fetch_job_rows(session, principal.tenant_id, job_id)
    if not rows:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"no job {job_id!r}")
    return _summarize(job_id, rows)


@router.get("/{job_id}/artifact")
async def get_job_artifact(
    job_id: str, request: Request, session: SessionDep, principal: PrincipalDep
) -> Response:
    """Download a completed job's deliverable with its stored content type."""
    rows = await _fetch_job_rows(session, principal.tenant_id, job_id)
    if not rows:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"no job {job_id!r}")
    completed = next((r for r in rows if r.action == "capability_completed"), None)
    if completed is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=f"job {job_id!r} has no artifact yet"
        )
    after = completed.after_json or {}
    artifact_id = str(after.get("artifact_id") or "")
    content_type = str(after.get("content_type") or "application/octet-stream")
    filename = str(after.get("filename") or "artifact")
    try:
        body = await _artifact_store(request.app).get_bytes(artifact_id)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return Response(
        content=body,
        media_type=content_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _iso(row: AuditLogRow) -> str:
    return row.timestamp.isoformat() if row.timestamp else ""
