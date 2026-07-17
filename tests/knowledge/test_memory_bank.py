"""Episteme renderers + build_memory_bank (deterministic, no LLM)."""

from __future__ import annotations

from pathlib import Path

from orchestrator.catalog.profile import ProjectProfile
from orchestrator.knowledge.areas import AreaIndex
from orchestrator.knowledge.renderers import (
    DataLayer,
    ModuleDeps,
    _area_prose,
    _is_test_module,
    _under_tests,
    collect_areas,
    module_page_slugs,
    render_architecture,
    render_area_page,
    render_domain_model,
    render_entity_page,
    render_glossary,
    render_module_page,
    render_tech_context,
    select_module_pages,
)
from orchestrator.knowledge.understand import build_memory_bank, memory_bank_dir
from orchestrator.pkg.facts import Edge, EdgeKind, FactBatch, Node, NodeKind, Provenance
from orchestrator.pkg.stats import summarise_store
from orchestrator.pkg.store import FactStore


def _store() -> FactStore:
    batch = FactBatch()
    src = Provenance(file="src/app/core.py", line=1)
    tst = Provenance(file="tests/test_core.py", line=1)
    batch.add_node(Node(id="py:app.core", kind=NodeKind.MODULE, name="app.core", provenance=src))
    batch.add_node(Node(id="py:app.core.Widget", kind=NodeKind.TYPE, name="Widget", provenance=src))
    batch.add_node(Node(id="py:app.core.run", kind=NodeKind.FUNCTION, name="run", provenance=src))
    batch.add_node(
        Node(id="py:tests.test_core", kind=NodeKind.MODULE, name="tests.test_core", provenance=tst)
    )
    batch.add_node(Node(id="py:tests.test_core.TFix", kind=NodeKind.TYPE, name="TFix", provenance=tst))
    batch.add_edge(Edge(src="py:app.core", dst="py:app.core.Widget", kind=EdgeKind.CONTAINS))
    batch.add_edge(Edge(src="py:app.core", dst="py:app.core.run", kind=EdgeKind.CONTAINS))
    batch.add_edge(Edge(src="py:tests.test_core", dst="py:tests.test_core.TFix", kind=EdgeKind.CONTAINS))
    return FactStore(batch)


def test_is_test_module() -> None:
    assert _is_test_module("tests.test_core") and _is_test_module("test_foo")
    assert not _is_test_module("orchestrator.sdlc.codegen")


def test_under_tests() -> None:
    n = Node(id="x", kind=NodeKind.TYPE, name="X", provenance=Provenance(file="tests/test_x.py", line=1))
    s = Node(id="y", kind=NodeKind.TYPE, name="Y", provenance=Provenance(file="src/app/y.py", line=1))
    assert _under_tests(n) and not _under_tests(s)


def test_architecture_excludes_test_modules() -> None:
    md = render_architecture(_store(), summarise_store(_store()), greenfield=False)
    assert "app.core" in md and "1 types, 1 functions" in md
    assert "tests.test_core" not in md  # source-only module map


def test_architecture_links_symbols_to_source() -> None:
    md = render_architecture(_store(), summarise_store(_store()), greenfield=False, src="../")
    assert "[`app.core`](../src/app/core.py#L1)" in md


def test_architecture_without_src_degrades_to_text_not_broken_links() -> None:
    """A bank outside the repo can't have working relative links — say the location instead."""
    md = render_architecture(_store(), summarise_store(_store()), greenfield=False, src=None)
    assert "`app.core` (`src/app/core.py:1`)" in md
    assert "](" not in md  # no link syntax at all


def test_architecture_hotspot_lists_its_callers() -> None:
    """The 'back' half of back-and-forth: a hotspot names who reaches it, each linked."""
    batch = FactBatch()
    p = Provenance(file="src/app/core.py", line=1)
    batch.add_node(Node(id="py:app.core", kind=NodeKind.MODULE, name="app.core", provenance=p))
    batch.add_node(Node(id="py:app.core.target", kind=NodeKind.FUNCTION, name="target", provenance=p))
    for caller in ("beta", "alpha"):  # deliberately not alphabetical — output must sort
        cid = f"py:app.core.{caller}"
        batch.add_node(
            Node(id=cid, kind=NodeKind.FUNCTION, name=caller, provenance=Provenance("src/app/core.py", 9))
        )
        batch.add_edge(Edge(src=cid, dst="py:app.core.target", kind=EdgeKind.CALLS, provenance=p))
    store = FactStore(batch)
    md = render_architecture(store, summarise_store(store), greenfield=False, src="../")
    assert "- called by [`alpha`](../src/app/core.py#L9), [`beta`](../src/app/core.py#L9)" in md


# --- phase 2: module pages ------------------------------------------------------


def test_module_page_slugs_are_filesystem_safe_and_collision_free() -> None:
    """Module names are wild across languages and become filenames."""
    slugs = module_page_slugs(["App.Api", "src/smf/smf-sm.c", "cpp:A::A", "plain"])
    for name, stem in slugs.items():
        assert "/" not in stem and ":" not in stem, f"{name} → {stem} is not a safe filename"
    assert len(set(slugs.values())) == len(slugs)


def test_module_page_slugs_disambiguate_case_insensitive_collisions() -> None:
    """macOS/Windows filesystems fold case: Foo.md and foo.md are the same file."""
    slugs = module_page_slugs(["app.Core", "app.core"])
    assert slugs["app.Core"].lower() != slugs["app.core"].lower()


def test_module_page_slugs_are_stable_regardless_of_input_order() -> None:
    a = module_page_slugs(["z.mod", "a.mod", "m.mod"])
    b = module_page_slugs(["m.mod", "z.mod", "a.mod"])
    assert a == b


def test_select_module_pages_is_capped_and_excludes_tests() -> None:
    store = _store()
    picked = select_module_pages(store)
    assert [m.name for m in picked] == ["app.core"]  # test module excluded, empty ones dropped
    assert select_module_pages(store, limit=0) == []


def _import_store() -> FactStore:
    """Two modules where `b` imports a *symbol* from `a`.

    This mirrors what the front-ends actually emit: an IMPORTS edge targets the imported
    **symbol** (``py:b -> py:a.helper``), never the module. An earlier fixture wired
    module→module edges by hand, which quietly encoded a wrong assumption and let a real
    bug ship — `a`'s importers were always empty because nothing points at a module id.
    """
    batch = FactBatch()
    pa, pb = Provenance(file="src/a.py", line=1), Provenance(file="src/b.py", line=1)
    batch.add_node(Node(id="py:a", kind=NodeKind.MODULE, name="a", provenance=pa))
    batch.add_node(Node(id="py:a.helper", kind=NodeKind.FUNCTION, name="helper", provenance=pa))
    batch.add_edge(Edge(src="py:a", dst="py:a.helper", kind=EdgeKind.CONTAINS))
    batch.add_node(Node(id="py:b", kind=NodeKind.MODULE, name="b", provenance=pb))
    batch.add_node(Node(id="py:b.use", kind=NodeKind.FUNCTION, name="use", provenance=pb))
    batch.add_edge(Edge(src="py:b", dst="py:b.use", kind=EdgeKind.CONTAINS))
    batch.add_edge(Edge(src="py:b", dst="py:a.helper", kind=EdgeKind.IMPORTS, provenance=pb))
    return FactStore(batch)


def _deps(store: FactStore) -> ModuleDeps:
    return ModuleDeps(store, AreaIndex(store))


def test_module_deps_resolve_symbol_targeted_imports_to_modules() -> None:
    """IMPORTS points at the imported symbol, so both ends must walk up to their module."""
    store = _import_store()
    deps = _deps(store)
    assert deps.imports["py:b"] == {"py:a"}
    assert deps.importers["py:a"] == {"py:b"}  # the backlink that was silently empty


def test_module_page_anchors_symbols_and_links_both_directions() -> None:
    store = _store()
    mod = next(m for m in store.nodes if m.name == "app.core")
    md = render_module_page(store, mod, src="../../", page_of={}, deps=_deps(store))
    assert "### `Widget`" in md and "### `run`" in md  # anchorable symbol headings
    assert "[← Episteme](../README.md)" in md  # a way back
    assert "[`src/app/core.py`](../../src/app/core.py)" in md  # out to source


def test_module_page_shows_importers_from_symbol_targeted_edges() -> None:
    """The regression: `a` is imported by `b`, and its page must say so."""
    store = _import_store()
    mod_a = store.node("py:a")
    assert mod_a is not None
    md = render_module_page(store, mod_a, src="../../", page_of={"py:b": "b"}, deps=_deps(store))
    assert "## Imported by" in md and "[`b`](b.md)" in md
    assert "helper" not in md.split("## Imported by")[1]  # the module, not the symbol


def test_module_page_never_links_to_a_page_that_was_not_written() -> None:
    store = _import_store()
    mod_b = store.node("py:b")
    assert mod_b is not None
    md = render_module_page(store, mod_b, src="../../", page_of={}, deps=_deps(store))
    assert "## Imports" in md
    assert "(a.md)" not in md  # `a` has no page → must not link to one


def test_module_page_render_is_stable_across_runs() -> None:
    store = _store()
    mod = next(m for m in store.nodes if m.name == "app.core")
    deps = _deps(store)
    assert render_module_page(store, mod, src="../../", page_of={}, deps=deps) == render_module_page(
        store, mod, src="../../", page_of={}, deps=deps
    )


# --- phase 3: area pages, derived prose, scoped diagrams ------------------------


def test_collect_areas_lifts_module_imports_to_area_edges() -> None:
    store = _import_store()
    areas = collect_areas(store, _deps(store))
    assert areas["b"].imports == {"a"} and areas["a"].importers == {"b"}
    assert areas["a"].functions == 1


def test_area_prose_is_derived_and_names_the_role() -> None:
    """The deterministic tier: states what the graph knows, guesses at nothing."""
    store = _import_store()
    areas = collect_areas(store, _deps(store))
    total = len(areas)
    assert "foundation" in _area_prose(areas["a"], total)  # depended on, depends on none
    assert "leaf" in _area_prose(areas["b"], total)  # depends on others, nothing needs it


def test_area_diagram_never_emits_click_directives() -> None:
    """GitHub renders mermaid securityLevel=strict: `click` does nothing there, and can
    make it refuse the diagram outright. The linked legend navigates instead."""
    store = _import_store()
    areas = collect_areas(store, _deps(store))
    md = render_area_page(
        areas["b"], total_areas=2, src="../../", area_pages={"a": "a", "b": "b"}, module_pages={}
    )
    assert "```mermaid" in md and "click" not in md
    assert "**In the diagram:**" in md and "[`a`](a.md)" in md  # legend does the linking


def test_area_diagram_declares_a_mutual_neighbour_once() -> None:
    """An area that both imports and is imported by another appeared twice — one node
    line and one legend entry each. Both edges stay; the cycle is a real fact."""
    store = _import_store()
    batch = FactBatch()
    for n in store.nodes:
        batch.add_node(n)
    for e in store.edges_of_kind(EdgeKind.CONTAINS) + store.edges_of_kind(EdgeKind.IMPORTS):
        batch.add_edge(e)
    # ...and now `a` imports back from `b`, making it mutual.
    batch.add_edge(Edge(src="py:a", dst="py:b.use", kind=EdgeKind.IMPORTS))
    mutual = FactStore(batch)
    areas = collect_areas(mutual, _deps(mutual))
    md = render_area_page(areas["b"], total_areas=2, src="../../", area_pages={"a": "a"}, module_pages={})
    diagram = md.split("```mermaid")[1].split("```")[0]
    assert diagram.count('["a"]') == 1, "neighbour declared twice"
    assert "n1 --> n0" in diagram and "n0 --> n1" in diagram  # both directions kept
    legend = next(ln for ln in md.splitlines() if ln.startswith("**In the diagram:**"))
    assert legend.count("[`a`](a.md)") == 1  # legend lists it once
    # ...but the doc still names it under both dependency headings — that's the mutual
    # dependency stated from each end, not a duplicate.
    assert "## Depends on" in md and "## Depended on by" in md


def test_area_page_render_is_stable_across_runs() -> None:
    store = _import_store()
    areas = collect_areas(store, _deps(store))
    kw = {"total_areas": 2, "src": "../../", "area_pages": {"a": "a"}, "module_pages": {}}
    assert render_area_page(areas["b"], **kw) == render_area_page(areas["b"], **kw)  # type: ignore[arg-type]


def test_build_writes_area_pages_and_architecture_lists_areas(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir(parents=True)
    (tmp_path / "src" / "core.py").write_text("def helper() -> int:\n    return 1\n")
    build_memory_bank(tmp_path, refresh=True)
    assert list((tmp_path / "episteme" / "areas").glob("*.md")), "expected an area page"
    assert "## Areas" in (tmp_path / "episteme" / "architecture.md").read_text()


def test_build_writes_module_pages_and_architecture_links_down(tmp_path: Path) -> None:
    (tmp_path / "src" / "app").mkdir(parents=True)
    (tmp_path / "src" / "app" / "core.py").write_text(
        "class Widget:\n    pass\n\n\ndef run() -> int:\n    return 1\n"
    )
    result = build_memory_bank(tmp_path, refresh=True)
    pages = list((tmp_path / "episteme" / "modules").glob("*.md"))
    assert pages, "expected at least one module page"
    assert any(name.startswith("modules/") for name in result["files"])
    arch = (tmp_path / "episteme" / "architecture.md").read_text()
    assert "](modules/" in arch  # the map links down into the pages


def test_build_reaps_orphaned_module_pages(tmp_path: Path) -> None:
    """A page for code that no longer exists is worse than no page — it's a confident lie."""
    (tmp_path / "src" / "app").mkdir(parents=True)
    (tmp_path / "src" / "app" / "core.py").write_text("def run() -> int:\n    return 1\n")
    build_memory_bank(tmp_path, refresh=True)
    stale = tmp_path / "episteme" / "modules" / "app.deleted.md"
    stale.write_text("# gone\n")
    build_memory_bank(tmp_path, refresh=True)
    assert not stale.exists()
    assert (tmp_path / "episteme" / "modules" / "app.core.md").exists()


# --- phase 4: entities + blast radius -------------------------------------------


def _entity_store() -> FactStore:
    """A tiny schema: orders -> customers by FK, and a query file that touches both."""
    batch = FactBatch()
    p = Provenance(file="db/schema.sql", line=1)
    q = Provenance(file="db/queries.sql", line=1)
    for tid, name, line in (("sql:customers", "customers", 1), ("sql:orders", "orders", 6)):
        batch.add_node(
            Node(id=tid, kind=NodeKind.ENTITY, name=name, provenance=Provenance("db/schema.sql", line))
        )
    for fid, fname in (
        ("sql:customers.id", "customers.id"),
        ("sql:customers.email", "customers.email"),
        ("sql:orders.id", "orders.id"),
    ):
        batch.add_node(Node(id=fid, kind=NodeKind.FIELD, name=fname, provenance=p))
        batch.add_edge(Edge(src=fid.rsplit(".", 1)[0], dst=fid, kind=EdgeKind.CONTAINS))
    batch.add_edge(Edge(src="sql:orders", dst="sql:customers", kind=EdgeKind.REFERENCES, provenance=p))
    batch.add_node(Node(id="sql:db/queries.sql", kind=NodeKind.MODULE, name="db/queries.sql", provenance=q))
    batch.add_edge(Edge(src="sql:db/queries.sql", dst="sql:customers", kind=EdgeKind.READS, provenance=q))
    batch.add_edge(Edge(src="sql:db/queries.sql", dst="sql:orders", kind=EdgeKind.WRITES, provenance=q))
    return FactStore(batch)


def test_entity_page_renders_foreign_keys_from_both_ends() -> None:
    """`references_of` alone answers "what does this point at" and leaves the question a
    reader actually has — "who depends on me" — unanswerable."""
    store = _entity_store()
    pages = {"sql:customers": "customers", "sql:orders": "orders"}
    customers = store.node("sql:customers")
    assert customers is not None
    md = render_entity_page(store, customers, src="../../", entity_pages=pages, data=DataLayer(store))
    assert "## Referenced by" in md and "[`orders`](orders.md)" in md  # the missing half
    assert "## References" not in md  # customers points at nothing


def test_entity_page_strips_the_table_prefix_from_field_names() -> None:
    """Field names are table-qualified (`customers.id`); repeating the table on every
    row of its own page is noise."""
    store = _entity_store()
    customers = store.node("sql:customers")
    assert customers is not None
    md = render_entity_page(store, customers, src="../../", entity_pages={}, data=DataLayer(store))
    assert "- `email`" in md and "- `id`" in md
    assert "customers.email" not in md


def test_entity_page_names_the_code_that_reads_and_writes_it() -> None:
    store = _entity_store()
    data = DataLayer(store)
    orders = store.node("sql:orders")
    assert orders is not None
    md = render_entity_page(store, orders, src="../../", entity_pages={}, data=data)
    assert "## Written by" in md and "db/queries.sql" in md
    assert "## Read by" not in md  # queries.sql writes orders, reads customers


def test_entity_page_and_domain_model_are_stable_across_runs() -> None:
    store = _entity_store()
    ent = store.node("sql:orders")
    assert ent is not None
    a = render_entity_page(store, ent, src="../../", entity_pages={}, data=DataLayer(store))
    b = render_entity_page(store, ent, src="../../", entity_pages={}, data=DataLayer(store))
    assert a == b
    assert render_domain_model(store, src="../") == render_domain_model(store, src="../")


def test_domain_model_tabulates_entities_and_links_to_pages() -> None:
    store = _entity_store()
    md = render_domain_model(store, src="../", entity_pages={"sql:customers": "customers"})
    assert "| Table | Fields | References | Referenced by |" in md
    assert "[`customers`](entities/customers.md)" in md
    assert "`orders`" in md  # no page → named, not linked
    assert "(entities/orders.md)" not in md


def test_domain_model_falls_back_when_there_are_no_entities() -> None:
    md = render_domain_model(_store(), src="../")
    assert "No DB/ORM entities detected" in md and "Widget" in md


def test_hotspots_exclude_third_party_and_test_symbols() -> None:
    """The raw ranking is topped by `json.dumps` / `pytest.raises` called from tests. A
    blast radius for code you cannot change crowds out the code you can."""
    batch = FactBatch()
    p = Provenance(file="src/app/core.py", line=1)
    batch.add_node(Node(id="py:app.core", kind=NodeKind.MODULE, name="app.core", provenance=p))
    batch.add_node(Node(id="py:app.core.mine", kind=NodeKind.FUNCTION, name="mine", provenance=p))
    batch.add_edge(Edge(src="py:app.core", dst="py:app.core.mine", kind=EdgeKind.CONTAINS))
    # A third-party symbol (no provenance) with more callers than ours...
    batch.add_node(Node(id="py:json.dumps", kind=NodeKind.FUNCTION, name="dumps", external=True))
    # ...and a test helper, which is grounded but not architecture.
    tp = Provenance(file="tests/test_core.py", line=3)
    batch.add_node(Node(id="py:tests.test_core.helper", kind=NodeKind.FUNCTION, name="helper", provenance=tp))
    for i in range(5):
        cid = f"py:app.core.c{i}"
        batch.add_node(Node(id=cid, kind=NodeKind.FUNCTION, name=f"c{i}", provenance=p))
        batch.add_edge(Edge(src=cid, dst="py:json.dumps", kind=EdgeKind.CALLS, provenance=p))
        batch.add_edge(Edge(src=cid, dst="py:tests.test_core.helper", kind=EdgeKind.CALLS, provenance=p))
    batch.add_edge(Edge(src="py:app.core.c0", dst="py:app.core.mine", kind=EdgeKind.CALLS, provenance=p))
    store = FactStore(batch)
    md = render_architecture(store, summarise_store(store, top_n=50), greenfield=False, src="../")
    hotspots = md.split("## Call hotspots")[1]
    assert "`mine`" in hotspots
    assert "dumps" not in hotspots  # third-party: no provenance
    assert "helper" not in hotspots  # lives under tests/


def test_hotspots_report_transitive_blast_radius() -> None:
    """`call_count` is direct callers only and understates what a change reaches."""
    batch = FactBatch()
    p = Provenance(file="src/app/core.py", line=1)
    batch.add_node(Node(id="py:app.core", kind=NodeKind.MODULE, name="app.core", provenance=p))
    for name in ("target", "mid", "outer"):
        nid = f"py:app.core.{name}"
        batch.add_node(Node(id=nid, kind=NodeKind.FUNCTION, name=name, provenance=p))
        batch.add_edge(Edge(src="py:app.core", dst=nid, kind=EdgeKind.CONTAINS))
    batch.add_edge(Edge(src="py:app.core.mid", dst="py:app.core.target", kind=EdgeKind.CALLS, provenance=p))
    batch.add_edge(Edge(src="py:app.core.outer", dst="py:app.core.mid", kind=EdgeKind.CALLS, provenance=p))
    store = FactStore(batch)
    md = render_architecture(store, summarise_store(store, top_n=50), greenfield=False, src="../")
    # target has 1 direct caller but 2 symbols transitively depend on it.
    assert "1 call-sites · **reaches 2 symbols** (≤2 hops)" in md


def test_build_writes_entity_pages_and_reaps_them(tmp_path: Path) -> None:
    (tmp_path / "db").mkdir()
    (tmp_path / "db" / "schema.sql").write_text(
        "CREATE TABLE customers (id INTEGER PRIMARY KEY);\n"
        "CREATE TABLE orders (id INTEGER PRIMARY KEY, "
        "customer_id INTEGER REFERENCES customers(id));\n"
    )
    build_memory_bank(tmp_path, refresh=True)
    ents = tmp_path / "episteme" / "entities"
    assert (ents / "customers.md").is_file()
    assert "Referenced by" in (ents / "customers.md").read_text()
    stale = ents / "dropped_table.md"
    stale.write_text("# gone\n")
    build_memory_bank(tmp_path, refresh=True)
    assert not stale.exists()


def test_architecture_render_is_stable_across_runs() -> None:
    """Committed docs must diff clean: same facts in → byte-identical markdown out."""
    a = render_architecture(_store(), summarise_store(_store()), greenfield=False, src="../")
    b = render_architecture(_store(), summarise_store(_store()), greenfield=False, src="../")
    assert a == b


def test_architecture_greenfield_note() -> None:
    md = render_architecture(FactStore(FactBatch()), summarise_store(FactStore(FactBatch())), greenfield=True)
    assert "Greenfield" in md


def test_domain_model_falls_back_to_source_types() -> None:
    md = render_domain_model(_store())
    assert "`Widget`" in md and "TFix" not in md  # test fixtures excluded


def test_glossary_excludes_tests() -> None:
    md = render_glossary(_store())
    assert "Widget" in md and "TFix" not in md


def test_tech_context_table() -> None:
    md = render_tech_context(ProjectProfile.from_repo("."), greenfield=False)
    assert "| Languages |" in md and "| Test runner |" in md


def test_memory_bank_dir_override(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    assert memory_bank_dir(tmp_path) == tmp_path / "episteme"
    monkeypatch.setenv("ORCHESTRATOR_MEMORY_BANK_DIR", str(tmp_path / "mb"))
    assert memory_bank_dir(tmp_path) == tmp_path / "mb"


def test_existing_bank_dir_falls_back_to_legacy_memory_bank(tmp_path: Path) -> None:
    """Repos analysed before the rename must not silently report 'no knowledge'."""
    from orchestrator.knowledge.understand import existing_bank_dir

    assert existing_bank_dir(tmp_path) == tmp_path / "episteme"  # neither exists → canonical
    (tmp_path / "memory-bank").mkdir()
    assert existing_bank_dir(tmp_path) == tmp_path / "memory-bank"  # legacy present → read it
    (tmp_path / "episteme").mkdir()
    assert existing_bank_dir(tmp_path) == tmp_path / "episteme"  # canonical wins once built


def test_existing_bank_dir_override_beats_legacy(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """An explicit target must never be silently overridden by a stray legacy dir."""
    from orchestrator.knowledge.understand import existing_bank_dir

    (tmp_path / "memory-bank").mkdir()
    assert existing_bank_dir(tmp_path, out_dir=tmp_path / "x") == tmp_path / "x"
    monkeypatch.setenv("ORCHESTRATOR_MEMORY_BANK_DIR", str(tmp_path / "env"))
    assert existing_bank_dir(tmp_path) == tmp_path / "env"


def test_build_memory_bank_writes_files(tmp_path: Path) -> None:
    (tmp_path / "src" / "app").mkdir(parents=True)
    (tmp_path / "src" / "app" / "core.py").write_text(
        "class Widget:\n    pass\n\n\ndef run() -> int:\n    return 1\n"
    )
    result = build_memory_bank(tmp_path, refresh=True)
    mb = tmp_path / "episteme"
    assert mb.is_dir()
    for f in ("README.md", "architecture.md", "domain-model.md", "tech-context.md", "conventions.md"):
        assert (mb / f).is_file()
    assert not result["greenfield"]
    assert "Widget" in (mb / "domain-model.md").read_text()


def test_build_memory_bank_greenfield(tmp_path: Path) -> None:
    result = build_memory_bank(tmp_path, refresh=True)
    assert result["greenfield"] is True
    assert "Greenfield" in (tmp_path / "episteme" / "architecture.md").read_text()


def _repo_with_memory_bank(tmp_path: Path) -> Path:
    (tmp_path / "src" / "app").mkdir(parents=True)
    (tmp_path / "src" / "app" / "core.py").write_text("class Widget:\n    pass\n")
    build_memory_bank(tmp_path, refresh=True)
    return tmp_path


def test_memory_bank_grounding_includes_domain_knowledge(tmp_path: Path) -> None:
    from orchestrator.knowledge.access import memory_bank_grounding

    _repo_with_memory_bank(tmp_path)
    block = memory_bank_grounding(tmp_path)
    assert block.startswith("PROJECT KNOWLEDGE")
    assert "Widget" in block  # domain type surfaced


def test_memory_bank_grounding_absent(tmp_path: Path) -> None:
    from orchestrator.knowledge.access import memory_bank_grounding

    assert memory_bank_grounding(tmp_path) == ""


def test_read_memory_bank_sections_and_section(tmp_path: Path) -> None:
    from orchestrator.knowledge.access import read_memory_bank

    _repo_with_memory_bank(tmp_path)
    listing = read_memory_bank(tmp_path)
    assert listing["exists"] and "architecture.md" in listing["sections"]
    one = read_memory_bank(tmp_path, "domain-model")
    assert one["section"] == "domain-model.md" and "Widget" in one["content"]


def test_read_memory_bank_missing(tmp_path: Path) -> None:
    from orchestrator.knowledge.access import read_memory_bank

    assert read_memory_bank(tmp_path)["exists"] is False


def test_grounder_includes_memory_bank(tmp_path: Path) -> None:
    from orchestrator.sdlc.grounding import PKGCodegenGrounder

    _repo_with_memory_bank(tmp_path)
    grounder = PKGCodegenGrounder.from_repo(tmp_path, use_cache=False)
    ctx = grounder.context_for_spec({"title": "add a Widget feature", "summary": "work with Widget"})
    assert "PROJECT KNOWLEDGE" in ctx  # committed memory bank fed into codegen grounding
