"""Onboard configured MCP servers: discover their tools and route calls.

The registry is the orchestrator-facing surface for Phase 1: it loads the
``mcpServers`` config, discovers each server's tools (namespaced ``server:tool``,
filtered to the allow-list), and routes a call to the right server. A server
that's unreachable is skipped (logged) so one bad server doesn't blank the rest.

Read-mostly by design: only allow-listed tools are exposed or callable, so a
server can't surface a mutating tool the operator didn't opt into. (Wiring these
into the gateway ``ToolContract`` registry + budget/audit is Phase-1 Slice 2.)
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from orchestrator.mcp.client import MCPClient, SessionMCPClient
from orchestrator.mcp.config import MCPServerConfig, load_mcp_configs
from orchestrator.mcp.models import MCPServerStatus, MCPTool, MCPToolResult
from orchestrator.mcp.schema_types import encode_for_schema, needs_schema_lookup

logger = logging.getLogger("orchestrator.mcp")

ClientFactory = Callable[[MCPServerConfig], MCPClient]


def _default_factory(config: MCPServerConfig) -> MCPClient:
    return SessionMCPClient(config)


def _classify(exc: Exception) -> tuple[str, str]:
    """``(kind, remedy)`` for a failed tool listing.

    ``config`` means the operator must change something and retrying will not help;
    ``unreachable`` means the server may simply be down. Matching on message text is
    unlovely but these strings come from our own client and from the OS, and getting the
    *class* right matters more than the precision: telling someone to install an extra when
    the server is merely down wastes a minute, whereas telling them nothing at all — which
    is what happened before — wasted considerably more.
    """
    msg = str(exc)
    if "'mcp' extra" in msg:
        return ("config", "install it: pip install 'synaptixs-spine[mcp]'")
    if isinstance(exc, FileNotFoundError) or "executable file not found" in msg or "No such file" in msg:
        return ("config", "the server's `command` is not on PATH — is it installed?")
    if "permission denied" in msg.lower():
        return ("config", "the server's `command` is not executable")
    if isinstance(exc, TimeoutError | ConnectionError) or "timed out" in msg.lower():
        return ("unreachable", "the server did not respond — is it running?")
    return ("unreachable", "")


class MCPRegistry:
    """Aggregates the tools of every configured MCP server."""

    def __init__(
        self, configs: list[MCPServerConfig], *, client_factory: ClientFactory = _default_factory
    ) -> None:
        self._configs = {c.name: c for c in configs if c.enabled}
        self._factory = client_factory
        # Input schemas seen during discovery, keyed `server:tool`. Every session with an
        # MCP server costs a spawned process or an HTTP connection, and `_encoded` needed a
        # schema the registry had *already been told* — so a single `call` carrying a
        # structured argument opened two: one to re-ask for the schema, one to make the
        # call. Discovery fills this; `_encoded` reads it and only pays the round-trip on a
        # miss. Refreshed on every `probe`, so a changed schema is picked up the next time
        # anything lists tools; a tool the server *removes* leaves a stale entry, which
        # costs nothing — the call it would encode for is refused by the server either way.
        self._schemas: dict[str, dict[str, Any]] = {}
        # `readOnlyHint` per `server:tool`, filled the same way (and for the same reason) as
        # `_schemas`: `call` enforces the write gate and must not open a session of its own to
        # learn what discovery already knows. `None` means the server did not say.
        self._read_only: dict[str, bool | None] = {}

    @classmethod
    def from_config(
        cls, path: str | Path | None = None, *, client_factory: ClientFactory = _default_factory
    ) -> MCPRegistry:
        return cls(load_mcp_configs(path), client_factory=client_factory)

    def server_names(self) -> list[str]:
        return list(self._configs)

    async def probe(self) -> list[MCPServerStatus]:
        """Ask every configured server for its tools, reporting **why** any failed.

        The counterpart to :meth:`list_tools`, which swallows failures so one bad server
        cannot blank the rest. That leniency is right for a run and wrong for a human: it
        made a missing ``mcp`` extra, an unpulled image, a typo in ``command`` and a dead
        server indistinguishable — all of them an empty list plus a log line nobody reads.
        Surfaces to ``orchestrator mcp list``.
        """
        out: list[MCPServerStatus] = []
        for name, cfg in self._configs.items():
            if cfg.allow is None:
                logger.warning("mcp.no_allowlist", extra={"server": name})
            try:
                tools = await self._factory(cfg).list_tools()
            except Exception as exc:  # noqa: BLE001 — a down server must not blank the rest
                kind, remedy = _classify(exc)
                # Redact before truncating: an HTTP error quotes the url it hit, which may
                # carry an expanded ``${VAR}`` token, and a cut could leave half of one.
                error = cfg.redact(str(exc))
                logger.warning("mcp.list_failed", extra={"server": name, "error": error[:200], "kind": kind})
                out.append(MCPServerStatus(name=name, kind=kind, error=error[:300], remedy=remedy))
                continue
            allowed = tuple(t for t in tools if cfg.allows(t.name))
            self._schemas.update({f"{name}:{t.name}": t.input_schema for t in allowed})
            self._read_only.update({f"{name}:{t.name}": t.read_only for t in allowed})
            out.append(MCPServerStatus(name=name, tools=allowed))
        return out

    async def list_tools(self) -> list[MCPTool]:
        """Every allow-listed tool across all reachable servers (namespaced).

        Failures are skipped rather than raised — one unreachable server must not blank the
        others mid-run. Use :meth:`probe` when a human needs to know what went wrong.
        """
        return [t for status in await self.probe() for t in status.tools]

    async def call(
        self, qualified_name: str, arguments: dict[str, Any], *, vouched: bool = False
    ) -> MCPToolResult:
        """Invoke ``server:tool`` — refuses unknown servers, non-allow-listed tools and write tools.

        A tool that is not declared read-only (``readOnlyHint: true``) is refused unless its server
        is ``write_enabled`` — the rule :class:`MCPToolHandler` has always applied, enforced here
        because intake, the docs pull, ``db.py`` and the CLI call the registry directly (B43). An
        unannotated tool (``read_only=None``) is *unknown*, not safe.

        ``vouched=True`` is the caller saying it has already cleared this tool: the docs pull
        checks every tool against ``readOnlyHint`` or the source's ``trust_read_only`` before
        calling, and ``db.py`` sends only the constant SELECTs it wrote. Nothing else should pass it.

        Where discovery has not run, the first call to a tool costs one ``list_tools`` session to
        learn its hint; the answer is cached. Skipped for a ``write_enabled`` server and a vouched
        call. If the lookup fails the call is refused — an unknown tool is not assumed read-only.
        """
        server, _, tool = qualified_name.partition(":")
        cfg = self._configs.get(server)
        if cfg is None:
            known = ", ".join(self._configs) or "none configured"
            raise KeyError(f"unknown MCP server {server!r} (known: {known})")
        if not tool or not cfg.allows(tool):
            raise PermissionError(f"tool {qualified_name!r} is not allow-listed on server {server!r}")
        client = self._factory(cfg)
        if not (vouched or cfg.write_enabled):
            await self._require_read_only(client, qualified_name)
        return await client.call_tool(tool, await self._encoded(client, server, tool, arguments))

    async def _require_read_only(self, client: MCPClient, qualified_name: str) -> None:
        """Raise ``PermissionError`` unless discovery says ``qualified_name`` is read-only."""
        if qualified_name not in self._read_only:
            server, _, _ = qualified_name.partition(":")
            try:
                tools = await client.list_tools()
            except Exception as exc:  # noqa: BLE001 — the reason is reported, and the call is refused
                raise PermissionError(
                    f"MCP tool {qualified_name!r} was not called: could not check whether it is read-only "
                    f"({str(exc)[:120] or type(exc).__name__}). Set write_enabled on server {server!r} "
                    "to call it without that check."
                ) from exc
            self._read_only.update({f"{server}:{t.name}": t.read_only for t in tools})
            self._schemas.update({f"{server}:{t.name}": t.input_schema for t in tools})
        if self._read_only.get(qualified_name) is not True:
            raise PermissionError(
                f"MCP tool {qualified_name!r} is not declared read-only (readOnlyHint) and its server "
                f"is not write_enabled — set write_enabled on server {qualified_name.partition(':')[0]!r} "
                "to allow it."
            )

    async def _encoded(
        self, client: MCPClient, server: str, tool: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        """Honour a server that declares a structured argument as a JSON string.

        Skipped entirely unless some argument is a ``dict``/``list``, so the common
        all-scalar call pays nothing for the schema round-trip. A discovery failure
        passes the arguments through untouched: this is a convenience over the raw
        call, and it must never be the reason one fails.

        The schema is taken from the discovery cache when discovery has already run —
        which it has for every caller that lists tools before calling them, the agentic
        loop included. That is the whole of the saving: one session per call instead of
        two, without giving the client a lifecycle for anyone to leak.
        """
        if not needs_schema_lookup(arguments):
            return arguments
        cached = self._schemas.get(f"{server}:{tool}")
        if cached is not None:
            return encode_for_schema(cached, arguments)
        try:
            tools = await client.list_tools()
        except Exception as exc:  # noqa: BLE001 — the call itself is still worth attempting
            logger.warning("mcp.schema_lookup_failed", extra={"tool": tool, "error": str(exc)[:200]})
            return arguments
        self._schemas.update({f"{server}:{t.name}": t.input_schema for t in tools})
        return encode_for_schema(next((t.input_schema for t in tools if t.name == tool), None), arguments)


__all__ = ["ClientFactory", "MCPRegistry"]
