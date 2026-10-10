"""PKG-grounded codegen: spec-driven retrieval + context injection."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest

from orchestrator.core.llm import CompletionResult, Message
from orchestrator.pkg import (
    FactBatch,
    FactStore,
    GroundedRetriever,
    Node,
    NodeKind,
    Provenance,
)
from orchestrator.sdlc.codegen import LLMCodegenAdapter
from orchestrator.sdlc.grounding import PKGCodegenGrounder, _spec_query

LEDGER = '''\
class TokenLedger:
    """Tracks per-stage token usage."""

    def record(self, stage, result):
        return None
'''

UNRELATED = "class WebhookRouter:\n    pass\n"


def _repo(tmp_path: Path) -> Path:
    (tmp_path / "ledger.py").write_text(LEDGER, encoding="utf-8")
    (tmp_path / "webhook.py").write_text(UNRELATED, encoding="utf-8")
    return tmp_path


SPEC = {
    "title": "Persist the token ledger",
    "summary": "Save TokenLedger usage records to disk and load them back.",
    "acceptance_criteria": ["a saved ledger round-trips"],
}


# ---- relevant_symbols -------------------------------------------------------


def test_relevant_symbols_ranks_lexical_matches() -> None:
    batch = FactBatch()
    batch.add_node(Node("py:a.TokenLedger", NodeKind.TYPE, "TokenLedger", "python", Provenance("a.py", 1)))
    batch.add_node(
        Node("py:a.WebhookRouter", NodeKind.TYPE, "WebhookRouter", "python", Provenance("a.py", 9))
    )
    batch.add_node(Node("py:ext.thing", NodeKind.MODULE, "thing", "python", external=True))
    r = GroundedRetriever(FactStore(batch))

    hits = r.relevant_symbols("persist the token ledger to disk")
    assert [n.id for n in hits] == ["py:a.TokenLedger"]  # unrelated + external excluded


def test_relevant_symbols_empty_query_or_no_overlap() -> None:
    batch = FactBatch()
    batch.add_node(Node("py:a.Thing", NodeKind.TYPE, "Thing", "python", Provenance("a.py", 1)))
    r = GroundedRetriever(FactStore(batch))
    assert r.relevant_symbols("") == []
    assert r.relevant_symbols("completely unrelated words") == []


# ---- PKGCodegenGrounder -----------------------------------------------------


def test_context_includes_matching_symbol_with_source(tmp_path: Path) -> None:
    grounder = PKGCodegenGrounder.from_repo(_repo(tmp_path))
    context = grounder.context_for_spec(SPEC)
    assert "py:ledger.TokenLedger" in context
    assert "Tracks per-stage token usage" in context  # real source read off disk
    assert "ledger.py:1" in context  # provenance shown
    assert "WebhookRouter" not in context  # irrelevant symbol excluded


def test_context_empty_when_repo_has_nothing_relevant(tmp_path: Path) -> None:
    (tmp_path / "webhook.py").write_text(UNRELATED, encoding="utf-8")
    grounder = PKGCodegenGrounder.from_repo(tmp_path)
    assert grounder.context_for_spec(SPEC) == ""


def test_explicit_entry_point_survives_verbose_graph_ticket(tmp_path: Path) -> None:
    """A named owner must not disappear behind generic graph/export matches."""
    source = tmp_path / "extractor.py"
    source.write_text("class RepoCodeExtractor:\n    def extract(self):\n        return []\n")
    (tmp_path / "graph.py").write_text("def export():\n    return []\n")
    batch = FactBatch()
    batch.add_node(
        Node(
            "py:extractor.RepoCodeExtractor",
            NodeKind.TYPE,
            "RepoCodeExtractor",
            "python",
            Provenance("extractor.py", 1),
        )
    )
    batch.add_node(
        Node(
            "py:extractor.RepoCodeExtractor.extract",
            NodeKind.FUNCTION,
            "extract",
            "python",
            Provenance("extractor.py", 2),
        )
    )
    for index in range(12):
        batch.add_node(
            Node(
                f"py:graph.PythonCallsGraphExport{index}",
                NodeKind.FUNCTION,
                f"PythonCallsGraphExport{index}",
                "python",
                Provenance("graph.py", 1),
            )
        )
    store = FactStore(batch)
    grounder = PKGCodegenGrounder(GroundedRetriever(store), root=tmp_path, store=store)
    spec = {
        "title": "Python CALLS graph export",
        "summary": (
            "The Python CALLS graph export omits typed parameter calls. "
            "The public entry point is RepoCodeExtractor.extract. "
            "Preserve provenance and deterministic graph output."
        ),
    }
    context = grounder.context_for_spec(spec)
    assert context.index("py:extractor.RepoCodeExtractor.extract") < context.index("py:graph.")
    assert "extractor.py:2" in context
    assert context == grounder.context_for_spec(spec)


def test_qualified_lowercase_entry_points_survive_verbose_ticket(tmp_path: Path) -> None:
    """Exact qualified function names outrank generic lexical matches."""
    source = tmp_path / "app.py"
    source.write_text(
        "def api_search():\n    return []\n\ndef api_search_stream():\n    return []\n",
        encoding="utf-8",
    )
    batch = FactBatch()
    for name, line in (("api_search", 1), ("api_search_stream", 4)):
        batch.add_node(
            Node(
                f"py:wizard.app.{name}",
                NodeKind.FUNCTION,
                name,
                "python",
                Provenance("app.py", line),
            )
        )
    for index in range(12):
        batch.add_node(
            Node(
                f"py:graph.SearchGraphExport{index}",
                NodeKind.FUNCTION,
                f"SearchGraphExport{index}",
                "python",
                Provenance("app.py", 1),
            )
        )
    store = FactStore(batch)
    grounder = PKGCodegenGrounder(GroundedRetriever(store), root=tmp_path, store=store)
    context = grounder.context_for_spec(
        {
            "title": "Search graph export",
            "summary": (
                "The Search graph export needs rules. Update "
                "wizard.app.api_search and wizard.app.api_search_stream."
            ),
        }
    )
    assert "py:wizard.app.api_search" in context
    assert "py:wizard.app.api_search_stream" in context
    assert context.index("py:wizard.app.api_search_stream") < context.index("py:graph.")
    assert context == grounder.context_for_spec(
        {
            "title": "Search graph export",
            "summary": (
                "The Search graph export needs rules. Update "
                "wizard.app.api_search and wizard.app.api_search_stream."
            ),
        }
    )


# ---- context_for_required_entry_points (SSPN-120) --------------------------

RULE_LOADERS = '''\
class RuleLoader:
    def load(self):
        raise NotImplementedError


class ColumnRuleLoader(RuleLoader):
    """Loads column rules without ever compiling them -- the ONTM-4 bug."""

    def load(self):
        return self.read_raw()

    def read_raw(self):
        return []


class ReferenceRuleLoader(RuleLoader):
    """Loads reference rules and compiles them -- the correctly-wired sibling."""

    def load(self):
        return self.compiler.compile(self.read_raw())

    def read_raw(self):
        return []
'''


def _rule_loader_repo(tmp_path: Path) -> Path:
    (tmp_path / "rules.py").write_text(RULE_LOADERS, encoding="utf-8")
    return tmp_path


def _requirement(entry_point: str | None) -> Any:
    from orchestrator.sdlc.required_behavior import Requirement

    return Requirement(
        requirement_id="default-wiring",
        description="",
        command=("python3", "-c", "pass"),
        entry_point=entry_point,
    )


def test_surfaces_the_sibling_that_wires_correctly(tmp_path: Path) -> None:
    grounder = PKGCodegenGrounder.from_repo(_rule_loader_repo(tmp_path), use_cache=False)
    context = grounder.context_for_required_entry_points([_requirement("ColumnRuleLoader")])
    assert "ReferenceRuleLoader" in context
    assert "compiles them -- the correctly-wired sibling" in context


def test_does_not_surface_the_target_itself_or_the_base(tmp_path: Path) -> None:
    grounder = PKGCodegenGrounder.from_repo(_rule_loader_repo(tmp_path), use_cache=False)
    context = grounder.context_for_required_entry_points([_requirement("ColumnRuleLoader")])
    assert "ColumnRuleLoader" not in context  # the buggy symbol itself proves nothing
    assert "class RuleLoader" not in context  # the base, not a sibling implementation


def test_empty_without_an_entry_point(tmp_path: Path) -> None:
    grounder = PKGCodegenGrounder.from_repo(_rule_loader_repo(tmp_path), use_cache=False)
    assert grounder.context_for_required_entry_points([_requirement(None)]) == ""


def test_empty_when_the_entry_point_does_not_resolve(tmp_path: Path) -> None:
    grounder = PKGCodegenGrounder.from_repo(_rule_loader_repo(tmp_path), use_cache=False)
    assert grounder.context_for_required_entry_points([_requirement("NoSuchSymbol")]) == ""


def test_empty_when_the_entry_point_has_no_siblings(tmp_path: Path) -> None:
    """A target with no shared base (or no PKG at all) just gets no extra grounding."""
    (tmp_path / "standalone.py").write_text("class Standalone:\n    pass\n", encoding="utf-8")
    grounder = PKGCodegenGrounder.from_repo(tmp_path, use_cache=False)
    assert grounder.context_for_required_entry_points([_requirement("Standalone")]) == ""


def test_grounding_is_additive_not_gated_on_lexical_match(tmp_path: Path) -> None:
    """_grounding() must combine context_for_spec and the sibling grounding -- an empty
    lexical match must not suppress a sibling finding (and vice versa)."""
    from orchestrator.sdlc.codegen import LLMCodegenAdapter

    grounder = PKGCodegenGrounder.from_repo(_rule_loader_repo(tmp_path), use_cache=False)
    (tmp_path / ".spine").mkdir()
    (tmp_path / ".spine" / "required-behavior.yaml").write_text(
        "requirements:\n"
        "  - id: default-wiring\n"
        "    entry_point: ColumnRuleLoader\n"
        "    command: ['python3', '-c', 'pass']\n",
        encoding="utf-8",
    )
    adapter = LLMCodegenAdapter(Mock(), grounder=grounder)
    # An unrelated spec: context_for_spec alone would find nothing relevant here.
    block = adapter._grounding({"title": "Unrelated", "summary": "Nothing to do with rules"}, tmp_path)
    assert "ReferenceRuleLoader" in block


def test_rust_symbol_source_uses_rust_fence(tmp_path: Path) -> None:
    pytest.importorskip("tree_sitter_rust_orchard", reason="install the 'rust' extra")
    (tmp_path / "Cargo.toml").write_text('[package]\nname="demo"\nversion="0.1.0"\n')
    src = tmp_path / "src"
    src.mkdir()
    (src / "lib.rs").write_text("pub struct Widget;\n", encoding="utf-8")
    context = PKGCodegenGrounder.from_repo(tmp_path, use_cache=False).context_for_symbols(["Widget"])
    assert "rust:demo@lib/demo::Widget" in context
    assert "```rust\npub struct Widget;" in context


def test_context_folds_in_documentation_for_reused_symbols(tmp_path: Path) -> None:
    _repo(tmp_path)
    (tmp_path / "README.md").write_text(
        "# Ledger\nThe `TokenLedger` records per-stage usage and persists it to disk.\n",
        encoding="utf-8",
    )
    context = PKGCodegenGrounder.from_repo(tmp_path).context_for_spec(SPEC)
    assert "py:ledger.TokenLedger" in context  # the code is still grounded
    # …and its human documentation rides along, tied to the section that names it.
    assert "Documented in `README.md#ledger`" in context
    assert "records per-stage usage and persists it to disk" in context


def test_spec_query_concatenates_prose_fields() -> None:
    q = _spec_query(SPEC)
    assert "Persist the token ledger" in q and "round-trips" in q


def test_comprehension_compounds_as_the_repo_grows(tmp_path: Path) -> None:
    """The thesis behind wiring this into `sdlc feature`: a greenfield target
    grounds on nothing, but once a ticket lands code, the *next* ticket's
    codegen sees it. Per-target, no LLM — pure retrieval over the worktree."""
    repo = tmp_path / "target"
    repo.mkdir()

    # Ticket 1 against a greenfield repo: no existing-codebase context.
    assert PKGCodegenGrounder.from_repo(repo, use_cache=False).context_for_spec(SPEC) == ""

    # Ticket 1 lands a TokenLedger module into the target repo.
    (repo / "ledger.py").write_text(LEDGER, encoding="utf-8")

    # Ticket 2 (related spec) now grounds on ticket 1's real code.
    context = PKGCodegenGrounder.from_repo(repo, use_cache=False).context_for_spec(SPEC)
    assert "py:ledger.TokenLedger" in context
    assert "Tracks per-stage token usage" in context


# ---- adapter injection ------------------------------------------------------


class _CapturingLLM:
    def __init__(self) -> None:
        self.last_user = ""

    async def complete(
        self,
        messages: list[Message],
        *,
        model: str,
        response_format: object | None = None,
        json_object: bool = False,
        temperature: float | None = None,
        max_tokens: int | None = None,
        tools: object = None,
        tool_choice: str | None = None,
    ) -> CompletionResult:
        self.last_user = messages[-1].content
        return CompletionResult(
            '{"files": [{"path": "out.py", "content": "x = 1\\n"}], "summary": "ok"}',
            model,
            1,
            1,
            0.0,
            1.0,
        )


class _FixedGrounder:
    def context_for_spec(self, spec: dict[str, Any]) -> str:
        return "EXISTING CODEBASE CONTEXT: TokenLedger lives in orchestrator.core.llm"


async def test_adapter_prepends_grounding_to_implement(tmp_path: Path) -> None:
    llm = _CapturingLLM()
    adapter = LLMCodegenAdapter(llm, grounder=_FixedGrounder())
    await adapter.implement(spec=SPEC, path=str(tmp_path), issue_key="ENG-1")
    assert llm.last_user.startswith("EXISTING CODEBASE CONTEXT")
    assert "SPEC:" in llm.last_user


async def test_adapter_grounds_refine_too(tmp_path: Path) -> None:
    llm = _CapturingLLM()
    adapter = LLMCodegenAdapter(llm, grounder=_FixedGrounder())
    await adapter.refine(spec=SPEC, path=str(tmp_path), issue_key="ENG-1", failures="boom")
    assert llm.last_user.startswith("EXISTING CODEBASE CONTEXT")
    assert "FAILURE OUTPUT:" in llm.last_user


async def test_adapter_without_grounder_unchanged(tmp_path: Path) -> None:
    llm = _CapturingLLM()
    adapter = LLMCodegenAdapter(llm)
    await adapter.implement(spec=SPEC, path=str(tmp_path), issue_key="ENG-1")
    assert llm.last_user.startswith("Issue: ENG-1")


# ---- grounder factory (worker fan-out: one adapter, many worktrees) --------


async def test_grounder_factory_builds_per_root_and_caches(tmp_path: Path) -> None:
    calls: list[Path] = []

    def factory(root: Path) -> _FixedGrounder:
        calls.append(root)
        return _FixedGrounder()

    llm = _CapturingLLM()
    adapter = LLMCodegenAdapter(llm, grounder_factory=factory)

    wt_a, wt_b = tmp_path / "a", tmp_path / "b"
    wt_a.mkdir()
    wt_b.mkdir()
    await adapter.implement(spec=SPEC, path=str(wt_a), issue_key="A-1")
    assert llm.last_user.startswith("EXISTING CODEBASE CONTEXT")  # grounded from the factory
    await adapter.author_tests(spec=SPEC, path=str(wt_a), issue_key="A-1")  # same root → cached
    await adapter.implement(spec=SPEC, path=str(wt_b), issue_key="B-1")  # new root → built again
    assert calls == [wt_a.resolve(), wt_b.resolve()]  # one build per distinct worktree


async def test_explicit_grounder_takes_precedence_over_factory(tmp_path: Path) -> None:
    def factory(root: Path) -> _FixedGrounder:  # should never be called
        raise AssertionError("factory used despite an explicit grounder")

    llm = _CapturingLLM()
    adapter = LLMCodegenAdapter(llm, grounder=_FixedGrounder(), grounder_factory=factory)
    await adapter.implement(spec=SPEC, path=str(tmp_path), issue_key="ENG-1")
    assert llm.last_user.startswith("EXISTING CODEBASE CONTEXT")


async def test_grounder_factory_failure_never_breaks_codegen(tmp_path: Path) -> None:
    def factory(root: Path) -> _FixedGrounder:
        raise RuntimeError("extraction blew up")

    llm = _CapturingLLM()
    adapter = LLMCodegenAdapter(llm, grounder_factory=factory)
    # Codegen proceeds ungrounded rather than raising.
    await adapter.implement(spec=SPEC, path=str(tmp_path), issue_key="ENG-1")
    assert llm.last_user.startswith("Issue: ENG-1")
