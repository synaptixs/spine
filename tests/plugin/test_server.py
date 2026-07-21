"""The orchestrator-as-MCP-server plugin: tool impls + a stdio dogfood smoke."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

from orchestrator.plugin.server import (
    blast_radius,
    doctor,
    explain_symbol,
    ingest_preview,
    investigate,
    localize,
    map_repo,
    pkg_grounding,
    regression_gaps,
    root_cause,
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


# ---- comprehension / graph-query tools (read-only, no `mcp` extra needed) ---------

_APP = "def validate(x):\n    if not x:\n        raise ValueError('empty')\n    return True\n"
_WEB = "import app\n\n\ndef handler(x):\n    return app.validate(x)\n"
_TEST = "import app\n\n\ndef test_validate():\n    assert app.validate(1)\n"


def _comprehension_repo(tmp_path: Path) -> str:
    (tmp_path / "app.py").write_text(_APP, encoding="utf-8")
    (tmp_path / "web.py").write_text(_WEB, encoding="utf-8")
    (tmp_path / "test_app.py").write_text(_TEST, encoding="utf-8")
    return str(tmp_path)


def test_map_repo_structured_and_markdown(tmp_path: Path) -> None:
    out = map_repo(_comprehension_repo(tmp_path))
    assert "python" in out["languages"]
    assert {"languages", "call_hotspots", "coverage", "recommendations", "markdown"} <= set(out)
    assert out["files"] >= 3 and "total_areas" in out["coverage"]
    assert out["markdown"].startswith("# Current State")


def test_map_repo_rejects_unknown_lens(tmp_path: Path) -> None:
    assert "error" in map_repo(_comprehension_repo(tmp_path), lens="martian")


def test_blast_radius_reports_callers_and_touches(tmp_path: Path) -> None:
    out = blast_radius(_comprehension_repo(tmp_path), "validate")
    assert out["found"]
    m = out["matches"][0]
    # `handler` calls `validate`, so it's a caller and in the blast radius.
    assert m["caller_count"] >= 1
    assert any("handler" in c["id"] for c in m["callers"])
    assert "markdown" in out


def test_blast_radius_not_found(tmp_path: Path) -> None:
    out = blast_radius(_comprehension_repo(tmp_path), "does_not_exist")
    assert out["found"] is False and out["matches"] == []


def test_explain_symbol_lists_callers(tmp_path: Path) -> None:
    out = explain_symbol(_comprehension_repo(tmp_path), "validate")
    assert out["found"]
    assert any("handler" in c for c in out["matches"][0]["called_by"])


def test_investigate_lands_on_real_symbols(tmp_path: Path) -> None:
    out = investigate(_comprehension_repo(tmp_path), "validate rejects empty input")
    names = {h["name"] for h in out["landing"]}
    assert "validate" in names
    assert out["markdown"].startswith("# Investigation")


def test_investigate_requires_a_ticket(tmp_path: Path) -> None:
    assert "error" in investigate(_comprehension_repo(tmp_path), "", "")


def test_localize_resolves_the_fault_frame(tmp_path: Path) -> None:
    repo = _comprehension_repo(tmp_path)
    trace = (
        "Traceback (most recent call last):\n"
        f'  File "{Path(repo) / "app.py"}", line 3, in validate\n'
        "    raise ValueError('empty')\n"
        "ValueError: empty\n"
    )
    out = localize(repo, trace)
    assert out["grounded"] and out["fault"] is not None
    assert out["fault"]["func"] == "validate"
    assert "ValueError" in out["exception"]


def test_localize_requires_a_trace(tmp_path: Path) -> None:
    assert "error" in localize(_comprehension_repo(tmp_path), "   ")


def test_regression_gaps_flags_untested_caller(tmp_path: Path) -> None:
    # A test exercises `validate`, but `handler` (in its blast radius) has no covering test.
    out = regression_gaps(_comprehension_repo(tmp_path), symbol="validate")
    assert out["found"]
    assert any(u["name"] == "handler" for u in out["uncovered"])


def test_regression_gaps_needs_symbol_or_trace(tmp_path: Path) -> None:
    assert "error" in regression_gaps(_comprehension_repo(tmp_path))


async def test_root_cause_deterministic_by_default(tmp_path: Path) -> None:
    repo = _comprehension_repo(tmp_path)
    trace = (
        "Traceback (most recent call last):\n"
        f'  File "{Path(repo) / "app.py"}", line 3, in validate\n'
        "    raise ValueError('empty')\n"
        "ValueError: empty\n"
    )
    out = await root_cause(repo, trace)  # use_llm defaults to False — no key needed
    assert out["used_llm"] is False
    assert "validate" in out["fault_site"]
    assert out["hypotheses"] and "markdown" in out


async def test_root_cause_requires_a_bug(tmp_path: Path) -> None:
    assert "error" in await root_cause(_comprehension_repo(tmp_path), "   ")


async def test_root_cause_llm_without_model_errors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # use_llm=true without a configured model must fail fast with a clear message (no crash).
    monkeypatch.setattr("orchestrator.sdlc.codegen.resolve_codegen_model", lambda *a, **k: None)
    out = await root_cause(_comprehension_repo(tmp_path), "boom", use_llm=True)
    assert "error" in out and "model" in out["error"]


def test_bad_repo_path_returns_error_not_exception(tmp_path: Path) -> None:
    missing = str(tmp_path / "nope")
    assert "error" in map_repo(missing)
    assert "error" in blast_radius(missing, "x")


def test_disallowed_git_url_is_rejected() -> None:
    # A URL on a non-allowlisted host is refused by the same SSRF guard as the CLI (no clone).
    out = map_repo("https://evil.example.com/x/y.git")
    assert "error" in out


def test_comprehension_tools_are_registered() -> None:
    from orchestrator.plugin.server import _TOOLS

    names = {fn.__name__ for fn in _TOOLS}
    assert {
        "map_repo",
        "blast_radius",
        "explain_symbol",
        "investigate",
        "localize",
        "regression_gaps",
        "root_cause",
    } <= names


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


async def test_sdlc_feature_passes_greenfield_brownfield_params(monkeypatch: pytest.MonkeyPatch) -> None:
    # repo/language/layout/package_name must reach run_feature so a host (the Codex
    # app) can drive both greenfield (layout=new) and brownfield (layout=existing).
    seen: dict[str, Any] = {}

    async def _capture(source: str, **kwargs: Any) -> Any:
        seen.update(kwargs)
        from orchestrator.sdlc.feature_runner import FeatureRunResult

        return FeatureRunResult(
            passed=True,
            intent_id="i",
            issue_key="DRY-1",
            title="t",
            branch="b",
            worktree="/tmp/x",
            grounding_chars=0,
            iterations=1,
            live=False,
            files=[],
        )

    monkeypatch.setattr("orchestrator.sdlc.feature_runner.run_feature", _capture)
    await sdlc_feature(
        "file://./spec.md",
        repo="me/app",
        language="cpp",
        layout="existing",
        package_name="widgets",
    )
    assert seen["repo"] == "me/app"
    assert seen["language"] == "cpp"
    assert seen["layout_mode"] == "existing"  # tool's `layout` → runner's `layout_mode`
    assert seen["package_name"] == "widgets"


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
