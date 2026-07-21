"""Shareable HTML report — self-contained, deterministic, theme-aware, two lenses."""

from __future__ import annotations

from collections import Counter

from orchestrator.catalog.profile import ProjectProfile
from orchestrator.knowledge.current_state import CurrentState, compute_current_state
from orchestrator.knowledge.report_html import render_report_html
from orchestrator.pkg.facts import Edge, EdgeKind, FactBatch, Node, NodeKind, Provenance


def _node(nid: str, kind: NodeKind, name: str, file: str = "f.cs") -> Node:
    return Node(nid, kind, name, "csharp", Provenance(file, 1))


def _batch() -> FactBatch:
    b = FactBatch()
    api = _node("c:App.Api", NodeKind.MODULE, "App.Api")
    biz = _node("c:App.Biz", NodeKind.MODULE, "App.Biz")
    ctrl = _node("c:App.Api.UserController", NodeKind.TYPE, "UserController", "UserController.cs")
    svc = _node("c:App.Biz.UserService", NodeKind.TYPE, "UserService", "UserService.cs")
    for n in (api, biz, ctrl, svc):
        b.add_node(n)
    b.add_edge(Edge(api.id, ctrl.id, EdgeKind.CONTAINS, Provenance("UserController.cs", 1)))
    b.add_edge(Edge(biz.id, svc.id, EdgeKind.CONTAINS, Provenance("UserService.cs", 1)))
    b.add_edge(Edge(api.id, biz.id, EdgeKind.IMPORTS, Provenance("UserController.cs", 1)))
    # A hot function: `Validate` called from several sites → a blast-radius hotspot.
    hot = _node("c:App.Biz.UserService.Validate", NodeKind.FUNCTION, "Validate", "UserService.cs")
    b.add_node(hot)
    b.add_edge(Edge(svc.id, hot.id, EdgeKind.CONTAINS, Provenance("UserService.cs", 5)))
    for i in range(4):
        caller = _node(f"c:App.Api.UserController.M{i}", NodeKind.FUNCTION, f"M{i}", "UserController.cs")
        b.add_node(caller)
        b.add_edge(Edge(ctrl.id, caller.id, EdgeKind.CONTAINS, Provenance("UserController.cs", i)))
        b.add_edge(Edge(caller.id, hot.id, EdgeKind.CALLS, Provenance("UserController.cs", i)))
    return b


_PROFILE = ProjectProfile(
    languages=frozenset({"csharp"}),
    framework="aspnet",
    has_db=False,
    has_migrations=False,
    test_runner=None,
    task_type="feature",
)


def _state() -> CurrentState:
    return compute_current_state(_batch(), _PROFILE)


def test_self_contained_document() -> None:
    html = render_report_html(_state(), repo_name="myrepo", sha="abc123def456789", edges=3, grounded=4)
    assert html.startswith("<!doctype html>")
    assert "<style>" in html and "</style>" in html  # inline CSS
    # Nothing fetched: no external links, scripts, or remote images (invariant #5). An
    # *inline* <script> is fine (Phase 3 filtering) — `src=` catches any external one.
    for needle in ("http://", "https://", "src=", "<link"):
        assert needle not in html, f"report must be self-contained; found {needle!r}"
    assert "myrepo" in html
    assert "abc123def456" in html  # sha truncated to 12 chars


def test_theme_aware() -> None:
    html = render_report_html(_state())
    assert "prefers-color-scheme:dark" in html


def test_timestamp_optional_and_deterministic() -> None:
    a = render_report_html(_state(), timestamp=None)
    b = render_report_html(_state(), timestamp=None)
    assert a == b  # same state in → same bytes out (no LLM, no clock)
    assert "Generated" not in a
    assert "Generated 2026" in render_report_html(_state(), timestamp="2026-07-21 10:00 UTC")


def test_stakeholder_lens_drops_jargon_sections() -> None:
    dev = render_report_html(_state(), lens="developer")
    stake = render_report_html(_state(), lens="stakeholder")
    assert "Blast-radius hotspots" in dev
    assert "Blast-radius hotspots" not in stake
    assert "Risk &amp; health" not in stake
    # Overview + architecture survive both lenses.
    assert "Overview" in stake and "Architecture" in stake


def test_html_escaping() -> None:
    s = _state()
    s.auth_surface = ["<script>Evil</script>"]
    html = render_report_html(s)
    assert "<script>Evil" not in html
    assert "&lt;script&gt;Evil" in html


def test_empty_sections_omitted() -> None:
    s = _state()
    s.auth_surface = []
    s.recent_areas = []
    s.recommendations = []
    html = render_report_html(s)
    assert "Security surface" not in html
    assert "Recent activity" not in html
    assert "Recommendations" not in html


def test_architecture_order_is_name_tiebroken() -> None:
    # Equal-weight areas must order by name so the layout diffs cleanly (invariant #3) —
    # not by set-iteration order, which varies across processes.
    s = _state()
    s.area_types = Counter({"src.zzz": 2, "src.aaa": 2})
    s.area_funcs = Counter()
    html = render_report_html(s)
    assert html.index("src.aaa") < html.index("src.zzz")


def test_svg_diagram_replaces_placeholder() -> None:
    # Phase 2: the architecture section renders an inline SVG, not the old zone-card list.
    html = render_report_html(_state())
    assert '<svg class="arch"' in html
    assert 'class="zones"' not in html


def _coverage_batch() -> FactBatch:
    """A hotspot H called by two uncovered production functions and one test."""
    b = FactBatch()
    mod = _node("py:app", NodeKind.MODULE, "app", "app.py")
    b.add_node(mod)
    hot = _node("py:app.validate", NodeKind.FUNCTION, "validate", "app.py")
    b.add_node(hot)
    b.add_edge(Edge(mod.id, hot.id, EdgeKind.CONTAINS, Provenance("app.py", 1)))
    for name in ("handle_a", "handle_b"):  # production callers, no test reaches them
        p = _node(f"py:app.{name}", NodeKind.FUNCTION, name, "app.py")
        b.add_node(p)
        b.add_edge(Edge(mod.id, p.id, EdgeKind.CONTAINS, Provenance("app.py", 2)))
        b.add_edge(Edge(p.id, hot.id, EdgeKind.CALLS, Provenance("app.py", 3)))
    t = _node("py:tests.test_it", NodeKind.FUNCTION, "test_it", "tests/test_it.py")
    b.add_node(t)
    b.add_edge(Edge(t.id, hot.id, EdgeKind.CALLS, Provenance("tests/test_it.py", 4)))
    return b


def test_spotlight_uses_graph_when_store_given() -> None:
    from orchestrator.pkg.store import FactStore

    s = compute_current_state(_coverage_batch(), _PROFILE)
    with_store = render_report_html(s, store=FactStore(_coverage_batch()))
    without = render_report_html(s)
    assert "dependents across" in with_store  # impact_across quantified
    assert "call sites" in without  # graph-free fallback


def test_blast_radius_coverage_gaps() -> None:
    from orchestrator.pkg.store import FactStore

    s = compute_current_state(_coverage_batch(), _PROFILE)
    html = render_report_html(s, store=FactStore(_coverage_batch()))
    assert "Untested in the blast radius" in html
    # The two production callers have no covering test and must be listed.
    assert "handle_a" in html and "handle_b" in html


def test_filter_toolbar_and_inline_script() -> None:
    html = render_report_html(_state())
    assert 'id="report-search"' in html  # the filter box
    assert "<script>" in html and "</script>" in html  # inline, no src
    assert "addEventListener('input'" in html  # wires the live filter
    # Filter targets the rendered data (table rows / list items), not a duplicated copy.
    assert "table tbody tr" in html and "querySelectorAll" in html


def test_header_counts_rendered() -> None:
    s = _state()
    s.counts = {"Type": 12, "Function": 40, "Module": 3}
    html = render_report_html(s, grounded=50, edges=99)
    assert "55" in html  # node count = sum of counts
    assert "99" in html  # edges
    assert Counter(s.counts).total() == 55
