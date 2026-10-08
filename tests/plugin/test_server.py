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
    # a symbol no interface declares: direct callers as before, and no reach through one (B21)
    assert m["interface_caller_count"] == 0 and m["interface_callers"] == []
    assert "Called through an interface" not in out["markdown"]


def test_blast_radius_not_found(tmp_path: Path) -> None:
    out = blast_radius(_comprehension_repo(tmp_path), "does_not_exist")
    assert out["found"] is False and out["matches"] == []


def test_explain_symbol_lists_callers(tmp_path: Path) -> None:
    out = explain_symbol(_comprehension_repo(tmp_path), "validate")
    assert out["found"]
    assert any("handler" in c for c in out["matches"][0]["called_by"])
    assert out["matches"][0]["called_through_interface"] == []


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


# ---- docs in the blast radius (SSPN-79) ---------------------------------------------

_CLIENT = "class Client:\n    def ask(self, q):\n        return q\n"


def _documented_repo(tmp_path: Path) -> str:
    """A public class nothing in the repo calls, described by three doc sections."""
    (tmp_path / "lib").mkdir()
    (tmp_path / "lib" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "lib" / "client.py").write_text(_CLIENT, encoding="utf-8")
    (tmp_path / "README.md").write_text(
        "# Quickstart\n\nCreate a `Client`.\n\n# Reference\n\n`Client` answers questions.\n",
        encoding="utf-8",
    )
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "guide.md").write_text("The `lib.client` module holds the SDK.\n", encoding="utf-8")
    return str(tmp_path)


def test_blast_radius_lists_the_docs_that_describe_a_zero_caller_class(tmp_path: Path) -> None:
    out = blast_radius(_documented_repo(tmp_path), "Client")
    m = out["matches"][0]
    # The code graph's honest answer is "nothing calls it"; the docs say who depends on it.
    assert m["caller_count"] == 0
    assert m["doc_count"] == 3
    assert [(d["doc"], d["via"]) for d in m["docs"]] == [
        ("README.md#quickstart", "symbol"),
        ("README.md#reference", "symbol"),
        ("docs/guide.md", "module"),
    ]
    assert all(d["origin"] == "repo" for d in m["docs"])
    assert m["docs"][0]["where"] == "README.md:1"
    assert "Documented in (3)" in out["markdown"]


def test_a_method_inherits_its_class_and_module_docs(tmp_path: Path) -> None:
    m = blast_radius(_documented_repo(tmp_path), "ask")["matches"][0]
    assert {(d["doc"], d["via"]) for d in m["docs"]} == {
        ("README.md#quickstart", "class"),
        ("README.md#reference", "class"),
        ("docs/guide.md", "module"),
    }


def test_docs_never_count_as_code_touches(tmp_path: Path) -> None:
    """MENTIONS edges must not leak into `touches` or make a doc title a `find` hit — the code
    impact numbers are exactly what they were before docs joined the answer."""
    repo = _documented_repo(tmp_path)
    m = blast_radius(repo, "Client")["matches"][0]
    assert not any(t["id"].startswith("doc:") for t in m["touches"])
    assert m["touch_count"] == len(m["touches"])
    assert blast_radius(repo, "README.md#quickstart")["found"] is False


def test_docs_naming_callers_are_counted_not_listed(tmp_path: Path) -> None:
    repo = _comprehension_repo(tmp_path)
    (tmp_path / "README.md").write_text("Requests go through `handler`.\n", encoding="utf-8")
    m = blast_radius(repo, "validate")["matches"][0]
    assert m["doc_count"] == 0 and m["docs"] == []
    assert m["related_doc_count"] == 1


def test_explain_symbol_carries_the_same_docs(tmp_path: Path) -> None:
    m = explain_symbol(_documented_repo(tmp_path), "Client")["matches"][0]
    assert m["doc_count"] == 3
    assert [d["doc"] for d in m["docs"]][:2] == ["README.md#quickstart", "README.md#reference"]


def test_blast_radius_without_docs_reports_zero(tmp_path: Path) -> None:
    m = blast_radius(_comprehension_repo(tmp_path), "validate")["matches"][0]
    assert m["doc_count"] == 0 and m["docs"] == [] and m["related_doc_count"] == 0
    assert "Documented in" not in blast_radius(_comprehension_repo(tmp_path), "validate")["markdown"]


def test_across_repos_a_doc_binds_only_its_own_repository(tmp_path: Path) -> None:
    """D26: both repos define `create_order`; each repo's doc describes its own, never the other's."""
    (tmp_path / "billing").mkdir()
    (tmp_path / "billing" / "API.md").write_text("`create_order` takes a payload.\n", encoding="utf-8")
    (tmp_path / "web" / "app").mkdir(parents=True)
    (tmp_path / "web" / "app" / "routes.py").write_text(
        "def create_order(form):\n    return form\n", encoding="utf-8"
    )
    (tmp_path / "web" / "NOTES.md").write_text("Our `create_order` renders the form.\n", encoding="utf-8")
    matches = blast_radius(symbol="create_order", repos=_repos_config(tmp_path))["matches"]
    by_repo = {("billing" if "billing" in m["id"] else "web"): [d["doc"] for d in m["docs"]] for m in matches}
    assert by_repo == {"billing": ["API.md"], "web": ["NOTES.md"]}


def test_across_repos_only_the_matched_repository_is_linked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A neighbour in another repository is never a reason to read that repository's docs, and
    a repository the merged graph already extracted is not extracted again."""
    import orchestrator.pkg as pkg
    import orchestrator.pkg.doc_link as doc_link

    (tmp_path / "billing").mkdir()
    (tmp_path / "billing" / "API.md").write_text("`create_order` takes a payload.\n", encoding="utf-8")
    config = _repos_config(tmp_path)
    linked: list[str] = []
    real_link = doc_link.link_docs

    def spy(batch: Any, root: Any) -> Any:
        linked.append(Path(root).name)
        return real_link(batch, root)

    def no_second_extraction(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("per-repo docs re-extracted a repository the merged graph already holds")

    monkeypatch.setattr(doc_link, "link_docs", spy)
    monkeypatch.setattr(pkg, "load_or_extract", no_second_extraction)
    m = blast_radius(symbol="create_order", repos=config)["matches"][0]
    assert [d["doc"] for d in m["docs"]] == ["API.md"]
    assert linked == ["billing"]


def test_blast_radius_and_explain_symbol_report_the_same_doc_radius(tmp_path: Path) -> None:
    """`DiskStore.save` is reached only through `Store.save`; docs naming that interface caller
    count for both tools, not just one."""
    pytest.importorskip("tree_sitter_java", reason="install the 'java' extra")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "App.java").write_text(
        "interface Store {\n    void save();\n}\n\n"
        "class DiskStore implements Store {\n    public void save() {}\n}\n\n"
        "class Checkout {\n    private Store store;\n\n    void pay() {\n        store.save();\n    }\n}\n",
        encoding="utf-8",
    )
    (tmp_path / "README.md").write_text("`Checkout.pay` saves the order.\n", encoding="utf-8")
    keys = ("doc_count", "docs", "related_doc_count")
    blast = {m["id"]: {k: m[k] for k in keys} for m in blast_radius(str(tmp_path), "save")["matches"]}
    explain = {m["id"]: {k: m[k] for k in keys} for m in explain_symbol(str(tmp_path), "save")["matches"]}
    impl = next(i for i in blast if "DiskStore" in i)
    assert blast[impl]["related_doc_count"] == 1
    assert explain == blast


def test_docs_are_ordered_by_how_close_they_are_then_by_title(tmp_path: Path) -> None:
    repo = _documented_repo(tmp_path)
    (tmp_path / "API.md").write_text("Import `lib.client`.\n", encoding="utf-8")  # sorts before README
    m = blast_radius(repo, "Client")["matches"][0]
    assert [(d["doc"], d["via"]) for d in m["docs"]] == [
        ("README.md#quickstart", "symbol"),
        ("README.md#reference", "symbol"),
        ("API.md", "module"),
        ("docs/guide.md", "module"),
    ]


def test_a_doc_naming_a_method_and_its_class_is_listed_once_as_the_closer(tmp_path: Path) -> None:
    repo = _documented_repo(tmp_path)
    (tmp_path / "METHODS.md").write_text("`Client.ask` sends one `Client` query.\n", encoding="utf-8")
    m = blast_radius(repo, "ask")["matches"][0]
    assert [(d["doc"], d["via"]) for d in m["docs"] if d["doc"] == "METHODS.md"] == [("METHODS.md", "symbol")]


def test_the_docs_list_is_capped_with_the_full_count_kept(tmp_path: Path) -> None:
    repo = _documented_repo(tmp_path)
    for i in range(30):
        (tmp_path / "docs" / f"page{i:02}.md").write_text("Use `Client`.\n", encoding="utf-8")
    out = blast_radius(repo, "Client")
    m = out["matches"][0]
    assert m["doc_count"] == 33 and len(m["docs"]) == 25
    assert "Documented in (33, top 10 shown)" in out["markdown"]


def test_a_doc_already_listed_is_not_counted_again_as_related(tmp_path: Path) -> None:
    repo = _comprehension_repo(tmp_path)
    (tmp_path / "README.md").write_text("`validate` is called by `handler`.\n", encoding="utf-8")
    m = blast_radius(repo, "validate")["matches"][0]
    assert m["doc_count"] == 1 and m["related_doc_count"] == 0


def test_a_doc_linking_failure_degrades_to_no_docs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Docs are extra evidence: the code answer must survive a failure to produce them."""
    import orchestrator.pkg.doc_link as doc_link

    def boom(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("binder exploded")

    monkeypatch.setattr(doc_link, "link_docs", boom)
    out = blast_radius(_documented_repo(tmp_path), "Client")
    assert out["found"] and out["matches"][0]["docs"] == []
    assert "RuntimeError: binder exploded" in out["docs_unavailable"]
    assert "docs_unavailable" in explain_symbol(str(tmp_path), "Client")


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


async def test_understand_repo_builds_the_bank_and_names_where_to_start(tmp_path: Path) -> None:
    from orchestrator.knowledge.understand import BANK_DIRNAME
    from orchestrator.plugin.server import read_memory_bank, understand_repo

    repo = _ledger_repo(tmp_path)
    out = await understand_repo(str(repo))
    assert "error" not in out, out
    assert out["dir"] == str(repo / BANK_DIRNAME)
    assert out["files_written"] > 0 and (repo / BANK_DIRNAME / "README.md").exists()
    assert "README.md" in out["entry_pages"]
    assert "read_memory_bank" in out["markdown"]
    # The point of the tool: a bank an assistant can now read.
    bank = read_memory_bank(str(repo))
    assert "error" not in bank


async def test_understand_repo_check_is_current_then_stale(tmp_path: Path) -> None:
    from orchestrator.plugin.server import understand_repo

    repo = _ledger_repo(tmp_path)
    assert (await understand_repo(str(repo), check=True))["absent"] is True  # nothing built yet
    await understand_repo(str(repo))
    current = await understand_repo(str(repo), check=True)
    assert current["ok"] is True and "current" in current["summary"]
    # The code moves on; the committed pages no longer describe it.
    (repo / "router.py").write_text("class WebhookRouter:\n    pass\n", encoding="utf-8")
    stale = await understand_repo(str(repo), check=True)
    assert stale["ok"] is False and (stale["stale"] or stale["missing"])
    assert "stale" in stale["summary"]


async def test_understand_repo_refuses_to_build_into_a_clone_that_vanishes(tmp_path: Path) -> None:
    from orchestrator.plugin.server import understand_repo

    out = await understand_repo("https://github.com/example/repo.git")  # allow-listed host, never cloned
    assert "error" in out and "out=" in out["error"]
    # A relative `out` is the same trap in a subprocess whose cwd is not the repo.
    assert "error" in await understand_repo("https://github.com/example/repo.git", out="episteme")


async def test_understand_repo_writes_where_out_says(tmp_path: Path) -> None:
    from orchestrator.plugin.server import understand_repo

    (tmp_path / "repo").mkdir()
    repo = _ledger_repo(tmp_path / "repo")
    target = tmp_path / "elsewhere"
    out = await understand_repo(str(repo), out=str(target))
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


# ---- the gated half of the back half: address-review, complete, remediate, audit ----


async def test_the_gated_tools_refuse_without_confirm_before_touching_anything(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from orchestrator.plugin.server import sdlc_address_review, sdlc_complete, sdlc_remediate

    def boom(*a: Any, **k: Any) -> Any:
        raise AssertionError("must not reach the engine")

    monkeypatch.setattr("orchestrator.sdlc.review_response.checkout_pr_worktree", boom)
    monkeypatch.setattr("orchestrator.sdlc.complete.complete_issue_for_pr", boom)
    monkeypatch.setattr("orchestrator.spine.execute_remediations", boom)

    out = await sdlc_address_review("https://gh/o/r/pull/1")
    assert "confirm=true" in out["error"] and "push" in out["error"]
    out = await sdlc_complete("https://gh/o/r/pull/1")
    assert "confirm=true" in out["error"] and "tracker" in out["error"]
    out = await sdlc_remediate("r.json", "m.json", live=True)
    assert "confirm=true" in out["error"] and "PR" in out["error"]


async def test_sdlc_address_review_checks_out_then_responds(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from orchestrator.plugin.server import sdlc_address_review
    from orchestrator.sdlc.review_response import ReviewResponse

    seen: dict[str, Any] = {}

    async def checkout(repo_url: str, pr: str, **kw: Any) -> tuple[Path, str]:
        seen["clone"] = (repo_url, pr)
        return tmp_path, "feat/abc/PROJ-1"

    async def respond(deps: Any, **kw: Any) -> ReviewResponse:
        seen["respond"] = kw
        return ReviewResponse(comments=2, addressed=True, green=True, refines=1, detail="pushed")

    monkeypatch.setattr("orchestrator.sdlc.review_response.checkout_pr_worktree", checkout)
    monkeypatch.setattr("orchestrator.sdlc.review_response.respond_to_pr_feedback", respond)
    monkeypatch.setattr("orchestrator.sdlc.worker.build_deps", lambda: object())
    monkeypatch.delenv("SDLC_REPO_URL", raising=False)
    monkeypatch.chdir(tmp_path)  # no .env to bridge SDLC_REPO_URL back in

    assert "SDLC_REPO_URL" in (await sdlc_address_review("pr", confirm=True))["error"]
    out = await sdlc_address_review(
        "https://gh/o/r/pull/1", repo="https://gh/o/r.git", bot_login="bot", confirm=True
    )
    assert out["addressed"] and out["branch"] == "feat/abc/PROJ-1" and out["comments"] == 2
    assert seen["clone"] == ("https://gh/o/r.git", "https://gh/o/r/pull/1")
    assert seen["respond"]["branch"] == "feat/abc/PROJ-1" and seen["respond"]["bot_login"] == "bot"


async def test_sdlc_address_review_reports_a_failed_checkout_step(monkeypatch: pytest.MonkeyPatch) -> None:
    from orchestrator.plugin.server import sdlc_address_review
    from orchestrator.sdlc.review_response import PRCheckoutError

    async def checkout(repo_url: str, pr: str, **kw: Any) -> Any:
        raise PRCheckoutError("gh", "not logged in")

    monkeypatch.setattr("orchestrator.sdlc.review_response.checkout_pr_worktree", checkout)
    out = await sdlc_address_review("pr", repo="u", confirm=True)
    assert out["step"] == "gh" and "not logged in" in out["error"]


async def test_sdlc_complete_returns_the_completion_or_the_coded_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from orchestrator.plugin.server import sdlc_complete
    from orchestrator.sdlc.complete import CompleteError, CompletionResult

    async def ok(pr: str, **kw: Any) -> CompletionResult:
        return CompletionResult(issue="PROJ-1", pr=pr, merged=True, status=kw["status"], backlog_done=False)

    monkeypatch.setattr("orchestrator.sdlc.complete.complete_issue_for_pr", ok)
    out = await sdlc_complete("https://gh/o/r/pull/1", status="Closed", confirm=True)
    assert out["issue"] == "PROJ-1" and out["status"] == "Closed"

    async def unmerged(pr: str, **kw: Any) -> CompletionResult:
        raise CompleteError("PR is not merged", code=3)

    monkeypatch.setattr("orchestrator.sdlc.complete.complete_issue_for_pr", unmerged)
    out = await sdlc_complete("pr", confirm=True)
    assert out["code"] == 3 and "not merged" in out["error"]


async def test_sdlc_remediate_needs_readable_inputs_and_a_known_severity(tmp_path: Path) -> None:
    from orchestrator.plugin.server import sdlc_remediate

    assert "min_severity" in (await sdlc_remediate("r", "m", min_severity="loud"))["error"]
    out = await sdlc_remediate(str(tmp_path / "missing.json"), str(tmp_path / "m.json"))
    assert "drift report" in out["error"]


async def test_sdlc_remediate_runs_each_material_finding_in_safe_mode(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import json as _json

    from orchestrator.plugin.server import sdlc_remediate

    report = tmp_path / "report.json"
    report.write_text(_json.dumps({"findings": []}), encoding="utf-8")
    mappings = tmp_path / "mappings.json"
    mappings.write_text("{}", encoding="utf-8")

    class _Store:
        def __init__(self, path: str) -> None:
            pass

        def load(self) -> dict[str, Any]:
            return {}

        def code_for_iri(self) -> dict[str, list[str]]:
            return {}

    class _Outcome:
        entity_key, title, ok, detail, result = "Order", "Fix Order", True, "branch left", "feat/x"

    seen: dict[str, Any] = {}

    async def execute(report: Any, *, runner: Any, **kw: Any) -> list[Any]:
        seen["min_severity"] = kw["min_severity"]
        return [_Outcome()]

    monkeypatch.setattr("orchestrator.spine.MappingStore", _Store)
    monkeypatch.setattr("orchestrator.spine.infer_entity_iris", lambda report, mappings: {})
    monkeypatch.setattr("orchestrator.spine.execute_remediations", execute)

    out = await sdlc_remediate(str(report), str(mappings), min_severity="critical")
    assert out["live"] is False and out["tasks"] == 1 and out["ok"] == 1
    assert out["outcomes"][0]["entity"] == "Order" and "[OK] `Order`" in out["markdown"]
    assert seen["min_severity"] == "critical"


async def test_audit_repo_needs_a_model_and_then_reports_findings(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from orchestrator.plugin.server import audit_repo

    monkeypatch.setattr("orchestrator.sdlc.codegen.resolve_codegen_model", lambda *a, **k: None)
    assert "ORCHESTRATOR_INTAKE_MODEL" in (await audit_repo(str(tmp_path)))["error"]

    from orchestrator.personas.auditor import AuditResult, Finding

    async def fake_audit(root: Any, *, llm: Any, model: str, focus: str, **kw: Any) -> AuditResult:
        return AuditResult(
            summary=f"looked for {focus}",
            findings=[Finding(title="Unchecked input", file="a.py", line=3, severity="warning", detail="d")],
            unresolved=[],
            steps=4,
            stopped_reason="submitted",
        )

    monkeypatch.setattr("orchestrator.sdlc.codegen.resolve_codegen_model", lambda *a, **k: "some-model")
    monkeypatch.setattr("orchestrator.personas.run_audit", fake_audit)
    monkeypatch.setattr("orchestrator.core.llm.LiteLLMClient", lambda *a, **k: object())
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    out = await audit_repo(str(tmp_path), focus="input handling")
    assert out["summary"] == "looked for input handling" and out["steps"] == 4
    assert out["findings"][0]["file"] == "a.py" and out["findings"][0]["line"] == 3
    assert "Unchecked input" in out["markdown"]


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
    spends_and_writes = {
        "sdlc_feature",
        "sdlc_start_run",
        "sdlc_decide_gate",
        "registry_decide",
        "sdlc_address_review",
        "sdlc_complete",
        "sdlc_remediate",
    }
    # understand_repo: a write under episteme/. requirements_answer: one change's proposal.md.
    plans = {"sdlc_plan", "sdlc_approve", "understand_repo", "requirements_answer"}
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
        "requirements_answer",
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
        if hints["read_only_hint"] and not hints["idempotent_hint"]:
            assert scope == SCOPE_RUN, name  # reads only, but a model ran: audit_repo
        elif hints["read_only_hint"]:
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


async def test_approving_records_the_issue_type_the_document_was_derived_with(tmp_path: Path) -> None:
    """The gate re-derives the plan with it, so a Bug planned through the CLI and approved here
    is not refused as changed (ledger B18). This tool plans untyped, and records exactly that."""
    import json

    from orchestrator.plugin.server import sdlc_approve, sdlc_plan
    from orchestrator.sdlc.builddoc import approval_path, plan_dir

    repo = _tiny_repo(tmp_path)
    await sdlc_plan(str(repo), _plan_spec())
    sdlc_approve(str(repo), "TCK-9", decided_by="falcon")
    assert json.loads(approval_path("TCK-9", root=repo).read_text(encoding="utf-8"))["issue_type"] == ""

    document = plan_dir(repo) / "TCK-9-build.md"
    text = document.read_text(encoding="utf-8")
    document.write_text(
        text.replace("**Issue type:** untyped — set it with `--issue-type`", "**Issue type:** `Bug`"),
        encoding="utf-8",
    )
    sdlc_approve(str(repo), "TCK-9", decided_by="falcon")
    assert json.loads(approval_path("TCK-9", root=repo).read_text(encoding="utf-8"))["issue_type"] == "Bug"


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


def test_explain_symbol_across_repos_names_the_repo_and_the_reach(tmp_path: Path) -> None:
    out = explain_symbol(symbol="create_order", repos=_repos_config(tmp_path))
    assert out["found"], out
    match = out["matches"][0]
    assert match["repo"] == "billing"
    assert match["cross_repo_count"] >= 1 and any(c["repo"] == "web" for c in match["cross_repo"])
    assert out["standing"]["repos"] == ["billing", "web"]
    # Single-repo answers do not grow multi-repo keys.
    single = explain_symbol(str(tmp_path / "billing"), "create_order")
    assert "repo" not in single["matches"][0] and "standing" not in single


def test_regression_gaps_across_repos_flags_the_uncovered_symbol_in_the_other_service(tmp_path: Path) -> None:
    out = regression_gaps(symbol="create_order", repos=_repos_config(tmp_path))
    assert out["found"], out
    assert out["target_repo"] == "billing"
    assert all("repo" in u for u in out["uncovered"])
    # The headline: a change to billing's handler reaches web's caller, which nothing tests.
    elsewhere = out["uncovered_elsewhere"]
    assert elsewhere and all(u["repo"] == "web" for u in elsewhere)
    assert any(u["name"] == "place_order" for u in elsewhere)
    single = regression_gaps(str(tmp_path / "billing"), symbol="create_order")
    assert "uncovered_elsewhere" not in single and all("repo" not in u for u in single["uncovered"])


_TRACE_ROUTES = """\
Traceback (most recent call last):
  File "/srv/app/routes.py", line 8, in create_order
    return {"id": 1}
KeyError: 'id'
"""


def test_localize_across_repos_says_which_repo_a_frame_landed_in(tmp_path: Path) -> None:
    out = localize(trace=_TRACE_ROUTES, repos=_repos_config(tmp_path))
    resolved = [f for f in out["frames"] if f["resolved"]]
    assert resolved and resolved[0]["repo"] == "billing" and resolved[0]["candidates"] == []
    assert out["ambiguous_frames"] == []
    assert out["standing"]["repos"] == ["billing", "web"]
    single = localize(str(tmp_path / "billing"), _TRACE_ROUTES)
    assert "repo" not in single["frames"][0] and "ambiguous_frames" not in single


def test_localize_across_repos_reports_a_frame_two_services_could_own(tmp_path: Path) -> None:
    """Both repos have app/routes.py with a function covering line 8: the first match must
    not win silently."""
    a = _repo_at(tmp_path / "svc-a", "routes.py", _BILLING)
    b = _repo_at(tmp_path / "svc-b", "routes.py", _BILLING)
    cfg = tmp_path / "repos.yaml"
    cfg.write_text(f"repos:\n  svc-a: {a}\n  svc-b: {b}\n", encoding="utf-8")
    out = localize(trace=_TRACE_ROUTES, repos=str(cfg))
    frame = next(f for f in out["frames"] if f["resolved"])
    assert frame["candidates"], frame  # the other repo's match is listed, not dropped
    assert {frame["repo"], *(c.split("@")[0].split(":")[-1] for c in frame["candidates"])} == {
        "svc-a",
        "svc-b",
    }
    assert out["ambiguous_frames"] and out["ambiguous_frames"][0]["also"] == frame["candidates"]


def test_docs_for_across_repos_answers_each_repo_on_its_own(tmp_path: Path) -> None:
    cfg = _repos_config(tmp_path)
    (tmp_path / "billing" / "README.md").write_text(
        "# Billing\n\n`create_order` handles POST /v1/orders.\n", encoding="utf-8"
    )
    out = docs_for(symbol="create_order", repos=cfg)
    assert set(out["repos"]) == {"billing", "web"}
    assert out["repos"]["billing"]["found"] and out["repos"]["billing"]["matches"][0]["docs"]
    assert out["repos"]["web"].get("found") is not True  # no docs there at all
    # The README is uncommitted, so billing is not reproducible; web still is.
    assert out["repos"]["billing"]["reproducible"] is False and out["repos"]["web"]["reproducible"] is True
    assert out["standing"] == {"repos": ["billing", "web"], "reproducible": False, "untrusted": ["billing"]}
    assert "## billing" in out["markdown"] and "## web" in out["markdown"]
    coverage = docs_for(repos=cfg)
    assert coverage["repos"]["billing"]["docs"] >= 1


def test_every_multi_repo_tool_refuses_both_or_neither_of_repo_path_and_repos(tmp_path: Path) -> None:
    cfg = _repos_config(tmp_path)
    both = str(tmp_path / "billing")
    assert "exactly one" in explain_symbol(repo_path=both, symbol="x", repos=cfg)["error"]
    assert "exactly one" in explain_symbol(symbol="x")["error"]
    assert "exactly one" in regression_gaps(repo_path=both, symbol="x", repos=cfg)["error"]
    assert "exactly one" in localize(repo_path=both, trace="t\n", repos=cfg)["error"]
    assert "exactly one" in docs_for(repo_path=both, repos=cfg)["error"]
    assert "exactly one" in docs_for()["error"]
    assert "error" in docs_for(repos=str(tmp_path / "missing.yaml"))


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


def test_a_constructor_reports_who_creates_its_type(tmp_path: Path) -> None:
    pytest.importorskip("tree_sitter_java", reason="install the 'java' extra")
    (tmp_path / "Job.java").write_text(
        "package a;\npublic class Job { public Job() { } public Job(int n) { } }\n", encoding="utf-8"
    )
    (tmp_path / "Use.java").write_text(
        "package a;\npublic class Use { void go() { new Job(); new Job(2); } void run() { go(); } }\n",
        encoding="utf-8",
    )
    out = blast_radius(repo_path=str(tmp_path), symbol="Job")
    by_id = {m["id"]: m for m in out["matches"]}
    # B22 (D1): a creation lands on the Type, so its callers are its creators …
    assert [c["id"] for c in by_id["java:a.Job"]["callers"]] == ["java:a.Use.go"]
    assert "instantiated_via_type_count" not in by_id["java:a.Job"]
    # … and the constructor node, which no edge targets, reports them apart instead of "0 callers" (D8)
    ctor = by_id["java:a.Job.Job"]
    assert ctor["caller_count"] == 0 and ctor["instantiated_via_type_count"] == 1
    assert ctor["instantiated_via_type"][0]["id"] == "java:a.Use.go"
    assert "Instantiated through its type (1)" in out["markdown"]
    explained = {m["id"]: m for m in explain_symbol(repo_path=str(tmp_path), symbol="Job")["matches"]}
    assert explained["java:a.Job.Job"]["instantiated_via_type"] == ["java:a.Use.go"]
    assert "instantiated_via_type" not in explained["java:a.Job"]
    # a method not named for its type is not a constructor
    assert (
        "instantiated_via_type_count" not in blast_radius(repo_path=str(tmp_path), symbol="go")["matches"][0]
    )


def test_a_constructors_reach_across_repos_is_its_types(tmp_path: Path) -> None:
    pytest.importorskip("tree_sitter_java", reason="install the 'java' extra")
    pytest.importorskip("tree_sitter_kotlin", reason="install the 'kotlin' extra")
    (tmp_path / "lib" / "src" / "shared").mkdir(parents=True)
    (tmp_path / "lib" / "src" / "shared" / "Money.java").write_text(
        "package shared;\npublic class Money { public Money(int c) { } }\n", encoding="utf-8"
    )
    (tmp_path / "app" / "src" / "app").mkdir(parents=True)
    (tmp_path / "app" / "src" / "app" / "Pay.kt").write_text(
        "package app\n\nimport shared.Money\n\nclass Pay {\n    fun charge() { val m = Money(5) }\n}\n",
        encoding="utf-8",
    )
    cfg = tmp_path / "repos.yaml"
    cfg.write_text(
        "repos:\n  lib: lib\n  app: app\njoins:\n  - kind: package\n    consumer: app\n    provider: lib\n",
        encoding="utf-8",
    )
    by_id = {m["id"]: m for m in blast_radius(symbol="Money", repos=str(cfg))["matches"]}
    ctor = by_id["java:lib@shared.Money.Money"]
    # "instantiated from another repo" and "no dependents in other repos" in one answer was the bug
    assert ctor["instantiated_via_type_count"] == 1
    assert ctor["cross_repo_count"] == by_id["java:lib@shared.Money"]["cross_repo_count"] == 1
    explained = {m["id"]: m for m in explain_symbol(symbol="Money", repos=str(cfg))["matches"]}
    assert explained["java:lib@shared.Money.Money"]["cross_repo_count"] == 1


# ---- B52: a name with many matches is clipped honestly ---------------------------------


def _same_name_repo(tmp_path: Path, how_many: int) -> str:
    """`how_many` classes, each with a method called `summary` — one short name, many symbols."""
    body = "".join(f"class C{i}:\n    def summary(self):\n        return {i}\n\n\n" for i in range(how_many))
    (tmp_path / "shapes.py").write_text(body, encoding="utf-8")
    (tmp_path / "README.md").write_text("Every shape has a `summary` method.\n", encoding="utf-8")
    return str(tmp_path)


def test_blast_radius_says_how_many_matches_it_clipped(tmp_path: Path) -> None:
    out = blast_radius(_same_name_repo(tmp_path, 9), "summary")
    assert out["match_count"] == 9 and out["truncated"] is True
    assert len(out["matches"]) == 7  # the cap
    assert "Showing 7 of 9 matches" in out["markdown"]
    assert "Class.name" in out["markdown"]  # and how to select one


def test_a_name_within_the_cap_is_not_reported_truncated(tmp_path: Path) -> None:
    repo = _same_name_repo(tmp_path, 7)
    for out in (blast_radius(repo, "summary"), explain_symbol(repo, "summary"), docs_for(repo, "summary")):
        assert out["match_count"] == len(out["matches"]) == 7
        assert out["truncated"] is False
    assert "Showing" not in blast_radius(repo, "summary")["markdown"]


def test_a_qualified_name_reaches_a_match_past_the_cap(tmp_path: Path) -> None:
    repo = _same_name_repo(tmp_path, 9)
    # by id order C0..C6 are shown for the plain name, so C8 is hidden without a qualifier
    assert "py:shapes.C8.summary" not in [m["id"] for m in blast_radius(repo, "summary")["matches"]]
    for out in (
        blast_radius(repo, "C8.summary"),
        explain_symbol(repo, "C8.summary"),
        docs_for(repo, "C8.summary"),
    ):
        assert [m["id"] for m in out["matches"]] == ["py:shapes.C8.summary"]
        assert out["match_count"] == 1 and out["truncated"] is False
    # and a full id selects the same one
    assert [m["id"] for m in blast_radius(repo, "py:shapes.C8.summary")["matches"]] == [
        "py:shapes.C8.summary"
    ]


def test_the_three_tools_agree_on_the_match_count(tmp_path: Path) -> None:
    repo = _same_name_repo(tmp_path, 9)
    counts = {
        blast_radius(repo, "summary")["match_count"],
        explain_symbol(repo, "summary")["match_count"],
        docs_for(repo, "summary")["match_count"],
    }
    assert counts == {9}
    assert docs_for(repo, "summary")["truncated"] is True


def test_a_name_nothing_matches_reports_zero_matches(tmp_path: Path) -> None:
    repo = _same_name_repo(tmp_path, 3)
    for out in (blast_radius(repo, "nope"), explain_symbol(repo, "nope"), docs_for(repo, "nope")):
        assert out["found"] is False and out["matches"] == []
        assert out["match_count"] == 0 and out["truncated"] is False


def test_the_clipped_list_is_the_same_every_time(tmp_path: Path) -> None:
    repo = _same_name_repo(tmp_path, 9)
    first = blast_radius(repo, "summary")
    second = blast_radius(repo, "summary")
    assert [m["id"] for m in first["matches"]] == [m["id"] for m in second["matches"]]
    assert [m["id"] for m in first["matches"]] == [f"py:shapes.C{i}.summary" for i in range(7)]


def test_a_multi_repo_answer_reports_its_match_count_too(tmp_path: Path) -> None:
    config = _repos_config(tmp_path)
    for tool in (blast_radius, explain_symbol):
        out = tool(symbol="create_order", repos=config)
        assert out["match_count"] == len(out["matches"]) and out["truncated"] is False


def test_docs_for_and_explain_symbol_say_what_they_clipped(tmp_path: Path) -> None:
    repo = _same_name_repo(tmp_path, 9)
    docs = docs_for(repo, "summary")
    assert docs["match_count"] == 9 and docs["truncated"] is True and len(docs["matches"]) == 7
    assert "Showing 7 of 9 matches" in docs["markdown"]
    explained = explain_symbol(repo, "summary")
    assert explained["match_count"] == 9 and explained["truncated"] is True
    assert len(explained["matches"]) == 7


def _two_repos_sharing_a_name(tmp_path: Path, each: int) -> str:
    def body(owner: str) -> str:
        return "".join(
            f"class {owner}{i}:\n    def summary(self):\n        return {i}\n\n\n" for i in range(each)
        )

    billing = _repo_at(tmp_path / "billing", "shapes.py", body("B"))
    web = _repo_at(tmp_path / "web", "shapes.py", body("W"))
    cfg = tmp_path / "repos.yaml"
    cfg.write_text(f"repos:\n  billing: {billing}\n  web: {web}\n", encoding="utf-8")
    return str(cfg)


def test_a_merged_graph_clips_honestly_across_repositories(tmp_path: Path) -> None:
    config = _two_repos_sharing_a_name(tmp_path, 5)  # 5 + 5 = 10 `summary` methods in all
    for tool in (blast_radius, explain_symbol):
        out = tool(symbol="summary", repos=config)
        assert out["match_count"] == 10 and out["truncated"] is True and len(out["matches"]) == 7
    assert "Showing 7 of 10 matches" in blast_radius(symbol="summary", repos=config)["markdown"]
    # a Class.name picks one, across the repositories, and is counted as one
    one = blast_radius(symbol="W3.summary", repos=config)
    assert one["match_count"] == 1 and one["truncated"] is False and len(one["matches"]) == 1


def test_docs_for_answers_each_repository_with_its_own_count(tmp_path: Path) -> None:
    config = _two_repos_sharing_a_name(tmp_path, 9)
    for name in ("billing", "web"):
        (tmp_path / name / "README.md").write_text("Each shape has a `summary` method.\n", encoding="utf-8")
    out = docs_for(symbol="summary", repos=config)
    for name in ("billing", "web"):
        per_repo = out["repos"][name]
        assert per_repo["match_count"] == 9 and per_repo["truncated"] is True


# ---- B62: the Called by line says it is a floor -----------------------------------------


def test_the_called_by_line_says_it_counts_only_what_the_graph_can_type(tmp_path: Path) -> None:
    repo = _comprehension_repo(tmp_path)
    line = next(ln for ln in blast_radius(repo, "validate")["markdown"].splitlines() if "Called by (" in ln)
    assert "at least" in line and "the graph can type" in line


def test_zero_callers_is_not_reported_as_none(tmp_path: Path) -> None:
    # `handler` has no caller in the graph; "(0)" alone reads as "nothing calls this".
    line = next(
        ln
        for ln in blast_radius(_comprehension_repo(tmp_path), "handler")["markdown"].splitlines()
        if "Called by (" in ln
    )
    assert line.startswith("- **Called by (0, at least")


# ---- B62: possible untraced callers --------------------------------------------------------

_UNTRACED = """\
class Store:
    def get(self, key):
        return key


def typed(s: Store):
    return s.get(1)


def untyped(s):
    return s.get(2)


def other(d):
    return d.get("x")
"""


def _untraced_repo(tmp_path: Path, source: str = _UNTRACED) -> str:
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "shop.py").write_text(source, encoding="utf-8")
    return str(tmp_path)


def test_blast_radius_reports_the_untraced_calls_apart_from_the_callers(tmp_path: Path) -> None:
    out = blast_radius(_untraced_repo(tmp_path), "get")
    [m] = out["matches"]
    # the graph's answer is untouched: one caller, the one it could type
    assert m["caller_count"] == 1 and [c["id"] for c in m["callers"]] == ["py:shop.typed"]
    # and the rest is reported as a hint, with the evidence a reader needs to judge it
    u = m["unresolved_calls"]
    assert (u["count"], u["shown"], u["declared"]) == (2, 2, 1)
    assert sorted((s["receiver"], s["caller"]) for s in u["sites"]) == [
        ("d", "py:shop.other"),
        ("s", "py:shop.untyped"),
    ]
    assert all(s["at"].startswith("shop.py:") for s in u["sites"])
    assert "Possible untraced" in out["markdown"] and "unverified" in out["markdown"]
    assert "`s.get`" in out["markdown"] and "`d.get`" in out["markdown"]


def test_explain_symbol_carries_the_same_untraced_calls(tmp_path: Path) -> None:
    repo = _untraced_repo(tmp_path)
    assert (
        explain_symbol(repo, "get")["matches"][0]["unresolved_calls"]
        == blast_radius(repo, "get")["matches"][0]["unresolved_calls"]
    )


def test_a_symbol_nothing_untraced_calls_says_so_rather_than_saying_nothing(tmp_path: Path) -> None:
    out = blast_radius(_comprehension_repo(tmp_path), "validate")
    m = out["matches"][0]
    assert m["unresolved_calls"] == {"count": 0, "shown": 0, "declared": 1, "sites": []}
    assert "No untraced calls named `validate`" in out["markdown"]


def test_a_symbol_in_a_language_that_is_not_tracked_gets_null_and_a_note_never_zero(tmp_path: Path) -> None:
    (tmp_path / "Foo.java").write_text("class Foo {\n    void run() {}\n}\n", encoding="utf-8")
    out = blast_radius(str(tmp_path), "run")
    m = next(x for x in out["matches"] if x["id"].startswith("java:"))
    assert m["unresolved_calls"] is None
    assert "not tracked" in m["unresolved_calls_note"] and "java" in m["unresolved_calls_note"]
    assert "not tracked" in out["markdown"]


def test_a_class_has_no_untraced_calls_key_because_the_question_does_not_apply(tmp_path: Path) -> None:
    m = blast_radius(_untraced_repo(tmp_path), "Store")["matches"][0]
    assert m["kind"] == "Type" and "unresolved_calls" not in m


def test_when_the_list_cannot_be_loaded_the_code_answer_survives_and_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import orchestrator.pkg.persistence as persistence

    def boom(*_a: Any, **_k: Any) -> None:
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(persistence, "load_with_unbound", boom)
    m = blast_radius(_untraced_repo(tmp_path), "get")["matches"][0]
    assert m["caller_count"] == 1  # the graph answer is intact
    assert m["unresolved_calls"] is None and "not available" in m["unresolved_calls_note"]


def test_the_untraced_list_is_bounded_and_the_count_is_exact(tmp_path: Path) -> None:
    body = "class Store:\n    def get(self, k):\n        return k\n\n\n"
    body += "".join(f"def f{i:02d}(s):\n    return s.get({i})\n\n\n" for i in range(30))
    out = blast_radius(_untraced_repo(tmp_path, body), "get")
    u = out["matches"][0]["unresolved_calls"]
    assert u["count"] == 30 and u["shown"] == len(u["sites"]) == 25
    assert [s["caller"] for s in u["sites"]] == [f"py:shop.f{i:02d}" for i in range(25)]
    assert "30" in out["markdown"] and "shown" in out["markdown"]


def _merged_untraced_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, billing: str, web: str) -> str:
    import orchestrator.pkg.persistence as persistence

    monkeypatch.setattr(persistence, "default_cache_dir", lambda: tmp_path / "cache")
    billing_repo = _repo_at(tmp_path / "billing", "shop.py", billing)
    web_repo = _repo_at(tmp_path / "web", "shop.py", web)
    config = tmp_path / "repos.yaml"
    config.write_text(f"repos:\n  billing: {billing_repo}\n  web: {web_repo}\n", encoding="utf-8")
    return str(config)


def test_a_merged_graph_reports_each_symbols_own_repository_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _merged_untraced_config(
        tmp_path,
        monkeypatch,
        "class Store:\n    def get(self, k):\n        return k\n\n\ndef untyped(s):\n    return s.get(2)\n",
        "class Cache:\n    def get(self, k):\n        return k\n\n\ndef other(d):\n    return d.get(1)\n",
    )
    out = blast_radius(symbol="get", repos=config)
    got = {m["id"]: m["unresolved_calls"] for m in out["matches"]}
    assert sorted(got) == ["py:billing@app.shop.Store.get", "py:web@app.shop.Cache.get"]
    # each symbol shows its own repository's call, scoped to name nodes of the merged graph; the other
    # repository's `get` is a different function, so its caller is not offered as a hint for this one
    assert [s["caller"] for s in got["py:billing@app.shop.Store.get"]["sites"]] == [
        "py:billing@app.shop.untyped"
    ]
    assert [s["caller"] for s in got["py:web@app.shop.Cache.get"]["sites"]] == ["py:web@app.shop.other"]
    assert all((u["count"], u["declared"]) == (1, 1) for u in got.values())
    assert out["standing"]["repos"] == ["billing", "web"]


def test_a_call_into_a_name_only_another_repository_declares_is_not_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Across repositories the graph models calls only through declared joins, never as plain method
    # calls, so web calling billing's `charge` through an untyped receiver is not a hint here.
    config = _merged_untraced_config(
        tmp_path,
        monkeypatch,
        "class Billing:\n    def charge(self, amount):\n        return amount\n",
        "def pay(client):\n    return client.charge(5)\n",
    )
    [m] = blast_radius(symbol="charge", repos=config)["matches"]
    assert m["unresolved_calls"]["count"] == 0


def _many_untraced(tmp_path: Path, n: int) -> str:
    body = "class Store:\n    def get(self, k):\n        return k\n\n\n"
    body += "".join(f"def f{i:02d}(s):\n    return s.get({i})\n\n\n" for i in range(n))
    return _untraced_repo(tmp_path, body)


def test_the_untraced_list_at_the_cap_is_whole_and_one_past_it_is_clipped(tmp_path: Path) -> None:
    exact = blast_radius(_many_untraced(tmp_path / "a", 25), "get")["matches"][0]["unresolved_calls"]
    assert (exact["count"], exact["shown"]) == (25, 25)
    over = blast_radius(_many_untraced(tmp_path / "b", 26), "get")["matches"][0]["unresolved_calls"]
    assert (over["count"], over["shown"]) == (26, 25)


def test_the_markdown_lists_ten_untraced_sites_and_says_how_many_there_are(tmp_path: Path) -> None:
    out = blast_radius(_many_untraced(tmp_path, 12), "get")
    [line] = [ln for ln in out["markdown"].splitlines() if "Possible untraced" in ln]
    assert line.count("`s.get`") == 10 and "10 of 12 shown" in line
    whole = blast_radius(_many_untraced(tmp_path / "c", 10), "get")["markdown"]
    assert "shown" not in next(ln for ln in whole.splitlines() if "Possible untraced" in ln)


def test_explain_symbol_says_null_for_an_untracked_language_and_for_an_unavailable_list(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import orchestrator.pkg.persistence as persistence

    (tmp_path / "Foo.java").write_text("class Foo {\n    void run() {}\n}\n", encoding="utf-8")
    m = next(x for x in explain_symbol(str(tmp_path), "run")["matches"] if x["id"].startswith("java:"))
    assert m["unresolved_calls"] is None and "not tracked" in m["unresolved_calls_note"]

    def boom(*_a: Any, **_k: Any) -> None:
        raise RuntimeError("no")

    monkeypatch.setattr(persistence, "load_with_unbound", boom)
    other = tmp_path / "py"
    other.mkdir()
    n = explain_symbol(_untraced_repo(other), "get")["matches"][0]
    assert n["unresolved_calls"] is None and "not available" in n["unresolved_calls_note"]
    assert n["called_by"]  # the graph answer is intact
