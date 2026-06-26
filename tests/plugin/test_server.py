"""The orchestrator-as-MCP-server plugin: tool impls + a stdio dogfood smoke."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

from orchestrator.plugin.server import (
    doctor,
    ingest_preview,
    pkg_grounding,
    sdlc_decide_gate,
    sdlc_feature,
    sdlc_run_result,
    sdlc_run_status,
    sdlc_start_run,
)

LEDGER = '''\
class TokenLedger:
    """Tracks per-stage token usage."""

    def record(self, stage, result):
        return None
'''


# ---- tool implementations (no `mcp` extra needed) ---------------------------


def test_doctor_returns_readiness_structure() -> None:
    out = doctor()
    assert isinstance(out["all_passed"], bool)
    names = {c["name"] for c in out["checks"]}
    assert {"LLM provider", "Confluence", "Jira"} <= names


def test_pkg_grounding_surfaces_existing_symbols(tmp_path: Path) -> None:
    (tmp_path / "ledger.py").write_text(LEDGER, encoding="utf-8")
    out = pkg_grounding(str(tmp_path), "persist the token ledger to disk")
    assert out["chars"] > 0
    assert "TokenLedger" in out["context"]


def test_pkg_grounding_empty_for_unrelated_repo(tmp_path: Path) -> None:
    (tmp_path / "unrelated.py").write_text("class WebhookRouter:\n    pass\n", encoding="utf-8")
    out = pkg_grounding(str(tmp_path), "persist the token ledger to disk")
    assert out["chars"] == 0 and out["context"] == ""


async def test_ingest_preview_summarizes_a_dry_run(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Intent:
        def __init__(self, i: str, t: str) -> None:
            self.id, self.title = i, t

    class _Plan:
        documents = [object()]
        intents = [_Intent("intent-csv-export", "CSV export")]
        gaps: list[Any] = []
        blocked = False

    class _Service:
        async def analyze(self, root: str) -> _Plan:
            return _Plan()

    monkeypatch.setattr("orchestrator.intake.factory.build_service_for", lambda *a, **k: _Service())
    out = await ingest_preview("file://./spec.md")
    assert out["intent_count"] == 1
    assert out["intents"][0]["id"] == "intent-csv-export"
    assert out["blocked"] is False


# ---- sdlc_feature: the gated "deliver a ticket" tool ------------------------


async def test_sdlc_feature_live_requires_confirm() -> None:
    # The gate: a live run (real Jira + PR) is refused without explicit confirm.
    with pytest.raises(PermissionError, match="confirm"):
        await sdlc_feature("file://./spec.md", live=True, confirm=False)


async def test_sdlc_feature_safe_maps_result(monkeypatch: pytest.MonkeyPatch) -> None:
    from orchestrator.sdlc.feature_runner import FeatureRunResult

    async def _fake_run(source: str, **kwargs: Any) -> FeatureRunResult:
        return FeatureRunResult(
            passed=True,
            intent_id="intent-x",
            issue_key="DRY-1",
            title="t",
            branch="feat/x",
            worktree="/tmp/x",
            grounding_chars=12,
            iterations=1,
            live=False,
            files=["stack.py"],
        )

    monkeypatch.setattr("orchestrator.sdlc.feature_runner.run_feature", _fake_run)
    out = await sdlc_feature("file://./spec.md")
    assert out["passed"] and out["issue_key"] == "DRY-1" and out["files"] == ["stack.py"]
    assert out["live"] is False and out["pr_url"] is None


async def test_sdlc_feature_maps_run_error(monkeypatch: pytest.MonkeyPatch) -> None:
    from orchestrator.sdlc.feature_runner import FeatureRunError

    async def _boom(source: str, **kwargs: Any) -> Any:
        raise FeatureRunError("tests failed", code=1)

    monkeypatch.setattr("orchestrator.sdlc.feature_runner.run_feature", _boom)
    out = await sdlc_feature("file://./spec.md")
    assert out["passed"] is False and "tests failed" in out["error"]


# ---- job-style autonomous run tools -----------------------------------------


async def test_sdlc_start_run_create_jira_requires_confirm() -> None:
    # The write gate: starting a run that writes real Jira issues needs confirm.
    # Refused before any Temporal connection, so no workflow is started.
    with pytest.raises(PermissionError, match="confirm"):
        await sdlc_start_run("file://./spec.md", create_jira=True, confirm=False)


async def test_sdlc_start_run_delegates_to_run_control(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    async def _fake_start(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {"sdlc_id": "run123", "workflow_id": "task-run123"}

    monkeypatch.setattr("orchestrator.sdlc.run_control.start_run", _fake_start)
    out = await sdlc_start_run("file://./spec.md", max_features=1)
    assert out["sdlc_id"] == "run123"
    # Safe by default: create_jira stays off.
    assert captured["create_jira"] is False and captured["max_features"] == 1


async def test_sdlc_decide_gate_delegates(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    async def _fake_decide(sdlc_id: str, gate: str, action: str, **kwargs: Any) -> dict[str, Any]:
        captured.update({"sdlc_id": sdlc_id, "gate": gate, "action": action, **kwargs})
        return {"gate": "sdlc-run123-0", "action": action, "state": "approved"}

    monkeypatch.setattr("orchestrator.sdlc.run_control.decide_gate", _fake_decide)
    out = await sdlc_decide_gate("run123", "intents", "approve", rationale="ok")
    assert out["state"] == "approved"
    assert captured["gate"] == "intents" and captured["rationale"] == "ok"


async def test_sdlc_run_status_and_result_delegate(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_status(sdlc_id: str) -> dict[str, Any]:
        return {"sdlc_id": sdlc_id, "status": "RUNNING", "awaiting_gate": "sdlc-run123-0"}

    async def _fake_result(sdlc_id: str) -> dict[str, Any]:
        return {"sdlc_id": sdlc_id, "status": "COMPLETED", "result": {"ok": True}}

    monkeypatch.setattr("orchestrator.sdlc.run_control.run_status", _fake_status)
    monkeypatch.setattr("orchestrator.sdlc.run_control.run_result", _fake_result)
    status = await sdlc_run_status("run123")
    result = await sdlc_run_result("run123")
    assert status["awaiting_gate"] == "sdlc-run123-0"
    assert result["status"] == "COMPLETED" and result["result"] == {"ok": True}


# ---- remote (http) server builder (Phase C) ---------------------------------


@pytest.mark.skipif(importlib.util.find_spec("mcp") is None, reason="needs the 'mcp' extra")
def test_http_server_refuses_public_bind_without_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    from orchestrator.plugin.server import build_http_server

    monkeypatch.delenv("ORCHESTRATOR_MCP_TOKEN", raising=False)
    monkeypatch.delenv("ORCHESTRATOR_MCP_INTROSPECTION_URL", raising=False)
    with pytest.raises(RuntimeError, match="without auth"):
        build_http_server(host="0.0.0.0", port=8080)


@pytest.mark.skipif(importlib.util.find_spec("mcp") is None, reason="needs the 'mcp' extra")
def test_http_server_loopback_unauthenticated_is_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    from orchestrator.plugin.server import build_http_server

    monkeypatch.delenv("ORCHESTRATOR_MCP_TOKEN", raising=False)
    monkeypatch.delenv("ORCHESTRATOR_MCP_INTROSPECTION_URL", raising=False)
    server = build_http_server(host="127.0.0.1", port=8080)
    assert server.settings.auth is None


@pytest.mark.skipif(importlib.util.find_spec("mcp") is None, reason="needs the 'mcp' extra")
def test_http_server_wires_static_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    from orchestrator.plugin.server import build_http_server

    monkeypatch.setenv("ORCHESTRATOR_MCP_TOKEN", "s3cret")
    monkeypatch.setenv("ORCHESTRATOR_MCP_RESOURCE_URL", "https://mcp.example.com")
    monkeypatch.delenv("ORCHESTRATOR_MCP_INTROSPECTION_URL", raising=False)
    server = build_http_server(host="0.0.0.0", port=8080, path="/mcp")
    # Auth is configured, so a public bind is permitted and the tools are registered.
    assert server.settings.auth is not None
    assert server.settings.port == 8080 and server.settings.host == "0.0.0.0"


# ---- dogfood: drive the real stdio server (needs the `mcp` extra) -----------


@pytest.mark.skipif(importlib.util.find_spec("mcp") is None, reason="needs the 'mcp' extra")
async def test_plugin_serves_tools_over_stdio() -> None:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(command=sys.executable, args=["-m", "orchestrator.plugin"])
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        tools = await session.list_tools()
        assert {
            "doctor",
            "ingest_preview",
            "pkg_grounding",
            "sdlc_feature",
            "sdlc_start_run",
            "sdlc_run_status",
            "sdlc_decide_gate",
            "sdlc_run_result",
        } <= {t.name for t in tools.tools}
        result = await session.call_tool("doctor", {})
        assert result.isError is False
