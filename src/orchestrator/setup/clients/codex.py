"""Codex: the ``[mcp_servers.spine]`` tables of ``~/.codex/config.toml``.

**This adapter edits the file rather than calling ``codex mcp add``.** That command writes
only the command and its arguments. Builds also need ``tool_timeout_sec`` raised
(AGENT_GUIDE, Codex configuration), so the file needs editing anyway, and one writer is
easier to reason about than two.

**Only Spine's own tables are touched.** The rest of the file, including other servers'
tokens kept in plain text, is copied through byte for byte. The ``spine`` tables are cut
from their header line to the next header that is not one of theirs, and the new block is
appended at the end.

**It never runs ``codex mcp list --json``,** which prints every server's env values.
"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

from orchestrator.setup.clients.base import find_program
from orchestrator.setup.entry import SERVER_NAME, ServerEntry

STARTUP_TIMEOUT_SEC = 60
TOOL_TIMEOUT_SEC = 600

_TABLE = f"mcp_servers.{SERVER_NAME}"


class Codex:
    name = "codex"

    def program(self) -> Path | None:
        return find_program(
            "codex",
            [
                Path("/Applications/Codex.app/Contents/Resources/codex"),
                Path(
                    "/Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex"
                ),
            ],
        )

    def config_path(self) -> Path:
        return Path.home() / ".codex" / "config.toml"

    def _table(self) -> dict[str, object] | None:
        path = self.config_path()
        if not path.is_file():
            return None
        try:
            data = tomllib.loads(path.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError):
            return None
        table = (data.get("mcp_servers") or {}).get(SERVER_NAME)
        return table if isinstance(table, dict) else None

    def current(self) -> ServerEntry | None:
        table = self._table()
        if table is None:
            return None
        env = table.get("env") or {}
        args = table.get("args") or ()
        return ServerEntry(
            command=str(table.get("command", "")),
            args=tuple(str(a) for a in args) if isinstance(args, list) else (),
            env=tuple((str(k), str(v)) for k, v in env.items()) if isinstance(env, dict) else (),
        )

    def matches(self, wanted: ServerEntry) -> bool:
        have, table = self.current(), self._table() or {}
        return (
            have is not None
            and have.command == wanted.command
            and have.args == wanted.args
            and have.env_dict == wanted.env_dict
            and table.get("startup_timeout_sec") == STARTUP_TIMEOUT_SEC
            and table.get("tool_timeout_sec") == TOOL_TIMEOUT_SEC
        )

    def write(self, wanted: ServerEntry) -> None:
        path = self.config_path()
        text = path.read_text(encoding="utf-8") if path.is_file() else ""
        rest = _without_spine_tables(text).rstrip("\n")
        block = render_block(wanted)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{rest}\n\n{block}" if rest else block, encoding="utf-8")


def render_block(entry: ServerEntry) -> str:
    """The ``spine`` tables as TOML. JSON string and array syntax is valid TOML for these values."""
    lines = [
        f"[{_TABLE}]",
        f"command = {json.dumps(entry.command)}",
        f"args = {json.dumps(list(entry.args))}",
        f"startup_timeout_sec = {STARTUP_TIMEOUT_SEC}",
        f"tool_timeout_sec = {TOOL_TIMEOUT_SEC}",
    ]
    if entry.env:
        lines += ["", f"[{_TABLE}.env]"] + [f"{key} = {json.dumps(value)}" for key, value in entry.env]
    return "\n".join(lines) + "\n"


def _is_spine_header(stripped: str) -> bool:
    return stripped in (f"[{_TABLE}]",) or stripped.startswith(f"[{_TABLE}.")


def _without_spine_tables(text: str) -> str:
    """``text`` with every ``[mcp_servers.spine]`` and ``[mcp_servers.spine.*]`` table cut out."""
    kept: list[str] = []
    inside = False
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        if stripped.startswith("["):
            inside = _is_spine_header(stripped)
        if not inside:
            kept.append(line)
    return "".join(kept)
