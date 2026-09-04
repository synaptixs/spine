"""POST /v1/audit — a caller records its own action; the actor is the principal, never the body."""

from __future__ import annotations

from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import cast

import httpx
import pytest
from sqlalchemy import Table
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from orchestrator.registry.api.app import create_app
from orchestrator.registry.api.config import Settings
from orchestrator.registry.db.models import AuditLogRow

_AUTH = {"X-API-Key": "dev-key"}


@pytest.fixture
async def app() -> AsyncIterator[SimpleNamespace]:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    async with engine.begin() as conn:
        await conn.run_sync(cast(Table, AuditLogRow.__table__).create)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    application = create_app(Settings())
    application.router.lifespan_context = None  # type: ignore[assignment]
    application.state.session_factory = factory
    yield SimpleNamespace(app=application)
    await engine.dispose()


def _client(app: object) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test", headers=_AUTH)  # type: ignore[arg-type]


async def test_a_caller_records_its_own_action_and_reads_it_back(app: SimpleNamespace) -> None:
    async with _client(app.app) as c:
        r = await c.post(
            "/v1/audit",
            json={
                "action": "mcp_tool_invoked",
                "resource_type": "mcp_tool",
                "resource_id": "sdlc_feature",
                "after": {"principal": {"client_id": "static"}, "outcome": "ok"},
            },
        )
        assert r.status_code == 201, r.text
        row = r.json()
        assert row["action"] == "mcp_tool_invoked" and row["resource_id"] == "sdlc_feature"
        assert row["actor"]  # the principal the key resolves to — not something the body said
        assert row["after"]["outcome"] == "ok"
        listed = (await c.get("/v1/audit?resource_type=mcp_tool")).json()["items"]
        assert [x["resource_id"] for x in listed] == ["sdlc_feature"]


async def test_the_body_cannot_name_an_actor_or_a_tenant(app: SimpleNamespace) -> None:
    async with _client(app.app) as c:
        for extra in ({"actor": "someone-else"}, {"tenant_id": "other"}):
            r = await c.post(
                "/v1/audit",
                json={"action": "a", "resource_type": "t", "resource_id": "r", **extra},
            )
            assert r.status_code == 422, r.text  # extra="forbid"


async def test_an_unauthenticated_write_is_refused(app: SimpleNamespace) -> None:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app.app), base_url="http://test") as c:
        r = await c.post("/v1/audit", json={"action": "a", "resource_type": "t", "resource_id": "r"})
        assert r.status_code in (401, 403)
