"""End-to-end API tests against the live registry service."""

from __future__ import annotations

import os
from typing import Any

import httpx
import pytest
from asgi_lifespan import LifespanManager
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from orchestrator.registry.api.app import create_app
from orchestrator.registry.api.config import Settings
from orchestrator.registry.db.models import AuditLogRow

pytestmark = pytest.mark.integration

API_KEY = "test-key"


def _agent_payload() -> dict[str, Any]:
    return {
        "metadata": {
            "id": "research.summarizer",
            "version": "0.1.0",
            "description": "Summarize.",
            "tags": ["research"],
        },
        "spec": {
            "outputs": [
                {"name": "confidence", "type": "float"},
                {"name": "caveats", "type": "list[str]"},
            ],
            "model": "claude-opus-4-7",
        },
    }


def _settings_for_session() -> Settings:
    return Settings(
        database_url=os.getenv(
            "ORCHESTRATOR_TEST_DATABASE_URL",
            "postgresql+psycopg://orchestrator:orchestrator@localhost:5433/orchestrator",
        ),
        api_key=API_KEY,
    )


def _client_app() -> FastAPI:
    return create_app(_settings_for_session())


async def test_end_to_end_template_lifecycle(session: AsyncSession) -> None:
    app = _client_app()
    headers = {"X-API-Key": API_KEY}

    async with (
        LifespanManager(app) as manager,
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=manager.app),
            base_url="http://test",
            headers=headers,
        ) as client,
    ):
        # register
        r = await client.post("/v1/agent-templates", json=_agent_payload())
        assert r.status_code == 201, r.text
        assert r.json()["status"] == "draft"

        # GET by id+version
        r = await client.get("/v1/agent-templates/research.summarizer/0.1.0")
        assert r.status_code == 200

        # list with tag filter
        r = await client.get("/v1/agent-templates", params={"tag": "research"})
        assert r.status_code == 200
        items = r.json()["items"]
        assert len(items) == 1

        # duplicate registration returns 409
        r = await client.post("/v1/agent-templates", json=_agent_payload())
        assert r.status_code == 409

        # publish
        r = await client.post("/v1/agent-templates/research.summarizer/0.1.0/publish")
        assert r.status_code == 200
        assert r.json()["status"] == "published"

        # latest published lookup
        r = await client.get("/v1/agent-templates/research.summarizer")
        assert r.status_code == 200
        assert r.json()["version"] == "0.1.0"

        # deprecate
        r = await client.post("/v1/agent-templates/research.summarizer/0.1.0/deprecate")
        assert r.status_code == 200
        assert r.json()["status"] == "deprecated"

    # audit log captured register, publish, deprecate
    rows = (await session.execute(select(AuditLogRow))).scalars().all()
    actions = sorted(r.action for r in rows)
    assert actions == ["deprecate", "publish", "register"]


async def test_missing_api_key_returns_401(session: AsyncSession) -> None:
    app = _client_app()
    async with (
        LifespanManager(app) as manager,
        httpx.AsyncClient(transport=httpx.ASGITransport(app=manager.app), base_url="http://test") as client,
    ):
        r = await client.get("/v1/agent-templates")
    assert r.status_code == 401


async def test_invalid_payload_returns_400_with_failures(session: AsyncSession) -> None:
    app = _client_app()
    payload = _agent_payload()
    payload["metadata"]["id"] = "BAD-ID"
    async with (
        LifespanManager(app) as manager,
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=manager.app),
            base_url="http://test",
            headers={"X-API-Key": API_KEY},
        ) as client,
    ):
        r = await client.post("/v1/agent-templates", json=payload)
    assert r.status_code == 400
