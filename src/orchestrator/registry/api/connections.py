"""Connections API (D1 + config editing): MCP servers + source/tracker status.

``GET /v1/connections`` turns `doctor` + the MCP config into a management surface:
- **sources** — the readiness env checks (Confluence / Jira / Notion / GitHub /
  LLM), by variable presence only.
- **MCP servers** — every server in an ``mcpServers`` config (default location or
  a ``?config=<path>`` you point at), each *tested* live for reachability + its
  allow-listed tools.

Editing (add / update / remove servers → writes ``mcp.json``) is **gated behind
``ORCHESTRATOR_MCP_CONFIG_WRITABLE``** (default off): a stdio server's ``command``
is executed by the MCP process, so config-write is a code-execution surface. When
off, ``POST``/``DELETE`` here return 403 and the page shows the config path so you
can edit the file directly. Invoking MCP tools from the browser stays unsupported.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field

from orchestrator.registry.api.deps import PrincipalDep

router = APIRouter(prefix="/v1/connections", tags=["connections"])

_TEST_TIMEOUT_S = 5.0


class ToolInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    read_only: bool | None
    description: str


class ServerInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    transport: str
    target: str  # command (stdio) or url (http)
    enabled: bool
    write_enabled: bool
    allow: list[str] | None  # allow-listed tool names (null = all)
    reachable: bool
    tools: list[ToolInfo]
    error: str | None


class CheckInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    passed: bool
    optional: bool
    detail: str


class ConnectionsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sources: list[CheckInfo]
    config_path: str  # the resolved mcp.json path (default or ?config=)
    mcp_config_present: bool
    writable: bool  # can servers be edited from the UI? (ORCHESTRATOR_MCP_CONFIG_WRITABLE)
    servers: list[ServerInfo]


def _factory(request: Request) -> Any:
    from orchestrator.mcp import SessionMCPClient

    return getattr(request.app.state, "mcp_client_factory", None) or SessionMCPClient


def _writable(request: Request) -> bool:
    return bool(getattr(request.app.state.settings, "mcp_config_writable", False))


def _transport(cfg: Any) -> str:
    try:
        return str(cfg.transport)
    except Exception:  # noqa: BLE001 — a malformed server shouldn't blank the surface
        return "unknown"


async def _test_server(cfg: Any, factory: Any) -> ServerInfo:
    base = {
        "name": cfg.name,
        "transport": _transport(cfg),
        "target": cfg.command or cfg.url or "",
        "enabled": cfg.enabled,
        "write_enabled": cfg.write_enabled,
        "allow": list(cfg.allow) if cfg.allow else None,
    }
    if not cfg.enabled:
        return ServerInfo(**base, reachable=False, tools=[], error="disabled")
    try:
        tools = await asyncio.wait_for(factory(cfg).list_tools(), timeout=_TEST_TIMEOUT_S)
    except TimeoutError:
        return ServerInfo(**base, reachable=False, tools=[], error="timed out")
    except Exception as exc:  # noqa: BLE001 — a down/misconfigured server is reported, not fatal
        return ServerInfo(**base, reachable=False, tools=[], error=str(exc)[:300])
    allowed = [
        ToolInfo(name=t.name, read_only=t.read_only, description=t.description)
        for t in tools
        if cfg.allows(t.name)
    ]
    return ServerInfo(**base, reachable=True, tools=allowed, error=None)


@router.get("", response_model=ConnectionsResponse)
async def connections(
    request: Request, _principal: PrincipalDep, config: str | None = None
) -> ConnectionsResponse:
    from orchestrator.doctor import run_env_checks
    from orchestrator.mcp import MCPConfigError, load_mcp_configs
    from orchestrator.mcp.config import resolve_config_path

    sources = [
        CheckInfo(name=c.name, passed=c.passed, optional=c.optional, detail=c.detail)
        for c in run_env_checks()
    ]
    cfg_path = resolve_config_path(config)
    present = cfg_path.is_file()
    writable = _writable(request)
    try:
        configs = load_mcp_configs(config)
    except MCPConfigError as exc:
        error_server = ServerInfo(
            name="(config)",
            transport="unknown",
            target=str(cfg_path),
            enabled=False,
            write_enabled=False,
            allow=None,
            reachable=False,
            tools=[],
            error=str(exc)[:300],
        )
        return ConnectionsResponse(
            sources=sources,
            config_path=str(cfg_path),
            mcp_config_present=present,
            writable=writable,
            servers=[error_server],
        )
    factory = _factory(request)
    servers = list(await asyncio.gather(*(_test_server(c, factory) for c in configs)))
    return ConnectionsResponse(
        sources=sources,
        config_path=str(cfg_path),
        mcp_config_present=present,
        writable=writable,
        servers=servers,
    )


# --------------------------------------------------------------------------- #
# Config editing (gated by ORCHESTRATOR_MCP_CONFIG_WRITABLE)
# --------------------------------------------------------------------------- #
class ServerSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1)
    command: str | None = Field(default=None, description="stdio: the command to run")
    url: str | None = Field(default=None, description="http: the server URL")
    args: list[str] = Field(default_factory=list)
    allow: list[str] | None = Field(default=None, description="allow-listed tool names (null = all)")
    enabled: bool = True
    config: str | None = Field(default=None, description="Override the mcp.json path to write.")


def _require_writable(request: Request) -> None:
    if not _writable(request):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="MCP config editing is disabled — set ORCHESTRATOR_MCP_CONFIG_WRITABLE=1 to enable, "
            "or edit the config file directly.",
        )


@router.post("/servers", response_model=ServerInfo, status_code=status.HTTP_201_CREATED)
async def add_server(body: ServerSpec, request: Request, _principal: PrincipalDep) -> ServerInfo:
    """Add or update an MCP server in the config, then test it. Gated."""
    _require_writable(request)
    from orchestrator.mcp import MCPConfigError, MCPServerConfig, upsert_mcp_server

    if bool(body.command) == bool(body.url):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="provide exactly one of 'command' (stdio) or 'url' (http)",
        )
    spec: dict[str, Any] = {
        "command": body.command,
        "url": body.url,
        "args": list(body.args) or None,
        "allow": list(body.allow) if body.allow is not None else None,
        "enabled": body.enabled,
    }
    try:
        upsert_mcp_server(body.config, body.name, spec)
    except (MCPConfigError, OSError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    cfg = MCPServerConfig(
        name=body.name,
        command=body.command,
        args=tuple(body.args),
        url=body.url,
        allow=tuple(body.allow) if body.allow is not None else None,
        enabled=body.enabled,
    )
    return await _test_server(cfg, _factory(request))


@router.delete("/servers/{name}", status_code=status.HTTP_200_OK)
async def remove_server(
    name: str, request: Request, _principal: PrincipalDep, config: str | None = None
) -> dict[str, bool]:
    """Remove an MCP server from the config. Gated."""
    _require_writable(request)
    from orchestrator.mcp import remove_mcp_server

    removed = remove_mcp_server(config, name)
    if not removed:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"no server {name!r}")
    return {"removed": True}
