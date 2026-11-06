"""What ``orchestrator setup`` writes into Claude Code and Codex (SSPN-341, D3 D6–D9 D14)."""

from __future__ import annotations

import json
import tomllib

from typer.testing import CliRunner

from orchestrator.cli import app

from .conftest import SetupEnv

EXTRAS = "mcp,docs,c,cpp,csharp,go,java,kotlin,perl,php,sql,typescript"


def _setup(*args: str) -> str:
    result = CliRunner().invoke(app, ["setup", *args])
    assert result.exit_code == 0, result.output
    return result.output


def _expected_env(setup_env: SetupEnv, level: str = "plan") -> dict[str, str]:
    return {"ORCHESTRATOR_DOTENV": str(setup_env.user_env), "ORCHESTRATOR_MCP_LEVEL": level}


# --- Claude Code -----------------------------------------------------------------------


def test_claude_gets_a_user_scope_entry_through_its_own_cli(setup_env: SetupEnv) -> None:
    _setup("--yes")

    entry = json.loads(setup_env.claude_json.read_text())["mcpServers"]["spine"]
    assert entry["command"] == "uvx"
    assert entry["args"] == ["--from", f"synaptixs-spine[{EXTRAS}]@latest", "orchestrator-mcp"]
    assert entry["env"] == _expected_env(setup_env)
    claude_calls = [c for c in setup_env.call_log() if c.startswith("claude ")]
    assert claude_calls and all('"list"' not in c for c in claude_calls), claude_calls


def test_a_different_claude_entry_is_backed_up_then_replaced(setup_env: SetupEnv) -> None:
    old = {"mcpServers": {"spine": {"command": "orchestrator-mcp", "args": [], "env": {}}}}
    setup_env.claude_json.write_text(json.dumps(old))

    output = _setup("--yes")

    entry = json.loads(setup_env.claude_json.read_text())["mcpServers"]["spine"]
    assert entry["command"] == "uvx"
    backups = list(setup_env.home.glob(".claude.json.bak-spine-*"))
    assert len(backups) == 1 and json.loads(backups[0].read_text()) == old
    assert "replace" in output


def test_level_and_pin_reach_the_entry(setup_env: SetupEnv) -> None:
    _setup("--yes", "--level", "build", "--pin", "3.69.0")

    entry = json.loads(setup_env.claude_json.read_text())["mcpServers"]["spine"]
    assert entry["args"][1] == f"synaptixs-spine[{EXTRAS}]==3.69.0"
    assert entry["env"]["ORCHESTRATOR_MCP_LEVEL"] == "build"


def test_an_unknown_level_is_refused_before_anything_is_written(setup_env: SetupEnv) -> None:
    result = CliRunner().invoke(app, ["setup", "--yes", "--level", "everything"])

    assert result.exit_code != 0
    # The refusal names the levels that exist. A missing `setup` command also exits non-zero,
    # so the exit code alone would pass on code that never checked the level.
    assert "pkg, plan, build" in result.output
    assert not setup_env.claude_json.exists() and not setup_env.codex_toml.exists()


# --- Codex -----------------------------------------------------------------------------


def test_codex_gets_the_entry_with_timeouts_and_env(setup_env: SetupEnv) -> None:
    _setup("--yes")

    server = tomllib.loads(setup_env.codex_toml.read_text())["mcp_servers"]["spine"]
    assert server["command"] == "uvx"
    assert server["args"] == ["--from", f"synaptixs-spine[{EXTRAS}]@latest", "orchestrator-mcp"]
    assert server["startup_timeout_sec"] == 60
    assert server["tool_timeout_sec"] == 600
    assert server["env"] == _expected_env(setup_env)


def test_the_rest_of_the_codex_config_is_kept_exactly(setup_env: SetupEnv) -> None:
    setup_env.codex_toml.parent.mkdir(parents=True)
    others = (
        'model = "gpt-5"\n\n'
        '[mcp_servers.atlassian]\ncommand = "uvx"\nargs = ["mcp-atlassian"]\n\n'
        '[mcp_servers.atlassian.env]\nJIRA_API_TOKEN = "t"\n'
    )
    setup_env.codex_toml.write_text(
        others + '\n[mcp_servers.spine]\ncommand = "orchestrator-mcp"\nargs = []\n'
    )

    _setup("--yes")

    text = setup_env.codex_toml.read_text()
    assert text.startswith(others)
    parsed = tomllib.loads(text)
    assert parsed["model"] == "gpt-5"
    assert parsed["mcp_servers"]["atlassian"]["env"] == {"JIRA_API_TOKEN": "t"}
    assert parsed["mcp_servers"]["spine"]["command"] == "uvx"
    assert len(list(setup_env.codex_toml.parent.glob("config.toml.bak-spine-*"))) == 1


# --- Asking, previewing, choosing (D3, D14) ---------------------------------------------


def test_dry_run_prints_the_changes_and_writes_nothing(setup_env: SetupEnv) -> None:
    output = _setup("--dry-run")

    assert "claude" in output.lower() and "codex" in output.lower()
    assert not setup_env.claude_json.exists() and not setup_env.codex_toml.exists()
    assert not any(c.startswith("claude ") and '"add"' in c for c in setup_env.call_log())


def test_without_a_terminal_and_without_yes_it_only_previews(setup_env: SetupEnv) -> None:
    # CliRunner's stdin is not a TTY: an agent or a CI job running setup gets a preview.
    output = _setup()

    assert "--yes" in output
    assert not setup_env.claude_json.exists() and not setup_env.codex_toml.exists()


def test_client_limits_setup_to_the_named_clients(setup_env: SetupEnv) -> None:
    _setup("--yes", "--client", "codex")

    assert setup_env.codex_toml.exists()
    assert not setup_env.claude_json.exists()


def test_a_client_that_is_not_installed_is_skipped_with_its_reason(setup_env: SetupEnv) -> None:
    (setup_env.bin / "claude").unlink()

    output = _setup("--yes")

    assert "claude" in output.lower() and "not found" in output.lower()
    assert not setup_env.claude_json.exists()
    assert setup_env.codex_toml.exists()
