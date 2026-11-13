"""The KG track's fix-forward (P7, SSPN-358): the graph-side defects the post-merge review found.

* **B143** — a package Module (one Module per Go directory, JVM package, C# namespace) took its
  ``test`` fact from whichever member file sorted first, so ``store/cache_test.go`` made the whole
  ``go:store`` package test code and the product package left the knowledge base (D40). The same
  first-file provenance made ``FactStore.owning_module``'s file step miss a node in any other file.
* **B144** — the JVM ``*Spec`` clause read product classes (``V1PodSpec``, the JDK's
  ``PKCS8EncodedKeySpec``) as tests (D41).
* **B145** — Rust ``#[cfg(test)] mod clock`` beside ``#[cfg(not(test))] mod clock`` share an id, and
  the product twin read as test (D42).
* **B147** — the MCP server's in-memory graph kept a walk that could not read part of the tree (D47).
* **B148** — a walk rooted at an unreadable *sub*directory raised instead of yielding nothing (D45).
"""

from __future__ import annotations

import os
import stat
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest

from orchestrator.pkg.extractor import RepoCodeExtractor
from orchestrator.pkg.facts import Edge, EdgeKind, FactBatch, Node, NodeKind, Provenance
from orchestrator.pkg.store import FactStore
from orchestrator.pkg.test_rule import is_test_path

_root_reads_everything = pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0, reason="root reads a chmod-000 directory anyway"
)


def _write(root: Path, files: dict[str, str]) -> Path:
    for rel, body in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    return root


def _owner(batch: FactBatch, node_id: str) -> Node:
    owner = FactStore(batch).owning_module(node_id)
    assert owner is not None, f"{node_id} has no owning module"
    return owner


def _named(batch: FactBatch, kind: NodeKind, name: str) -> Node:
    hits = [n for n in batch.nodes if n.kind is kind and n.name == name and not n.external]
    assert hits, f"no {kind.value} named {name}: {sorted(n.id for n in batch.nodes)}"
    return hits[0]


# ---- B143: a package Module is test only when every member file is ------------------------------

#: One package whose first file by name is a test, beside a product file. ``grammar`` is the
#: tree-sitter module the language needs; ``product`` names a product symbol in the package.
_MIXED_PACKAGES = {
    "go": (
        "tree_sitter_go",
        {
            "go.mod": "module example.com/m\n\ngo 1.21\n",
            "store/cache_test.go": 'package store\n\nimport "testing"\n\nfunc TestGet(t *testing.T) {}\n',
            "store/store.go": "package store\n\nfunc Get() int { return 1 }\n",
        },
        (NodeKind.FUNCTION, "Get"),
    ),
    "java-androidTest": (
        "tree_sitter_java",
        {
            "app/src/androidTest/java/com/acme/web/ControllerTest.java": (
                "package com.acme.web;\nclass ControllerTest { void t() {} }\n"
            ),
            "app/src/main/java/com/acme/web/Controller.java": (
                "package com.acme.web;\npublic class Controller { public int get() { return 1; } }\n"
            ),
        },
        (NodeKind.TYPE, "Controller"),
    ),
    "kotlin-androidTest": (
        "tree_sitter_kotlin",
        {
            "app/src/androidTest/kotlin/com/acme/web/ControllerTest.kt": (
                "package com.acme.web\n\nclass ControllerTest { fun t() {} }\n"
            ),
            "app/src/main/kotlin/com/acme/web/Controller.kt": (
                "package com.acme.web\n\nclass Controller { fun get(): Int = 1 }\n"
            ),
        },
        (NodeKind.TYPE, "Controller"),
    ),
    "csharp": (
        "tree_sitter_c_sharp",
        {
            # The test project's folder sorts first, and both files declare `namespace Shop`.
            "Api.Tests/CartTests.cs": (
                "namespace Shop {\n  public class CartTests { public void T() {} }\n}\n"
            ),
            "Shop/Cart.cs": "namespace Shop {\n  public class Cart { public int Total() { return 1; } }\n}\n",
        },
        (NodeKind.TYPE, "Cart"),
    ),
}


@pytest.mark.parametrize("case", sorted(_MIXED_PACKAGES))
def test_a_package_holding_product_code_is_never_test_code(case: str, tmp_path: Path) -> None:
    grammar, files, (kind, name) = _MIXED_PACKAGES[case]
    pytest.importorskip(grammar, reason=f"install the extra that provides {grammar}")
    batch = RepoCodeExtractor().extract(_write(tmp_path, files))

    product = _named(batch, kind, name)
    assert not product.test
    owner = _owner(batch, product.id)
    assert not owner.test, f"{owner.id} (file {owner.provenance}) holds product code {product.id}"


def test_a_go_package_of_only_test_files_is_still_test_code(tmp_path: Path) -> None:
    pytest.importorskip("tree_sitter_go", reason="install the 'go' extra")
    batch = RepoCodeExtractor().extract(
        _write(
            tmp_path,
            {
                "go.mod": "module example.com/m\n\ngo 1.21\n",
                "store/store.go": "package store\n\nfunc Get() int { return 1 }\n",
                "e2e/a_test.go": 'package e2e\n\nimport "testing"\n\nfunc TestA(t *testing.T) {}\n',
                "e2e/b_test.go": 'package e2e\n\nimport "testing"\n\nfunc TestB(t *testing.T) {}\n',
            },
        )
    )
    assert _owner(batch, _named(batch, NodeKind.FUNCTION, "TestA").id).test
    assert not _owner(batch, _named(batch, NodeKind.FUNCTION, "Get").id).test


def test_a_python_test_module_stays_test_code_and_a_product_one_product(tmp_path: Path) -> None:
    batch = RepoCodeExtractor().extract(
        _write(
            tmp_path,
            {
                "app/cart.py": "def total():\n    return 1\n",
                "tests/test_cart.py": "def test_total():\n    assert True\n",
            },
        )
    )
    modules = {n.id: n.test for n in batch.nodes if n.kind is NodeKind.MODULE and not n.external}
    assert modules["py:app.cart"] is False
    assert modules["py:tests.test_cart"] is True


def _package_batch() -> FactBatch:
    """A package Module whose first file is ``a.go``, a member in ``b.go``, and an Endpoint in
    ``b.go`` with no ``CONTAINS`` parent — the shape route front-ends emit."""
    batch = FactBatch()
    mod = Node("go:store", NodeKind.MODULE, "store", "go", Provenance("store/a.go", 1))
    batch.add_node(mod)
    for nid, name, file in (("go:store.A", "A", "store/a.go"), ("go:store.B", "B", "store/b.go")):
        batch.add_node(Node(nid, NodeKind.FUNCTION, name, "go", Provenance(file, 3)))
        batch.add_edge(Edge(mod.id, nid, EdgeKind.CONTAINS, Provenance(file, 3)))
    batch.add_node(Node("go:route:GET /b", NodeKind.ENDPOINT, "GET /b", "go", Provenance("store/b.go", 9)))
    return batch


def test_a_node_with_no_parent_belongs_to_the_package_module_of_any_member_file() -> None:
    owner = FactStore(_package_batch()).owning_module("go:route:GET /b")
    assert owner is not None and owner.id == "go:store"


def test_a_file_with_its_own_module_keeps_it_over_a_package_member_index() -> None:
    """A file a package spans *and* that declares a Module of its own keeps its own (P5's order)."""
    batch = _package_batch()
    batch.add_node(Node("go:other", NodeKind.MODULE, "other", "go", Provenance("store/b.go", 1)))
    owner = FactStore(batch).owning_module("go:route:GET /b")
    assert owner is not None and owner.id == "go:other"


# ---- B144: `Spec` is not a test suffix anywhere ----------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "kubernetes/client-java-api/src/main/java/io/kubernetes/client/openapi/models/V1PodSpec.java",
        "src/java.base/share/classes/java/security/spec/PKCS8EncodedKeySpec.java",
        "src/main/java/org/springframework/cloud/gateway/route/builder/GatewayFilterSpec.java",
        "src/main/kotlin/shop/CartSpec.kt",
    ],
)
def test_a_jvm_spec_class_outside_a_test_folder_is_product_code(path: str) -> None:
    assert is_test_path(path) is False


@pytest.mark.parametrize(
    "path",
    [
        "src/test/kotlin/shop/CartSpec.kt",  # Kotest
        "src/test/groovy/shop/CartSpec.groovy",  # Spock
        "src/commonTest/kotlin/shop/CartSpec.kt",  # Kotlin Multiplatform
        "src/jvmTest/kotlin/shop/CartSpec.kt",
    ],
)
def test_a_jvm_spec_class_in_a_test_folder_is_still_a_test(path: str) -> None:
    assert is_test_path(path) is True


# ---- B145: Rust `cfg` twins ------------------------------------------------------------------------


def _rust(tmp_path: Path, lib: str) -> FactBatch:
    pytest.importorskip("tree_sitter_rust_orchard", reason="install the 'rust' extra")
    return RepoCodeExtractor().extract(
        _write(
            tmp_path,
            {
                "Cargo.toml": '[package]\nname = "demo"\nversion = "0.1.0"\nedition = "2021"\n',
                "src/lib.rs": lib,
            },
        )
    )


def test_a_cfg_twin_module_reads_as_product(tmp_path: Path) -> None:
    batch = _rust(
        tmp_path,
        "#[cfg(not(test))]\nmod clock {\n    pub fn now() -> u64 { 42 }\n}\n\n"
        "#[cfg(test)]\nmod clock {\n    pub fn now() -> u64 { 0 }\n}\n\n"
        "pub fn stamp() -> u64 { clock::now() }\n",
    )
    tested = sorted(n.id for n in batch.nodes if n.test)
    assert tested == [], f"product code read as test: {tested}"


def test_a_lone_cfg_test_module_is_still_test_code(tmp_path: Path) -> None:
    batch = _rust(
        tmp_path,
        "pub fn stamp() -> u64 { 1 }\n\n"
        "#[cfg(test)]\nmod tests {\n    #[test]\n    fn stamps() { assert_eq!(super::stamp(), 1); }\n}\n",
    )
    tested = sorted(n.name for n in batch.nodes if n.test)
    assert "stamps" in tested and "tests" in tested
    assert "stamp" not in tested


def test_a_cfg_twin_does_not_unmark_a_test_function_inside_a_lone_test_module(tmp_path: Path) -> None:
    batch = _rust(
        tmp_path,
        "#[cfg(not(test))]\nmod clock {\n    pub fn now() -> u64 { 42 }\n}\n\n"
        "#[cfg(test)]\nmod clock {\n    pub fn now() -> u64 { 0 }\n}\n\n"
        "#[cfg(test)]\nmod tests {\n    #[test]\n    fn ticks() {}\n}\n",
    )
    tested = sorted(n.name for n in batch.nodes if n.test)
    assert tested == ["tests", "ticks"]


# ---- B147: the MCP memo never keeps a partial walk ---------------------------------------------------


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


@pytest.fixture
def _memo() -> Iterator[None]:
    from orchestrator.plugin import graph_memo

    graph_memo.clear()
    yield
    graph_memo.clear()


@pytest.mark.usefixtures("_memo")
def test_the_memo_does_not_keep_a_graph_whose_walk_could_not_read_part_of_the_tree(tmp_path: Path) -> None:
    from orchestrator.pkg.walk_rules import WalkHealth
    from orchestrator.plugin import graph_memo

    root = _write(tmp_path / "repo", {"pkg/a.py": "def a():\n    return 1\n"})
    _git(root, "init", "-q")
    _git(root, "-c", "user.email=x@y", "-c", "user.name=x", "add", "-A")
    _git(root, "-c", "user.email=x@y", "-c", "user.name=x", "commit", "-qm", "init")
    assert graph_memo.tree_key(root) is not None

    partial = FactBatch()
    partial.walk_health = WalkHealth(unreadable=("locked",))
    graph_memo.load(root, with_unbound=False, loader=lambda: (partial, None, None))
    assert graph_memo.kept_batch(root) is None

    whole = FactBatch()
    whole.walk_health = WalkHealth()
    graph_memo.load(root, with_unbound=False, loader=lambda: (whole, None, None))
    assert graph_memo.kept_batch(root) is not None


# ---- B148: an unreadable walk root ---------------------------------------------------------------------


@pytest.fixture
def _locked_tests(tmp_path: Path) -> Iterator[Path]:
    root = _write(
        tmp_path / "repo", {"src/app.py": "x = 1\n", "tests/test_app.py": "def test_x(mocker): ...\n"}
    )
    (root / "tests").chmod(0)
    try:
        yield root
    finally:
        (root / "tests").chmod(stat.S_IRWXU)


@_root_reads_everything
def test_a_walk_rooted_at_an_unreadable_subdirectory_yields_nothing_and_says_so(_locked_tests: Path) -> None:
    from orchestrator.pkg.walk_rules import WalkHealth, walk_repo

    health = WalkHealth()
    assert list(walk_repo(_locked_tests / "tests", health=health)) == []
    assert health.partial


@_root_reads_everything
def test_the_extractor_still_refuses_an_unreadable_root_and_names_it(_locked_tests: Path) -> None:
    from orchestrator.pkg.walk_rules import UnreadableRootError, walk_repo

    target = _locked_tests / "tests"
    with pytest.raises(UnreadableRootError) as raised:
        list(walk_repo(target, strict_root=True))
    assert str(target) in str(raised.value)
    assert "repository root" not in str(raised.value)
    with pytest.raises(UnreadableRootError):
        RepoCodeExtractor().extract(target)


@_root_reads_everything
def test_python_test_setup_survives_an_unreadable_tests_folder(_locked_tests: Path) -> None:
    from orchestrator.sdlc.testenv import _tests_use_mocker

    assert _tests_use_mocker(_locked_tests) is False


# ---- added after mutation pass 1 (each kills a survivor) ------------------------------------------


def test_a_module_never_counts_an_external_members_file() -> None:
    """M3: an ``external`` node is a placeholder, so its provenance is never one of the Module's files,
    even where the edge to it names no file of its own and the child's would be the fallback."""
    from orchestrator.pkg.extractor import mark_tests

    batch = FactBatch()
    batch.add_node(
        Node("py:tests.test_x", NodeKind.MODULE, "test_x", "python", Provenance("tests/test_x.py", 1))
    )
    batch.add_node(
        Node(
            "py:lib.thing", NodeKind.TYPE, "thing", "python", Provenance("src/lib/thing.py", 1), external=True
        )
    )
    batch.add_edge(Edge("py:tests.test_x", "py:lib.thing", EdgeKind.CONTAINS))
    marked = {n.id: n.test for n in mark_tests(batch).nodes}
    assert marked == {"py:tests.test_x": True, "py:lib.thing": False}


def test_an_edge_with_an_empty_file_falls_back_to_its_childs_file() -> None:
    """M28: a ``CONTAINS`` edge whose provenance names no file says nothing about where it was declared."""
    from orchestrator.pkg.extractor import mark_tests

    batch = FactBatch()
    batch.add_node(Node("go:e2e", NodeKind.MODULE, "e2e", "go", Provenance("e2e/a_test.go", 1)))
    batch.add_node(Node("go:e2e.TestB", NodeKind.FUNCTION, "TestB", "go", Provenance("e2e/b_test.go", 3)))
    batch.add_edge(Edge("go:e2e", "go:e2e.TestB", EdgeKind.CONTAINS, Provenance("", 0)))
    assert {n.id: n.test for n in mark_tests(batch).nodes}["go:e2e"] is True


def test_the_member_file_step_only_ever_answers_a_module() -> None:
    """M6: a Type that ``CONTAINS`` a method defined in another file is not that file's module."""
    batch = _package_batch()
    batch.add_node(Node("go:store.T", NodeKind.TYPE, "T", "go", Provenance("store/a.go", 5)))
    batch.add_edge(Edge("go:store", "go:store.T", EdgeKind.CONTAINS, Provenance("store/a.go", 5)))
    batch.add_node(Node("go:store.T.M", NodeKind.FUNCTION, "M", "go", Provenance("store/c.go", 2)))
    batch.add_edge(Edge("go:store.T", "go:store.T.M", EdgeKind.CONTAINS, Provenance("store/c.go", 2)))
    batch.add_node(Node("go:route:GET /c", NodeKind.ENDPOINT, "GET /c", "go", Provenance("store/c.go", 9)))
    assert FactStore(batch).owning_module("go:route:GET /c") is None


def test_one_extractor_reused_forgets_the_last_repositorys_twins(tmp_path: Path) -> None:
    """M10: the product scopes are per run, like the test scopes."""
    pytest.importorskip("tree_sitter_rust_orchard", reason="install the 'rust' extra")
    cargo = '[package]\nname = "demo"\nversion = "0.1.0"\nedition = "2021"\n'
    twins = _write(
        tmp_path / "a",
        {
            "Cargo.toml": cargo,
            "src/lib.rs": "#[cfg(not(test))]\nmod clock {\n    pub fn now() {}\n}\n"
            "#[cfg(test)]\nmod clock {\n    pub fn now() {}\n}\n",
        },
    )
    lone = _write(
        tmp_path / "b",
        {"Cargo.toml": cargo, "src/lib.rs": "#[cfg(test)]\nmod clock {\n    pub fn now() {}\n}\n"},
    )
    extractor = RepoCodeExtractor()
    extractor.extract(twins)
    tested = sorted(n.name for n in extractor.extract(lone).nodes if n.test)
    assert tested == ["clock", "now"]


def test_a_simple_base_nothing_declares_is_named_as_read(tmp_path: Path) -> None:
    """M26: ``implements Runnable`` in ``package app`` is ``java:Runnable``, not ``java:app.Runnable``."""
    pytest.importorskip("tree_sitter_java", reason="install the 'java' extra")
    batch = RepoCodeExtractor().extract(
        _write(
            tmp_path,
            {"src/main/java/app/Job.java": "package app;\npublic class Job implements Runnable {}\n"},
        )
    )
    bases = sorted(e.dst for e in batch.edges if e.kind is EdgeKind.IMPLEMENTS)
    assert bases == ["java:Runnable"]


def test_an_external_module_is_no_package_for_the_same_package_guess() -> None:
    """M25: only a module the repository declares makes ``java:<pkg>.Base`` a same-package guess."""
    from orchestrator.pkg.java_extractor import _external_bases

    batch = FactBatch()
    batch.add_node(Node("java:app", NodeKind.MODULE, "app", "java", external=True))
    batch.add_node(Node("java:app.Job", NodeKind.TYPE, "Job", "java", Provenance("Job.java", 2)))
    batch.add_edge(Edge("java:app", "java:app.Job", EdgeKind.CONTAINS, Provenance("Job.java", 2)))
    batch.add_edge(Edge("java:app.Job", "java:app.Runnable", EdgeKind.IMPLEMENTS, Provenance("Job.java", 2)))
    bases = sorted(e.dst for e in _external_bases(batch).edges if e.kind is EdgeKind.IMPLEMENTS)
    assert bases == ["java:app.Runnable"]


def test_a_test_module_stays_test_when_a_shared_id_child_points_at_a_header() -> None:
    """Found by P7's old-vs-new ``understand`` diff on fmt: C/C++ ids are global by name (B142), so a
    function a test file defines can carry the provenance of the product header that declares it.
    A Module spans the files its ``CONTAINS`` edges are declared in, not its children's files."""
    from orchestrator.pkg.extractor import mark_tests

    batch = FactBatch()
    batch.add_node(
        Node("cpp:test/base-test.cc", NodeKind.MODULE, "base-test", "cpp", Provenance("test/base-test.cc", 1))
    )
    batch.add_node(Node("cpp:copy", NodeKind.FUNCTION, "copy", "cpp", Provenance("include/fmt/base.h", 2081)))
    batch.add_edge(
        Edge("cpp:test/base-test.cc", "cpp:copy", EdgeKind.CONTAINS, Provenance("test/base-test.cc", 40))
    )
    marked = {n.id: n.test for n in mark_tests(batch).nodes}
    assert marked["cpp:test/base-test.cc"] is True
