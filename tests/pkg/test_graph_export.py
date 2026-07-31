"""Whole-graph exports: complete, valid, and byte-identical on re-export.

The byte-equality tests are the point of this file. Every exit criterion in the G5 spec
claims "identical commit → identical bytes", and that only stays true if something checks
it — unstable ``set``/``dict`` iteration is the usual way it silently stops being true.

``ET.parse`` calls below carry ``noqa: S314``. The rule guards against XML-attack payloads in
*untrusted* input; here the input is a file this test wrote moments earlier from an in-process
fixture, so there is no untrusted data and no reason to add ``defusedxml`` as a dependency.
Parsing with a real XML parser is the whole point — it is what proves the writer emits a
well-formed document, which asserting on strings would not.
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from orchestrator.pkg.facts import Edge, EdgeKind, FactBatch, Node, NodeKind, Provenance
from orchestrator.pkg.graph_export import (
    GRAPH_FORMATS,
    WRITERS,
    export_dot,
    export_graphml,
    export_json,
)


def _batch() -> FactBatch:
    """A small graph with the awkward cases: XML-hostile names, a dangling edge, no provenance."""
    b = FactBatch()
    b.add_node(Node(id="py:app.mod", kind=NodeKind.MODULE, name="app.mod", language="python"))
    b.add_node(
        Node(
            id="py:app.mod.parse",
            kind=NodeKind.FUNCTION,
            name="parse",
            language="python",
            provenance=Provenance(file="app/mod.py", line=10, end_line=20),
        )
    )
    # A name that must be escaped in XML and quoted in DOT.
    b.add_node(
        Node(
            id='cpp:Widget<T&>::run("x")',
            kind=NodeKind.FUNCTION,
            name='run("x") & <T>',
            language="cpp",
            provenance=Provenance(file="src/w.cpp", line=3),
        )
    )
    b.add_node(Node(id="py:external.thing", kind=NodeKind.TYPE, name="thing", external=True))
    b.add_edge(Edge(src="py:app.mod", dst="py:app.mod.parse", kind=EdgeKind.CONTAINS))
    b.add_edge(
        Edge(
            src="py:app.mod.parse",
            dst='cpp:Widget<T&>::run("x")',
            kind=EdgeKind.CALLS,
            provenance=Provenance(file="app/mod.py", line=12),
        )
    )
    # Dangling: no node declares this id.
    b.add_edge(Edge(src="py:app.mod.parse", dst="py:nowhere.at.all", kind=EdgeKind.CALLS))
    return b


@pytest.mark.parametrize("fmt", GRAPH_FORMATS)
def test_reexport_is_byte_identical(fmt: str, tmp_path: Path) -> None:
    """The property the spec sells: same facts in, same bytes out. Diffable, reviewable."""
    a, c = tmp_path / f"a.{fmt}", tmp_path / f"c.{fmt}"
    WRITERS[fmt](_batch(), a)
    WRITERS[fmt](_batch(), c)
    assert a.read_bytes() == c.read_bytes()


@pytest.mark.parametrize("fmt", GRAPH_FORMATS)
def test_export_is_complete_not_truncated(fmt: str, tmp_path: Path) -> None:
    """Exports carry every node and every edge — unlike the bounded *visual* surfaces."""
    counts = WRITERS[fmt](_batch(), tmp_path / f"g.{fmt}")
    assert counts["nodes"] == 4
    assert counts["edges"] == 3


def test_graphml_is_well_formed_and_declares_every_edge_endpoint(tmp_path: Path) -> None:
    """Strict readers reject an edge whose endpoint isn't declared, so the dangling id
    must appear as a node rather than being dropped with the edge."""
    path = tmp_path / "g.graphml"
    counts = export_graphml(_batch(), path)
    root = ET.parse(path).getroot()  # noqa: S314 — self-written file; raises on malformed XML

    ns = "{http://graphml.graphdrawing.org/xmlns}"
    declared = {n.get("id") for n in root.iter(f"{ns}node")}
    endpoints = {e.get("source") for e in root.iter(f"{ns}edge")} | {
        e.get("target") for e in root.iter(f"{ns}edge")
    }
    assert endpoints <= declared, "an edge references a node the file never declares"
    assert "py:nowhere.at.all" in declared
    assert counts["dangling"] == 1


def test_graphml_escapes_xml_hostile_names(tmp_path: Path) -> None:
    """`Widget<T&>::run("x")` must survive as data, not break the document."""
    path = tmp_path / "g.graphml"
    export_graphml(_batch(), path)
    raw = path.read_text(encoding="utf-8")
    assert "<T&>" not in raw, "raw ampersand/angle bracket leaked into the XML"
    parsed = ET.parse(path).getroot()  # noqa: S314 — self-written file
    names = {d.text for d in parsed.iter("{http://graphml.graphdrawing.org/xmlns}data")}
    assert 'run("x") & <T>' in names


def test_dangling_placeholder_carries_no_kind(tmp_path: Path) -> None:
    """We know the id and nothing else. Inventing a NodeKind for it would extend the
    universal vocabulary for a renderer's convenience — what invariant #1 forbids."""
    path = tmp_path / "g.json"
    export_json(_batch(), path)
    doc = json.loads(path.read_text(encoding="utf-8"))
    placeholder = next(n for n in doc["nodes"] if n["id"] == "py:nowhere.at.all")
    assert placeholder["dangling"] is True
    assert "kind" not in placeholder


def test_json_carries_edges_unlike_pkg_extract_json(tmp_path: Path) -> None:
    """`pkg extract --json` omits edges; a graph projection without edges isn't a graph."""
    path = tmp_path / "g.json"
    export_json(_batch(), path)
    doc = json.loads(path.read_text(encoding="utf-8"))
    assert doc["summary"]["edges"] == 3
    kinds = {e["kind"] for e in doc["edges"]}
    assert kinds == {"CONTAINS", "CALLS"}
    contains = next(e for e in doc["edges"] if e["kind"] == "CONTAINS")
    assert (contains["src"], contains["dst"]) == ("py:app.mod", "py:app.mod.parse")


def test_json_records_provenance_and_groundedness(tmp_path: Path) -> None:
    path = tmp_path / "g.json"
    export_json(_batch(), path)
    doc = json.loads(path.read_text(encoding="utf-8"))
    parse = next(n for n in doc["nodes"] if n["id"] == "py:app.mod.parse")
    assert parse["provenance"] == {"file": "app/mod.py", "line": 10, "end_line": 20}
    assert parse["grounded"] is True
    external = next(n for n in doc["nodes"] if n["id"] == "py:external.thing")
    assert external["grounded"] is False


def test_dot_quotes_ids_and_declares_endpoints(tmp_path: Path) -> None:
    path = tmp_path / "g.dot"
    export_dot(_batch(), path)
    raw = path.read_text(encoding="utf-8")
    assert raw.startswith("digraph pkg {")
    assert raw.rstrip().endswith("}")
    # The quote inside the C++ id must be backslash-escaped, not terminate the DOT string.
    assert r"cpp:Widget<T&>::run(\"x\")" in raw
    assert '"py:nowhere.at.all" [label="py:nowhere.at.all", dangling="true"];' in raw


def test_empty_graph_still_writes_a_valid_file(tmp_path: Path) -> None:
    """A repo with no extractable facts must produce a readable empty export, not a crash."""
    for fmt in GRAPH_FORMATS:
        path = tmp_path / f"empty.{fmt}"
        counts = WRITERS[fmt](FactBatch(), path)
        assert counts == {"nodes": 0, "edges": 0, "dangling": 0}
        assert path.read_text(encoding="utf-8").strip()
    ET.parse(tmp_path / "empty.graphml")  # noqa: S314 — self-written file
    assert json.loads((tmp_path / "empty.json").read_text(encoding="utf-8"))["nodes"] == []
