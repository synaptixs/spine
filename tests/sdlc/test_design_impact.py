"""Blast-radius + unverified-reference annotation for the design stage (C1 + C9)."""

from __future__ import annotations

from orchestrator.pkg import FactStore
from orchestrator.pkg.facts import Edge, EdgeKind, FactBatch, Node, NodeKind, Provenance
from orchestrator.sdlc.design import produce_design, render_design_md
from orchestrator.sdlc.impact import blast_radius, render_md, to_dict, unverified_references


def _node(nid: str, kind: NodeKind, name: str, file: str, line: int = 1) -> Node:
    return Node(id=nid, kind=kind, name=name, language="python", provenance=Provenance(file, line))


def _graph() -> FactBatch:
    """report.py defines render/to_row; web.py imports report and calls render."""
    b = FactBatch()
    report = _node("py:report", NodeKind.MODULE, "report.py", "report.py")
    web = _node("py:web", NodeKind.MODULE, "web.py", "web.py")
    render = _node("py:report.render", NodeKind.FUNCTION, "render", "report.py", 10)
    to_row = _node("py:report.to_row", NodeKind.FUNCTION, "to_row", "report.py", 20)
    handler = _node("py:web.handler", NodeKind.FUNCTION, "handler", "web.py", 5)
    for n in (report, web, render, to_row, handler):
        b.add_node(n)
    b.add_edge(Edge("py:report", "py:report.render", EdgeKind.CONTAINS))
    b.add_edge(Edge("py:report", "py:report.to_row", EdgeKind.CONTAINS))
    b.add_edge(Edge("py:web", "py:web.handler", EdgeKind.CONTAINS))
    b.add_edge(Edge("py:web", "py:report", EdgeKind.IMPORTS))  # web imports report
    b.add_edge(Edge("py:web.handler", "py:report.render", EdgeKind.CALLS, Provenance("web.py", 6)))
    b.add_edge(Edge("py:report.to_row", "py:report.render", EdgeKind.CALLS, Provenance("report.py", 22)))
    return b


# --------------------------------------------------------------------------- #
# C1 — blast radius
# --------------------------------------------------------------------------- #
def test_blast_radius_resolves_module_importers_and_hotspots() -> None:
    store = FactStore(_graph())
    br = blast_radius(store, ["report.py"])

    assert br.grounded and br.call_graph_available
    assert len(br.modules) == 1
    m = br.modules[0]
    assert m.ref == "report.py" and m.module == "report.py"
    assert m.importers == 1 and "web.py" in m.importer_names
    # render is called by handler + to_row → the top hotspot
    names = [s.name for s in m.hotspots]
    assert "render" in names
    top = next(s for s in m.hotspots if s.name == "render")
    assert top.callers == 2


def test_unresolved_reference_is_flagged_when_grounded() -> None:
    store = FactStore(_graph())
    br = blast_radius(store, ["report.py", "ghost.py"])
    assert "ghost.py" in br.unresolved
    assert unverified_references(br) == ["ghost.py"]  # C9: absent path surfaced


def test_greenfield_suppresses_unverified_references() -> None:
    """An ungrounded graph makes every path 'absent' — don't flag them all."""
    store = FactStore(FactBatch())
    br = blast_radius(store, ["anything.py"])
    assert br.grounded is False
    assert unverified_references(br) == []
    assert render_md(to_dict(br)) == ""  # nothing to render on greenfield


def test_no_call_graph_reports_module_impact_only() -> None:
    """A graph with IMPORTS but no CALLS (e.g. TS/Java) → module-level only, stated."""
    b = FactBatch()
    b.add_node(_node("py:a", NodeKind.MODULE, "a.py", "a.py"))
    b.add_node(_node("py:b", NodeKind.MODULE, "b.py", "b.py"))
    b.add_edge(Edge("py:b", "py:a", EdgeKind.IMPORTS))
    br = blast_radius(FactStore(b), ["a.py"])
    assert br.call_graph_available is False
    assert br.modules[0].hotspots == ()
    md = render_md(to_dict(br))
    assert "Call graph unavailable" in md


# --------------------------------------------------------------------------- #
# render + design integration
# --------------------------------------------------------------------------- #
def test_render_md_includes_sections() -> None:
    store = FactStore(_graph())
    md = render_md(to_dict(blast_radius(store, ["report.py", "ghost.py"])))
    assert "## Blast radius" in md
    assert "imported by 1 module(s): web.py" in md
    assert "high fan-in: `render`" in md
    assert "## ⚠ Unverified references" in md and "`ghost.py`" in md


async def test_produce_design_annotates_with_blast_radius() -> None:
    store = FactStore(_graph())
    overview = {"modules": [{"module": "report.py", "nodes": 3}]}
    spec = {"title": "Tweak render", "summary": "x", "acceptance_criteria": ["works"]}
    # heuristic (no LLM) design draws files_to_touch from the overview modules
    design = await produce_design(spec, overview=overview, store=store)
    bd = design["blast_radius"]
    assert bd["grounded"] is True
    assert any(m["ref"] == "report.py" for m in bd["modules"])
    # and it renders through the design markdown
    assert "## Blast radius" in render_design_md(spec, design)


async def test_produce_design_without_store_is_unannotated() -> None:
    design = await produce_design(
        {"title": "t", "acceptance_criteria": ["a"]}, overview={"modules": []}, store=None
    )
    assert "blast_radius" not in design
    assert "## Blast radius" not in render_design_md({"title": "t"}, design)
