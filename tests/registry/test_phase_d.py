"""Phase D — Connect & intake studio: connections (D1), intake studio (D2)."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from orchestrator.mcp.models import MCPTool
from orchestrator.registry.api.app import create_app
from orchestrator.registry.api.config import Settings

_AUTH = {"X-API-Key": "dev-key"}


def _no_db_app(**kw: object) -> object:
    app = create_app(Settings(database_url="postgresql+psycopg://stub/stub", **kw))  # type: ignore[arg-type]
    app.router.lifespan_context = None  # type: ignore[assignment]
    return app


async def _login(c: httpx.AsyncClient) -> None:
    assert (await c.post("/login", json={"api_key": "dev-key"})).status_code == 204


# --------------------------------------------------------------------------- #
# D1 — connections
# --------------------------------------------------------------------------- #
class _FakeClient:
    def __init__(self, cfg: object) -> None:
        self._cfg = cfg

    async def list_tools(self) -> list[MCPTool]:
        name = getattr(self._cfg, "name", "")
        if name == "bad":
            raise RuntimeError("connection refused")
        return [
            MCPTool(server=name, name="search", description="find things", read_only=True),
            MCPTool(server=name, name="write_it", description="mutate", read_only=False),
        ]


async def test_connections_lists_and_tests_mcp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = tmp_path / "mcp.json"
    cfg.write_text(
        json.dumps(
            {"mcpServers": {"good": {"command": "x", "allow": ["search"]}, "bad": {"url": "http://y"}}}
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("ORCHESTRATOR_MCP_CONFIG", str(cfg))
    app = _no_db_app()
    app.state.mcp_client_factory = _FakeClient  # type: ignore[attr-defined]

    transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
    async with httpx.AsyncClient(transport=transport, base_url="http://test", headers=_AUTH) as c:
        d = (await c.get("/v1/connections")).json()

    assert d["mcp_config_present"] is True
    servers = {s["name"]: s for s in d["servers"]}
    assert servers["good"]["reachable"] is True
    # only the allow-listed tool is surfaced (write_it is filtered out)
    assert [t["name"] for t in servers["good"]["tools"]] == ["search"]
    assert servers["good"]["tools"][0]["read_only"] is True
    assert servers["bad"]["reachable"] is False and "connection refused" in servers["bad"]["error"]
    assert d["sources"]  # env/tracker checks present


async def test_connections_no_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORCHESTRATOR_MCP_CONFIG", str(tmp_path / "absent.json"))
    app = _no_db_app()
    transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
    async with httpx.AsyncClient(transport=transport, base_url="http://test", headers=_AUTH) as c:
        d = (await c.get("/v1/connections")).json()
    assert d["mcp_config_present"] is False and d["servers"] == []


async def test_connections_requires_auth() -> None:
    app = _no_db_app()
    transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        assert (await c.get("/v1/connections")).status_code == 401


# --------------------------------------------------------------------------- #
# Pages + nav
# --------------------------------------------------------------------------- #
async def test_phase_d_pages_render_and_navigate() -> None:
    app = _no_db_app()
    transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        assert (await c.get("/app/connections")).status_code == 303  # login required
        await _login(c)
        conn = await c.get("/app/connections")
        intake = await c.get("/app/intake")
        home = await c.get("/app")

    assert "Connections · Spine" in conn.text and "/static/connections.js" in conn.text
    assert "Intake studio · Spine" in intake.text and "/static/intake-studio.js" in intake.text
    # the studio surfaces the live CLI-only flows honestly, not as web buttons
    assert "address-review" in intake.text and "openspec draft" in intake.text and "--live" in intake.text
    # nav sections + home cards
    assert ">Connect</p>" in home.text and 'href="/app/connections"' in home.text
    assert 'href="/app/intake"' in home.text
