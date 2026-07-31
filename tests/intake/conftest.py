"""Isolate intake tests from whatever MCP config the developer happens to have.

The source builders now prefer a governed MCP server over direct REST credentials wherever
one is onboarded (``factory.mcp_server_for``). That resolution reads ``./mcp.json`` — so
without this fixture a developer with a real ``mcp.json`` in the repo root silently reroutes
every REST-source test through MCP, while CI, where the file is gitignored and absent, keeps
testing the REST path.

That is the worst kind of test: green in CI, red on the machine of whoever is actually
changing the code, and disagreeing about *which code path ran*. Point the config at a path
that cannot exist, so REST is the default here; tests that want MCP set
``ORCHESTRATOR_MCP_CONFIG`` themselves.
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolate_mcp_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORCHESTRATOR_MCP_CONFIG", str(tmp_path / "no-such-mcp.json"))
