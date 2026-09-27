"""MCP server configuration — the ``mcpServers`` file shape, plus an allow-list.

Adopts the de-facto ``mcpServers`` JSON shape that Claude Desktop / Claude Code
/ Codex already use, so a developer can point the orchestrator at the config
they already have. Transport is inferred: ``command`` → stdio, ``url`` → HTTP.
Our one addition is ``allow`` — a per-server allow-list of tool names; only
allow-listed tools are exposed/callable (``null``/absent = all tools, which the
registry warns about). Auth/secrets ride in ``env`` (stdio) or ``headers``
(http); never inline a raw secret you don't want in the file — ``headers`` values
and ``url`` expand ``${VAR}`` from the environment at load time (see
:func:`_expand`), so the file can carry ``"Bearer ${MCP_TOKEN}"`` instead.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_CONFIG_ENV = "ORCHESTRATOR_MCP_CONFIG"
DEFAULT_CONFIG_FILE = "mcp.json"


class MCPConfigError(ValueError):
    """The MCP config file is missing required shape."""


@dataclass(frozen=True)
class MCPServerConfig:
    """One onboarded MCP server. ``command`` ⇒ stdio; ``url`` ⇒ HTTP."""

    name: str
    command: str | None = None
    args: tuple[str, ...] = ()
    env: dict[str, str] = field(default_factory=dict)
    # ``url`` and ``headers`` hold *expanded* values — what goes on the wire — so they
    # stay out of ``repr``: a config that reaches a log line or a traceback must not
    # carry a token with it. ``url_template`` is the file's own spelling, for display.
    url: str | None = field(default=None, repr=False)
    headers: dict[str, str] = field(default_factory=dict, repr=False)
    allow: tuple[str, ...] | None = None
    enabled: bool = True
    # Governance: mutating tools (not flagged read-only by the server) are
    # refused unless the operator opts the server in. Read tools are unaffected.
    write_enabled: bool = False
    url_template: str | None = field(default=None, compare=False)
    # (expanded value, "${VAR}") for every placeholder the loader filled in; see redact().
    expansions: tuple[tuple[str, str], ...] = field(default=(), repr=False, compare=False)

    @property
    def display_url(self) -> str | None:
        """The url as written in the file — placeholders intact, safe to show."""
        return self.url_template if self.url_template is not None else self.url

    def redact(self, text: str) -> str:
        """``text`` with every expanded value put back to its ``${VAR}`` placeholder.

        For error strings, which quote whatever the transport saw (an HTTP error names
        the full url, query-string token and all). Longest value first, so a value that
        contains another is replaced whole. Substring replacement can over-redact — a
        short value garbles an unrelated match — and that is the right way to be wrong.
        """
        for value, placeholder in sorted(self.expansions, key=lambda e: -len(e[0])):
            if value:
                text = text.replace(value, placeholder)
        return text

    @property
    def transport(self) -> str:
        if self.command:
            return "stdio"
        if self.url:
            return "http"
        raise MCPConfigError(f"server {self.name!r} has neither 'command' (stdio) nor 'url' (http)")

    def allows(self, tool_name: str) -> bool:
        """True when ``tool_name`` is exposed (allow-list, or all when unset)."""
        return self.allow is None or tool_name in self.allow


# ``$${VAR}`` (escape) is tried before ``${VAR}`` so a left-to-right scan consumes the
# escape whole. ``$VAR`` and a lone ``$`` match neither alternative and pass through.
_PLACEHOLDER = re.compile(r"\$\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _expand(value: str, *, server: str, where: str, seen: list[tuple[str, str]]) -> str:
    """Fill ``${VAR}`` in ``value`` from ``os.environ``; ``$${VAR}`` is a literal ``${VAR}``.

    Why only the braced form: header values legitimately contain ``$`` (prices,
    templating) and a shell-style ``$VAR`` rule would rewrite them silently. Why an unset
    variable is an error and not an empty string: ``Authorization: Bearer `` reaches the
    server as a confusing 401, where naming the missing variable and the server here is a
    one-line fix. Each substitution is appended to ``seen`` so the config can redact it
    back out of anything it later displays (:meth:`MCPServerConfig.redact`).
    """

    def fill(m: re.Match[str]) -> str:
        if m.group(1) is not None:
            return "${" + m.group(1) + "}"
        var = m.group(2)
        if var not in os.environ:
            raise MCPConfigError(
                f"server {server!r}: {where} references ${{{var}}}, but {var} is not set in the environment"
            )
        resolved = os.environ[var]
        seen.append((resolved, "${" + var + "}"))
        return resolved

    return _PLACEHOLDER.sub(fill, value)


def load_mcp_configs(path: str | Path | None = None) -> list[MCPServerConfig]:
    """Load ``mcpServers`` from a JSON file. Empty list when the file is absent.

    Path precedence: explicit ``path`` > ``$ORCHESTRATOR_MCP_CONFIG`` > ``mcp.json``.
    ``headers`` values and ``url`` have ``${VAR}`` expanded (:func:`_expand`); nothing
    else does. The file itself is never rewritten, so writers keep the placeholders.
    """
    p = Path(path or os.getenv(DEFAULT_CONFIG_ENV) or DEFAULT_CONFIG_FILE)
    if not p.is_file():
        return []
    try:
        data: Any = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise MCPConfigError(f"{p}: invalid JSON ({exc})") from exc
    servers = data.get("mcpServers") if isinstance(data, dict) else None
    if not isinstance(servers, dict):
        raise MCPConfigError(f"{p}: expected an object with a top-level 'mcpServers' map")

    configs: list[MCPServerConfig] = []
    for name, raw in servers.items():
        if not isinstance(raw, dict):
            raise MCPConfigError(f"{p}: server {name!r} must be an object")
        configs.append(server_config_from_spec(str(name), raw))
    return configs


def server_config_from_spec(name: str, raw: dict[str, Any]) -> MCPServerConfig:
    """One ``mcpServers`` entry → :class:`MCPServerConfig`, placeholders expanded.

    Split out of :func:`load_mcp_configs` so a caller holding a spec it has just
    written (the Connections page's add) builds the same expanded config the next
    load would, rather than testing a literal ``${VAR}`` url.
    """
    allow = raw.get("allow")
    seen: list[tuple[str, str]] = []
    url_template = raw.get("url")
    url = _expand(str(url_template), server=name, where="url", seen=seen) if url_template else url_template
    headers = {
        str(k): _expand(str(v), server=name, where=f"header {str(k)!r}", seen=seen)
        for k, v in (raw.get("headers") or {}).items()
    }
    return MCPServerConfig(
        name=name,
        command=raw.get("command"),
        args=tuple(str(a) for a in (raw.get("args") or [])),
        env={str(k): str(v) for k, v in (raw.get("env") or {}).items()},
        url=url,
        headers=headers,
        allow=tuple(str(a) for a in allow) if isinstance(allow, list) else None,
        enabled=bool(raw.get("enabled", True)),
        write_enabled=bool(raw.get("write_enabled", False)),
        url_template=url_template,
        expansions=tuple(seen),
    )


def resolve_config_path(path: str | None = None) -> Path:
    """The mcp.json path to read/write. Precedence: explicit ``path`` >
    ``$ORCHESTRATOR_MCP_CONFIG`` > ``mcp.json`` in the cwd. ``~`` is expanded."""
    return Path(path or os.getenv(DEFAULT_CONFIG_ENV) or DEFAULT_CONFIG_FILE).expanduser()


def _read_config_doc(p: Path) -> dict[str, Any]:
    if not p.is_file():
        return {}
    try:
        data: Any = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise MCPConfigError(f"{p}: invalid JSON ({exc})") from exc
    if not isinstance(data, dict):
        raise MCPConfigError(f"{p}: expected a JSON object")
    return data


def upsert_mcp_server(path: str | None, name: str, spec: dict[str, Any]) -> Path:
    """Add or replace the ``mcpServers[name]`` entry in the config file, creating
    the file (and parents) if needed. Preserves other keys. Returns the path."""
    if not name.strip():
        raise MCPConfigError("server name is required")
    p = resolve_config_path(path)
    doc = _read_config_doc(p)
    servers = doc.get("mcpServers")
    if not isinstance(servers, dict):
        servers = {}
    servers[name] = {k: v for k, v in spec.items() if v is not None}
    doc["mcpServers"] = servers
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    return p


def remove_mcp_server(path: str | None, name: str) -> bool:
    """Remove ``mcpServers[name]``. Returns True if it existed."""
    p = resolve_config_path(path)
    doc = _read_config_doc(p)
    servers = doc.get("mcpServers")
    if not isinstance(servers, dict) or name not in servers:
        return False
    del servers[name]
    doc["mcpServers"] = servers
    p.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    return True


__all__ = [
    "DEFAULT_CONFIG_ENV",
    "MCPConfigError",
    "MCPServerConfig",
    "load_mcp_configs",
    "remove_mcp_server",
    "resolve_config_path",
    "server_config_from_spec",
    "upsert_mcp_server",
]
