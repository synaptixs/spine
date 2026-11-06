"""Claude Code: a user-scope entry, written through Claude's own CLI.

- **It reads ``~/.claude.json`` directly, never through ``claude mcp list``.** That command
  starts every configured server to health-check it, measured on 2.1.312.
- **It writes with ``claude mcp add -s user``, not by editing the file.** ``~/.claude.json``
  is Claude's live state file and Claude rewrites it while running, so a direct edit could
  race it. User scope is the top-level ``mcpServers``.
- **Replacing an entry is remove, then add.** What ``add`` does to an existing name is
  unknown, so it is never relied on; the live run in P5 records it.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from orchestrator.setup.clients.base import find_program
from orchestrator.setup.entry import SERVER_NAME, ServerEntry


class ClientError(RuntimeError):
    """A client's own CLI refused a change. The message never carries the CLI's output."""


class ClaudeCode:
    name = "claude"

    def program(self) -> Path | None:
        home = Path.home()
        return find_program(
            "claude", [home / ".local" / "bin" / "claude", home / ".claude" / "local" / "claude"]
        )

    def config_path(self) -> Path:
        return Path.home() / ".claude.json"

    def current(self) -> ServerEntry | None:
        path = self.config_path()
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        raw = (data.get("mcpServers") or {}).get(SERVER_NAME) if isinstance(data, dict) else None
        if not isinstance(raw, dict):
            return None
        env = raw.get("env") or {}
        return ServerEntry(
            command=str(raw.get("command", "")),
            args=tuple(str(a) for a in raw.get("args") or ()),
            env=tuple((str(k), str(v)) for k, v in env.items()),
        )

    def matches(self, wanted: ServerEntry) -> bool:
        have = self.current()
        return (
            have is not None
            and have.command == wanted.command
            and have.args == wanted.args
            and have.env_dict == wanted.env_dict
        )

    def write(self, wanted: ServerEntry) -> None:
        program = self.program()
        if program is None:
            raise ClientError("claude is not installed")
        if self.current() is not None:
            self._run([str(program), "mcp", "remove", SERVER_NAME, "-s", "user"], "remove")
        env_args = [part for key, value in wanted.env for part in ("-e", f"{key}={value}")]
        self._run(
            [
                str(program),
                "mcp",
                "add",
                "-s",
                "user",
                SERVER_NAME,
                *env_args,
                "--",
                wanted.command,
                *wanted.args,
            ],
            "add",
        )

    @staticmethod
    def _run(argv: list[str], what: str) -> None:
        done = subprocess.run(argv, capture_output=True, text=True, check=False)
        if done.returncode != 0:
            raise ClientError(f"`claude mcp {what}` failed (exit {done.returncode})")
