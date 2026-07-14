"""Run artifacts: the filesystem store (shared across processes) + /v1/runs/*/artifacts."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import Table
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from orchestrator.registry.api.app import create_app
from orchestrator.registry.api.config import Settings
from orchestrator.registry.db.models import AuditLogRow
from orchestrator.registry.repositories import AuditLogRepo
from orchestrator.runtime.artifacts import FilesystemArtifactStore

_AUTH = {"X-API-Key": "dev-key"}
RUN = "RUN-A"


# --------------------------------------------------------------------------- #
# Filesystem store
# --------------------------------------------------------------------------- #
async def test_fs_store_round_trip_and_prefix(tmp_path: Path) -> None:
    store = FilesystemArtifactStore(root=str(tmp_path))
    await store.put_bytes("run/R/comprehension/current-state.md", b"# hi\n", "text/markdown")
    await store.put_json("run/R/comprehension/comprehension.json", {"nodes": 3})
    assert await store.get_bytes("run/R/comprehension/current-state.md") == b"# hi\n"
    assert (await store.get_json("run/R/comprehension/comprehension.json"))["nodes"] == 3
    keys = store.list_prefix("run/R/comprehension")
    assert "run/R/comprehension/current-state.md" in keys
    with pytest.raises(LookupError):
        await store.get_bytes("run/R/missing.md")


async def test_fs_store_rejects_escape(tmp_path: Path) -> None:
    store = FilesystemArtifactStore(root=str(tmp_path / "root"))
    with pytest.raises(ValueError, match="escapes"):
        await store.put_bytes("../evil.txt", b"x")


# --------------------------------------------------------------------------- #
# Endpoint
# --------------------------------------------------------------------------- #
@pytest_asyncio.fixture
async def app_ctx(tmp_path: Path) -> AsyncIterator[SimpleNamespace]:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    async with engine.begin() as conn:
        await conn.run_sync(cast(Table, AuditLogRow.__table__).create)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    store = FilesystemArtifactStore(root=str(tmp_path / "artifacts"))
    # A comprehension artifact the worker "wrote", + the audit manifest that lists it.
    key = f"run/{RUN}/comprehension/current-state.md"
    await store.put_bytes(key, b"# Current State\n", "text/markdown")
    async with factory() as s:
        await AuditLogRepo(s).write(
            actor="worker",
            action="sdlc_repo_comprehended",
            resource_type="sdlc",
            resource_id=RUN,
            trace_id=RUN,
            after={"counts": {"nodes": 5}, "artifacts": {"current-state.md": key}},
        )
        # A design artifact the design wave "wrote", + its per-issue manifest.
        design_key = f"run/{RUN}/feature/SDLC-1/design.md"
        await store.put_bytes(design_key, b"# Design\n", "text/markdown")
        await AuditLogRepo(s).write(
            actor="worker",
            action="feature_designed",
            resource_type="sdlc",
            resource_id=RUN,
            trace_id=RUN,
            after={"issue_key": "SDLC-1", "artifacts": {"design.md": design_key}},
        )
        await s.commit()

    app = create_app(Settings())
    app.router.lifespan_context = None  # type: ignore[assignment]
    app.state.session_factory = factory
    app.state.artifact_store = store
    yield SimpleNamespace(app=app, key=key)
    await engine.dispose()


def _client(app: object) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test", headers=_AUTH)  # type: ignore[arg-type]


async def test_lists_run_artifacts_from_manifest(app_ctx: SimpleNamespace) -> None:
    async with _client(app_ctx.app) as c:
        items = (await c.get(f"/v1/runs/{RUN}/artifacts")).json()["items"]
    by_kind = {i["kind"]: i for i in items}
    # both milestones surface: M1 comprehension + M2 design
    assert by_kind["comprehension"]["name"] == "current-state.md"
    assert by_kind["comprehension"]["key"] == app_ctx.key
    assert by_kind["design"]["name"] == "design.md"
    assert by_kind["design"]["key"].endswith("feature/SDLC-1/design.md")


async def test_downloads_run_artifact(app_ctx: SimpleNamespace) -> None:
    async with _client(app_ctx.app) as c:
        r = await c.get(f"/v1/runs/{RUN}/artifacts/download", params={"key": app_ctx.key})
    assert r.status_code == 200
    assert r.content == b"# Current State\n"
    assert r.headers["content-type"].startswith("text/markdown")
    assert "current-state.md" in r.headers["content-disposition"]


async def test_download_rejects_key_outside_run(app_ctx: SimpleNamespace) -> None:
    async with _client(app_ctx.app) as c:
        r = await c.get(f"/v1/runs/{RUN}/artifacts/download", params={"key": "run/OTHER/x.md"})
    assert r.status_code == 400
