"""B44: codegen edits only what the ticket is about.

Measured 2026-09-27 on the codegen benchmark: on OpenAI models Spine + PKG edited 6-9 unrelated
tracked files per run, and every one came from its own design — the module a ticket named by its
dotted name was not recognised, word matches were listed as files to touch, and codegen harvested
the blast radius as files "this ticket is going to change". These tests pin the three fixes:
named modules resolve through the graph, a create ticket's matches are read-only, and a confident
edit scope refuses out-of-scope edits before they are written.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from orchestrator.sdlc.scope import EditScope


def _graph(*modules: tuple[str, str]) -> Any:
    """A store holding Python modules as the extractor names them: dotted name → file."""
    from orchestrator.pkg import FactStore
    from orchestrator.pkg.facts import Edge, EdgeKind, FactBatch, Node, NodeKind, Provenance

    batch = FactBatch()
    for name, file in modules:
        mid = f"py:{name}"
        batch.add_node(Node(mid, NodeKind.MODULE, name, "python", Provenance(file, 1)))
        leaf = name.rsplit(".", 1)[-1]
        fid = f"{mid}.{leaf}_summary"
        batch.add_node(Node(fid, NodeKind.FUNCTION, f"{leaf}_summary", "python", Provenance(file, 5)))
        batch.add_edge(Edge(mid, fid, EdgeKind.CONTAINS))
    if any(name == "orchestrator.codereview.verifiers" for name, _ in modules):
        tid = "py:orchestrator.codereview.verifiers.Finding"
        where = Provenance("src/orchestrator/codereview/verifiers.py", 45)
        batch.add_node(Node(tid, NodeKind.TYPE, "Finding", "python", where))
    # A Java package: one Module node shared by the package, provenance on whichever file came first.
    batch.add_node(
        Node(
            "java:com.acme.orders",
            NodeKind.MODULE,
            "com.acme.orders",
            "java",
            Provenance("src/Address.java", 1),
        )
    )
    return FactStore(batch)


_MODULES = (
    ("orchestrator.codereview.verifiers", "src/orchestrator/codereview/verifiers.py"),
    ("orchestrator.pkg", "src/orchestrator/pkg/__init__.py"),
    ("orchestrator.pkg.docs", "src/orchestrator/pkg/docs.py"),
    ("orchestrator.core.llm", "src/orchestrator/core/llm/__init__.py"),
    ("orchestrator.catalog.models", "src/orchestrator/catalog/models.py"),
)


# --- F1: a module the ticket names by its dotted name is stated --------------------------


def test_a_dotted_module_in_the_ticket_resolves_through_the_graph() -> None:
    from orchestrator.sdlc.design import _stated_modules

    spec = {"technical_notes": "Reuse worst_severity from orchestrator.codereview.verifiers."}
    assert _stated_modules(spec, _graph(*_MODULES)) == ["src/orchestrator/codereview/verifiers.py"]


def test_the_longest_module_prefix_wins_over_a_symbol_suffix() -> None:
    from orchestrator.sdlc.design import _stated_modules

    spec = {"summary": "turn orchestrator.codereview.verifiers.Finding objects into text"}
    assert _stated_modules(spec, _graph(*_MODULES)) == ["src/orchestrator/codereview/verifiers.py"]


def test_a_package_and_the_next_word_resolve_to_that_child_module() -> None:
    from orchestrator.sdlc.design import _stated_modules

    spec = {"technical_notes": "Consume the finding type from the orchestrator.pkg docs module."}
    assert _stated_modules(spec, _graph(*_MODULES)) == ["src/orchestrator/pkg/docs.py"]


def test_a_package_with_no_matching_child_is_the_package_itself() -> None:
    from orchestrator.sdlc.design import _stated_modules

    spec = {"technical_notes": "Reuse TokenLedger from orchestrator.core.llm; do not redefine it."}
    assert _stated_modules(spec, _graph(*_MODULES)) == ["src/orchestrator/core/llm/__init__.py"]


def test_a_dotted_name_the_graph_does_not_hold_is_not_a_file() -> None:
    from orchestrator.sdlc.design import _stated_modules

    spec = {"summary": "e.g. see version 1.2 and orchestrator.nothing.here for context"}
    assert _stated_modules(spec, _graph(*_MODULES)) == []
    assert _stated_modules(spec, None) == []


# --- F2: files to edit apart from files to read ------------------------------------------

_CREATE = {
    "title": "Summarise code-review findings for a PR comment",
    "summary": "Add a new module with summarise_findings(findings).",
    "technical_notes": "Reuse the worst_severity helper from orchestrator.codereview.verifiers.",
    "acceptance_criteria": ["returns a one-line summary string"],
}


async def test_a_create_ticket_reads_the_module_it_names_and_edits_nothing_existing() -> None:
    from orchestrator.sdlc.design import produce_design

    design = await produce_design(
        {**_CREATE, "kind": "create"}, overview=None, store=_graph(*_MODULES), llm=None
    )

    assert design["files_to_touch"] == []
    assert design["files_to_read"] == ["src/orchestrator/codereview/verifiers.py"]
    assert design["files_origin"] == "module"
    assert EditScope.from_design(design, {"kind": "create"}).confident  # a create ticket is sure
    assert "Create a new module" in design["approach"] and ".py" not in design["approach"]
    # The word "models"/"summary" matching other modules no longer lists them anywhere.
    assert "src/orchestrator/catalog/models.py" not in design["files_to_read"]


async def test_an_edit_ticket_edits_the_module_it_names() -> None:
    from orchestrator.sdlc.design import produce_design

    design = await produce_design(
        {**_CREATE, "kind": "edit"}, overview=None, store=_graph(*_MODULES), llm=None
    )

    assert design["files_to_touch"][0] == "src/orchestrator/codereview/verifiers.py"
    assert design["files_to_read"] == []


async def test_a_ticket_of_unknown_kind_edits_the_module_it_names_and_keeps_its_landing() -> None:
    """Production specs carry no kind (review B1): a bug ticket naming `orchestrator.pkg.docs` names
    the file to fix, so it must be an edit target — and a module named in passing must not
    replace the file the ticket lands in."""
    from orchestrator.sdlc.design import produce_design

    spec = {"title": "Fix the docs summary", "summary": "orchestrator.pkg.docs drops a docs summary line"}
    design = await produce_design(spec, overview=None, store=_graph(*_MODULES), llm=None)

    assert design["files_to_touch"][0] == "src/orchestrator/pkg/docs.py"
    assert design["files_to_read"] == []
    assert design["files_origin"] == "module"
    assert not EditScope.from_design(design, spec).confident  # graph-resolved, never enforced


async def test_a_create_ticket_that_names_nothing_reads_its_word_matches() -> None:
    from orchestrator.sdlc.design import produce_design

    spec = {"title": "Summarise the docs report", "summary": "docs summary report", "kind": "create"}
    design = await produce_design(spec, overview=None, store=_graph(*_MODULES), llm=None)

    assert design["files_to_touch"] == []
    assert design["files_to_read"]
    assert any("listed to read, not to change" in r for r in design["risks"])


async def test_a_ticket_of_unknown_kind_keeps_its_word_matches_as_edit_targets() -> None:
    """The bug-ticket case the old behaviour exists for (see test_design): a word match is
    sometimes the only localisation, and demoting it would strip the ticket of its target."""
    from orchestrator.sdlc.design import produce_design

    spec = {"title": "Fix the docs summary", "summary": "docs summary drops a line"}
    design = await produce_design(spec, overview=None, store=_graph(*_MODULES), llm=None)

    assert design["files_to_touch"]
    assert design["files_to_read"] == []


def test_the_rendered_design_separates_edit_and_read() -> None:
    from orchestrator.sdlc.design import render_design_md
    from orchestrator.sdlc.scope import READ_HEADING

    md = render_design_md(
        _CREATE, {"approach": "A", "files_to_touch": ["src/a.py"], "files_to_read": ["src/b.py"]}
    )

    assert "## Files to edit\n- src/a.py" in md
    assert f"## {READ_HEADING}\n- src/b.py" in md
    assert "Files to touch" not in md


def test_codegen_takes_edit_paths_only_from_the_edit_sections(tmp_path: Path) -> None:
    from orchestrator.sdlc.codegen import _paths_from
    from orchestrator.sdlc.scope import READ_HEADING

    for rel in ("src/a.py", "src/b.py", "src/c.py"):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text("X = 1\n", encoding="utf-8")
    design = (
        "## Files to edit\n- src/a.py\n\n"
        f"## {READ_HEADING}\n- src/b.py\n\n"
        "## Blast radius\n- `src/c.py` — imported by 3 module(s)\n"
    )

    assert _paths_from({}, design, tmp_path) == ["src/a.py"]


def test_read_files_are_shown_as_reference_not_as_files_to_change(tmp_path: Path) -> None:
    from orchestrator.sdlc.codegen import _named_existing_files
    from orchestrator.sdlc.scope import READ_HEADING

    (tmp_path / "src").mkdir()
    (tmp_path / "src/b.py").write_text("def helper() -> int:\n    return 1\n", encoding="utf-8")

    block = _named_existing_files({}, tmp_path, f"## {READ_HEADING}\n- src/b.py\n")

    assert "REFERENCE FILES" in block and "do NOT modify" in block
    assert "EXISTING FILES THE SPEC NAMES" not in block


# --- F3: the scope guard ----------------------------------------------------------------


def test_the_scope_is_confident_only_when_the_ticket_stated_it_or_creates_code() -> None:
    stated = EditScope.from_design({"files_to_touch": ["src/a.py"], "files_origin": "stated"}, {})
    guessed = EditScope.from_design({"files_to_touch": ["src/a.py"], "files_origin": "landing"}, {})
    created = EditScope.from_design({"files_to_touch": [], "files_origin": "stated"}, {"kind": "create"})

    assert stated.confident and not guessed.confident and created.confident


def test_tests_and_a_new_packages_init_are_always_in_scope() -> None:
    scope = EditScope(files=("src/a.py",), confident=True)

    assert scope.allows("src/a.py")
    assert scope.allows("tests/sdlc/test_a.py")
    assert scope.allows("src/pkg/__init__.py", new_dirs=frozenset({"src/pkg"}))
    assert not scope.allows("src/pkg/__init__.py")
    assert not scope.allows("src/other.py")


def _tree(tmp_path: Path) -> Path:
    (tmp_path / "src/pkg").mkdir(parents=True)
    (tmp_path / "src/pkg/__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "src/pkg/verifiers.py").write_text("X = 1\n", encoding="utf-8")
    (tmp_path / "src/unrelated.py").write_text("Y = 2\n", encoding="utf-8")
    return tmp_path


def test_a_confident_scope_refuses_an_out_of_scope_edit_and_keeps_the_new_module(tmp_path: Path) -> None:
    from orchestrator.sdlc.codegen import apply_files

    root = _tree(tmp_path)
    change = apply_files(
        [
            {"path": "src/pkg/summary.py", "content": "def summarise() -> str:\n    return ''\n"},
            {"path": "src/pkg/__init__.py", "content": "from .summary import summarise\n"},
            {"path": "src/unrelated.py", "edits": [{"find": "Y = 2", "replace": "Y = 2  # not this ticket"}]},
        ],
        root,
        written_tracker={},
        grounded=False,
        scope=EditScope(create=True, confident=True),
    )

    assert (root / "src/unrelated.py").read_text() == "Y = 2\n"  # never written
    assert (root / "src/pkg/summary.py").exists()
    assert "summarise" in (root / "src/pkg/__init__.py").read_text()  # the new module's package
    assert "outside this ticket's scope" in change.summary


def test_a_change_that_only_comments_other_files_asks_for_one_corrective_retry(tmp_path: Path) -> None:
    from orchestrator.sdlc.codegen import CodegenError, apply_files

    root = _tree(tmp_path)
    with pytest.raises(CodegenError) as exc:
        apply_files(
            [{"path": "src/unrelated.py", "edits": [{"find": "Y = 2", "replace": "Y = 2  # see summary"}]}],
            root,
            written_tracker={},
            grounded=False,
            scope=EditScope(files=("src/pkg/verifiers.py",), confident=True),
        )

    assert "outside this ticket's scope" in str(exc.value)
    assert exc.value.empty_summary  # routes to the corrective retry
    assert (root / "src/unrelated.py").read_text() == "Y = 2\n"


def test_an_edit_tickets_named_file_is_always_writable(tmp_path: Path) -> None:
    from orchestrator.sdlc.codegen import apply_files

    root = _tree(tmp_path)
    apply_files(
        [{"path": "src/pkg/verifiers.py", "edits": [{"find": "X = 1", "replace": "X = 2"}]}],
        root,
        written_tracker={},
        grounded=False,
        scope=EditScope(files=("src/pkg/verifiers.py",), confident=True),
    )

    assert (root / "src/pkg/verifiers.py").read_text() == "X = 2\n"


def test_a_guessed_scope_reports_but_never_refuses(tmp_path: Path) -> None:
    """A scope built from word matches may be wrong; refusing on it could revert a correct fix."""
    from orchestrator.sdlc.codegen import apply_files

    root = _tree(tmp_path)
    change = apply_files(
        [{"path": "src/unrelated.py", "edits": [{"find": "Y = 2", "replace": "Y = 3"}]}],
        root,
        written_tracker={},
        grounded=False,
        scope=EditScope(files=("src/pkg/verifiers.py",), confident=False),
    )

    assert (root / "src/unrelated.py").read_text() == "Y = 3\n"
    assert "outside the design's edit list: src/unrelated.py" in change.summary


def test_no_scope_changes_nothing(tmp_path: Path) -> None:
    from orchestrator.sdlc.codegen import apply_files

    root = _tree(tmp_path)
    change = apply_files(
        [{"path": "src/unrelated.py", "edits": [{"find": "Y = 2", "replace": "Y = 3"}]}],
        root,
        written_tracker={},
        grounded=False,
    )

    assert (root / "src/unrelated.py").read_text() == "Y = 3\n"
    assert "scope" not in change.summary and "edit list" not in change.summary


# --- review pass 1 ---------------------------------------------------------------------


def test_a_real_code_change_outside_a_confident_scope_is_applied_and_reported(tmp_path: Path) -> None:
    """Wiring a change genuinely needs (registering a command, an export) must never be dropped:
    refusing it ships half a change (review S1/S2). Only no-code noise is refused."""
    from orchestrator.sdlc.codegen import apply_files

    root = _tree(tmp_path)
    change = apply_files(
        [{"path": "src/unrelated.py", "edits": [{"find": "Y = 2", "replace": "Y = 3"}]}],
        root,
        written_tracker={},
        grounded=False,
        scope=EditScope(files=("src/pkg/verifiers.py",), confident=True),
    )

    assert (root / "src/unrelated.py").read_text() == "Y = 3\n"
    assert "outside the design's edit list: src/unrelated.py" in change.summary


def test_a_docstring_only_edit_outside_scope_is_refused(tmp_path: Path) -> None:
    from orchestrator.sdlc.codegen import apply_files

    root = _tree(tmp_path)
    (root / "src/unrelated.py").write_text("def f() -> int:\n    return 1\n", encoding="utf-8")
    change = apply_files(
        [
            {"path": "src/pkg/summary.py", "content": "X = 1\n"},
            {
                "path": "src/unrelated.py",
                "edits": [
                    {"find": "def f() -> int:\n", "replace": 'def f() -> int:\n    """Not the summary."""\n'}
                ],
            },
        ],
        root,
        written_tracker={},
        grounded=False,
        scope=EditScope(create=True, confident=True),
    )

    assert '"""' not in (root / "src/unrelated.py").read_text()
    assert "not applied: src/unrelated.py" in change.summary


def test_a_non_python_edit_is_never_judged_as_no_code(tmp_path: Path) -> None:
    from orchestrator.sdlc.codegen import _changes_no_code

    assert _changes_no_code("a.py", "X = 1\n", "X = 1  # c\n")
    assert _changes_no_code("a.py", "def f():\n    pass\n", 'def f():\n    """d"""\n    pass\n')
    assert not _changes_no_code("a.py", "X = 1\n", "X = 2\n")
    assert not _changes_no_code("a.ts", "const x = 1;\n", "const x = 1; // c\n")


def test_test_files_in_every_front_ends_layout_are_in_scope() -> None:
    scope = EditScope(confident=True)
    for rel in (
        "src/test/java/FooTest.java",
        "pkg/foo_test.go",
        "web/src/foo.test.ts",
        "web/src/__tests__/a.ts",
        "App.Tests/FooTests.cs",
        "tests/x.py",
        "src/pkg/test_x.py",
    ):
        assert scope.allows(rel), rel
    assert not scope.allows("src/pkg/latest.py")


def test_scope_paths_are_compared_normalised() -> None:
    scope = EditScope.from_design({"files_to_touch": ["./src/a.py"], "files_origin": "stated"}, {})

    assert scope.allows("src/a.py")


def test_the_model_is_told_the_scope_up_front_only_when_it_is_enforced() -> None:
    from orchestrator.sdlc.codegen import _scope_block

    assert "SCOPE:" in _scope_block(EditScope(files=("src/a.py",), confident=True))
    assert _scope_block(EditScope(files=("src/a.py",), confident=False)) == ""
    assert _scope_block(None) == ""


async def test_a_create_design_rendered_for_codegen_keeps_its_reuse_module_read_only(tmp_path: Path) -> None:
    """Review B2: the Approach sentence named the reuse module and put it back in the "change
    these" block. End to end: produce → render → what codegen shows."""
    from orchestrator.sdlc.codegen import _named_existing_files, _paths_from
    from orchestrator.sdlc.design import produce_design, render_design_md

    rel = "src/orchestrator/codereview/verifiers.py"
    (tmp_path / rel).parent.mkdir(parents=True)
    (tmp_path / rel).write_text("def worst_severity() -> int:\n    return 1\n", encoding="utf-8")
    design = await produce_design(
        {**_CREATE, "kind": "create"}, overview=None, store=_graph(*_MODULES), llm=None
    )
    md = render_design_md(_CREATE, design)

    assert _paths_from(_CREATE, md, tmp_path) == []
    block = _named_existing_files(_CREATE, tmp_path, md)
    assert "REFERENCE FILES" in block and "verifiers.py" in block
    assert "EXISTING FILES THE SPEC NAMES" not in block


def test_a_module_the_graph_lacks_is_not_its_parent_package() -> None:
    from orchestrator.sdlc.design import _stated_modules

    spec = {"summary": "Add a new module orchestrator.codereview.summary_line for PR comments."}
    assert _stated_modules(spec, _graph(*_MODULES)) == []


def test_a_dotted_name_after_a_package_is_not_swallowed_as_its_word() -> None:
    from orchestrator.sdlc.design import _stated_modules

    spec = {"summary": "see orchestrator.pkg orchestrator.codereview.verifiers for both"}
    assert _stated_modules(spec, _graph(*_MODULES)) == [
        "src/orchestrator/pkg/__init__.py",
        "src/orchestrator/codereview/verifiers.py",
    ]


def test_a_non_python_package_is_not_resolved_to_an_arbitrary_file() -> None:
    from orchestrator.sdlc.design import _stated_modules

    spec = {"summary": "com.acme.orders placeOrder must validate the address"}
    assert _stated_modules(spec, _graph(*_MODULES)) == []


async def test_implement_resets_its_scope_even_when_generation_fails(tmp_path: Path) -> None:
    from orchestrator.sdlc.codegen import CodegenError, LLMCodegenAdapter

    adapter = LLMCodegenAdapter(object(), edit_scope=EditScope(files=("src/a.py",), confident=True))  # type: ignore[arg-type]
    seen: list[Any] = []

    async def _boom(*_a: Any, **_k: Any) -> Any:
        seen.append(adapter._implement_scope)
        raise CodegenError("model output had no 'files' list")

    adapter._generate = _boom  # type: ignore[method-assign]
    with pytest.raises(CodegenError):
        await adapter.implement(spec={"title": "t"}, path=str(tmp_path), issue_key="K-1")

    assert seen and seen[0] is not None  # enforced during implement
    assert adapter._implement_scope is None  # and never leaks into author_tests/refine


def test_a_re_exported_class_resolves_to_its_package_not_a_parent() -> None:
    """P4 regression: `orchestrator.core.llm.TokenLedger` is re-exported, not defined, by the
    package; walking past it to `orchestrator.core` sent the new module to the wrong package."""
    from orchestrator.sdlc.design import _stated_modules

    graph = _graph(*_MODULES, ("orchestrator.core", "src/orchestrator/core/__init__.py"))
    spec = {"summary": "renders the token ledger (orchestrator.core.llm.TokenLedger) as Markdown"}
    assert _stated_modules(spec, graph) == ["src/orchestrator/core/llm/__init__.py"]
