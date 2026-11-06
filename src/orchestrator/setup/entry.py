"""The one MCP server entry every client should carry.

Every client runs the same line (AGENT_GUIDE §3): ``uvx --from
'synaptixs-spine[<extras>]@latest' orchestrator-mcp``. The level and the keys file travel
as env vars, not as arguments, so an entry reads the same in every client's format.

- **The extras are every language with a prebuilt wheel**, plus the server and document
  ingestion, so no compiler is needed. Rust is left out because its grammar builds from
  source.
- **`@latest` is the default** (D9): each client start checks PyPI, and a new release
  arrives with no edit. ``pin`` writes ``==<version>`` instead.
- **The level defaults to ``plan``** (D8): the read-only graph tools, plus the plan and its
  approval. The tools that spend on builds stay behind ``build``.
- **``ORCHESTRATOR_DOTENV`` points at the user's keys file** (D6), never at one
  repository's ``.env``. A user-scope entry pointing at repository A would hand repository
  B, when B has no ``.env``, A's ``SDLC_REPO_URL``, because the server never overwrites a
  variable that is already set (``core/env.py``).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

SERVER_NAME = "spine"
PACKAGE = "synaptixs-spine"
SPINE_EXTRAS = (
    "mcp",
    "docs",
    "c",
    "cpp",
    "csharp",
    "go",
    "java",
    "kotlin",
    "perl",
    "php",
    "sql",
    "typescript",
)

#: The server's adoption levels, in order (``plugin.server.LEVELS``; a test keeps them equal).
#: Kept here so ``setup`` can check ``--level`` without importing the MCP SDK.
LEVELS = ("pkg", "plan", "build")
DEFAULT_LEVEL = "plan"

_VERSION = re.compile(r"[0-9][0-9A-Za-z.+!-]*")


@dataclass(frozen=True)
class ServerEntry:
    """A stdio MCP server, as every client stores one: a command, its arguments, its env."""

    command: str
    args: tuple[str, ...]
    env: tuple[tuple[str, str], ...]

    @property
    def env_dict(self) -> dict[str, str]:
        return dict(self.env)

    def describe(self) -> str:
        """One line for the terminal: the command line and the env var *names* only."""
        names = ", ".join(k for k, _ in self.env) or "none"
        return f"{self.command} {' '.join(self.args)}  (env: {names})"


def user_env_path() -> Path:
    """The user's keys file, ``$XDG_CONFIG_HOME/spine/.env`` (default ``~/.config``)."""
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "spine" / ".env"


def desired_entry(*, level: str = DEFAULT_LEVEL, pin: str | None = None) -> ServerEntry:
    """The entry setup writes. Raises ``ValueError`` for a level the server does not have."""
    if level not in LEVELS:
        raise ValueError(f"unknown level {level!r}: use one of {', '.join(LEVELS)}")
    if pin is not None and not _VERSION.fullmatch(pin):
        raise ValueError(f"--pin takes a release number such as 3.69.0, not {pin!r}")
    version = f"=={pin}" if pin else "@latest"
    spec = f"{PACKAGE}[{','.join(SPINE_EXTRAS)}]{version}"
    env = (("ORCHESTRATOR_DOTENV", str(user_env_path())), ("ORCHESTRATOR_MCP_LEVEL", level))
    return ServerEntry(command="uvx", args=("--from", spec, "orchestrator-mcp"), env=env)
