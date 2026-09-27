"""``${VAR}`` expansion in ``mcp.json`` ``headers`` values and ``url`` (SSPN-81).

A remote MCP server's bearer token used to sit in plaintext in ``mcp.json``, because the
loader read ``headers`` and ``url`` as literal strings. The file now carries a placeholder
and the secret comes from the environment at load time — so the other half of the contract
matters as much as the expansion: the secret must never travel back out, not into the file
on a write and not onto a screen.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from orchestrator.mcp.config import MCPConfigError, load_mcp_configs, upsert_mcp_server
from orchestrator.mcp.models import MCPTool, MCPToolResult
from orchestrator.mcp.registry import MCPRegistry
from orchestrator.registry.api.app import create_app
from orchestrator.registry.api.config import Settings

_SECRET = "tok-9f8e7d6c5b4a"


def _write(tmp_path: Path, servers: dict[str, Any]) -> Path:
    p = tmp_path / "mcp.json"
    p.write_text(json.dumps({"mcpServers": servers}), encoding="utf-8")
    return p


def _one(tmp_path: Path, spec: dict[str, Any]) -> Any:
    [cfg] = load_mcp_configs(_write(tmp_path, {"remote": spec}))
    return cfg


# --------------------------------------------------------------------------- #
# Expansion
# --------------------------------------------------------------------------- #
def test_header_value_expands_from_the_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REMOTE_TOKEN", _SECRET)
    cfg = _one(
        tmp_path, {"url": "https://mcp.example/mcp", "headers": {"Authorization": "Bearer ${REMOTE_TOKEN}"}}
    )
    assert cfg.headers == {"Authorization": f"Bearer {_SECRET}"}


def test_url_expands_from_the_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_HOST", "mcp.example")
    cfg = _one(tmp_path, {"url": "https://${MCP_HOST}/mcp"})
    assert cfg.url == "https://mcp.example/mcp"
    assert cfg.transport == "http"


def test_unset_variable_names_the_variable_and_the_server(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Never silently send ``Authorization: Bearer `` — refuse, and say what to set."""
    monkeypatch.delenv("REMOTE_TOKEN", raising=False)
    p = _write(
        tmp_path, {"remote": {"url": "https://x/mcp", "headers": {"Authorization": "Bearer ${REMOTE_TOKEN}"}}}
    )
    with pytest.raises(MCPConfigError) as info:
        load_mcp_configs(p)
    assert "REMOTE_TOKEN" in str(info.value)
    assert "'remote'" in str(info.value)


def test_unset_variable_in_url_is_refused_too(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MCP_HOST", raising=False)
    with pytest.raises(MCPConfigError, match="MCP_HOST"):
        _one(tmp_path, {"url": "https://${MCP_HOST}/mcp"})


def test_empty_variable_is_set_and_expands_to_empty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Set-but-empty is the operator's explicit choice; only *unset* is refused."""
    monkeypatch.setenv("SUFFIX", "")
    cfg = _one(tmp_path, {"url": "https://x/mcp${SUFFIX}"})
    assert cfg.url == "https://x/mcp"


def test_double_dollar_escapes_a_literal_placeholder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NOT_A_VAR", raising=False)  # an escape must not consult the env
    cfg = _one(tmp_path, {"url": "https://x/mcp", "headers": {"X-Template": "$${NOT_A_VAR}"}})
    assert cfg.headers == {"X-Template": "${NOT_A_VAR}"}


def test_bare_dollar_forms_are_left_untouched(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TOKEN", _SECRET)
    cfg = _one(
        tmp_path, {"url": "https://x/mcp?a=$TOKEN", "headers": {"X-Price": "$5 or $TOKEN", "X-Lone": "$"}}
    )
    assert cfg.url == "https://x/mcp?a=$TOKEN"
    assert cfg.headers == {"X-Price": "$5 or $TOKEN", "X-Lone": "$"}


def test_multiple_placeholders_in_one_value(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_USER", "alice")
    monkeypatch.setenv("MCP_PASS", _SECRET)
    cfg = _one(
        tmp_path,
        {"url": "https://x/mcp", "headers": {"X-Auth": "${MCP_USER}:${MCP_PASS}:$${MCP_USER}"}},
    )
    assert cfg.headers == {"X-Auth": f"alice:{_SECRET}:${{MCP_USER}}"}


def test_config_without_placeholders_is_unchanged(tmp_path: Path) -> None:
    cfg = _one(tmp_path, {"url": "http://localhost:8080/mcp", "headers": {"Authorization": "Bearer x"}})
    assert cfg.url == "http://localhost:8080/mcp"
    assert cfg.headers == {"Authorization": "Bearer x"}
    assert cfg.display_url == "http://localhost:8080/mcp"


def test_command_args_env_and_allow_are_not_expanded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Out of scope by decision: stdio ``env`` already merges ``os.environ`` in the client."""
    monkeypatch.setenv("X", "expanded")
    cfg = _one(
        tmp_path,
        {"command": "run-${X}", "args": ["${X}"], "env": {"K": "${X}"}, "allow": ["${X}"]},
    )
    assert cfg.command == "run-${X}"
    assert cfg.args == ("${X}",)
    assert cfg.env == {"K": "${X}"}
    assert cfg.allow == ("${X}",)


# --------------------------------------------------------------------------- #
# The secret never travels back out
# --------------------------------------------------------------------------- #
def test_writing_the_file_keeps_the_raw_placeholder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A load followed by an upsert of *another* server must not bake the secret in."""
    monkeypatch.setenv("REMOTE_TOKEN", _SECRET)
    p = _write(
        tmp_path, {"remote": {"url": "https://x/mcp", "headers": {"Authorization": "Bearer ${REMOTE_TOKEN}"}}}
    )
    assert load_mcp_configs(p)[0].headers["Authorization"] == f"Bearer {_SECRET}"

    upsert_mcp_server(str(p), "other", {"url": "https://${MCP_HOST}/mcp", "enabled": True})
    text = p.read_text(encoding="utf-8")
    assert _SECRET not in text
    doc = json.loads(text)
    assert doc["mcpServers"]["remote"]["headers"]["Authorization"] == "Bearer ${REMOTE_TOKEN}"
    assert doc["mcpServers"]["other"]["url"] == "https://${MCP_HOST}/mcp"


def test_repr_never_shows_an_expanded_secret(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A config that reaches a log line or a traceback must not carry the token with it."""
    monkeypatch.setenv("REMOTE_TOKEN", _SECRET)
    cfg = _one(
        tmp_path,
        {"url": "https://x/mcp?key=${REMOTE_TOKEN}", "headers": {"Authorization": "Bearer ${REMOTE_TOKEN}"}},
    )
    assert _SECRET not in repr(cfg)
    assert cfg.display_url == "https://x/mcp?key=${REMOTE_TOKEN}"


def test_redact_swaps_expanded_values_back_to_placeholders(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("REMOTE_TOKEN", _SECRET)
    cfg = _one(tmp_path, {"url": "https://x/mcp?key=${REMOTE_TOKEN}"})
    msg = f"Client error '401 Unauthorized' for url 'https://x/mcp?key={_SECRET}'"
    assert cfg.redact(msg) == "Client error '401 Unauthorized' for url 'https://x/mcp?key=${REMOTE_TOKEN}'"


class _Boom:
    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    async def list_tools(self) -> list[MCPTool]:
        raise self._exc

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> MCPToolResult:
        raise self._exc


async def test_probe_error_never_shows_the_secret(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``orchestrator mcp list`` prints ``probe()``'s errors, and HTTP errors quote the url."""
    monkeypatch.setenv("REMOTE_TOKEN", _SECRET)
    [cfg] = load_mcp_configs(_write(tmp_path, {"remote": {"url": "https://x/mcp?key=${REMOTE_TOKEN}"}}))
    exc = RuntimeError(f"401 Unauthorized for url 'https://x/mcp?key={_SECRET}'")
    [status] = await MCPRegistry([cfg], client_factory=lambda _c: _Boom(exc)).probe()
    assert _SECRET not in status.error
    assert "${REMOTE_TOKEN}" in status.error


async def test_connections_surface_shows_the_placeholder_not_the_secret(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``GET /v1/connections`` reports each server's url as its ``target``, and its error."""
    monkeypatch.setenv("REMOTE_TOKEN", _SECRET)
    p = _write(tmp_path, {"remote": {"url": "https://x/mcp?key=${REMOTE_TOKEN}"}})
    async with _connections_client(writable=False) as c:
        body = (await c.get(f"/v1/connections?config={p}")).text
    assert _SECRET not in body
    [server] = json.loads(body)["servers"]
    assert server["target"] == "https://x/mcp?key=${REMOTE_TOKEN}"
    assert "${REMOTE_TOKEN}" in server["error"]


async def test_connections_add_saves_the_placeholder_and_tests_the_expanded_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The add endpoint used to test the url exactly as typed — a literal ``${VAR}``."""
    monkeypatch.setenv("REMOTE_TOKEN", _SECRET)
    p = tmp_path / "mcp.json"
    async with _connections_client(writable=True) as c:
        r = await c.post(
            "/v1/connections/servers",
            json={"name": "remote", "url": "https://x/mcp?key=${REMOTE_TOKEN}", "config": str(p)},
        )
    assert r.status_code == 201, r.text
    assert _SECRET not in r.text
    info = r.json()
    assert info["target"] == "https://x/mcp?key=${REMOTE_TOKEN}"
    assert info["error"] == "refused: https://x/mcp?key=${REMOTE_TOKEN}"  # hit expanded, shown redacted
    assert _SECRET not in p.read_text(encoding="utf-8")
    assert json.loads(p.read_text(encoding="utf-8"))["mcpServers"]["remote"]["url"] == (
        "https://x/mcp?key=${REMOTE_TOKEN}"
    )


async def test_connections_add_with_unset_variable_saves_and_reports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("REMOTE_TOKEN", raising=False)
    p = tmp_path / "mcp.json"
    async with _connections_client(writable=True) as c:
        r = await c.post(
            "/v1/connections/servers",
            json={"name": "remote", "url": "https://x/mcp?key=${REMOTE_TOKEN}", "config": str(p)},
        )
    assert r.status_code == 201, r.text
    info = r.json()
    assert info["reachable"] is False
    assert "REMOTE_TOKEN" in info["error"] and "'remote'" in info["error"]
    assert "remote" in json.loads(p.read_text(encoding="utf-8"))["mcpServers"]


def _connections_client(*, writable: bool) -> httpx.AsyncClient:
    """The registry app, with a client factory that fails quoting the url it was given."""
    app = create_app(Settings(database_url="postgresql+psycopg://stub/stub", mcp_config_writable=writable))
    app.router.lifespan_context = None  # type: ignore[assignment]
    app.state.mcp_client_factory = lambda cfg: _Boom(RuntimeError(f"refused: {cfg.url}"))
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test", headers={"X-API-Key": "dev-key"}
    )
