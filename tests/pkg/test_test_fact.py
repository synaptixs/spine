"""`Node.test`: the test rule stored on the graph, once, at extraction (P3; D7, D16, D20; B138).

Nine surfaces used to re-derive "is this test code?" from paths and names at render time, and
gave nine answers. The graph now carries the answer on every code node it walked, so a consumer
reads a fact instead of keeping a tenth copy of the rule. These tests pin where the fact comes
from (the path rule, and Rust's in-file markers), that it survives every way a graph leaves the
process (cache, SQLite, RDF, `pkg extract --json`), and that a stale cache cannot pass it off as
`False`.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from typer.testing import CliRunner

from orchestrator.pkg import persistence
from orchestrator.pkg.accuracy import CorpusError, score_case
from orchestrator.pkg.export import export_sqlite
from orchestrator.pkg.extractor import RepoCodeExtractor, mark_tests
from orchestrator.pkg.facts import Edge, EdgeKind, FactBatch, Node, NodeKind, Provenance
from orchestrator.pkg.persistence import (
    FactCacheError,
    facts_from_dict,
    facts_to_dict,
    load_facts,
    save_facts,
)


def _write(root: Path, files: dict[str, str]) -> None:
    for rel, content in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def _python_repo(root: Path) -> None:
    _write(
        root,
        {
            "app/cart.py": "import os\n\ndef total(x):\n    return x\n",
            "app/latest.py": "def newest():\n    return 1\n",  # B138: not a test
            "app/client.py": "def test_client():\n    return 1\n",  # a product function named like a test
            "tests/test_cart.py": "import json\nfrom app.cart import total\n\ndef test_total():\n"
            "    assert total(1) == 1\n",
        },
    )


# ---- the field ----------------------------------------------------------------------------------


def test_a_node_is_product_code_unless_the_graph_says_otherwise() -> None:
    node = Node("py:a", NodeKind.MODULE, "a", "python")
    assert node.test is False
    assert Node("py:t", NodeKind.MODULE, "t", "python", test=True).test is True


# ---- set once, at extraction --------------------------------------------------------------------


def test_extraction_marks_code_in_test_files_and_nothing_else(tmp_path: Path) -> None:
    _python_repo(tmp_path)
    batch = RepoCodeExtractor().extract(tmp_path)
    by_id = {n.id: n for n in batch.nodes}

    assert by_id["py:tests.test_cart"].test
    assert by_id["py:tests.test_cart.test_total"].test
    assert not by_id["py:app.cart"].test
    assert not by_id["py:app.cart.total"].test
    assert not by_id["py:app.latest"].test  # B138
    assert not by_id["py:app.client.test_client"].test  # D12: a path rule, not a name rule


def test_an_external_node_is_never_test_code(tmp_path: Path) -> None:
    """`json` is imported from a test file; the placeholder for it is not the test's code."""
    _python_repo(tmp_path)
    batch = RepoCodeExtractor().extract(tmp_path)
    externals = [n for n in batch.nodes if n.external]
    assert externals, "the fixture imports two modules from outside the tree"
    assert not [n.id for n in externals if n.test]


def test_mark_tests_never_marks_an_external_node_and_keeps_the_batch_otherwise() -> None:
    """An external placeholder located in a test file, or named by a front-end, is still not test code."""
    batch = FactBatch()
    batch.add_node(Node("py:app", NodeKind.MODULE, "app", "python", Provenance("app.py", 1)))
    batch.add_node(Node("py:tests.t", NodeKind.MODULE, "t", "python", Provenance("tests/t.py", 1)))
    batch.add_node(
        Node("py:requests", NodeKind.MODULE, "requests", "python", Provenance("tests/t.py", 1), external=True)
    )
    batch.add_node(Node("rust:x::marked", NodeKind.FUNCTION, "marked", "rust", Provenance("src/lib.rs", 3)))
    batch.add_node(Node("rust:ext::marked", NodeKind.FUNCTION, "marked", "rust", external=True))
    batch.add_edge(Edge("py:tests.t", "py:app", EdgeKind.IMPORTS, Provenance("tests/t.py", 1)))

    marked = mark_tests(batch, {"rust:x::marked", "rust:ext::marked"})

    assert {n.id: n.test for n in marked.nodes} == {
        "py:app": False,
        "py:tests.t": True,  # the path rule
        "py:requests": False,  # external, though its provenance is a test file
        "rust:x::marked": True,  # a front-end mark
        "rust:ext::marked": False,  # external, though a front-end named it
    }
    assert [n.id for n in marked.nodes] == [n.id for n in batch.nodes]
    assert marked.edges == batch.edges


# ---- D16: Rust marks its tests in the file ------------------------------------------------------

_RUST_LIB = """\
pub fn total(x: u32) -> u32 {
    x
}

#[test]
fn total_is_identity() {
    let _ = total(1);
}

#[tokio::test]
async fn async_case() {}

#[cfg(test)]
mod tests {
    use super::*;

    fn helper() -> u32 {
        1
    }

    #[test]
    fn uses_helper() {
        let _ = total(helper());
    }
}

#[cfg(test)]
mod outlined;

#[cfg(not(test))]
mod real {
    pub fn run() {}
}

#[cfg(all(test, feature = "slow"))]
mod gated {
    fn slow() {}
}
"""


def test_rust_in_file_test_markers_set_the_fact(tmp_path: Path) -> None:
    pytest.importorskip("tree_sitter_rust_orchard", reason="install the 'rust' extra")
    _write(
        tmp_path,
        {
            "Cargo.toml": '[package]\nname="shop"\nversion="0.1.0"\nedition="2021"\n',
            "src/lib.rs": _RUST_LIB,
            "src/outlined.rs": "fn in_outlined() {}\n",
            "tests/api.rs": "fn it_works() {}\n",
        },
    )
    batch = RepoCodeExtractor().extract(tmp_path)
    test = {n.id: n.test for n in batch.nodes if not n.external}
    crate = "rust:shop@lib/shop"

    assert test[crate] is False
    assert test[f"{crate}::total"] is False
    assert test[f"{crate}::total_is_identity"] is True  # #[test]
    assert test[f"{crate}::async_case"] is True  # #[tokio::test]
    assert test[f"{crate}::tests"] is True  # #[cfg(test)] mod, inline
    assert test[f"{crate}::tests::helper"] is True  # everything inside it
    assert test[f"{crate}::tests::uses_helper"] is True
    assert test[f"{crate}::outlined"] is True  # #[cfg(test)] mod, in its own file
    assert test[f"{crate}::outlined::in_outlined"] is True
    assert test[f"{crate}::real"] is False  # #[cfg(not(test))] is the opposite
    assert test[f"{crate}::real::run"] is False
    assert test[f"{crate}::gated"] is False  # only a bare cfg(test) is read (D16)
    assert test["rust:shop@test/api::it_works"] is True  # the path half: `tests/`


# ---- cache format v5 ----------------------------------------------------------------------------


def _marked_batch() -> FactBatch:
    batch = FactBatch()
    batch.add_node(Node("py:app", NodeKind.MODULE, "app", "python", Provenance("app.py", 1)))
    batch.add_node(
        Node(
            "py:tests.test_app",
            NodeKind.MODULE,
            "test_app",
            "python",
            Provenance("tests/test_app.py", 1),
            test=True,
        )
    )
    batch.add_edge(Edge("py:tests.test_app", "py:app", EdgeKind.IMPORTS, Provenance("tests/test_app.py", 1)))
    return batch


def test_the_cache_keeps_the_fact(tmp_path: Path) -> None:
    path = tmp_path / "facts.json"
    save_facts(_marked_batch(), path)
    loaded = {n.id: n.test for n in load_facts(path).nodes}
    assert loaded == {"py:app": False, "py:tests.test_app": True}


def test_a_cache_written_before_the_fact_existed_is_refused_not_read_as_product(tmp_path: Path) -> None:
    """A v4 cache has no `test` key. Reading it would call every test product code, silently."""
    payload = facts_to_dict(_marked_batch())
    assert payload["version"] == 5
    payload["version"] = 4
    for node in payload["nodes"]:
        node.pop("test", None)
    with pytest.raises(FactCacheError):
        facts_from_dict(payload)
    stale = tmp_path / "old.json"
    stale.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(FactCacheError):
        load_facts(stale)


def test_load_or_extract_rebuilds_over_a_v4_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"
    _python_repo(repo)
    cache = tmp_path / "cache"
    monkeypatch.setattr(persistence, "repo_state", lambda root: ("a" * 40, False))
    first = persistence.load_or_extract(repo, cache_dir=cache)
    written = next(cache.rglob("*.json"))
    payload = json.loads(written.read_text(encoding="utf-8"))
    payload["version"] = 4
    for node in payload["nodes"]:
        node.pop("test", None)
    written.write_text(json.dumps(payload), encoding="utf-8")

    again = persistence.load_or_extract(repo, cache_dir=cache)
    assert {n.id: n.test for n in again.nodes} == {n.id: n.test for n in first.nodes}
    assert {n.id for n in again.nodes if n.test}  # the rebuilt graph carries the fact


# ---- the projections ----------------------------------------------------------------------------


def test_the_sqlite_export_has_a_test_column(tmp_path: Path) -> None:
    db = tmp_path / "facts.db"
    export_sqlite(_marked_batch(), db)
    conn = sqlite3.connect(db)
    try:
        rows = dict(conn.execute("SELECT id, test FROM modules").fetchall())
    finally:
        conn.close()
    assert rows == {"py:app": 0, "py:tests.test_app": 1}


def test_the_rdf_graph_has_a_test_property() -> None:
    from rdflib import Literal, Namespace
    from rdflib.namespace import XSD

    from orchestrator.pkg.rdf import DEFAULT_NAMESPACE, facts_to_graph, symbol_iri

    ns = Namespace(DEFAULT_NAMESPACE)
    graph = facts_to_graph(_marked_batch())
    for node_id, value in (("py:app", 0), ("py:tests.test_app", 1)):
        assert (symbol_iri(ns, node_id), ns["hasTest"], Literal(value, datatype=XSD.integer)) in graph


def test_pkg_extract_json_says_which_nodes_are_tests(tmp_path: Path) -> None:
    from orchestrator.cli import app

    _python_repo(tmp_path)
    result = CliRunner().invoke(app, ["pkg", "extract", str(tmp_path), "--json"])
    assert result.exit_code == 0, result.output
    nodes = {n["id"]: n["test"] for n in json.loads(result.output)["nodes"]}
    assert nodes["py:tests.test_cart.test_total"] is True
    assert nodes["py:app.cart.total"] is False


# ---- the accuracy corpus can label the fact -----------------------------------------------------


def _case(tmp_path: Path, *, labelled_test: bool) -> Path:
    case = tmp_path / "case"
    _write(case / ".repo", {"tests/test_a.py": "def test_a():\n    pass\n"})
    spec = {
        "language": "python",
        "case": "labelled_test",
        "root": ".repo",
        "why": "a node label may state the test fact",
        "nodes": [
            {"id": "py:tests.test_a", "kind": "Module"},
            {"id": "py:tests.test_a.test_a", "kind": "Function", "test": labelled_test},
        ],
        "edges": [{"src": "py:tests.test_a", "dst": "py:tests.test_a.test_a", "kind": "CONTAINS"}],
    }
    (case / "expected.json").write_text(json.dumps(spec), encoding="utf-8")
    return case


def test_a_corpus_label_that_states_the_fact_is_checked(tmp_path: Path) -> None:
    report = score_case(_case(tmp_path, labelled_test=True))
    assert report.test_checked == 1


def test_a_corpus_label_the_extractor_contradicts_fails_the_case(tmp_path: Path) -> None:
    with pytest.raises(CorpusError, match="py:tests.test_a.test_a"):
        score_case(_case(tmp_path, labelled_test=False))
