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
    # System-architecture diagram: a mermaid flowchart with zone subgraphs + a
    # component-dependency table.
    assert "## System architecture" in md
    assert "```mermaid" in md and "subgraph" in md
    assert "### Component dependencies" in md
    assert "UserController" in md
    # richer content: overview prose + code structure (entry points / layout)
    assert "## Overview" in md
    assert "## Code structure" in md and "Layout — top components by zone" in md


def test_infrastructure_section_from_real_repo(tmp_path: Path) -> None:
    # the report reads infra from the repo's manifests/compose (deterministic, no LLM)
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname="x"\ndependencies=["fastapi","psycopg","temporalio"]\n'
        '[project.scripts]\nmytool="x.cli:app"\n',
        encoding="utf-8",
    )
    (tmp_path / "svc.py").write_text("class OrderService:\n    def place(self): return 1\n")
    md = build_current_state(tmp_path, lens="developer", refresh=True)
    assert "## Infrastructure & runtime" in md
    assert "PostgreSQL" in md and "Temporal (workflow engine)" in md and "FastAPI" in md
    assert "`mytool` → x.cli:app (console script)" in md  # entry point


def test_area_groups_by_directory_for_path_modules() -> None:
    # C/C++ modules are slash-paths: areas must be the directory, not the whole file
    # (so the architecture diagram shows components, not one node per file).
    from orchestrator.knowledge.current_state import _area, _zone

    assert _area("src/smf/smf-sm.c") == "src/smf"
    assert _area("lib/sbi/openapi/model/x.c") == "lib/sbi"
    assert _zone("src/smf") == "src"
    # dotted namespaces (C#/Java/Python) keep the first-two-segments behavior
    assert _area("App.Api.Controllers") == "App.Api"


def test_function_areas_group_by_owning_module_not_symbol_id() -> None:
    # Regression: C/C++ function ids are symbols (`cpp:HSL2RGB`, `cpp:Conn::read`),
    # not locations. Areas must come from the function's owning module / source file —
    # otherwise every function becomes its own component (and its own zone), flooding
    # the layout with thousands of one-fn entries on a real C++ repo.
    b = FactBatch()
    mod = _node("cpp:lib/svm/svm.cpp", NodeKind.MODULE, "lib/svm/svm.cpp", "lib/svm/svm.cpp")
    b.add_node(mod)
    # A free function contained directly by the module.
    free = Node("cpp:HSL2RGB", NodeKind.FUNCTION, "HSL2RGB", "cpp", Provenance("lib/svm/svm.cpp", 5))
    b.add_node(free)
    b.add_edge(Edge(mod.id, free.id, EdgeKind.CONTAINS, Provenance("lib/svm/svm.cpp", 5)))
    # A method whose class lives in a `.h` (no owning module node) — must fall back to
    # the source file it's defined in, not its `Conn::read` symbol id.
    meth = Node("cpp:Conn::read", NodeKind.FUNCTION, "read", "cpp", Provenance("lib/svm/svm.cpp", 9))
    b.add_node(meth)
    b.add_edge(Edge("cpp:Conn", meth.id, EdgeKind.CONTAINS, Provenance("lib/svm/svm.cpp", 9)))

    s = compute_current_state(b, _PROFILE)
    assert set(s.area_funcs) == {"lib/svm"}  # both collapse to the directory component
    assert s.area_funcs["lib/svm"] == 2
    assert "HSL2RGB" not in s.area_funcs and "Conn::read" not in s.area_funcs


def test_call_graph_hotspots_rendered() -> None:
    # Functions with the most incoming CALLS edges surface as "most-depended-upon".
    b = FactBatch()
    mod = _node("c:src/util.c", NodeKind.MODULE, "src/util.c", "src/util.c")
    helper = _node("c:helper", NodeKind.FUNCTION, "helper", "src/util.c")
    b.add_node(mod)
    b.add_node(helper)
    for i in range(3):
        caller = _node(f"c:caller{i}", NodeKind.FUNCTION, f"caller{i}", "src/util.c")
        b.add_node(caller)
        b.add_edge(Edge(caller.id, helper.id, EdgeKind.CALLS, Provenance("src/util.c", i)))
    s = compute_current_state(b, _PROFILE)
    assert s.call_hotspots and s.call_hotspots[0] == ("helper", 3)
    md = render_current_state(s, lens="developer")
    assert "## Call graph — most-depended-upon functions" in md and "`helper`" in md


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


def test_vendored_and_generated_paths_excluded() -> None:
    # Live-proving on a large real C repo showed vendored/generated trees skew the
    # "what is THIS project" metrics. _is_generated now catches common conventions
    # (path substrings), language-agnostically — not just the C# markers.
    from orchestrator.knowledge.current_state import _is_generated

    def n(file: str) -> Node:
        return Node("c:x", NodeKind.TYPE, "x", "c", Provenance(file, 1))

    assert _is_generated(n("lib/sbi/openapi/external/cJSON.c"))  # /external/
    assert _is_generated(n("lib/sbi/contrib/multipart_parser.c"))  # /contrib/
    assert _is_generated(n("lib/ipfw/objs/include/ip_fw.h"))  # /objs/
    assert _is_generated(n("subprojects/freediameter/foo.c"))  # /subprojects/
    assert _is_generated(n("third_party/x/y.c"))  # /third_party/
    # the project's own code is NOT flagged
    assert not _is_generated(n("src/amf/amf-context.c"))
    assert not _is_generated(n("lib/core/ogs-hash.c"))
