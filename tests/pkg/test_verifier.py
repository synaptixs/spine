"""GroundingVerifier v0: SHACL conformance + stale-fact detection."""

from __future__ import annotations

import re
from pathlib import Path

from orchestrator.pkg import (
    Edge,
    EdgeKind,
    FactBatch,
    GroundingVerifier,
    Node,
    NodeKind,
    Provenance,
    RepoCodeExtractor,
    facts_to_graph,
)

NS = "https://ontology.example.com/enterprise/"

# A shape over the round-trip vocabulary: every Function must carry a name.
SHAPES = """\
@prefix sh: <http://www.w3.org/ns/shacl#> .
@prefix : <https://ontology.example.com/enterprise/> .
@prefix shapes: <https://ontology.example.com/shapes/enterprise/> .

shapes:FunctionShape a sh:NodeShape ;
  sh:targetClass :Function ;
  sh:property [
    sh:path :hasName ;
    sh:minCount 1 ;
    sh:message "Function must have a name." ;
  ] .
"""


def _batch() -> FactBatch:
    b = FactBatch()
    b.add_node(Node("py:m", NodeKind.MODULE, "m", "python", Provenance("m.py", 1)))
    b.add_node(Node("py:m.f", NodeKind.FUNCTION, "f", "python", Provenance("m.py", 1, 2)))
    b.add_edge(Edge("py:m", "py:m.f", EdgeKind.CONTAINS, Provenance("m.py", 1)))
    return b


# ---- RDF materialisation ----------------------------------------------------


def test_facts_to_graph_emits_individuals_and_edges() -> None:
    g = facts_to_graph(_batch())
    turtle = g.serialize(format="turtle")
    assert "a :Function" in turtle and "a :Module" in turtle
    assert ':hasName "f"' in turtle
    assert ":contains" in turtle
    assert ':hasFile "m.py"' in turtle


# ---- SHACL ------------------------------------------------------------------


def test_shacl_conforming_batch_yields_no_findings(tmp_path: Path) -> None:
    shapes = tmp_path / "shapes.ttl"
    shapes.write_text(SHAPES, encoding="utf-8")
    verifier = GroundingVerifier(_batch(), shapes_path=shapes)
    assert verifier.shacl_findings() == []


def test_shacl_violation_maps_to_provenance(tmp_path: Path) -> None:
    shapes = tmp_path / "shapes.ttl"
    # Stricter shape the batch can't satisfy: Functions must carry an endLine.
    shapes.write_text(
        SHAPES.replace(":hasName ;", ":endLine ;").replace("must have a name", "must have an endLine"),
        encoding="utf-8",
    )
    batch = FactBatch()
    batch.add_node(Node("py:m.g", NodeKind.FUNCTION, "g", "python", Provenance("m.py", 7)))  # no end_line
    verifier = GroundingVerifier(batch, shapes_path=shapes)

    (finding,) = verifier.shacl_findings()
    assert finding.rule == "shacl_violation"
    assert "endLine" in finding.message
    assert (finding.file, finding.line, finding.symbol_id) == ("m.py", 7, "py:m.g")


def test_no_shapes_path_means_no_shacl_findings() -> None:
    assert GroundingVerifier(_batch()).shacl_findings() == []


# ---- staleness --------------------------------------------------------------


def _extracted_repo(tmp_path: Path) -> tuple[Path, FactBatch]:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "m.py").write_text("def f():\n    return 1\n\n\ndef g():\n    return 2\n", encoding="utf-8")
    return repo, RepoCodeExtractor().extract(repo)


def test_fresh_source_has_no_stale_findings(tmp_path: Path) -> None:
    repo, batch = _extracted_repo(tmp_path)
    assert GroundingVerifier(batch).stale_findings(repo) == []


def test_removed_symbol_is_flagged_stale(tmp_path: Path) -> None:
    repo, batch = _extracted_repo(tmp_path)
    (repo / "m.py").write_text("def f():\n    return 1\n", encoding="utf-8")  # g() deleted

    findings = GroundingVerifier(batch).stale_findings(repo, ["m.py"])
    assert [f.symbol_id for f in findings] == ["py:m.g"]
    assert findings[0].rule == "stale_fact" and "no longer defines" in findings[0].message


def test_deleted_file_makes_all_its_facts_stale(tmp_path: Path) -> None:
    repo, batch = _extracted_repo(tmp_path)
    (repo / "m.py").unlink()
    stale = {f.symbol_id for f in GroundingVerifier(batch).stale_findings(repo)}
    assert {"py:m", "py:m.f", "py:m.g"} <= stale


# ---- documentation drift (phase 3) ------------------------------------------


def test_doc_findings_flag_stale_symbol_claims(tmp_path: Path) -> None:
    repo, batch = _extracted_repo(tmp_path)
    (repo / "README.md").write_text(
        "`f` is the entry point; the old `deleted_helper` is gone. See `docs/x.md`.\n",
        encoding="utf-8",
    )
    findings = GroundingVerifier(batch).doc_findings(repo)
    mentions = {m.group(1) for f in findings if (m := re.search(r"references `([^`]+)`", f.message))}
    assert "deleted_helper" in mentions  # doc claims a symbol the graph lacks
    assert "f" not in mentions  # a real symbol doesn't drift
    assert "docs/x.md" not in mentions  # a path is filtered by symbolish_drift
    assert all(f.rule == "doc_drift" and f.file == "README.md" for f in findings)


def test_doc_findings_empty_without_docs(tmp_path: Path) -> None:
    repo, batch = _extracted_repo(tmp_path)
    assert GroundingVerifier(batch).doc_findings(repo) == []
