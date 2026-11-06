"""P4 of the KG track: the nine "is this a test?" copies are gone; every surface reads one answer.

Graph consumers read the fact the extractor stored (``Node.test``); code that asks about a path
that is not a graph node (a worktree's changed files, a pull request's diff) calls the one rule,
``orchestrator.pkg.test_rule.is_test_path``. Each test below drives a consumer through its own
entry point, so it pins what the user sees, not which helper was called:

* B123 (SSPN-290): coverage's *name* half made flask's public ``Flask.test_client`` a test, so
  what it calls read "exercised". Only a language that marks tests inside the file (Rust's
  ``#[test]``) may make a product-path symbol a test, and it does so through the fact.
* B124 (SSPN-291): the knowledge base's module rule and ``state``'s path rule missed
  ``__tests__/``, ``.spec.`` and ``.test.``; ``state``'s "Largest type" table and size buckets
  counted test types at all.
* B139 (SSPN-330): the build document read ``intake.specs`` and ``sdlc.own_tests`` as tests and
  claimed a change was "contained".

Nodes are built with ``test=`` set the way ``extractor.mark_tests`` sets it, because that is the
only place the fact comes from. A node whose ``test`` disagrees with its path shows the consumer
reads the fact and does not re-derive it from the path (invariant 1).
"""

from __future__ import annotations

import ast
import importlib
from pathlib import Path

import pytest

from orchestrator.catalog.profile import ProjectProfile
from orchestrator.codereview.github_client import ChangedFile, PRDiff
from orchestrator.codereview.verifiers import SecretsVerifier, Severity, StyleVerifier
from orchestrator.knowledge import renderers
from orchestrator.knowledge.current_state import compute_current_state
from orchestrator.pkg import FactStore, test_rule
from orchestrator.pkg.facts import Edge, EdgeKind, FactBatch, Node, NodeKind, Provenance
from orchestrator.pkg.retrieval import GroundedRetriever
from orchestrator.sdlc import builddoc, impact
from orchestrator.sdlc.coverage import CoverageIndex, build_regression_plan
from orchestrator.sdlc.test_location import locate_tests

_SRC = Path(__file__).resolve().parents[2] / "src" / "orchestrator"


def _fn(nid: str, name: str, file: str, *, test: bool = False, language: str = "python") -> Node:
    return Node(nid, NodeKind.FUNCTION, name, language, Provenance(file, 1), test=test)


def _calls(batch: FactBatch, src: Node, dst: Node) -> None:
    assert src.provenance is not None
    batch.add_edge(Edge(src.id, dst.id, EdgeKind.CALLS, src.provenance))


# ---- the property: one rule, nowhere copied ----------------------------------------------------

#: Function names the nine copies went by. A new one with any of these names is a tenth copy.
_COPY_NAMES = {
    "is_test_node",
    "_is_test_path",
    "_is_test_module",
    "_is_test_file",
    "_looks_like_a_test",
    "_under_tests",
}


def _defined_functions() -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for path in sorted(_SRC.rglob("*.py")):
        rel = path.relative_to(_SRC).as_posix()
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                found.append((rel, node.name))
    return found


def test_no_module_defines_its_own_test_rule() -> None:
    defined = _defined_functions()
    copies = sorted(f"{rel}:{name}" for rel, name in defined if name in _COPY_NAMES)
    rules = sorted(f"{rel}:{name}" for rel, name in defined if name == "is_test_path")

    assert copies == []
    assert rules == ["pkg/test_rule.py:is_test_path"]


@pytest.mark.parametrize(
    "module",
    [
        "orchestrator.sdlc.testpaths",
        "orchestrator.sdlc.own_tests",
        "orchestrator.sdlc.feature_runner",
        "orchestrator.sdlc.codegen",
        "orchestrator.sdlc.test_location",
        "orchestrator.codereview.verifiers",
    ],
)
def test_every_path_consumer_asks_the_one_rule(module: str) -> None:
    assert getattr(importlib.import_module(module), "is_test_path", None) is test_rule.is_test_path


# ---- coverage (B123) ------------------------------------------------------------------------------


def _flask_like() -> tuple[FactStore, Node]:
    """`Flask.test_client` is public API in `src/flask/app.py`; it is the only caller of `_make`."""
    batch = FactBatch()
    client = _fn("py:flask.app.Flask.test_client", "test_client", "src/flask/app.py")
    make = _fn("py:flask.testing._make", "_make", "src/flask/testing.py")
    for n in (client, make):
        batch.add_node(n)
    _calls(batch, client, make)
    return FactStore(batch), make


def test_a_product_function_named_test_star_does_not_cover_what_it_calls() -> None:
    store, make = _flask_like()

    plan = build_regression_plan(store, make.id)

    assert not plan.target_covered
    assert plan.covering_tests == []
    assert not CoverageIndex(store).is_covered(make.id)


def test_a_product_function_named_test_star_is_in_the_blast_radius() -> None:
    """The other half of B123: as a "test" it was dropped from what a change reaches."""
    store, make = _flask_like()

    reach = [n.name for n in CoverageIndex(store).blast_radius(make.id)]

    assert reach == ["test_client"]


def _rust(*, marked: bool, name: str) -> tuple[FactStore, Node]:
    batch = FactBatch()
    parse = _fn("rust:crate.parse", "parse", "src/lib.rs", language="rust")
    test = _fn(f"rust:crate.tests.{name}", name, "src/lib.rs", test=marked, language="rust")
    for n in (parse, test):
        batch.add_node(n)
    _calls(batch, test, parse)
    return FactStore(batch), parse


@pytest.mark.parametrize("name", ["parses_empty_input", "test_parse"])
def test_a_rust_test_marked_in_the_file_covers_what_it_calls(name: str) -> None:
    store, parse = _rust(marked=True, name=name)

    assert build_regression_plan(store, parse.id).target_covered
    assert CoverageIndex(store).is_covered(parse.id)


def test_a_rust_function_named_test_star_without_the_marker_is_product_code() -> None:
    store, parse = _rust(marked=False, name="test_parse")

    assert not build_regression_plan(store, parse.id).target_covered


# ---- the knowledge base (B124) ------------------------------------------------------------------


def _ts_module(name: str, file: str, *, test: bool) -> Node:
    return Node(f"ts:{name}", NodeKind.MODULE, name, "typescript", Provenance(file, 1), test=test)


def test_a_typescript_test_module_gets_no_module_page() -> None:
    batch = FactBatch()
    for name, file, test in (
        ("src/cart", "src/cart.ts", False),
        ("test/unit/cart.test", "test/unit/cart.test.ts", True),
        ("src/__tests__/cart.spec", "src/__tests__/cart.spec.ts", True),
    ):
        mod = _ts_module(name, file, test=test)
        fn = _fn(f"{mod.id}.f", "f", file, test=test, language="typescript")
        batch.add_node(mod)
        batch.add_node(fn)
        batch.add_edge(Edge(mod.id, fn.id, EdgeKind.CONTAINS, fn.provenance))

    pages = [m.name for m in renderers.select_module_pages(FactStore(batch))]

    assert pages == ["src/cart"]


def test_a_rust_test_marked_in_the_file_is_a_test_call_site() -> None:
    store, parse = _rust(marked=True, name="parses_empty_input")

    assert renderers.Importance(store).caller_split(parse.id) == "1 test call-site"


_PROFILE = ProjectProfile(
    languages=frozenset({"typescript"}),
    framework=None,
    has_db=False,
    has_migrations=False,
    test_runner=None,
    task_type="feature",
)


def _state_batch() -> FactBatch:
    """A product module, a jest test beside it that imports it, a Go-style test type, a test `main`."""
    batch = FactBatch()
    cart = _ts_module("shop/cart", "shop/cart.ts", test=False)
    spec = _ts_module("shop/__tests__/cart.spec", "shop/__tests__/cart.spec.ts", test=True)
    add = _fn("ts:shop/cart.add", "add", "shop/cart.ts", language="typescript")
    big = Node("ts:shop/cart.Cart", NodeKind.TYPE, "Cart", "typescript", Provenance("shop/cart.ts", 1))
    fixture = Node(
        "ts:shop/__tests__/cart.spec.Fixture",
        NodeKind.TYPE,
        "Fixture",
        "typescript",
        Provenance("shop/__tests__/cart.spec.ts", 1),
        test=True,
    )
    main = _fn("ts:shop/main.spec.main", "main", "shop/main.spec.ts", test=True, language="typescript")
    for n in (cart, spec, add, big, fixture, main):
        batch.add_node(n)
    batch.add_edge(Edge(cart.id, add.id, EdgeKind.CONTAINS, add.provenance))
    batch.add_edge(Edge(cart.id, big.id, EdgeKind.CONTAINS, big.provenance))
    batch.add_edge(Edge(spec.id, fixture.id, EdgeKind.CONTAINS, fixture.provenance))
    for i in range(3):
        m = _fn(f"{big.id}.m{i}", f"m{i}", "shop/cart.ts", language="typescript")
        batch.add_node(m)
        batch.add_edge(Edge(big.id, m.id, EdgeKind.CONTAINS, m.provenance))
    for i in range(30):  # a fat fixture: bigger than any product type
        f = Node(f"{fixture.id}.f{i}", NodeKind.FIELD, f"f{i}", "typescript", fixture.provenance, test=True)
        batch.add_node(f)
        batch.add_edge(Edge(fixture.id, f.id, EdgeKind.CONTAINS, f.provenance))
    batch.add_edge(Edge(spec.id, add.id, EdgeKind.IMPORTS, spec.provenance))
    return batch


def test_state_counts_an_area_a_jest_test_imports_as_tested() -> None:
    state = compute_current_state(_state_batch(), _PROFILE)

    assert state.tested_areas == 1


def test_state_leaves_test_types_out_of_the_largest_types_and_the_size_buckets() -> None:
    state = compute_current_state(_state_batch(), _PROFILE)

    assert [name for name, _, _ in state.hotspots] == ["Cart"]
    assert sum(state.size_dist.values()) == 1


def test_state_does_not_offer_a_test_main_as_an_entry_point() -> None:
    state = compute_current_state(_state_batch(), _PROFILE)

    assert not any("main.spec" in ep for ep in state.entry_points)


# ---- the build document (B139) ------------------------------------------------------------------


def _py_module(dotted: str, *, test: bool) -> Node:
    file = "src/" + dotted.replace(".", "/") + ".py"
    return Node(f"py:{dotted}", NodeKind.MODULE, dotted, "python", Provenance(file, 1), test=test)


def _containment(importers: list[tuple[str, bool]]) -> str:
    batch = FactBatch()
    target = _py_module("orchestrator.sdlc.coverage", test=False)
    fn = _fn("py:orchestrator.sdlc.coverage.plan", "plan", "src/orchestrator/sdlc/coverage.py")
    batch.add_node(target)
    batch.add_node(fn)
    batch.add_edge(Edge(target.id, fn.id, EdgeKind.CONTAINS, fn.provenance))
    for dotted, test in importers:
        mod = _py_module(dotted, test=test)
        batch.add_node(mod)
        batch.add_edge(Edge(mod.id, target.id, EdgeKind.IMPORTS, mod.provenance))
    br = impact.blast_radius(FactStore(batch), ["src/orchestrator/sdlc/coverage.py"])
    prose = builddoc._blast_prose(impact.to_dict(br))
    return next(line for line in prose.splitlines() if line.startswith("**Containment:**"))


@pytest.mark.parametrize("dotted", ["orchestrator.intake.specs", "orchestrator.sdlc.own_tests"])
def test_a_product_module_named_like_a_test_is_not_called_a_test_importer(dotted: str) -> None:
    line = _containment([(dotted, False)])

    assert "only importers are tests" not in line
    assert dotted in line


def test_a_change_only_tests_import_is_contained() -> None:
    line = _containment([("tests.sdlc.test_coverage", True)])

    assert "the only importers are tests (tests.sdlc.test_coverage)" in line


def test_a_design_written_before_the_fact_claims_no_containment() -> None:
    """A `design.json` saved by an older Spine carries importer names and no test flags. Reading
    every importer as product is the safe direction: it never claims "contained"."""
    bd = {
        "call_graph_available": True,
        "modules": [{"module": "m", "where": "m.py", "importers": 1, "importer_names": ["tests.test_m"]}],
    }

    containment = next(line for line in builddoc._blast_prose(bd).splitlines() if "Containment" in line)

    assert "only importers are tests" not in containment


def test_the_regression_list_names_test_importers_by_the_fact(tmp_path: Path) -> None:
    batch = FactBatch()
    target = _py_module("orchestrator.sdlc.coverage", test=False)
    batch.add_node(target)
    for dotted, test in (("orchestrator.intake.specs", False), ("cart.spec", True)):
        mod = _py_module(dotted, test=test)
        batch.add_node(mod)
        batch.add_edge(Edge(mod.id, target.id, EdgeKind.IMPORTS, mod.provenance))

    evidence = builddoc.collect_evidence(
        FactStore(batch), files=["src/orchestrator/sdlc/coverage.py"], root=tmp_path
    )

    assert evidence["regression"] == ["cart.spec"]


# ---- the ranker -----------------------------------------------------------------------------------


def test_the_ranker_leaves_out_a_jest_spec_file() -> None:
    batch = FactBatch()
    for nid, file, test in (
        ("ts:src/cart.checkout", "src/cart.ts", False),
        ("ts:src/cart.spec.checkout", "src/cart.spec.ts", True),
    ):
        batch.add_node(_fn(nid, "checkout", file, test=test, language="typescript"))

    files = [s.node.id for s in GroundedRetriever(FactStore(batch)).scored_symbols("checkout")]

    assert files == ["ts:src/cart.checkout"]


# ---- the path consumers ---------------------------------------------------------------------------


def _diff(filename: str, line: str) -> PRDiff:
    patch = f"@@ -0,0 +1,1 @@\n+{line}"
    return PRDiff("a/b", 1, "sha", (ChangedFile(filename, "modified", 1, 0, patch),))


def test_review_flags_a_print_in_a_product_file_whose_name_contains_test() -> None:
    findings = StyleVerifier().scan(_diff("src/latest.py", 'print("x")'))

    assert [f.rule for f in findings] == ["stray_print"]


def test_review_blocks_a_secret_in_a_product_file_whose_name_contains_test() -> None:
    findings = SecretsVerifier().scan(_diff("src/attestation/client.py", 'token = "s3cr3tvalue99"'))

    assert [(f.rule, f.severity) for f in findings] == [("generic_secret", Severity.BLOCKER)]


def test_review_still_softens_a_secret_in_a_jest_spec() -> None:
    findings = SecretsVerifier().scan(_diff("src/__tests__/client.spec.ts", 'token = "s3cr3tvalue99"'))

    assert [(f.rule, f.severity) for f in findings] == [("generic_secret", Severity.WARNING)]


def test_codegen_does_not_count_a_jest_spec_as_testable_source() -> None:
    from orchestrator.sdlc.codegen import _has_testable_source

    assert not _has_testable_source([Path("src/cart.spec.ts")])
    assert _has_testable_source([Path("src/cart.ts")])


def test_test_location_finds_a_perl_test_named_for_the_module(tmp_path: Path) -> None:
    (tmp_path / "lib" / "Shop").mkdir(parents=True)
    (tmp_path / "lib" / "Shop" / "Cart.pm").write_text("package Shop::Cart; 1;\n")
    (tmp_path / "t").mkdir()
    (tmp_path / "t" / "cart.t").write_text("use Test::More;\n")

    placements = locate_tests(tmp_path, ["lib/Shop/Cart.pm"])

    assert [(p.directory, p.reason, p.example) for p in placements] == [("t", "name", "t/cart.t")]


def test_test_location_never_offers_conftest_as_the_example(tmp_path: Path) -> None:
    """`conftest.py` is test code by the rule, but a fixture file is not one to imitate."""
    (tmp_path / "pkg" / "tests").mkdir(parents=True)
    (tmp_path / "pkg" / "cart.py").write_text("x = 1\n")
    for name in ("conftest.py", "test_other.py"):
        (tmp_path / "pkg" / "tests" / name).write_text("\n")

    placements = locate_tests(tmp_path, ["pkg/cart.py"])

    assert [(p.directory, p.example) for p in placements] == [("pkg/tests", "pkg/tests/test_other.py")]


def test_codegen_reads_a_written_file_relative_to_its_checkout(tmp_path: Path) -> None:
    """The session tracks absolute paths; a checkout under a `tests/` folder is not all tests."""
    from orchestrator.sdlc.codegen import _has_testable_source

    root = tmp_path / "tests" / "MyApp.Tests" / "repo"
    (root / "src").mkdir(parents=True)
    written = root / "src" / "cart.py"
    written.write_text("x = 1\n")

    assert _has_testable_source([written], root=root)
    assert not _has_testable_source([root / "tests" / "test_cart.py"], root=root)
