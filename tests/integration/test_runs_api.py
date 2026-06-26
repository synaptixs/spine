"""G12: integration test for GET /v1/runs (the runs-dashboard data) + /console.

Writes SDLC audit rows through the real repo, then asserts the endpoint
groups them per sdlc_id with the derived state. Mirrors the approvals/trace
API integration harness; deselected by default (needs Postgres).
"""

from __future__ import annotations

import os

import httpx
import pytest
from asgi_lifespan import LifespanManager
from sqlalchemy.ext.asyncio import AsyncSession

from orchestrator.core.llm import MockLLMClient
from orchestrator.registry.api.app import create_app
from orchestrator.registry.api.config import Settings
from orchestrator.registry.repositories import AuditLogRepo

pytestmark = pytest.mark.integration

API_KEY = "test-key"


def _settings() -> Settings:
    return Settings(
        database_url=os.getenv(
            "ORCHESTRATOR_TEST_DATABASE_URL",
            "postgresql+psycopg://orchestrator:orchestrator@localhost:5433/orchestrator",
        ),
        api_key=API_KEY,
    )


async def _audit(session: AsyncSession, sdlc_id: str, action: str) -> None:
    await AuditLogRepo(session).write(
        actor="system", action=action, resource_type="sdlc", resource_id=sdlc_id, trace_id=sdlc_id
    )


async def test_runs_endpoint_lists_and_derives_state(session: AsyncSession) -> None:
    await _audit(session, "run-merged-01", "sdlc_intake_analyzed")
    await _audit(session, "run-merged-01", "sdlc_prs_merged")
    await _audit(session, "run-running-02", "sdlc_issues_created")
    await session.commit()

    app = create_app(_settings(), llm_client=MockLLMClient())
    headers = {"X-API-Key": API_KEY}
    async with (
        LifespanManager(app) as manager,
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=manager.app), base_url="http://test", headers=headers
        ) as client,
    ):
        resp = await client.get("/v1/runs")
        assert resp.status_code == 200, resp.text
        by_id = {r["sdlc_id"]: r for r in resp.json()["items"]}
        assert by_id["run-merged-01"]["state"] == "merged"
        assert by_id["run-merged-01"]["events"] == 2
        assert by_id["run-running-02"]["state"] == "running"

        # console shell is reachable and data-free (no auth needed)
        page = await client.get("/console")
        assert page.status_code == 200
        assert "Orchestrator Console" in page.text


async def test_runs_endpoint_requires_api_key(session: AsyncSession) -> None:
    _ = session
    app = create_app(_settings(), llm_client=MockLLMClient())
    async with (
        LifespanManager(app) as manager,
        httpx.AsyncClient(transport=httpx.ASGITransport(app=manager.app), base_url="http://test") as client,
    ):
        resp = await client.get("/v1/runs")  # no X-API-Key
    assert resp.status_code == 401
