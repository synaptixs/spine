"""The orchestrator-as-MCP-server plugin: tool impls + a stdio dogfood smoke."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

from orchestrator.plugin.server import (
    blast_radius,
    docs_for,
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


def test_doctor_says_which_install_is_answering() -> None:
    """The stale-install case: a host launched an ``orchestrator-mcp`` from an old venv
    and saw only "Connection closed". The tool now names its version, interpreter and SDK
    so the host can see the mismatch."""
    import sys

    server = doctor()["server"]
    assert server["package"] == "synaptixs-spine"
    assert server["interpreter"] == sys.executable
    assert "mcp" in server["extras"]


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


def test_docs_for_summary_and_symbol(tmp_path: Path) -> None:
    repo = _comprehension_repo(tmp_path)
    (tmp_path / "README.md").write_text("The `validate` function checks input.\n", encoding="utf-8")
    summary = docs_for(repo)
    assert summary["docs"] == 1
    assert summary["documented_symbols"] >= 1
    assert "coverage" in summary["markdown"].lower()

    hit = docs_for(repo, "validate")
    assert hit["found"] is True
    assert any("README.md" in m["docs"] for m in hit["matches"])


def test_docs_for_no_docs_reports_zero(tmp_path: Path) -> None:
    assert docs_for(_comprehension_repo(tmp_path))["docs"] == 0


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
    built = build_http_server(host="127.0.0.1", port=8080)
    assert built.server.settings.auth is None


@pytest.mark.skipif(importlib.util.find_spec("mcp") is None, reason="needs the 'mcp' extra")
def test_http_server_wires_static_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    from orchestrator.plugin.server import build_http_server

    monkeypatch.setenv("ORCHESTRATOR_MCP_TOKEN", "s3cret")
    monkeypatch.setenv("ORCHESTRATOR_MCP_RESOURCE_URL", "https://mcp.example.com")
    monkeypatch.delenv("ORCHESTRATOR_MCP_INTROSPECTION_URL", raising=False)
    built = build_http_server(host="0.0.0.0", port=8080, path="/mcp")
    # Auth is configured, so a public bind is permitted and the tools are registered.
    assert built.server.settings.auth is not None
    assert built.transport["port"] == 8080 and built.transport["host"] == "0.0.0.0"


# ---- the free half of the back half: understand, profile, design, baseline ----------


def _ledger_repo(tmp_path: Path) -> Path:
    (tmp_path / "ledger.py").write_text(LEDGER, encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_ledger.py").write_text(
        "from ledger import TokenLedger\n\n\ndef test_record():\n    TokenLedger().record('s', None)\n",
        encoding="utf-8",
    )
    return tmp_path


def test_understand_repo_builds_the_bank_and_names_where_to_start(tmp_path: Path) -> None:
    from orchestrator.knowledge.understand import BANK_DIRNAME
    from orchestrator.plugin.server import read_memory_bank, understand_repo

    repo = _ledger_repo(tmp_path)
    out = understand_repo(str(repo))
    assert "error" not in out, out
    assert out["dir"] == str(repo / BANK_DIRNAME)
    assert out["files_written"] > 0 and (repo / BANK_DIRNAME / "README.md").exists()
    assert "README.md" in out["entry_pages"]
    assert "read_memory_bank" in out["markdown"]
    # The point of the tool: a bank an assistant can now read.
    bank = read_memory_bank(str(repo))
    assert "error" not in bank


def test_understand_repo_check_is_current_then_stale(tmp_path: Path) -> None:
    from orchestrator.plugin.server import understand_repo

    repo = _ledger_repo(tmp_path)
    assert understand_repo(str(repo), check=True)["absent"] is True  # nothing built yet
    understand_repo(str(repo))
    current = understand_repo(str(repo), check=True)
    assert current["ok"] is True and "current" in current["summary"]
    # The code moves on; the committed pages no longer describe it.
    (repo / "router.py").write_text("class WebhookRouter:\n    pass\n", encoding="utf-8")
    stale = understand_repo(str(repo), check=True)
    assert stale["ok"] is False and (stale["stale"] or stale["missing"])
    assert "stale" in stale["summary"]


def test_understand_repo_refuses_to_build_into_a_clone_that_vanishes(tmp_path: Path) -> None:
    from orchestrator.plugin.server import understand_repo

    out = understand_repo("https://github.com/example/repo.git")  # allow-listed host, never cloned
    assert "error" in out and "out=" in out["error"]
    # A relative `out` is the same trap in a subprocess whose cwd is not the repo.
    assert "error" in understand_repo("https://github.com/example/repo.git", out="episteme")


def test_understand_repo_writes_where_out_says(tmp_path: Path) -> None:
    from orchestrator.plugin.server import understand_repo

    (tmp_path / "repo").mkdir()
    repo = _ledger_repo(tmp_path / "repo")
    target = tmp_path / "elsewhere"
    out = understand_repo(str(repo), out=str(target))
    assert out["dir"] == str(target) and (target / "README.md").exists()


def test_profile_repo_reads_the_project(tmp_path: Path) -> None:
    from orchestrator.plugin.server import profile_repo

    out = profile_repo(str(_ledger_repo(tmp_path)), intent="Add a refund endpoint")
    assert "python" in out["languages"]
    assert out["task_type"] and "task type:" in out["markdown"]


async def test_design_change_is_grounded_and_never_writes(tmp_path: Path) -> None:
    from orchestrator.plugin.server import design_change

    repo = _ledger_repo(tmp_path)
    before = sorted(p.name for p in repo.rglob("*") if p.is_file())
    out = await design_change(
        str(repo),
        {
            "intent_id": "LED-1",
            "title": "Persist the ledger",
            "summary": "Write it to disk",
            "acceptance_criteria": ["survives restart"],
        },
    )
    assert "error" not in out, out
    assert out["title"] == "Persist the ledger" and out["used_llm"] is False
    assert "Persist the ledger" in out["markdown"]
    assert isinstance(out["unverified_references"], list)
    assert sorted(p.name for p in repo.rglob("*") if p.is_file()) == before  # nothing written


async def test_design_change_refuses_a_bad_spec_naming_the_valid_fields(tmp_path: Path) -> None:
    from orchestrator.plugin.server import design_change

    out = await design_change(str(_ledger_repo(tmp_path)), {"title": "x", "invented": True})
    assert "error" in out and "title" in out["valid_fields"]
    assert "error" in await design_change(str(tmp_path), "not an object")  # type: ignore[arg-type]


async def test_design_change_with_llm_needs_a_model(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from orchestrator.plugin.server import design_change

    # The catalog can resolve a default model from a real .env, and this test must never
    # reach a provider — so make the model unresolvable the way the root_cause test does.
    monkeypatch.setattr("orchestrator.sdlc.codegen.resolve_codegen_model", lambda *a, **k: None)
    out = await design_change(
        str(_ledger_repo(tmp_path)),
        {"intent_id": "X-1", "title": "x", "acceptance_criteria": ["works"]},
        use_llm=True,
    )
    assert "error" in out and "ORCHESTRATOR_INTAKE_MODEL" in out["error"]


def test_sdlc_baseline_scores_the_gate_over_this_repos_graph(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from orchestrator.plugin.server import sdlc_baseline

    monkeypatch.setenv("SPINE_RUN_STATE", str(tmp_path / "state"))  # an empty run store
    out = sdlc_baseline(str(_ledger_repo(tmp_path)))
    assert "error" not in out, out
    assert out["gate"]["cases"] > 0 and 0.0 <= out["gate"]["accuracy"] <= 1.0
    assert out["runs"]["runs"] == 0
    assert "refus" in out["markdown"].lower()


# ---- operator tools: over the registry, with a mock transport ----------------------


def _registry_with(handler: object, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the tools at a RegistryClient over a MockTransport — no server."""
    import httpx

    import orchestrator.plugin.registry_client as rc

    monkeypatch.setattr(
        rc,
        "registry_client",
        lambda: rc.RegistryClient("http://test", "k", transport=httpx.MockTransport(handler)),  # type: ignore[arg-type]
    )


async def test_registry_runs_lists_with_a_table(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    from orchestrator.plugin.server import registry_runs

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/runs" and request.url.params["limit"] == "5"
        return httpx.Response(
            200,
            json={
                "items": [
                    {"sdlc_id": "R1", "state": "running", "last_action": "sdlc.plan", "updated_at": "t"}
                ]
            },
        )

    _registry_with(handler, monkeypatch)
    out = await registry_runs(limit=5)
    assert out["count"] == 1 and out["items"][0]["sdlc_id"] == "R1"
    assert "| `R1` | running |" in out["markdown"]


async def test_registry_approvals_lists_what_waits(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    from orchestrator.plugin.server import registry_approvals

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "id": "sdlc-R1-0",
                        "title": "Approve intents",
                        "risk_classification": "medium",
                        "task_id": "task-R1",
                    }
                ]
            },
        )

    _registry_with(handler, monkeypatch)
    out = await registry_approvals()
    assert out["count"] == 1
    assert "`sdlc-R1-0`" in out["markdown"] and "Approve intents" in out["markdown"]


async def test_registry_decide_posts_the_action(monkeypatch: pytest.MonkeyPatch) -> None:
    import json as _json

    import httpx

    from orchestrator.plugin.server import registry_decide

    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"], seen["body"] = request.url.path, _json.loads(request.content)
        return httpx.Response(200, json={"id": "g1", "status": "rejected"})

    _registry_with(handler, monkeypatch)
    out = await registry_decide("g1", "reject", rationale="scope creep")
    assert out["action"] == "reject" and out["approval"]["status"] == "rejected"
    assert seen["path"] == "/v1/approvals/g1/reject" and seen["body"] == {"rationale": "scope creep"}


async def test_registry_decide_refuses_a_bad_action_before_any_call(monkeypatch: pytest.MonkeyPatch) -> None:
    from orchestrator.plugin.server import registry_decide

    def handler(request: object) -> None:
        raise AssertionError("must not reach the registry")

    _registry_with(handler, monkeypatch)
    assert "error" in await registry_decide("g1", "shrug")
    assert "modified_input" in (await registry_decide("g1", "modify_input"))["error"]


async def test_registry_trace_is_bounded_and_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    from orchestrator.plugin.server import registry_trace

    audit = [
        {
            "timestamp": f"t{i}",
            "actor": "a",
            "action": f"step.{i}",
            "resource_type": "sdlc",
            "resource_id": "R1",
        }
        for i in range(120)
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/tasks/R1/trace"  # the run id as listed — what the console uses
        return httpx.Response(
            200,
            json={
                "task_id": "R1",
                "audit": audit,
                "tool_invocations": [],
                "verifier_outcome": "pass",
                "replan_count": 1,
                "replan_budget": 3,
            },
        )

    _registry_with(handler, monkeypatch)
    out = await registry_trace("R1", tail=50)
    assert len(out["audit"]) == 50 and out["audit"][-1]["action"] == "step.119"  # the newest, kept
    assert out["truncated"] == {"audit": 70, "tool_invocations": 0}
    assert "last 50 of 120" in out["markdown"] and "replans: 1/3" in out["markdown"]


async def test_a_registry_that_is_down_is_an_error_with_a_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    from orchestrator.plugin.server import registry_approvals, registry_runs

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    _registry_with(handler, monkeypatch)
    for tool in (registry_runs, registry_approvals):
        out = await tool()
        assert "error" in out and "orchestrator up" in out["hint"] and out["registry"] == "http://test"


# ---- the tiers are metadata: annotations a host can act on -------------------------


def test_every_registered_tool_has_a_tier() -> None:
    """Total by construction: a tool added to ``_TOOLS`` without a ``_TIER`` entry fails
    here, before it reaches a host with its cost unstated."""
    from orchestrator.plugin.server import _TIER, _TOOLS

    assert {fn.__name__ for fn in _TOOLS} == set(_TIER)


def test_tiers_say_what_a_tool_can_cost() -> None:
    from orchestrator.plugin.server import _TOOLS, tool_annotations

    hints = {fn.__name__: tool_annotations(fn.__name__) for fn in _TOOLS}
    spends_and_writes = {"sdlc_feature", "sdlc_start_run", "sdlc_decide_gate", "registry_decide"}
    plans = {"sdlc_plan", "sdlc_approve", "understand_repo"}  # the last: a write under episteme/
    observes_a_run = {
        "sdlc_run_status",
        "sdlc_run_result",
        "registry_runs",
        "registry_approvals",
        "registry_trace",
    }
    comprehension = set(hints) - spends_and_writes - plans - observes_a_run

    for name in comprehension | observes_a_run:
        assert hints[name]["read_only_hint"] and not hints[name]["destructive_hint"], name
    for name in plans:
        assert not hints[name]["read_only_hint"] and not hints[name]["destructive_hint"], name
        assert hints[name]["idempotent_hint"], name  # re-running rewrites the same document
    for name in spends_and_writes:
        assert not hints[name]["read_only_hint"] and hints[name]["destructive_hint"], name
        assert not hints[name]["idempotent_hint"], name
    # `use_llm` spends tokens, but the tool still never changes code.
    assert hints["root_cause"]["read_only_hint"]
    # Only these never leave the machine; everything else may clone a URL or read a source.
    assert {n for n, h in hints.items() if not h["open_world_hint"]} == {
        "doctor",
        "pkg_joins",
        "sdlc_plan",
        "sdlc_approve",
    }


def test_an_untiered_tool_is_refused_at_registration() -> None:
    from orchestrator.plugin.server import tool_annotations

    with pytest.raises(KeyError):
        tool_annotations("a_tool_nobody_classified")


def test_all_exports_every_registered_tool() -> None:
    """``__all__`` drifted from ``_TOOLS`` once (four tools registered but not exported);
    the export list is what a reader and ``from … import *`` trust."""
    import orchestrator.plugin.server as mod

    assert {fn.__name__ for fn in mod._TOOLS} <= set(mod.__all__)
    assert mod.__all__ == sorted(mod.__all__, key=lambda x: (x.lower(), x))


@pytest.mark.skipif(importlib.util.find_spec("mcp") is None, reason="needs the 'mcp' extra")
async def test_annotations_reach_the_host() -> None:
    """What the host sees is the tier table, field for field — not a hand-copied subset."""
    from orchestrator.plugin.server import _TOOLS, build_server, tool_annotations

    tools = {t.name: t for t in await build_server().list_tools()}
    assert set(tools) == {fn.__name__ for fn in _TOOLS}
    for name, tool in tools.items():
        assert tool.annotations is not None, name
        assert tool.annotations.model_dump(exclude_none=True, exclude={"title"}) == tool_annotations(name), (
            name
        )


@pytest.mark.skipif(importlib.util.find_spec("mcp") is None, reason="needs the 'mcp' extra")
def test_registration_refuses_a_tool_without_a_tier(monkeypatch: pytest.MonkeyPatch) -> None:
    import orchestrator.plugin.server as mod

    def orphan() -> dict[str, Any]:
        return {}

    monkeypatch.setattr(mod, "_TOOLS", (*mod._TOOLS, orphan))
    with pytest.raises(RuntimeError, match="no tier"):
        mod.build_server()


# ---- the tiers are scopes: the guard on the HTTP transport ---------------------------


def test_every_tier_names_a_scope_and_it_follows_the_hints() -> None:
    from orchestrator.plugin.auth import SCOPE_PLAN, SCOPE_READ, SCOPE_RUN
    from orchestrator.plugin.server import _TOOLS, tool_annotations, tool_scope

    for fn in _TOOLS:
        name = fn.__name__
        hints, scope = tool_annotations(name), tool_scope(name)
        if hints["read_only_hint"]:
            assert scope == SCOPE_READ, name
        elif hints["destructive_hint"]:
            assert scope == SCOPE_RUN, name
        else:
            assert scope == SCOPE_PLAN, name
    assert tool_scope("registry_decide") == SCOPE_RUN and tool_scope("sdlc_plan") == SCOPE_PLAN


def test_an_untiered_tool_has_no_scope_either() -> None:
    from orchestrator.plugin.server import tool_scope

    with pytest.raises(KeyError):
        tool_scope("a_tool_nobody_classified")


@pytest.fixture
def _as_token(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Run the rest of the test as a caller whose verified bearer token carries `scopes`."""
    pytest.importorskip("mcp")
    from mcp.server.auth.middleware.auth_context import (  # type: ignore[attr-defined]
        AuthenticatedUser,
        auth_context_var,
    )
    from mcp.server.auth.provider import AccessToken

    def as_token(scopes: list[str] | None) -> None:
        if scopes is None:
            auth_context_var.set(None)
            return
        user = AuthenticatedUser(AccessToken(token="t", client_id="c", scopes=scopes, expires_at=None))
        auth_context_var.set(user)

    yield as_token
    auth_context_var.set(None)


def test_the_guard_refuses_a_token_without_the_tools_scope(_as_token: Any) -> None:
    from orchestrator.plugin.auth import SCOPE_READ, SCOPE_RUN
    from orchestrator.plugin.server import scope_denial

    _as_token([SCOPE_READ])
    denied = scope_denial("registry_decide", SCOPE_RUN)
    assert denied is not None
    assert denied["needs"] == SCOPE_RUN and denied["has"] == [SCOPE_READ]
    assert "registry_decide" in denied["error"] and SCOPE_RUN in denied["error"]


def test_the_guard_passes_a_token_with_the_scope_and_no_token_at_all(_as_token: Any) -> None:
    from orchestrator.plugin.auth import SCOPE_RUN
    from orchestrator.plugin.server import scope_denial

    _as_token([SCOPE_RUN])
    assert scope_denial("registry_decide", SCOPE_RUN) is None
    _as_token(None)  # stdio, or an unauthenticated loopback bind: nothing to check against
    assert scope_denial("registry_decide", SCOPE_RUN) is None


async def test_a_guarded_tool_returns_the_denial_instead_of_running(
    _as_token: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End to end through the wrapper the server registers: a read-only token calling a
    run-tier tool never reaches the registry."""
    from orchestrator.plugin.auth import SCOPE_READ, SCOPE_RUN
    from orchestrator.plugin.server import _scoped, registry_decide

    def handler(request: object) -> None:
        raise AssertionError("must not reach the registry")

    _registry_with(handler, monkeypatch)
    guarded = _scoped(registry_decide, SCOPE_RUN)
    _as_token([SCOPE_READ])
    out = await guarded("g1", "approve")
    assert out["needs"] == SCOPE_RUN


async def test_a_guarded_sync_tool_still_runs_when_allowed(_as_token: Any) -> None:
    from orchestrator.plugin.auth import SCOPE_READ
    from orchestrator.plugin.server import _scoped, doctor

    _as_token([SCOPE_READ])
    out = await _scoped(doctor, SCOPE_READ)()
    assert "all_passed" in out  # the sync tool ran, through the async wrapper


@pytest.mark.skipif(importlib.util.find_spec("mcp") is None, reason="needs the 'mcp' extra")
async def test_the_guard_does_not_change_what_a_host_sees() -> None:
    """The wrapper must be invisible in `list_tools`: same names, same input schema, same
    annotations, same description — the SDK builds those from what `functools.wraps` carries."""
    from orchestrator.plugin.server import _TOOLS, build_server, registry_decide, tool_annotations

    tools = {t.name: t for t in await build_server().list_tools()}
    assert set(tools) == {fn.__name__ for fn in _TOOLS}
    decide = tools["registry_decide"]
    assert set(decide.input_schema["properties"]) == {"approval_id", "action", "rationale", "modified_input"}
    assert decide.input_schema["required"] == ["approval_id", "action"]
    assert decide.description == registry_decide.__doc__
    for name, tool in tools.items():
        assert tool.annotations is not None
        assert tool.annotations.model_dump(exclude_none=True, exclude={"title"}) == tool_annotations(name), (
            name
        )


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
        assert result.is_error is False


# ---- sdlc_plan: the build document, for a host that has the model ----------


def _plan_spec(**over: Any) -> dict[str, Any]:
    spec = {
        "intent_id": "TCK-9",
        "title": "A ticket",
        "summary": "Something is broken in src/a.py.",
        "acceptance_criteria": ["It stops crashing."],
    }
    spec.update(over)
    return spec


def _tiny_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "a.py").write_text("def helper():\n    return 1\n", encoding="utf-8")
    return repo


async def test_sdlc_plan_returns_the_document_and_where_it_was_written(tmp_path: Path) -> None:
    from orchestrator.plugin.server import sdlc_plan

    result = await sdlc_plan(str(_tiny_repo(tmp_path)), _plan_spec())

    assert result["document"].startswith("# TCK-9 — build document")
    assert result["document"].count("\n## ") >= 12
    assert Path(result["path"]).is_file()


async def test_sdlc_plan_needs_no_model_and_no_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole point on a machine whose only model lives in the host app."""
    for var in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "ORCHESTRATOR_MODEL", "JIRA_API_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    from orchestrator.plugin.server import sdlc_plan

    result = await sdlc_plan(str(_tiny_repo(tmp_path)), _plan_spec())

    assert "error" not in result
    assert "## 12. Confidence" in result["document"]


async def test_an_invented_field_is_refused_with_the_valid_ones(tmp_path: Path) -> None:
    """A host's model drafts this spec; a key it made up must not render a document."""
    from orchestrator.plugin.server import sdlc_plan

    result = await sdlc_plan(str(_tiny_repo(tmp_path)), _plan_spec(notes="invented"))

    assert "document" not in result
    assert "notes" in result["error"]
    assert "acceptance_criteria" in result["valid_fields"]


async def test_a_spec_with_nothing_to_satisfy_is_refused(tmp_path: Path) -> None:
    from orchestrator.plugin.server import sdlc_plan

    result = await sdlc_plan(str(_tiny_repo(tmp_path)), _plan_spec(acceptance_criteria=[]))

    assert "nothing for the acceptance judge" in result["error"]


async def test_persist_can_be_turned_off(tmp_path: Path) -> None:
    from orchestrator.plugin.server import sdlc_plan
    from orchestrator.sdlc.builddoc import plan_dir

    repo = _tiny_repo(tmp_path)
    result = await sdlc_plan(str(repo), _plan_spec(), persist_plan=False)

    assert "path" not in result and result["document"]
    assert not plan_dir(repo).exists()


async def test_a_bad_repo_path_is_reported_not_raised(tmp_path: Path) -> None:
    from orchestrator.plugin.server import sdlc_plan

    result = await sdlc_plan(str(tmp_path / "nope"), _plan_spec())

    assert "error" in result and "document" not in result


async def test_the_plugin_and_the_cli_render_the_same_document(tmp_path: Path) -> None:
    """Two surfaces over one renderer must not be able to disagree."""
    from orchestrator.plugin.server import sdlc_plan
    from orchestrator.sdlc.builddoc import build_plan

    repo = _tiny_repo(tmp_path)
    spec = _plan_spec()
    via_tool = await sdlc_plan(str(repo), spec, persist_plan=False)
    via_cli = await build_plan(spec, root=repo)

    assert via_tool["document"] == via_cli


def test_sdlc_plan_is_registered() -> None:
    from orchestrator.plugin.server import _TOOLS, sdlc_plan

    assert sdlc_plan in _TOOLS


# ---- sdlc_approve: the gate, non-interactively -----------------------------


async def test_approving_binds_the_decision_to_the_document(tmp_path: Path) -> None:
    from orchestrator.plugin.server import sdlc_approve, sdlc_plan

    repo = _tiny_repo(tmp_path)
    await sdlc_plan(str(repo), _plan_spec())

    result = sdlc_approve(str(repo), "TCK-9", decided_by="falcon", note="read it")

    assert result["decision"] == "APPROVED" and result["decided_by"] == "falcon"
    after = await sdlc_plan(str(repo), _plan_spec())
    assert "**approved** by falcon" in after["document"]


async def test_a_rejection_says_who_and_why(tmp_path: Path) -> None:
    from orchestrator.plugin.server import sdlc_approve, sdlc_plan

    repo = _tiny_repo(tmp_path)
    await sdlc_plan(str(repo), _plan_spec())

    sdlc_approve(str(repo), "TCK-9", decided_by="falcon", note="wrong files", reject=True)

    after = await sdlc_plan(str(repo), _plan_spec())
    assert "**rejected** by falcon" in after["document"] and "wrong files" in after["document"]


def test_approving_a_plan_that_does_not_exist_is_refused(tmp_path: Path) -> None:
    from orchestrator.plugin.server import sdlc_approve

    result = sdlc_approve(str(_tiny_repo(tmp_path)), "TCK-9", decided_by="falcon")

    assert "no plan at" in result["error"]


async def test_an_approval_nobody_is_named_for_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A host may know its user; this process does not, and must not invent one."""
    from orchestrator.plugin import server as mod
    from orchestrator.plugin.server import sdlc_approve, sdlc_plan

    repo = _tiny_repo(tmp_path)
    await sdlc_plan(str(repo), _plan_spec())
    monkeypatch.setattr("orchestrator.sdlc.builddoc.decided_by_default", lambda _root="": "")

    assert "cannot tell who is approving" in sdlc_approve(str(repo), "TCK-9")["error"]
    assert mod.sdlc_approve in mod._TOOLS


# ---- multi-repo: one graph across several repositories ---------------------
# The case these exist for: an HTTP handler with **zero callers in its own source**. That
# answer is true and, on a single-repo graph, the most dangerous one the graph can give.

_BILLING = """\
from fastapi import FastAPI

app = FastAPI()


@app.post("/v1/orders")
def create_order(payload: dict) -> dict:
    return {"id": 1}
"""

_WEB_CLIENT = """\
import requests


def place_order(payload: dict) -> dict:
    return requests.post("http://billing/v1/orders", json=payload).json()
"""


def _repo_at(root: Path, name: str, body: str) -> Path:
    """A real git repo with one commit — a merged graph is only reproducible over clean trees."""
    import os
    import subprocess

    (root / "app").mkdir(parents=True, exist_ok=True)
    (root / "app" / name).write_text(body, encoding="utf-8")
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@e",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@e",
    }
    for args in (["init", "-q"], ["add", "-A"], ["commit", "-qm", "init"]):
        subprocess.run(["git", *args], cwd=root, check=True, env=env)
    return root


def _repos_config(tmp_path: Path, *, joins: bool = True) -> str:
    billing = _repo_at(tmp_path / "billing", "routes.py", _BILLING)
    web = _repo_at(tmp_path / "web", "client.py", _WEB_CLIENT)
    block = "joins:\n  - kind: http\n    consumer: web\n    provider: billing\n" if joins else ""
    cfg = tmp_path / "repos.yaml"
    cfg.write_text(f"repos:\n  billing: {billing}\n  web: {web}\n{block}", encoding="utf-8")
    return str(cfg)


def test_blast_radius_reports_dependents_in_another_repo(tmp_path: Path) -> None:
    out = blast_radius(symbol="create_order", repos=_repos_config(tmp_path))

    assert out["found"], out
    match = out["matches"][0]
    # Zero callers at home is the true answer, and on its own it is the misleading one.
    assert match["caller_count"] == 0
    assert match["cross_repo_count"] >= 1
    assert any(c["repo"] == "web" for c in match["cross_repo"])
    assert "Dependents in other repos" in out["markdown"]


def test_multi_repo_answers_carry_their_standing(tmp_path: Path) -> None:
    """A graph built over a dirty tree looks identical to one that is reproducible."""
    cfg = _repos_config(tmp_path)
    clean = blast_radius(symbol="create_order", repos=cfg)
    assert clean["standing"] == {
        "repos": ["billing", "web"],
        "reproducible": True,
        "untrusted": [],
    }

    (tmp_path / "web" / "app" / "client.py").write_text(_WEB_CLIENT + "# edit\n", encoding="utf-8")
    dirty = blast_radius(symbol="create_order", repos=cfg)
    assert dirty["standing"]["reproducible"] is False
    assert dirty["standing"]["untrusted"] == ["web"]


def test_investigate_across_repos_reports_cross_repo_landing(tmp_path: Path) -> None:
    out = investigate(title="change order creation", repos=_repos_config(tmp_path))

    landing = {h["name"]: h for h in out["landing"]}
    assert landing["create_order"]["cross_repo"] >= 1
    assert "dependent(s) in other repos" in out["markdown"]
    # `episteme/` belongs to one repository; a merged brief must not fill it from an arbitrary one.
    assert out["has_knowledge"] is False


def test_a_tool_takes_one_repo_or_many_but_never_both(tmp_path: Path) -> None:
    cfg = _repos_config(tmp_path)
    assert "exactly one" in blast_radius(repo_path=str(tmp_path), symbol="x", repos=cfg)["error"]
    assert "exactly one" in blast_radius(symbol="create_order")["error"]
    assert "exactly one" in investigate(repo_path=str(tmp_path), title="t", repos=cfg)["error"]


def test_pkg_joins_check_reports_what_the_declared_joins_placed(tmp_path: Path) -> None:
    from orchestrator.plugin.server import pkg_joins

    out = pkg_joins(_repos_config(tmp_path), "check")

    assert out["mode"] == "check"
    assert out["declared"] == 1
    assert out["joined"] >= 1
    assert out["per_join"][0]["join"] == "web -http-> billing"


def test_pkg_joins_check_says_nothing_is_declared_rather_than_zero_unplaced(tmp_path: Path) -> None:
    """ "0 unplaced" against no declarations is exactly the silence this command exists to break."""
    from orchestrator.plugin.server import pkg_joins

    out = pkg_joins(_repos_config(tmp_path, joins=False), "check")

    assert out["declared"] == 0
    assert "no joins declared" in out["note"]


def test_pkg_joins_propose_derives_the_topology_from_evidence(tmp_path: Path) -> None:
    from orchestrator.plugin.server import pkg_joins

    out = pkg_joins(_repos_config(tmp_path, joins=False), "propose")

    candidate = out["candidates"][0]
    assert (candidate["kind"], candidate["consumer"], candidate["provider"]) == ("http", "web", "billing")
    # A join producing zero edges is noise, so every candidate carries what it would create.
    assert candidate["edges"] >= 1
    assert candidate["already_declared"] is False


def test_pkg_joins_rejects_an_unknown_mode_and_a_missing_config(tmp_path: Path) -> None:
    from orchestrator.plugin import server as mod
    from orchestrator.plugin.server import pkg_joins

    assert "propose" in pkg_joins(_repos_config(tmp_path), "sideways")["error"]
    assert "cannot be read" in pkg_joins(str(tmp_path / "nope.yaml"), "check")["error"]
    assert mod.pkg_joins in mod._TOOLS


# ---- the nudge: a single-repo answer in a multi-repo project ----------------
# The single-repo path cannot fail loudly here. Point a tool at a directory and it extracts
# that directory — there is no error to raise, and `0 caller(s)` looks like every other answer.


def _repo_declaring_siblings(tmp_path: Path) -> Path:
    """A billing repo whose own `.spine/repos.yaml` names it and a `web` sibling."""
    billing = _repo_at(tmp_path / "billing", "routes.py", _BILLING)
    web = _repo_at(tmp_path / "web", "client.py", _WEB_CLIENT)
    (billing / ".spine").mkdir(exist_ok=True)
    (billing / ".spine" / "repos.yaml").write_text(
        f"repos:\n  billing: {billing}\n  web: {web}\n"
        "joins:\n  - kind: http\n    consumer: web\n    provider: billing\n",
        encoding="utf-8",
    )
    return billing


def test_a_single_repo_answer_says_the_project_declares_more(tmp_path: Path) -> None:
    billing = _repo_declaring_siblings(tmp_path)

    out = blast_radius(repo_path=str(billing), symbol="create_order")

    # The answer itself is unchanged and still true — of this repository.
    assert out["matches"][0]["caller_count"] == 0
    assert "cross_repo_count" not in out["matches"][0]
    # …but it no longer reads as the whole story.
    note = out["multi_repo_available"]
    assert note["declares"] == ["billing", "web"]
    assert "covers one repository" in note["note"]
    assert "repos=" in note["note"]


def test_the_nudge_reaches_every_comprehension_tool_that_takes_one_repo(tmp_path: Path) -> None:
    billing = str(_repo_declaring_siblings(tmp_path))

    assert "multi_repo_available" in map_repo(billing)
    assert "multi_repo_available" in explain_symbol(billing, "create_order")
    assert "multi_repo_available" in investigate(repo_path=billing, title="order creation")
    assert "multi_repo_available" in regression_gaps(billing, symbol="create_order")


def test_a_project_with_one_repo_hears_nothing(tmp_path: Path) -> None:
    """A note on every answer everywhere is a note nobody reads."""
    plain = _repo_at(tmp_path / "billing", "routes.py", _BILLING)

    assert "multi_repo_available" not in blast_radius(repo_path=str(plain), symbol="create_order")
    assert "multi_repo_available" not in map_repo(str(plain))


def test_a_config_too_broken_to_read_still_speaks_up(tmp_path: Path) -> None:
    """It is still evidence the project is multi-repo, and silence here is the failure mode."""
    billing = _repo_declaring_siblings(tmp_path)
    (billing / ".spine" / "repos.yaml").write_text("repos: [not, a, mapping]\n", encoding="utf-8")

    note = blast_radius(repo_path=str(billing), symbol="create_order")["multi_repo_available"]

    assert "could not be read" in note["note"]
    assert "declares" not in note


def test_the_merged_answer_does_not_nudge_toward_itself(tmp_path: Path) -> None:
    billing = _repo_declaring_siblings(tmp_path)
    config = str(billing / ".spine" / "repos.yaml")

    out = blast_radius(symbol="create_order", repos=config)

    assert "multi_repo_available" not in out
    assert out["matches"][0]["cross_repo_count"] >= 1


def test_an_approval_is_about_one_repo_and_is_not_nudged(tmp_path: Path) -> None:
    """A decision on one repository's plan is not a question about the others."""
    from orchestrator.plugin.server import sdlc_approve

    billing = _repo_declaring_siblings(tmp_path)

    out = sdlc_approve(str(billing), "TCK-9", decided_by="falcon")

    assert "no plan at" in out["error"]
    assert "multi_repo_available" not in out


def test_an_unreadable_repo_reports_the_error_and_nothing_else(tmp_path: Path) -> None:
    out = blast_radius(repo_path=str(tmp_path / "nowhere"), symbol="create_order")

    assert "error" in out
    assert "multi_repo_available" not in out
