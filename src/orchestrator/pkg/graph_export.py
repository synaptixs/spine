"""Whole-graph projections for other people's tools — GraphML, DOT, JSON.

The visualization gap was never really about our own renderer. A user who wants to explore
the graph in Gephi, yEd, Obsidian or Cytoscape could not, because the only projection was
the kind-per-table SQLite in :mod:`pkg.export`. These three writers close that: hand the
facts to tooling that already does layout, filtering and search far better than we intend to.

Two properties every writer here holds to, both of them load-bearing:

**Complete, never bounded.** Unlike the *visual* surfaces (see ``build_overview``'s
``truncated{}``), an export emits every node and every edge. The whole point of handing the
graph to Gephi is that Gephi filters; a silently truncated GraphML is worse than none,
because the reader draws conclusions from a subset without knowing it is one. If a limit is
ever genuinely needed, fail loudly — do not stop early.

**Byte-identical for an identical commit.** Sorted at every boundary, by node id and by the
edge key, so ``git diff`` on a committed export shows real change and nothing else. Unstable
``set``/``dict`` iteration is the usual way this breaks; two committed episteme regressions
came from exactly that. :func:`tests.pkg.test_graph_export` asserts byte equality rather
than trusting the intention.

Dangling edges — an edge whose endpoint is not among the nodes — are *materialised* as
explicit placeholder nodes rather than dropped. A GraphML edge referencing an undeclared
node is invalid and strict readers reject the file, but silently dropping the edge would
lose a real fact. The placeholder is marked ``dangling`` so the reader can tell it apart
from a node we actually extracted, and the count is reported back to the caller.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape
from xml.sax.saxutils import quoteattr as xml_attr

from orchestrator.pkg.facts import Edge, FactBatch, Node

GRAPH_FORMATS = ("graphml", "dot", "json")
"""Formats this module writes. ``sqlite`` lives in :mod:`pkg.export` and is handled there."""


def _sorted_nodes(batch: FactBatch) -> list[Node]:
    """Nodes in a stable order — by id, which is unique and language-prefixed."""
    return sorted(batch.nodes, key=lambda n: n.id)


def _sorted_edges(batch: FactBatch) -> list[Edge]:
    """Edges in a stable order. ``Edge.key()`` includes provenance, so parallel edges
    between the same pair keep a deterministic relative order instead of dict order."""
    return sorted(batch.edges, key=lambda e: e.key())


def _dangling_ids(nodes: Iterable[Node], edges: Iterable[Edge]) -> list[str]:
    """Edge endpoints that no node declares, sorted.

    Emitted as placeholder entries carrying **no kind**. That is deliberate: ``NodeKind`` is a
    closed, task-driven vocabulary and inventing an ``UNKNOWN`` member to make an exporter's
    life easier would be extending the universal fact schema for a renderer's benefit, which
    invariant #1 exists to prevent. A placeholder with no kind is also the honest record — we
    have an id and nothing else.
    """
    known = {n.id for n in nodes}
    missing: set[str] = set()
    for e in edges:
        if e.src not in known:
            missing.add(e.src)
        if e.dst not in known:
            missing.add(e.dst)
    return sorted(missing)


def _prov(n: Node) -> tuple[str, str, str]:
    """(file, line, end_line) as strings — empty when the node isn't grounded."""
    p = n.provenance
    if p is None:
        return ("", "", "")
    return (p.file, str(p.line), "" if p.end_line is None else str(p.end_line))


_GRAPHML_NODE_KEYS = (
    ("n_kind", "kind"),
    ("n_name", "name"),
    ("n_language", "language"),
    ("n_grounded", "grounded"),
    ("n_dangling", "dangling"),
    ("n_file", "file"),
    ("n_line", "line"),
    ("n_end_line", "end_line"),
)
_GRAPHML_EDGE_KEYS = (
    ("e_kind", "kind"),
    ("e_file", "file"),
    ("e_line", "line"),
)


def export_graphml(batch: FactBatch, path: Path | str) -> dict[str, int]:
    """Write the whole graph as GraphML — the format Gephi, yEd and Cytoscape all read."""
    nodes = _sorted_nodes(batch)
    edges = _sorted_edges(batch)
    dangling = _dangling_ids(nodes, edges)

    out: list[str] = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<graphml xmlns="http://graphml.graphdrawing.org/xmlns">',
    ]
    for key_id, attr in _GRAPHML_NODE_KEYS:
        out.append(f'  <key id="{key_id}" for="node" attr.name="{attr}" attr.type="string"/>')
    for key_id, attr in _GRAPHML_EDGE_KEYS:
        out.append(f'  <key id="{key_id}" for="edge" attr.name="{attr}" attr.type="string"/>')
    out.append('  <graph id="pkg" edgedefault="directed">')

    for n in nodes:
        file, line, end_line = _prov(n)
        out.append(f"    <node id={xml_attr(n.id)}>")
        for key_id, value in (
            ("n_kind", n.kind.value),
            ("n_name", n.name),
            ("n_language", n.language),
            ("n_grounded", "true" if n.grounded else "false"),
            ("n_file", file),
            ("n_line", line),
            ("n_end_line", end_line),
        ):
            if value:
                out.append(f'      <data key="{key_id}">{xml_escape(value)}</data>')
        out.append("    </node>")

    for mid in dangling:
        out.append(f"    <node id={xml_attr(mid)}>")
        out.append(f'      <data key="n_name">{xml_escape(mid)}</data>')
        out.append('      <data key="n_dangling">true</data>')
        out.append("    </node>")

    for i, e in enumerate(edges):
        out.append(f'    <edge id="e{i}" source={xml_attr(e.src)} target={xml_attr(e.dst)}>')
        out.append(f'      <data key="e_kind">{xml_escape(e.kind.value)}</data>')
        if e.provenance is not None:
            out.append(f'      <data key="e_file">{xml_escape(e.provenance.file)}</data>')
            out.append(f'      <data key="e_line">{e.provenance.line}</data>')
        out.append("    </edge>")

    out.append("  </graph>")
    out.append("</graphml>")
    Path(path).write_text("\n".join(out) + "\n", encoding="utf-8")
    return {"nodes": len(nodes), "edges": len(edges), "dangling": len(dangling)}


def _dot_id(value: str) -> str:
    """A double-quoted DOT ID. Backslash and quote are the only escapes DOT needs."""
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def export_dot(batch: FactBatch, path: Path | str) -> dict[str, int]:
    """Write the whole graph as Graphviz DOT.

    Note DOT is a *layout* language and Graphviz will compute positions itself. That does not
    breach invariant #3 (deterministic, seeded layout in Python) — the invariant governs what
    *we* render and ship. What the user's own copy of Graphviz does with an exported file is
    theirs to decide, exactly as it is for Gephi.
    """
    nodes = _sorted_nodes(batch)
    edges = _sorted_edges(batch)
    dangling = _dangling_ids(nodes, edges)

    out: list[str] = ["digraph pkg {", "  graph [rankdir=LR];", "  node [shape=box];"]
    for n in nodes:
        file, line, _ = _prov(n)
        attrs = [f"label={_dot_id(n.name)}", f"kind={_dot_id(n.kind.value)}"]
        if n.language:
            attrs.append(f"language={_dot_id(n.language)}")
        if file:
            attrs.append(f"file={_dot_id(file)}")
            attrs.append(f"line={_dot_id(line)}")
        out.append(f"  {_dot_id(n.id)} [{', '.join(attrs)}];")
    for mid in dangling:
        out.append(f'  {_dot_id(mid)} [label={_dot_id(mid)}, dangling="true"];')
    for e in edges:
        out.append(f"  {_dot_id(e.src)} -> {_dot_id(e.dst)} [kind={_dot_id(e.kind.value)}];")
    out.append("}")
    Path(path).write_text("\n".join(out) + "\n", encoding="utf-8")
    return {"nodes": len(nodes), "edges": len(edges), "dangling": len(dangling)}


def export_json(batch: FactBatch, path: Path | str) -> dict[str, int]:
    """Write the whole graph as JSON — for scripts, and for tools that read node/edge lists.

    Unlike ``pkg extract --json``, which emits nodes and a summary only, this carries **edges**.
    A graph projection without edges is not a graph.
    """
    nodes = _sorted_nodes(batch)
    edges = _sorted_edges(batch)
    dangling = _dangling_ids(nodes, edges)

    def node_obj(n: Node) -> dict[str, object]:
        file, line, end_line = _prov(n)
        obj: dict[str, object] = {
            "id": n.id,
            "kind": n.kind.value,
            "name": n.name,
            "language": n.language,
            "grounded": n.grounded,
        }
        if file:
            obj["provenance"] = {
                "file": file,
                "line": int(line),
                **({"end_line": int(end_line)} if end_line else {}),
            }
        return obj

    def edge_obj(e: Edge) -> dict[str, object]:
        obj: dict[str, object] = {"src": e.src, "dst": e.dst, "kind": e.kind.value}
        if e.provenance is not None:
            obj["provenance"] = {"file": e.provenance.file, "line": e.provenance.line}
        return obj

    doc = {
        "nodes": [
            *(node_obj(n) for n in nodes),
            *({"id": mid, "name": mid, "dangling": True} for mid in dangling),
        ],
        "edges": [edge_obj(e) for e in edges],
        "summary": {
            "nodes": len(nodes),
            "edges": len(edges),
            "dangling": len(dangling),
            "grounded": sum(1 for n in nodes if n.grounded),
        },
    }
    # sort_keys for byte-stability; a trailing newline so the file is a well-behaved text file.
    Path(path).write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"nodes": len(nodes), "edges": len(edges), "dangling": len(dangling)}


WRITERS = {
    "graphml": export_graphml,
    "dot": export_dot,
    "json": export_json,
}
"""Format name → writer. Keys match :data:`GRAPH_FORMATS`."""

__all__ = [
    "GRAPH_FORMATS",
    "WRITERS",
    "export_dot",
    "export_graphml",
    "export_json",
]
