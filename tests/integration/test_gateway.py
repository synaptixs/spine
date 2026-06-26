"""End-to-end gateway test: register the echo contract, invoke it, audit it."""

from __future__ import annotations

import os
from typing import Any

import httpx
import pytest
from asgi_lifespan import LifespanManager
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from orchestrator.gateway.api.app import create_app as create_gateway_app
from orchestrator.gateway.handlers import HandlerRegistry
from orchestrator.gateway.invocation import InvocationContext
from orchestrator.gateway.tools.echo import ECHO_CONTRACT_PAYLOAD, EchoHandler
from orchestrator.registry.api.config import Settings
from orchestrator.registry.db.models import (
    AgentTemplateRow,  # noqa: F401  (metadata)
    AuditLogRow,
    ToolContractRow,
)
from orchestrator.registry.repositories import VersionedRepo

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


async def _publish_echo_contract(session: AsyncSession) -> None:
    repo = VersionedRepo(session, ToolContractRow)
    await repo.create(
        id="tool.echo",
        version="0.1.0",
        description=ECHO_CONTRACT_PAYLOAD["metadata"]["description"],
        tags=list(ECHO_CONTRACT_PAYLOAD["metadata"]["tags"]),
        spec=ECHO_CONTRACT_PAYLOAD["spec"],
    )
    await repo.publish("tool.echo", "0.1.0")
    await session.commit()


async def test_end_to_end_invoke_echo(session: AsyncSession) -> None:
    await _publish_echo_contract(session)

    registry = HandlerRegistry()
    registry.register(EchoHandler())
    app = create_gateway_app(_settings(), handler_registry=registry)
    headers = {"X-API-Key": API_KEY, "X-Trace-Id": "trace-echo-1"}

    async with (
        LifespanManager(app) as manager,
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=manager.app),
            base_url="http://gw",
            headers=headers,
        ) as client,
    ):
        ready = await client.get("/readyz")
        assert ready.status_code == 200
        assert ready.json()["loaded_tools"] == 1

        resp = await client.post("/v1/tools/tool.echo/0.1.0/invoke", json={"msg": "hello"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["output"]["echoed"] == {"msg": "hello"}
    assert body["output"]["trace_id"] == "trace-echo-1"
    assert body["tool_id"] == "tool.echo"

    rows = (await session.execute(select(AuditLogRow))).scalars().all()
    invocations = [r for r in rows if r.action == "tool_invocation"]
    assert len(invocations) == 1
    after = invocations[0].after_json
    assert after is not None
    assert after["outcome"] == "success"
    assert after["tool_id"] == "tool.echo"
    assert after["inputs_hash"].startswith("sha256:")
    assert invocations[0].trace_id == "trace-echo-1"


async def test_unknown_tool_returns_404(session: AsyncSession) -> None:
    app = create_gateway_app(_settings(), handler_registry=HandlerRegistry())
    headers = {"X-API-Key": API_KEY}
    async with (
        LifespanManager(app) as manager,
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=manager.app), base_url="http://gw", headers=headers
        ) as client,
    ):
        r = await client.post("/v1/tools/tool.missing/0.1.0/invoke", json={})
    assert r.status_code == 404


async def test_handler_failure_is_audited_then_500(session: AsyncSession) -> None:
    class Failing:
        contract_id = "tool.echo"
        contract_version = "0.1.0"

        async def __call__(self, inputs: dict[str, Any], ctx: InvocationContext) -> dict[str, Any]:
            raise ValueError("boom")

    await _publish_echo_contract(session)
    registry = HandlerRegistry()
    registry.register(Failing())
    app = create_gateway_app(_settings(), handler_registry=registry)
    headers = {"X-API-Key": API_KEY}

    async with (
        LifespanManager(app) as manager,
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=manager.app), base_url="http://gw", headers=headers
        ) as client,
    ):
        r = await client.post("/v1/tools/tool.echo/0.1.0/invoke", json={"x": 1})
    assert r.status_code == 500

    rows = (await session.execute(select(AuditLogRow))).scalars().all()
    invocations = [r for r in rows if r.action == "tool_invocation"]
    assert len(invocations) == 1
    after = invocations[0].after_json
    assert after is not None
    assert after["outcome"] == "error"
    assert after["error_type"] == "ValueError"
    assert "boom" in after["error_message"]


async def test_missing_api_key_returns_401(session: AsyncSession) -> None:
    app = create_gateway_app(_settings(), handler_registry=HandlerRegistry())
    async with (
        LifespanManager(app) as manager,
        httpx.AsyncClient(transport=httpx.ASGITransport(app=manager.app), base_url="http://gw") as client,
    ):
        r = await client.post("/v1/tools/tool.echo/0.1.0/invoke", json={})
    assert r.status_code == 401
