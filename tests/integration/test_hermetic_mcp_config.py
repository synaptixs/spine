"""The gateway end-to-end test must not read the developer's own ``mcp.json`` (B42).

``test_end_to_end_invoke_echo`` asserts ``loaded_tools == 1`` but builds the gateway app, which loads
every server in ``$ORCHESTRATOR_MCP_CONFIG``, else ``mcp.json`` in the working directory. A checkout
with an onboarded server in its gitignored ``mcp.json`` got ``assert 9 == 1`` (eight allow-listed tools
plus the test's echo). The integration test needs Postgres and is deselected by default, so what is
proved here, with no database, is the fixture it now uses.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from orchestrator.mcp.config import load_mcp_configs

_ONBOARDED = {"mcpServers": {"atlassian": {"command": "docker", "args": ["run", "x"]}}}


def test_a_working_directory_mcp_json_is_read_when_nothing_isolates_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The precondition: this is the leak the fixture closes."""
    (tmp_path / "mcp.json").write_text(json.dumps(_ONBOARDED), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ORCHESTRATOR_MCP_CONFIG", raising=False)

    assert [c.name for c in load_mcp_configs()] == ["atlassian"]


def test_the_fixture_hides_a_working_directory_mcp_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, hermetic_mcp_config: Path
) -> None:
    # The developer's file is in the working directory, exactly as in the failing checkout.
    (tmp_path / "mcp.json").write_text(json.dumps(_ONBOARDED), encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    assert load_mcp_configs() == []


def test_the_fixture_points_at_a_file_it_owns(hermetic_mcp_config: Path) -> None:
    assert hermetic_mcp_config.is_file()
    assert json.loads(hermetic_mcp_config.read_text(encoding="utf-8")) == {"mcpServers": {}}


def test_the_gateway_test_uses_the_fixture() -> None:
    """The fixture is worth nothing if the test that needs it does not ask for it."""
    import ast

    source = Path(__file__).with_name("test_gateway.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    uses = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef)
        and node.name == "test_end_to_end_invoke_echo"
        and "hermetic_mcp_config" in {a.arg for a in node.args.args}
    ]
    assert uses, "test_end_to_end_invoke_echo must take the hermetic_mcp_config fixture"
