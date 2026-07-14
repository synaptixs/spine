"""MCP config editing: config-path read, and gated add/remove writes."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from orchestrator.mcp.config import remove_mcp_server, resolve_config_path, upsert_mcp_server
from orchestrator.mcp.models import MCPTool
from orchestrator.registry.api.app import create_app
from orchestrator.registry.api.config import Settings

_AUTH = {"X-API-Key": "dev-key"}


# --------------------------------------------------------------------------- #
# Writers (pure, tmp files)
# --------------------------------------------------------------------------- #
def test_resolve_config_path_precedence(monkeypatch: pytest.MonkeyPatch) -> None:
    assert resolve_config_path("/x/mcp.json") == Path("/x/mcp.json")
    monkeypatch.setenv("ORCHESTRATOR_MCP_CONFIG", "/env/mcp.json")
    assert resolve_config_path(None) == Path("/env/mcp.json")


def test_upsert_creates_updates_and_preserves(tmp_path: Path) -> None:
    p = tmp_path / "nested" / "mcp.json"  # parent auto-created
    upsert_mcp_server(str(p), "alpha", {"command": "run-a", "enabled": True, "url": None})
    upsert_mcp_server(str(p), "beta", {"url": "http://b", "enabled": True, "allow": ["t1"]})
    doc = json.loads(p.read_text(encoding="utf-8"))
    assert set(doc["mcpServers"]) == {"alpha", "beta"}  # both preserved
    assert doc["mcpServers"]["alpha"]["command"] == "run-a"
    assert "url" not in doc["mcpServers"]["alpha"]  # None values dropped
    assert doc["mcpServers"]["beta"]["allow"] == ["t1"]


def test_remove_returns_existed(tmp_path: Path) -> None:
    p = tmp_path / "mcp.json"
    upsert_mcp_server(str(p), "alpha", {"command": "x", "enabled": True})
    assert remove_mcp_server(str(p), "alpha") is True
    assert remove_mcp_server(str(p), "alpha") is False


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #
class _FakeClient:
    def __init__(self, cfg: object) -> None:
        self._cfg = cfg

    async def list_tools(self) -> list[MCPTool]:
        name = getattr(self._cfg, "name", "")
        return [MCPTool(server=name, name="ping", description="p", read_only=True)]


def _app(*, writable: bool) -> object:
    app = create_app(Settings(database_url="postgresql+psycopg://stub/stub", mcp_config_writable=writable))
    app.router.lifespan_context = None  # type: ignore[assignment]
    app.state.mcp_client_factory = _FakeClient
    return app


def _client(app: object) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test", headers=_AUTH)  # type: ignore[arg-type]


async def test_get_reports_config_path_and_writable(tmp_path: Path) -> None:
    cfg = tmp_path / "mcp.json"
    cfg.write_text(json.dumps({"mcpServers": {"srv": {"command": "x"}}}), encoding="utf-8")
    async with _client(_app(writable=True)) as c:
        d = (await c.get(f"/v1/connections?config={cfg}")).json()
    assert d["config_path"] == str(cfg) and d["writable"] is True and d["mcp_config_present"] is True
    assert [s["name"] for s in d["servers"]] == ["srv"]


async def test_add_server_gated_off_returns_403(tmp_path: Path) -> None:
    async with _client(_app(writable=False)) as c:
        r = await c.post(
            "/v1/connections/servers",
            json={"name": "x", "command": "run-x", "config": str(tmp_path / "mcp.json")},
        )
    assert r.status_code == 403


async def test_add_and_remove_server_when_writable(tmp_path: Path) -> None:
    cfg = tmp_path / "mcp.json"
    async with _client(_app(writable=True)) as c:
        added = await c.post(
            "/v1/connections/servers",
            json={"name": "svc", "command": "orchestrator-mcp", "allow": ["ping"], "config": str(cfg)},
        )
        assert added.status_code == 201, added.text
        info = added.json()
        assert info["name"] == "svc" and info["reachable"] is True and info["tools"][0]["name"] == "ping"
        # it was written to the file
        assert json.loads(cfg.read_text())["mcpServers"]["svc"]["command"] == "orchestrator-mcp"

        listed = (await c.get(f"/v1/connections?config={cfg}")).json()
        assert "svc" in [s["name"] for s in listed["servers"]]

        removed = await c.delete(f"/v1/connections/servers/svc?config={cfg}")
        assert removed.status_code == 200 and removed.json() == {"removed": True}
        assert (await c.delete(f"/v1/connections/servers/svc?config={cfg}")).status_code == 404


async def test_add_server_rejects_both_command_and_url(tmp_path: Path) -> None:
    async with _client(_app(writable=True)) as c:
        r = await c.post(
            "/v1/connections/servers",
            json={"name": "x", "command": "a", "url": "http://b", "config": str(tmp_path / "mcp.json")},
        )
    assert r.status_code == 400
