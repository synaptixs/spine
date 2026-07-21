"""Investigation brief (C4): ticket × codebase grounding, deterministic."""

from __future__ import annotations

from orchestrator.pkg import FactStore
from orchestrator.pkg.facts import Edge, EdgeKind, FactBatch, Node, NodeKind, Provenance
from orchestrator.sdlc.investigate import build_investigation, render_investigation_md


def _node(nid: str, kind: NodeKind, name: str, file: str, line: int = 1) -> Node:
    return Node(id=nid, kind=kind, name=name, language="python", provenance=Provenance(file, line))


def _graph() -> FactBatch:
    """auth.py defines authenticate(); web.py calls it."""
    b = FactBatch()
    auth = _node("py:auth", NodeKind.MODULE, "auth.py", "auth.py")
    web = _node("py:web", NodeKind.MODULE, "web.py", "web.py")
    authn = _node("py:auth.authenticate", NodeKind.FUNCTION, "authenticate", "auth.py", 10)
    handler = _node("py:web.handler", NodeKind.FUNCTION, "handler", "web.py", 5)
    for n in (auth, web, authn, handler):
        b.add_node(n)
    b.add_edge(Edge("py:auth", "py:auth.authenticate", EdgeKind.CONTAINS))
    b.add_edge(Edge("py:web", "py:web.handler", EdgeKind.CONTAINS))
    b.add_edge(Edge("py:web.handler", "py:auth.authenticate", EdgeKind.CALLS, Provenance("web.py", 6)))
    return b


def test_build_investigation_locates_symbols_and_callers() -> None:
    store = FactStore(_graph())
    inv = build_investigation("Fix authenticate 500", "authenticate throws on empty token", store=store)

    assert inv.grounded is True
    names = {land.name for land in inv.landing}
    assert "authenticate" in names
    hit = next(land for land in inv.landing if land.name == "authenticate")
    assert hit.callers == 1  # web.handler calls it
    assert hit.module == "auth.py"  # owning module resolved via CONTAINS
    assert "auth.py" in inv.areas


def test_prior_notes_passthrough_and_render() -> None:
    store = FactStore(_graph())
    inv = build_investigation(
        "authenticate bug", "x", store=store, prior_notes=["[fix-pattern] guard empty token (runs: 12)"]
    )
    md = render_investigation_md(inv)
    assert "# Investigation — authenticate bug" in md
    assert "## Where it lands in the code" in md and "`authenticate`" in md
    assert "## Prior art / related work" in md and "guard empty token" in md
    assert "## Suggested next step" in md and "orchestrator design" in md


def test_greenfield_is_honest() -> None:
    inv = build_investigation("Anything", "new feature", store=FactStore(FactBatch()))
    assert inv.grounded is False and inv.landing == []
    md = render_investigation_md(inv)
    assert "No knowledge graph yet" in md
    assert "None surfaced" in md  # no prior notes without the registry DB


def test_no_match_is_honest_when_grounded() -> None:
    store = FactStore(_graph())
    inv = build_investigation("Refactor billing invoices", "unrelated to the graph", store=store)
    assert inv.landing == []  # nothing lexically matched
    md = render_investigation_md(inv)
    assert "No symbols matched" in md
