"""Current State — synthesis from PKG facts + profile, two lenses. Deterministic."""

from __future__ import annotations

from pathlib import Path

from orchestrator.catalog.profile import ProjectProfile
from orchestrator.knowledge.current_state import (
    build_current_state,
    compute_current_state,
    render_current_state,
)
from orchestrator.pkg.facts import Edge, EdgeKind, FactBatch, Node, NodeKind, Provenance


def _node(nid: str, kind: NodeKind, name: str, file: str = "f.cs") -> Node:
    return Node(nid, kind, name, "csharp", Provenance(file, 1))


def _synthetic_batch() -> FactBatch:
    b = FactBatch()
    mod = _node("c:App.Api", NodeKind.MODULE, "App.Api")
    b.add_node(mod)
    ctrl = _node("c:App.Api.UserController", NodeKind.TYPE, "UserController", "UserController.cs")
    b.add_node(ctrl)
    b.add_edge(Edge(mod.id, ctrl.id, EdgeKind.CONTAINS, Provenance("UserController.cs", 1)))
    for i in range(30):  # a fat controller: 30 endpoints
        m = _node(f"{ctrl.id}.Get{i}", NodeKind.FUNCTION, f"Get{i}", "UserController.cs")
        b.add_node(m)
        b.add_edge(Edge(ctrl.id, m.id, EdgeKind.CONTAINS, Provenance("UserController.cs", i)))
    svc = _node("c:App.Biz.UserService", NodeKind.TYPE, "UserService", "UserService.cs")
    iface = _node("c:App.Biz.IUserService", NodeKind.TYPE, "IUserService", "IUserService.cs")
    biz = _node("c:App.Biz", NodeKind.MODULE, "App.Biz")
    for n in (biz, svc, iface):
        b.add_node(n)
    b.add_edge(Edge(biz.id, svc.id, EdgeKind.CONTAINS, Provenance("UserService.cs", 1)))
    b.add_edge(Edge(biz.id, iface.id, EdgeKind.CONTAINS, Provenance("IUserService.cs", 1)))
    # API depends on Biz (coupling)
    b.add_edge(Edge(mod.id, "c:App.Biz", EdgeKind.IMPORTS, Provenance("UserController.cs", 1)))
    return b


_PROFILE = ProjectProfile(
    languages=frozenset({"csharp"}),
    framework="aspnet",
    has_db=False,
    has_migrations=False,
    test_runner=None,
    task_type="feature",
)


def test_compute_metrics() -> None:
    s = compute_current_state(_synthetic_batch(), _PROFILE)
    assert s.controllers == 1
    assert s.endpoints == 30
    assert s.interfaces == 1
    assert s.layers["API · controllers"] == 1
    assert s.layers["Interfaces / contracts"] == 1
    assert ("App.Api", "App.Biz") in s.coupling
    assert s.tested_areas == 0


def test_recommendations_flag_tests_and_fat_controller() -> None:
    s = compute_current_state(_synthetic_batch(), _PROFILE)
    text = " ".join(t for _p, t in s.recommendations)
    assert any(p == "P1" for p, _ in s.recommendations)  # tests / security
    assert "fat controllers" in text.lower()  # 30 > 25 endpoints
    assert "UserController" in text


def test_developer_lens_renders_sections() -> None:
    s = compute_current_state(_synthetic_batch(), _PROFILE)
    md = render_current_state(s, lens="developer")
    assert "# Current State" in md
    assert "## Architecture — layers" in md
    assert "## Recommendations" in md
    assert "```mermaid" in md  # coupling diagram
    assert "UserController" in md


def test_stakeholder_lens_is_plain_language() -> None:
    s = compute_current_state(_synthetic_batch(), _PROFILE)
    md = render_current_state(s, lens="stakeholder")
    assert "# Current State" in md
    assert "web API" in md
    assert "## " not in md.split("\n", 1)[1]  # no technical section headers in the body


def test_auth_surface_and_recency_degrade(tmp_path: Path) -> None:
    b = _synthetic_batch()
    b.add_node(_node("c:App.Biz.AuthService", NodeKind.TYPE, "AuthService", "AuthService.cs"))
    s = compute_current_state(b, _PROFILE, root=tmp_path)  # tmp_path has no git history
    assert "AuthService" in s.auth_surface
    assert s.recent_areas == []  # git recency degrades gracefully without a repo
    md = render_current_state(s, lens="developer")
    assert "## Security surface" in md


def test_build_current_state_end_to_end_python(tmp_path: Path) -> None:
    # works on any language (Python extractor is always available — no grammar extra)
    (tmp_path / "svc.py").write_text(
        "class OrderService:\n    def place(self) -> int:\n        return 1\n", encoding="utf-8"
    )
    md = build_current_state(tmp_path, lens="developer", refresh=True)
    assert "# Current State" in md and "## At a glance" in md
