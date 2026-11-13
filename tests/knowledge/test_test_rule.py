"""B122, then the KG track's P4: the knowledge base tells test code from product code by the graph's fact.

`renderers._under_tests` knew only `test/` and `tests/` directories, so a Go `_test.go` file,
a `__tests__/` folder or a `*.spec.ts` beside the code read as production: test helpers were
counted as public symbols, ranked as hubs and listed in the domain model, and a caller split
said "(2)" where it meant "1 production · 1 test". B122 pointed it at coverage's path rule; P4
deleted both and has every page read `Node.test`, which extraction sets once from the one path
rule (`pkg.test_rule`). Path only: a name rule would make flask's public `Flask.test_client` a
test and drop it from the knowledge base.

Nodes here go through `mark_tests`, the extractor's own post-pass, so they carry the fact the
way a real graph does.
"""

from __future__ import annotations

import pytest

from orchestrator.knowledge import renderers
from orchestrator.pkg.extractor import mark_tests
from orchestrator.pkg.facts import Edge, EdgeKind, FactBatch, Node, NodeKind, Provenance
from orchestrator.pkg.store import FactStore
from orchestrator.pkg.test_rule import is_test_path


def _marked(node: Node) -> Node:
    batch = FactBatch()
    batch.add_node(node)
    return mark_tests(batch).nodes[0]


def _node(name: str, file: str, kind: NodeKind = NodeKind.FUNCTION, language: str = "go") -> Node:
    return _marked(Node(f"x:{file}:{name}", kind, name, language, Provenance(file, 1)))


def _under_tests(node: Node) -> bool:
    """What every page now reads."""
    return node.test


# ---- the rule itself (T1–T4) ------------------------------------------------


@pytest.mark.parametrize(
    "file",
    [
        "binding/binding_test.go",  # Go keeps tests beside the code
        "gin_test.go",
        "packages/runtime-core/__tests__/hydration.spec.ts",  # jest/vitest folder
        "src/calls.test.ts",  # a test file beside the code
        "src/calls.spec.js",
    ],
)
def test_a_test_file_outside_a_test_directory_is_test_code(file: str) -> None:
    assert _under_tests(_node("helper", file))


@pytest.mark.parametrize("file", ["tests/test_x.py", "test/unit/calls.test.ts", "pkg/tests/foo.go"])
def test_a_test_directory_is_still_test_code(file: str) -> None:
    assert _under_tests(_node("helper", file))


@pytest.mark.parametrize("file", ["src/app/y.py", "gin.go", "packages/runtime-core/src/renderer.ts"])
def test_product_code_is_not_test_code(file: str) -> None:
    assert not _under_tests(_node("run", file))


def test_a_node_without_provenance_is_not_test_code() -> None:
    assert not _under_tests(_marked(Node("x:y", NodeKind.FUNCTION, "y", "python")))


def test_a_product_function_named_like_a_test_stays_product_code() -> None:
    """flask's `Flask.test_client` is public API. The name rule would hide it (D2)."""
    assert not _under_tests(_node("test_client", "src/flask/app.py", language="python"))
    assert not _under_tests(_node("SpeedTest", "src/bench.py", kind=NodeKind.TYPE, language="python"))


# ---- one rule, the fact (T5) ---------------------------------------------------


@pytest.mark.parametrize(
    "file",
    [
        "tests/test_x.py",
        "binding/binding_test.go",
        "src/__tests__/thing.ts",
        "src/a.spec.ts",
        "t/10-cart.t",
        "src/app/y.py",
        "gin.go",
        "pkg\\binding\\binding_test.go",  # a Windows-style path
        "src\\tests\\foo.py",
        "pkg\\__tests__\\a.ts",
        "test\\unit\\calls.ts",
    ],
)
def test_the_fact_is_the_one_path_rule(file: str) -> None:
    assert _node("plain", file).test is is_test_path(file)


# ---- what the pages say (T6, T7) --------------------------------------------


def _gin_like() -> FactStore:
    """`New` is product code with one product caller and one Go test caller beside it."""
    batch = FactBatch()
    new, default = _node("New", "gin.go"), _node("Default", "gin.go")
    test_new = _node("TestNew", "gin_test.go")
    mock = _node("mockWriter", "gin_test.go", kind=NodeKind.TYPE)
    module = _marked(Node("go:<root>", NodeKind.MODULE, "<root>", "go", Provenance("gin.go", 1)))
    for n in (module, new, default, test_new, mock):
        batch.add_node(n)
    for n in (new, default, test_new, mock):
        batch.add_edge(Edge(module.id, n.id, EdgeKind.CONTAINS, n.provenance))
    batch.add_edge(Edge(default.id, new.id, EdgeKind.CALLS, Provenance("gin.go", 9)))
    batch.add_edge(Edge(test_new.id, new.id, EdgeKind.CALLS, Provenance("gin_test.go", 5)))
    return FactStore(batch)


def test_a_go_test_caller_counts_as_a_test_call_site_and_gets_no_vote() -> None:
    store = _gin_like()
    new = next(n for n in store.nodes if n.name == "New")
    imp = renderers.Importance(store)

    assert imp.caller_split(new.id) == "1 production · 1 test call-sites"
    assert (imp.prod_callers[new.id], imp.test_callers[new.id]) == (1, 1)
    # Test call-sites are worth nothing in the ranking (Finding 6): only `Default` votes.
    assert imp.score(new.id) == 2


def test_the_symbol_index_leaves_out_symbols_from_a_go_test_file() -> None:
    page = renderers.render_symbol_index(_gin_like(), page_of={})

    assert "`New`" in page and "`Default`" in page
    assert "TestNew" not in page
    assert "mockWriter" not in page


def test_the_symbol_index_keeps_a_product_function_named_like_a_test() -> None:
    batch = FactBatch()
    module = Node("py:flask.app", NodeKind.MODULE, "flask.app", "python", Provenance("src/flask/app.py", 1))
    client = Node(
        "py:flask.app.Flask.test_client",
        NodeKind.FUNCTION,
        "test_client",
        "python",
        Provenance("src/flask/app.py", 669),
    )
    batch.add_node(module)
    batch.add_node(client)
    batch.add_edge(Edge(module.id, client.id, EdgeKind.CONTAINS, client.provenance))

    assert "`test_client`" in renderers.render_symbol_index(FactStore(batch), page_of={})


# ---- the conventions page's Tests block (review finding, P4) ------------------


def _go_packages() -> FactStore:
    """Two Go packages, each with product and test files.

    A Go package is one module node, labelled with the first file the walk reached. In `ctx/`
    that is `afterfunc_test.go` (it sorts before `context.go`, as in Go's own `context/`); in
    `svc/` it is the product file `svc.go`.
    """
    batch = FactBatch()
    for pkg, first in (("ctx", "ctx/afterfunc_test.go"), ("svc", "svc/svc.go")):
        batch.add_node(_marked(Node(f"go:{pkg}", NodeKind.MODULE, pkg, "go", Provenance(first, 1))))
    for name, file in (
        ("AfterFunc", "ctx/context.go"),
        ("TestAfterFunc", "ctx/afterfunc_test.go"),
        ("Serve", "svc/svc.go"),
        ("TestServe", "svc/svc_test.go"),
        ("TestRoot", "root_test.go"),  # gin keeps most of its tests at the root
    ):
        batch.add_node(_node(name, file))
    return FactStore(batch)


def test_the_tests_block_counts_test_files_not_the_packages_they_sit_in() -> None:
    block = renderers._test_layout_block(_go_packages())

    assert "**3 test files**" in block
    assert "`ctx/` (1)" in block and "`svc/` (1)" in block and "`./` (1)" in block


def test_the_tests_block_still_counts_python_test_files() -> None:
    batch = FactBatch()
    for mod in ("test_a", "test_b"):
        batch.add_node(
            _marked(
                Node(
                    f"py:tests.{mod}",
                    NodeKind.MODULE,
                    f"tests.{mod}",
                    "python",
                    Provenance(f"tests/{mod}.py", 1),
                )
            )
        )
    batch.add_node(Node("py:app", NodeKind.MODULE, "app", "python", Provenance("src/app.py", 1)))

    block = renderers._test_layout_block(FactStore(batch))

    assert "**2 test files**, named `test_*.py` (100%)" in block
    assert "`tests/` (2)" in block


def test_a_production_module_named_test_star_reads_as_test_code() -> None:
    """The trade-off D3 accepted, pinned: the coverage path rule already reads it this way."""
    assert _under_tests(_node("_covering", "src/orchestrator/sdlc/test_location.py", language="python"))


def test_the_tests_block_ignores_a_file_known_only_from_external_symbols() -> None:
    batch = FactBatch()
    batch.add_node(
        Node(
            "py:pytest.fixture",
            NodeKind.FUNCTION,
            "fixture",
            "python",
            Provenance("tests/conftest.py", 3),
            external=True,
        )
    )

    assert "No test files found" in renderers._test_layout_block(FactStore(batch))


def test_the_tests_block_does_not_count_documentation_under_a_test_folder() -> None:
    """`link_docs` runs before the page renders, so fixture READMEs are grounded Doc nodes."""
    batch = FactBatch()
    batch.add_node(
        _marked(
            Node(
                "py:tests.test_core",
                NodeKind.MODULE,
                "tests.test_core",
                "python",
                Provenance("tests/test_core.py", 1),
            )
        )
    )
    for doc in ("tests/fixtures/README.md", "tests/fixtures/expected.txt"):
        batch.add_node(Node(f"doc:{doc}", NodeKind.DOC, doc, "markdown", Provenance(doc, 1)))

    block = renderers._test_layout_block(FactStore(batch))

    assert "**1 test files**, named `test_*.py` (100%)" in block
    assert "fixtures" not in block
