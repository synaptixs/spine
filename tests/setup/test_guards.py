"""The three guards ``orchestrator setup`` must never break (D16, SSPN-341).

Setup writes other applications' configuration, so these are properties of the whole
command rather than of any one client:

1. it writes nothing outside the user's home directory (here, the temporary one), seen
   through an audit hook so a write anywhere on disk counts;
2. no secret it reads, from a client's config or a client's own output, reaches what it prints;
3. an entry that already says what setup would write is left byte-for-byte as it was.

All three run through the real command line, the way a user meets them.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

from typer.testing import CliRunner

from orchestrator.cli import app

from .conftest import SetupEnv

PLANTED = "sk-ant-PLANTED-never-print-me-0123456789"


def _snapshot(root: Path) -> dict[str, str]:
    """Every file under ``root``, keyed by path, valued by a content hash."""
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def _setup(*args: str) -> str:
    result = CliRunner().invoke(app, ["setup", *args])
    assert result.exit_code == 0, result.output
    return result.output


_WRITE_EVENTS = {
    "os.rename",
    "os.replace",
    "os.remove",
    "os.mkdir",
    "os.chmod",
    "shutil.copyfile",
    "shutil.copymode",
}
_WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC
_watching: list[list[str]] = []


def _audit(event: str, args: tuple[object, ...]) -> None:
    """Note every path this process opens for writing, or renames, removes or creates."""
    if not _watching:
        return
    paths: list[object] = []
    if event == "open" and len(args) >= 3:
        path, mode, flags = args[0], args[1], args[2]
        writing = (isinstance(mode, str) and any(c in mode for c in "wax+")) or (
            isinstance(flags, int) and flags & _WRITE_FLAGS
        )
        if writing:
            paths.append(path)
    elif event in _WRITE_EVENTS:
        paths.extend(args[:2] if event in ("os.rename", "os.replace", "shutil.copyfile") else args[:1])
    for p in paths:
        if isinstance(p, (str, bytes, os.PathLike)):
            _watching[-1].append(os.fsdecode(p))


sys.addaudithook(_audit)  # an audit hook cannot be removed; it does nothing unless a test is watching


def test_setup_writes_nothing_outside_the_home_directory(setup_env: SetupEnv) -> None:
    # The audit hook sees every write this process makes, wherever it lands, so a path bug
    # that writes into the repository or the real home is caught, not just one inside
    # tmp_path. The fake clients write in their own processes, always under $HOME.
    before = _snapshot(setup_env.home)
    _watching.append([])
    try:
        _setup("--yes")
    finally:
        written = _watching.pop()

    home = str(setup_env.home.resolve())
    stray = sorted(
        {
            p
            for p in written
            if not str(Path(p).resolve()).startswith(home + os.sep)
            and "__pycache__" not in p  # the interpreter caching bytecode on first import
        }
    )
    assert stray == [], stray
    assert _snapshot(setup_env.home) != before, "setup --yes wrote nothing at all"


def test_no_secret_reaches_the_output(setup_env: SetupEnv) -> None:
    setup_env.codex_toml.parent.mkdir(parents=True)
    setup_env.codex_toml.write_text(
        '[mcp_servers.atlassian]\ncommand = "uvx"\nargs = ["mcp-atlassian"]\n\n'
        f'[mcp_servers.atlassian.env]\nJIRA_API_TOKEN = "{PLANTED}"\n'
    )
    # The old `spine` entry differs from what setup writes, so it is replaced, and the
    # replace line describes it: the likeliest place for one of its values to leak.
    setup_env.claude_json.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "jira": {"command": "x", "args": [], "env": {"TOKEN": PLANTED}},
                    "spine": {
                        "command": "orchestrator-mcp",
                        "args": [],
                        "env": {"ANTHROPIC_API_KEY": PLANTED},
                    },
                }
            }
        )
    )

    for args in (("--dry-run",), ("--yes",), ("--yes",)):
        output = _setup(*args)
        assert PLANTED not in output, args


def test_an_identical_entry_is_left_byte_identical(setup_env: SetupEnv) -> None:
    _setup("--yes")
    before = {p: p.read_bytes() for p in (setup_env.claude_json, setup_env.codex_toml)}
    calls_before = len(setup_env.call_log())

    output = _setup("--yes")

    assert {p: p.read_bytes() for p in before} == before
    assert output.count("already set") == 2, output
    assert not any('"add"' in line or '"remove"' in line for line in setup_env.call_log()[calls_before:]), (
        "a second run called a client to change something"
    )
