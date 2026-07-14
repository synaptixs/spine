"""Phase B — Repo Intelligence: overview builder, memory-bank API, pages, a job."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import httpx
import pytest_asyncio
from sqlalchemy import Table
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from orchestrator.pkg.facts import Edge, EdgeKind, FactBatch, Node, NodeKind, Provenance
from orchestrator.pkg.overview import build_overview
from orchestrator.registry.api import jobs
from orchestrator.registry.api.app import create_app
from orchestrator.registry.api.config import Settings
from orchestrator.registry.db.models import AuditLogRow
from orchestrator.runtime.artifacts import InMemoryArtifactStore

_AUTH = {"X-API-Key": "dev-key"}


def _no_db_app(**kw: object) -> object:
    app = create_app(Settings(database_url="postgresql+psycopg://stub/stub", **kw))  # type: ignore[arg-type]
    app.router.lifespan_context = None  # type: ignore[assignment]
    return app


async def _login(client: httpx.AsyncClient) -> None:
    assert (await client.post("/login", json={"api_key": "dev-key"})).status_code == 204


# --------------------------------------------------------------------------- #
# B4 — overview builder
# --------------------------------------------------------------------------- #
def test_build_overview_aggregates_and_bounds() -> None:
    batch = FactBatch()
    prov_a = Provenance(file="a.py", line=1)
    prov_b = Provenance(file="b.py", line=1)
    batch.add_node(Node(id="a:mod", kind=NodeKind.MODULE, name="a", provenance=prov_a))
    batch.add_node(Node(id="a:f", kind=NodeKind.FUNCTION, name="f", provenance=prov_a))
    batch.add_node(Node(id="b:g", kind=NodeKind.FUNCTION, name="g", provenance=prov_b))
    batch.add_node(Node(id="ext", kind=NodeKind.FUNCTION, name="ext", external=True))
    batch.add_edge(Edge(src="a:f", dst="b:g", kind=EdgeKind.CALLS))  # cross-module edge
    batch.add_edge(Edge(src="a:f", dst="ext", kind=EdgeKind.CALLS))

    o = build_overview(batch, max_modules=1, max_module_edges=10, max_symbols=10)
    assert o["summary"] == {"nodes": 4, "grounded_nodes": 3, "external_nodes": 1, "edges": 2}
    assert o["kinds"]["Function"] == 3
    assert o["edge_kinds"]["CALLS"] == 2
    # a.py has 2 nodes vs b.py's 1, so it ranks first; capped to 1 → truncated.
    assert o["totals"]["modules"] == 2 and o["truncated"]["modules"] is True
    assert [m["module"] for m in o["modules"]] == ["a.py"]
    assert o["module_edges"] == [{"src": "a.py", "dst": "b.py", "kind": "CALLS", "count": 1}]
    assert o["top_symbols"][0]["name"] == "f"  # highest degree (2 edges)


# --------------------------------------------------------------------------- #
# B3 — memory-bank read endpoint
# --------------------------------------------------------------------------- #
async def test_memory_bank_endpoint(tmp_path: Path) -> None:
    (tmp_path / "memory-bank").mkdir()
    (tmp_path / "memory-bank" / "architecture.md").write_text("# Arch\nhi", encoding="utf-8")
    (tmp_path / "memory-bank" / "README.md").write_text("# Index", encoding="utf-8")
    app = _no_db_app(workspace_root=str(tmp_path))
    transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
    async with httpx.AsyncClient(transport=transport, base_url="http://test", headers=_AUTH) as client:
        resp = await client.get("/v1/capabilities/memory-bank?repo=.")
        assert resp.status_code == 200
        data = resp.json()
        assert data["exists"] is True
        names = [f["name"] for f in data["files"]]
        assert names == ["README.md", "architecture.md"]  # sorted
        assert "# Arch" in dict((f["name"], f["markdown"]) for f in data["files"])["architecture.md"]

        # traversal is rejected before any read
        assert (await client.get("/v1/capabilities/memory-bank?repo=../etc")).status_code == 400


async def test_memory_bank_absent_returns_exists_false(tmp_path: Path) -> None:
    app = _no_db_app(workspace_root=str(tmp_path))
    transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
    async with httpx.AsyncClient(transport=transport, base_url="http://test", headers=_AUTH) as client:
        data = (await client.get("/v1/capabilities/memory-bank?repo=.")).json()
    assert data["exists"] is False and data["files"] == []


# --------------------------------------------------------------------------- #
# Pages + assets
# --------------------------------------------------------------------------- #
async def test_intelligence_pages_render_and_are_navigable() -> None:
    app = _no_db_app()
    transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
    pages = {
        "/app/understand": ("Understand · Spine", "understand.js"),
        "/app/state": ("Current State · Spine", "state.js"),
        "/app/memory-bank": ("Memory bank · Spine", "memory-bank.js"),
        "/app/graph": ("Knowledge graph · Spine", "graph.js"),
        "/app/catalog": ("Catalog · Spine", "catalog.js"),
    }
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # each page redirects to login when anonymous
        assert (await client.get("/app/understand")).status_code == 303
        await _login(client)
        for path, (title, script) in pages.items():
            r = await client.get(path)
            assert r.status_code == 200 and title in r.text and script in r.text
            assert 'id="repo"' in r.text  # the repo bar
        home = await client.get("/app")
        for href in ("/app/understand", "/app/state", "/app/graph"):
            assert f'href="{href}"' in home.text  # nav section + home cards
        assert ">Understand</p>" in home.text  # the sidebar section heading


async def test_intelligence_shared_assets() -> None:
    app = _no_db_app()
    transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        md = (await client.get("/static/md.js")).text
        jr = (await client.get("/static/jobrun.js")).text
    assert "renderMarkdown" in md and "<table class='md'>" in md
    assert "runJob" in jr and "EventSource" in jr and "/v1/jobs/" in jr


# --------------------------------------------------------------------------- #
# B2 — a real current-state job end to end (SQLite audit log + memory store)
# --------------------------------------------------------------------------- #
@pytest_asyncio.fixture
async def state_ctx(tmp_path: Path) -> AsyncIterator[SimpleNamespace]:
    # A minimal but real repo to analyse (keeps extraction fast + deterministic).
    (tmp_path / "app.py").write_text("def greet(name):\n    return f'hi {name}'\n", encoding="utf-8")
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    async with engine.begin() as conn:
        await conn.run_sync(cast(Table, AuditLogRow.__table__).create)
    app = create_app(Settings(workspace_root=str(tmp_path)))
    app.router.lifespan_context = None  # type: ignore[assignment]
    app.state.session_factory = async_sessionmaker(engine, expire_on_commit=False)
    app.state.artifact_store = InMemoryArtifactStore()
    yield SimpleNamespace(app=app)
    await engine.dispose()


async def test_state_job_produces_markdown_report(state_ctx: SimpleNamespace) -> None:
    transport = httpx.ASGITransport(app=state_ctx.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test", headers=_AUTH) as client:
        started = await client.post("/v1/capabilities/state", json={"repo": ".", "lens": "developer"})
        assert started.status_code == 202
        job_id = started.json()["job_id"]
    # drive the real build_current_state job to completion
    tasks = list(jobs._RUNNING)
    if tasks:
        import asyncio

        await asyncio.gather(*tasks, return_exceptions=True)

    async with httpx.AsyncClient(transport=transport, base_url="http://test", headers=_AUTH) as client:
        job = (await client.get(f"/v1/jobs/{job_id}")).json()
        assert job["state"] == "completed", job
        art = await client.get(f"/v1/jobs/{job_id}/artifact")
    assert art.status_code == 200
    assert art.headers["content-type"].startswith("text/markdown")
    assert "#" in art.text  # a markdown report
