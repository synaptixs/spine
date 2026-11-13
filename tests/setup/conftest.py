"""A temporary home and fake client CLIs for every ``orchestrator setup`` test (D16).

``setup`` writes other applications' configuration, so no test in this package may reach
the developer's real files. The autouse fixture points ``HOME`` and ``XDG_CONFIG_HOME`` at a
temporary directory and replaces ``PATH`` with a directory of fake client programs, before
any test body runs.

The fakes do what the real clients do to their own files, closely enough for setup's
behaviour to be checked: ``claude mcp add -s user`` writes the top-level ``mcpServers`` of
``~/.claude.json``, and ``codex mcp list --json`` prints every server's env values in plain
text, as the real one does. That is why setup must never print it. Every call is appended to
``calls.log`` outside the home directory, so a test can read exactly what setup ran.
"""

from __future__ import annotations

import os
import stat
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

_FAKE_CLAUDE = r"""#!{python}
import json, os, sys
from pathlib import Path
log = Path(os.environ["SPINE_TEST_CALLS"])
with log.open("a") as fh:
    fh.write("claude " + json.dumps(sys.argv[1:]) + "\n")
args = sys.argv[1:]
cfg = Path(os.environ["HOME"]) / ".claude.json"
data = json.loads(cfg.read_text()) if cfg.exists() else {{}}
if args[:2] == ["mcp", "add"]:
    rest = args[2:]
    sep = rest.index("--")
    opts, cmd = rest[:sep], rest[sep + 1:]
    env, scope, name, i = {{}}, "local", None, 0
    while i < len(opts):
        if opts[i] in ("-s", "--scope"):
            scope = opts[i + 1]; i += 2
        elif opts[i] in ("-e", "--env"):
            k, _, v = opts[i + 1].partition("="); env[k] = v; i += 2
        else:
            name = opts[i]; i += 1
    assert scope == "user", scope
    servers = data.setdefault("mcpServers", {{}})
    if name in servers:
        print(f"MCP server {{name}} already exists in user config", file=sys.stderr)
        sys.exit(1)
    servers[name] = {{"type": "stdio", "command": cmd[0], "args": cmd[1:], "env": env}}
    cfg.write_text(json.dumps(data, indent=2))
elif args[:2] == ["mcp", "remove"]:
    data.get("mcpServers", {{}}).pop(args[2], None)
    cfg.write_text(json.dumps(data, indent=2))
elif args[:2] == ["mcp", "list"]:
    print("the real `claude mcp list` starts every server; setup must not call it")
    sys.exit(3)
"""

_FAKE_CODEX = r"""#!{python}
import json, os, sys, tomllib
from pathlib import Path
log = Path(os.environ["SPINE_TEST_CALLS"])
with log.open("a") as fh:
    fh.write("codex " + json.dumps(sys.argv[1:]) + "\n")
cfg = Path(os.environ["HOME"]) / ".codex" / "config.toml"
if sys.argv[1:3] == ["mcp", "list"]:
    data = tomllib.loads(cfg.read_text()) if cfg.exists() else {{}}
    print(json.dumps(data.get("mcp_servers", {{}})))
"""


@dataclass(frozen=True)
class SetupEnv:
    """Where a test's fake world lives."""

    root: Path
    home: Path
    bin: Path
    calls: Path

    @property
    def config(self) -> Path:
        return self.home / ".config"

    @property
    def claude_json(self) -> Path:
        return self.home / ".claude.json"

    @property
    def codex_toml(self) -> Path:
        return self.home / ".codex" / "config.toml"

    @property
    def user_env(self) -> Path:
        return self.config / "spine" / ".env"

    def call_log(self) -> list[str]:
        return self.calls.read_text().splitlines() if self.calls.exists() else []


def _install(path: Path, body: str) -> None:
    path.write_text(body.format(python=sys.executable))
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


@pytest.fixture(autouse=True)
def setup_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SetupEnv:
    home = tmp_path / "home"
    home.mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _install(bin_dir / "claude", _FAKE_CLAUDE)
    _install(bin_dir / "codex", _FAKE_CODEX)
    calls = tmp_path / "calls.log"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.delenv("APPDATA", raising=False)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{Path(sys.executable).parent}")
    monkeypatch.setenv("SPINE_TEST_CALLS", str(calls))
    monkeypatch.delenv("ORCHESTRATOR_DOTENV", raising=False)
    monkeypatch.delenv("ORCHESTRATOR_MCP_LEVEL", raising=False)
    return SetupEnv(root=tmp_path, home=home, bin=bin_dir, calls=calls)
