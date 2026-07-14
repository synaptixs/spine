"""Phase 0: the capability API layer + in-process job runner (/v1/jobs).

Drives the runner over an in-memory SQLite audit log (StaticPool so every
session shares the one connection) with an in-memory artifact store — the same
in-process shape local ``up`` uses. Stub adapters keep it fast/deterministic;
the real understand/state/pkg adapters are exercised by their own service tests.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import httpx
import pytest_asyncio
from sqlalchemy import Table, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from orchestrator.registry.api import jobs
from orchestrator.registry.api.app import create_app
from orchestrator.registry.api.config import Settings
from orchestrator.registry.api.jobs import CapabilityResult, ProgressLog, start_capability_job
from orchestrator.registry.db.models import AuditLogRow
from orchestrator.runtime.artifacts import InMemoryArtifactStore

_AUTH = {"X-API-Key": "dev-key"}


@pytest_asyncio.fixture
async def ctx(tmp_path: Path) -> AsyncIterator[SimpleNamespace]:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(cast(Table, AuditLogRow.__table__).create)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    app = create_app(Settings(workspace_root=str(tmp_path)))
    app.router.lifespan_context = None  # type: ignore[assignment]
    app.state.session_factory = factory
    store = InMemoryArtifactStore()
    app.state.artifact_store = store
    yield SimpleNamespace(app=app, factory=factory, store=store, tmp=tmp_path)
    await engine.dispose()


async def _drain() -> None:
    """Await all in-flight background job tasks."""
    tasks = list(jobs._RUNNING)
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


def _ok_adapter(body: bytes = b"hello") -> jobs.CapabilityAdapter:
    def adapter(log: ProgressLog) -> CapabilityResult:
        log("step 1")
        log("step 2")
        return CapabilityResult(body, "text/plain", "out.txt", {"note": "done"})

    return adapter


def _boom_adapter(log: ProgressLog) -> CapabilityResult:
    raise RuntimeError("extraction blew up")


async def _rows(ctx: SimpleNamespace, job_id: str) -> list[AuditLogRow]:
    async with ctx.factory() as s:
        stmt = select(AuditLogRow).where(AuditLogRow.resource_id == job_id).order_by(AuditLogRow.timestamp)
        return list((await s.execute(stmt)).scalars().all())


# --------------------------------------------------------------------------- #
# Runner mechanics
# --------------------------------------------------------------------------- #
async def test_job_runs_streams_progress_and_stores_artifact(ctx: SimpleNamespace) -> None:
    job_id = await start_capability_job(
        app=ctx.app, tenant="default", actor="tester", kind="demo", adapter=_ok_adapter(), params={"x": 1}
    )
    await _drain()

    actions = [r.action for r in await _rows(ctx, job_id)]
    assert actions[0] == "capability_started"
    assert actions.count("capability_progress") == 2  # one per log() call
    assert actions[-1] == "capability_completed"

    completed = next(r for r in await _rows(ctx, job_id) if r.action == "capability_completed")
    after = completed.after_json or {}
    artifact_id = after["artifact_id"]
    assert artifact_id == f"job/{job_id}/out.txt"
    assert after["summary"] == {"note": "done"}
    assert await ctx.store.get_bytes(artifact_id) == b"hello"


async def test_job_failure_is_recorded_not_raised(ctx: SimpleNamespace) -> None:
    job_id = await start_capability_job(
        app=ctx.app, tenant="default", actor="tester", kind="demo", adapter=_boom_adapter, params={}
    )
    await _drain()

    rows = await _rows(ctx, job_id)
    assert rows[-1].action == "capability_failed"
    assert "extraction blew up" in (rows[-1].after_json or {})["error"]
    assert not any(r.action == "capability_completed" for r in rows)


# --------------------------------------------------------------------------- #
# Read surface
# --------------------------------------------------------------------------- #
async def test_jobs_endpoints_list_status_and_download(ctx: SimpleNamespace) -> None:
    job_id = await start_capability_job(
        app=ctx.app,
        tenant="default",
        actor="tester",
        kind="state",
        adapter=_ok_adapter(b"# report\n"),
        params={},
    )
    await _drain()

    transport = httpx.ASGITransport(app=ctx.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test", headers=_AUTH) as client:
        listed = await client.get("/v1/jobs")
        assert listed.status_code == 200
        summary = {j["job_id"]: j for j in listed.json()["items"]}[job_id]
        assert summary["state"] == "completed" and summary["kind"] == "state" and summary["has_artifact"]

        one = await client.get(f"/v1/jobs/{job_id}")
        assert one.status_code == 200 and one.json()["summary"] == {"note": "done"}

        art = await client.get(f"/v1/jobs/{job_id}/artifact")
        assert art.status_code == 200
        assert art.content == b"# report\n"
        assert art.headers["content-type"].startswith("text/plain")
        assert "out.txt" in art.headers["content-disposition"]

        missing = await client.get("/v1/jobs/nope")
        assert missing.status_code == 404


async def test_artifact_409_before_completion(ctx: SimpleNamespace) -> None:
    # A started-but-not-completed job: write only the started row.
    await jobs._audit(
        ctx.factory,
        tenant="default",
        actor="t",
        job_id="pending1",
        action="capability_started",
        after={"kind": "demo"},
    )
    transport = httpx.ASGITransport(app=ctx.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test", headers=_AUTH) as client:
        resp = await client.get("/v1/jobs/pending1/artifact")
    assert resp.status_code == 409


async def test_jobs_requires_auth(ctx: SimpleNamespace) -> None:
    transport = httpx.ASGITransport(app=ctx.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/v1/jobs")
    assert resp.status_code == 401


# --------------------------------------------------------------------------- #
# Capability endpoints
# --------------------------------------------------------------------------- #
async def test_catalog_and_profile_are_synchronous(ctx: SimpleNamespace) -> None:
    transport = httpx.ASGITransport(app=ctx.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test", headers=_AUTH) as client:
        catalog = await client.get("/v1/capabilities/catalog")
        assert catalog.status_code == 200 and len(catalog.json()["items"]) > 0

        # tmp_path (the workspace root) is an empty dir → a valid, boring profile.
        profile = await client.post("/v1/capabilities/profile", json={"repo": "."})
        assert profile.status_code == 200 and "languages" in profile.json()["profile"]


async def test_capability_rejects_bad_repo_and_lens(ctx: SimpleNamespace) -> None:
    transport = httpx.ASGITransport(app=ctx.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test", headers=_AUTH) as client:
        escape = await client.post("/v1/capabilities/state", json={"repo": "../etc"})
        assert escape.status_code == 400  # workspace-root traversal rejected before any job

        bad_lens = await client.post("/v1/capabilities/state", json={"repo": ".", "lens": "wizard"})
        assert bad_lens.status_code == 400
