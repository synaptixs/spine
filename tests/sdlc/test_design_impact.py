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


def test_a_route_handler_is_not_scored_as_safe_to_change() -> None:
    """A handler has no in-language callers, so filtering hotspots on ``callers_of``
    dropped every endpoint out of the blast radius and scored public API as touching
    nothing. It must appear, name its endpoint, and outrank an equal-fan-in internal."""
    b = _graph()
    b.add_node(
        Node(
            id="py:endpoint:GET /rows",
            kind=NodeKind.ENDPOINT,
            name="GET /rows",
            language="python",
            provenance=Provenance("web.py", 4),
        )
    )
    b.add_edge(Edge("py:endpoint:GET /rows", "py:web.handler", EdgeKind.EXPOSES, Provenance("web.py", 4)))

    br = blast_radius(FactStore(b), ["web.py"])
    hotspots = {h.name: h for m in br.modules for h in m.hotspots}

    assert "handler" in hotspots
    assert hotspots["handler"].callers == 0  # still honest: no call sites
    assert hotspots["handler"].exposed == 1
    assert "serves 1 endpoint(s)" in render_md(to_dict(br))


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


# --------------------------------------------------------------------------- #
# NSS-1209 — a namespace-keyed front-end (C#/Java/PHP; Go by directory) emits one
# MODULE node per namespace, so only the first file walked used to resolve and every
# sibling came back "absent from the knowledge graph".
# --------------------------------------------------------------------------- #
def _csharp_namespace_graph() -> FactBatch:
    """One MODULE for the namespace (provenance = ColumnKey.cs, the first file walked);
    three Types across three files; one other module importing the namespace."""
    b = FactBatch()
    ns = Node(
        id="csharp:Shared.Enums",
        kind=NodeKind.MODULE,
        name="Shared.Enums",
        language="csharp",
        provenance=Provenance("WebApp/Shared/Enums/ColumnKey.cs", 1),
    )
    b.add_node(ns)
    for name, file in (
        ("ColumnKey", "ColumnKey.cs"),
        ("ProductGroupHelper", "ProductGroup.cs"),
        ("ProductHistoryEventType", "ProductHistoryEventType.cs"),
    ):
        tid = f"csharp:Shared.Enums.{name}"
        b.add_node(
            Node(
                id=tid,
                kind=NodeKind.TYPE,
                name=name,
                language="csharp",
                provenance=Provenance(f"WebApp/Shared/Enums/{file}", 3),
            )
        )
        b.add_edge(Edge(ns.id, tid, EdgeKind.CONTAINS))
    grid = Node(
        id="csharp:Features.Grid",
        kind=NodeKind.MODULE,
        name="Features.Grid",
        language="csharp",
        provenance=Provenance("WebApp/Features/Grid.cs", 1),
    )
    b.add_node(grid)
    b.add_edge(Edge(grid.id, ns.id, EdgeKind.IMPORTS))
    return b


def test_nss_1209_a_file_in_a_shared_namespace_is_not_flagged_absent() -> None:
    """Field report NSS-1209: `ProductGroup.cs` and `ProductHistoryEventType.cs` were listed as
    "absent from the knowledge graph" two sections after their own symbols had been printed
    with line numbers. Every file a grounded node lives in must resolve; only a true ghost
    is absent. A bare basename — how a ticket usually writes it — resolves too."""
    store = FactStore(_csharp_namespace_graph())
    files = [
        "WebApp/Shared/Enums/ProductGroup.cs",
        "WebApp/Shared/Enums/ProductHistoryEventType.cs",
        "WebApp/Shared/Enums/ColumnKey.cs",
        "WebApp/Shared/Enums/Ghost.cs",
    ]
    br = blast_radius(store, files)
    assert unverified_references(br) == ["WebApp/Shared/Enums/Ghost.cs"]
    assert [m.module for m in br.modules] == ["Shared.Enums"] * 3
    assert blast_radius(store, ["ProductGroup.cs"]).unresolved == ()


def test_a_module_spanning_files_says_so_and_its_importers_are_counted_once() -> None:
    """The importer count belongs to the namespace, not to whichever file resolved. Say so in
    the design, and do not sum it once per file in the build document's reading."""
    from orchestrator.sdlc.builddoc import _blast_prose

    store = FactStore(_csharp_namespace_graph())
    br = blast_radius(store, ["WebApp/Shared/Enums/ProductGroup.cs", "WebApp/Shared/Enums/ColumnKey.cs"])
    assert all(m.spans == 3 and m.importers == 1 for m in br.modules)
    md = render_md(to_dict(br))
    assert "module `Shared.Enums` spans 3 file(s); imported by 1 module(s): Features.Grid" in md
    assert "2 module(s) change; 1 module(s) import them" in _blast_prose(to_dict(br))


def test_a_new_path_in_another_directory_is_not_resolved_to_a_namesake() -> None:
    """A design proposing a NEW `Payments/Client.cs` must stay unverified, not resolve to the
    existing `Legacy/Client.cs` by basename — the honest answer was the old one."""
    b = FactBatch()
    ns = Node("csharp:Legacy", NodeKind.MODULE, "Legacy", "csharp", Provenance("Legacy/Soap/Client.cs", 1))
    b.add_node(ns)
    b.add_node(
        Node(
            "csharp:Legacy.Client", NodeKind.TYPE, "Client", "csharp", Provenance("Legacy/Soap/Client.cs", 3)
        )
    )
    b.add_edge(Edge(ns.id, "csharp:Legacy.Client", EdgeKind.CONTAINS))
    br = blast_radius(FactStore(b), ["Payments/Oauth/Client.cs", "Client.cs"])
    assert unverified_references(br) == ["Payments/Oauth/Client.cs"]  # the bare name still resolves


def test_an_ambiguous_bare_basename_is_not_guessed() -> None:
    b = FactBatch()
    for ns, file in (("A", "a/Product.cs"), ("B", "b/Product.cs")):
        b.add_node(Node(f"csharp:{ns}", NodeKind.MODULE, ns, "csharp", Provenance(file, 1)))
        b.add_node(Node(f"csharp:{ns}.Product", NodeKind.TYPE, "Product", "csharp", Provenance(file, 3)))
        b.add_edge(Edge(f"csharp:{ns}", f"csharp:{ns}.Product", EdgeKind.CONTAINS))
    assert unverified_references(blast_radius(FactStore(b), ["Product.cs"])) == ["Product.cs"]


# --------------------------------------------------------------------------- #
# B160 / SSPN-376 — "Nothing in the product depends" must not contradict the fan-in
# --------------------------------------------------------------------------- #
def _b160_graph() -> FactBatch:
    """docs.py defines read(); a test imports the module, app.py imports and calls read()."""
    b = FactBatch()
    docs = _node("py:docs", NodeKind.MODULE, "docs", "docs.py")
    read = _node("py:docs.read", NodeKind.FUNCTION, "read", "docs.py", 3)
    test_mod = Node(
        id="py:tests.test_docs",
        kind=NodeKind.MODULE,
        name="tests.test_docs",
        language="python",
        provenance=Provenance("tests/test_docs.py", 1),
        test=True,
    )
    test_fn = Node(
        id="py:tests.test_docs.test_read",
        kind=NodeKind.FUNCTION,
        name="test_read",
        language="python",
        provenance=Provenance("tests/test_docs.py", 4),
        test=True,
    )
    app = _node("py:app", NodeKind.MODULE, "app", "app.py")
    main = _node("py:app.main", NodeKind.FUNCTION, "main", "app.py", 5)
    show = _node("py:app.show", NodeKind.FUNCTION, "show", "app.py", 9)
    for n in (docs, read, test_mod, test_fn, app, main, show):
        b.add_node(n)
    b.add_edge(Edge("py:docs", "py:docs.read", EdgeKind.CONTAINS))
    b.add_edge(Edge("py:tests.test_docs", "py:tests.test_docs.test_read", EdgeKind.CONTAINS))
    b.add_edge(Edge("py:app", "py:app.main", EdgeKind.CONTAINS))
    b.add_edge(Edge("py:tests.test_docs", "py:docs", EdgeKind.IMPORTS))  # import docs
    b.add_edge(Edge("py:app", "py:docs.read", EdgeKind.IMPORTS))  # from docs import read
    b.add_edge(
        Edge(
            "py:tests.test_docs.test_read",
            "py:docs.read",
            EdgeKind.CALLS,
            Provenance("tests/test_docs.py", 5),
        )
    )
    b.add_edge(Edge("py:app.main", "py:docs.read", EdgeKind.CALLS, Provenance("app.py", 6)))
    b.add_edge(Edge("py:app", "py:app.show", EdgeKind.CONTAINS))
    b.add_edge(Edge("py:app.show", "py:docs.read", EdgeKind.CALLS, Provenance("app.py", 10)))
    return b


def test_b160_a_module_imported_through_its_symbols_is_not_contained() -> None:
    from orchestrator.sdlc.builddoc import _blast_prose

    br = blast_radius(FactStore(_b160_graph()), ["docs.py"])
    (mod,) = br.modules

    assert set(mod.importer_names) == {"app", "tests.test_docs"}
    assert mod.test_importer_names == ("tests.test_docs",)
    assert "Nothing in the product depends" not in _blast_prose(to_dict(br))


def test_b160_a_hotspot_counts_its_product_callers() -> None:
    br = blast_radius(FactStore(_b160_graph()), ["docs.py"])
    (hot,) = br.modules[0].hotspots

    assert (hot.name, hot.callers, hot.product_callers) == ("read", 3, 2)  # one test caller, two product
    assert to_dict(br)["modules"][0]["hotspots"][0]["product_callers"] == 2


def _test_only(hotspots: list[dict[str, object]]) -> str:
    from orchestrator.sdlc.builddoc import _blast_prose

    bd = {
        "call_graph_available": True,
        "modules": [
            {
                "module": "docs",
                "where": "docs.py",
                "importers": 1,
                "importer_names": ["tests.test_docs"],
                "test_importer_names": ["tests.test_docs"],
                "hotspots": hotspots,
            }
        ],
    }
    return next(line for line in _blast_prose(bd).splitlines() if line.startswith("**Containment:**"))


def test_b160_containment_is_refused_when_a_symbol_has_product_callers() -> None:
    """Every importer the graph records is a test, but the table beside the sentence shows a symbol
    called from product code. The sentence must not contradict it."""
    line = _test_only(
        [{"name": "read", "where": "docs.py:3", "callers": 50, "transitive": 50, "product_callers": 7}]
    )

    assert "Nothing in the product depends" not in line
    assert "read" in line


def test_b160_containment_stands_when_only_tests_call() -> None:
    line = _test_only(
        [{"name": "read", "where": "docs.py:3", "callers": 4, "transitive": 4, "product_callers": 0}]
    )

    assert "Nothing in the product depends on what changes" in line


def test_b160_a_hotspot_saved_before_the_count_existed_claims_no_containment() -> None:
    """An older `design.json` has callers but no `product_callers`. Unknown is not zero."""
    line = _test_only([{"name": "read", "where": "docs.py:3", "callers": 4, "transitive": 4}])

    assert "Nothing in the product depends" not in line


def test_b160_an_uncalled_hotspot_saved_before_the_count_keeps_the_claim() -> None:
    """A route handler is a hotspot with no call sites; with nothing calling it there is no caller to
    be product code, so the claim is what it was before."""
    line = _test_only(
        [{"name": "handler", "where": "docs.py:9", "callers": 0, "transitive": 1, "exposed": 1}]
    )

    assert "Nothing in the product depends on what changes" in line
