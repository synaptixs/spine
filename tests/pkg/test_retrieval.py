"""PKG grounded retrieval: enclosing-symbol resolution + blast-radius briefs."""

from __future__ import annotations

from orchestrator.pkg import (
    Edge,
    EdgeKind,
    FactBatch,
    FactStore,
    GroundedRetriever,
    Node,
    NodeKind,
    Provenance,
)


def _store() -> FactStore:
    batch = FactBatch()
    # callee `total` spans lines 4-6 in invoice.py
    batch.add_node(
        Node("py:inv.Invoice.total", NodeKind.FUNCTION, "total", "python", Provenance("inv.py", 4, 6))
    )
    # a sibling method in the same file (lines 8-9) — same-file caller
    batch.add_node(
        Node("py:inv.Invoice.summary", NodeKind.FUNCTION, "summary", "python", Provenance("inv.py", 8, 9))
    )
    # a caller in a *different* file
    batch.add_node(
        Node("py:report.build", NodeKind.FUNCTION, "build", "python", Provenance("report.py", 12, 20))
    )
    batch.add_edge(
        Edge("py:report.build", "py:inv.Invoice.total", EdgeKind.CALLS, Provenance("report.py", 15))
    )
    batch.add_edge(
        Edge("py:inv.Invoice.summary", "py:inv.Invoice.total", EdgeKind.CALLS, Provenance("inv.py", 9))
    )
    return FactStore(batch)


def test_enclosing_symbol_uses_span() -> None:
    r = GroundedRetriever(_store())
    assert r.enclosing_symbol("inv.py", 5) is not None
    assert r.enclosing_symbol("inv.py", 5).id == "py:inv.Invoice.total"  # type: ignore[union-attr]
    assert r.enclosing_symbol("inv.py", 99) is None  # outside any span


def test_enclosing_symbol_picks_smallest_span() -> None:
    # line 9 is inside both summary (8-9) and nothing larger here → summary
    r = GroundedRetriever(_store())
    assert r.enclosing_symbol("inv.py", 9).id == "py:inv.Invoice.summary"  # type: ignore[union-attr]


def test_diff_impact_orders_by_caller_count() -> None:
    r = GroundedRetriever(_store())
    impacts = r.diff_impact({"inv.py": {5}})
    assert impacts[0].symbol.id == "py:inv.Invoice.total"
    assert {c.caller.id for c in impacts[0].callers} == {"py:report.build", "py:inv.Invoice.summary"}


def test_cross_file_callers_excludes_same_file() -> None:
    r = GroundedRetriever(_store())
    impact = r.impact_of("py:inv.Invoice.total")
    assert impact is not None
    cross = impact.cross_file_callers()
    assert [c.caller.id for c in cross] == ["py:report.build"]  # summary (same file) dropped


def test_render_brief_highlights_cross_file_breakage() -> None:
    r = GroundedRetriever(_store())
    brief = r.render(r.diff_impact({"inv.py": {5}}), cross_file_only=True)
    assert "Product Knowledge Graph" in brief
    assert "py:report.build" in brief and "report.py:15" in brief
    assert "py:inv.Invoice.summary" not in brief  # same-file caller not a breakage risk


def test_render_empty_when_no_downstream() -> None:
    batch = FactBatch()
    batch.add_node(Node("py:m.lonely", NodeKind.FUNCTION, "lonely", "python", Provenance("m.py", 1, 2)))
    r = GroundedRetriever(FactStore(batch))
    assert r.render(r.diff_impact({"m.py": {1}})) == ""
