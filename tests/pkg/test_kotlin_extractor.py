"""PKG: the Kotlin front-end maps Kotlin source onto the universal facts.

P1 comprehension — kotlin-support-roadmap.md §3.1, decisions D1-D7. The decisions
under test, spelled out because each one is a place a later change could quietly
regress:

* **D2** — ids carry Java's ``java:`` prefix while nodes carry
  ``language="kotlin"``. This is what makes a mixed Kotlin/Java Android module one
  graph instead of two; ``test_mixed_java_and_kotlin_share_one_namespace`` is the
  test that would fail if someone "tidied" the prefix to ``kt:``.
* **D4** — an extension function is a free function under the package module and
  the receiver is recorded nowhere.
* **D5** — companion members fold onto the enclosing type.
* **D6** — a constructor ``val``/``var`` is a Field; a bare constructor parameter
  is not; a top-level ``val`` is not.
* **D7** — one ``IMPLEMENTS`` edge kind for what Kotlin writes as one supertype
  list, with a ``finalize`` repoint for framework supertypes.

tree-sitter-kotlin is an optional extra, so these skip cleanly when it's absent.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from orchestrator.pkg import kotlin_extractor
from orchestrator.pkg.extractor import RepoCodeExtractor
from orchestrator.pkg.facts import EdgeKind, FactBatch, NodeKind
from orchestrator.pkg.kotlin_extractor import KotlinExtractor

pytest.importorskip("tree_sitter_kotlin", reason="install the 'kotlin' extra")

REPO_KT = """\
package com.shop.data

import com.shop.model.Topic
import com.shop.util.*
import com.shop.util.formatMoney
import kotlinx.coroutines.flow.Flow as Stream

typealias Callback = (Int) -> Unit

interface CartRepository {
    fun items(): List<Topic>
}

class OfflineCartRepository @Inject constructor(
    private val dao: CartDao,
    notALabel: String,
    var attempts: Int,
) : CartRepository, BaseRepository() {

    private val cache: MutableList<Topic> = mutableListOf()
    var label: String = "cart"

    override fun items(): List<Topic> = cache

    private fun reset() {}

    companion object Factory {
        const val ROUTE = "cart"
        fun create(): OfflineCartRepository = TODO()
    }

    class Nested {
        fun deep() = 1
    }
}

object CartRegistry {
    val size = 0
    fun register() {}
}

enum class SyncState { IDLE, RUNNING }

data class TopicEntity(val id: String, val title: String)

sealed interface Outcome

value class Money(val raw: Long)

annotation class Marker

fun topLevel(): Int = 1

fun String.slugify(): String = lowercase()

val TOP_LEVEL_CONSTANT = 7
"""


def _facts(tmp_path: Path, src: str = REPO_KT, name: str = "Cart.kt") -> FactBatch:
    f = tmp_path / name
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(src, encoding="utf-8")
    ex = KotlinExtractor()
    batch = ex.extract(path=f, module=ex.module_name(f, tmp_path), rel=name)
    return ex.finalize(batch)


def _repo_facts(tmp_path: Path, files: dict[str, str]) -> FactBatch:
    """Extract several files as one repository.

    Needed wherever the behaviour under test is about a *type declared in another
    file*, which since the deferred-call fix is the only way a same-package receiver
    resolves at all: an undeclared `CartDao` is no longer quietly invented under the
    caller's package, so a fixture that wants the edge has to declare the class the
    way the real code being modelled does.
    """
    ex = KotlinExtractor()
    batch = FactBatch()
    for name, src in files.items():
        f = tmp_path / name
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(src, encoding="utf-8")
        batch.merge(ex.extract(path=f, module=ex.module_name(f, tmp_path), rel=name))
    return ex.finalize(batch)


def _ids(batch: FactBatch, kind: NodeKind) -> set[str]:
    return {n.id for n in batch.nodes if n.kind is kind and not n.external}


def _edges(batch: FactBatch, kind: EdgeKind) -> set[tuple[str, str]]:
    return {(e.src, e.dst) for e in batch.edges if e.kind is kind}


# ---- D2/D3: module, prefix, language ---------------------------------------


def test_package_header_is_the_module(tmp_path: Path) -> None:
    batch = _facts(tmp_path)
    assert "java:com.shop.data" in _ids(batch, NodeKind.MODULE)


def test_ids_use_the_java_prefix_but_nodes_say_kotlin(tmp_path: Path) -> None:
    """D2: one JVM namespace, `language` is what distinguishes the front-end."""
    batch = _facts(tmp_path)
    kotlin_nodes = [n for n in batch.nodes if not n.external]
    assert kotlin_nodes, "expected grounded nodes"
    assert all(n.id.startswith("java:") for n in kotlin_nodes)
    assert all(n.language == "kotlin" for n in kotlin_nodes)


def test_file_without_a_package_falls_back_to_its_path(tmp_path: Path) -> None:
    """The `.kt` stays in the id: without it a root `Foo.kt` and `package Foo` share
    `java:Foo`, and a `class Bar` in each collapses into one node."""
    batch = _facts(tmp_path, "class Loose { fun go() {} }\n", "loose.kt")
    assert "java:loose.kt" in _ids(batch, NodeKind.MODULE)


def test_a_root_file_and_a_same_named_package_keep_distinct_types(tmp_path: Path) -> None:
    (tmp_path / "Foo.kt").write_text("class Bar { fun a() {} }\n")
    (tmp_path / "foo").mkdir()
    (tmp_path / "foo" / "Bar.kt").write_text("package Foo\nclass Bar { fun b() {} }\n")
    batch = RepoCodeExtractor(extractors=[KotlinExtractor()]).extract(tmp_path)
    types = {n.id for n in batch.nodes if n.kind is NodeKind.TYPE and n.grounded}
    assert {"java:Foo.kt.Bar", "java:Foo.Bar"} <= types


# ---- imports ----------------------------------------------------------------


def test_concrete_imports_get_edges_and_wildcards_do_not(tmp_path: Path) -> None:
    """A wildcard names no single symbol, so it must not invent an import target."""
    batch = _facts(tmp_path)
    imports = _edges(batch, EdgeKind.IMPORTS)
    assert ("java:com.shop.data", "java:com.shop.model.Topic") in imports
    assert ("java:com.shop.data", "java:com.shop.util.formatMoney") in imports
    assert ("java:com.shop.data", "java:com.shop.util") not in imports


def test_aliased_import_is_keyed_by_the_alias(tmp_path: Path) -> None:
    """`import a.b.Flow as Stream` — the file writes `Stream`, so resolution must too."""
    batch = _facts(tmp_path)
    assert (
        "java:com.shop.data",
        "java:kotlinx.coroutines.flow.Flow",
    ) in _edges(batch, EdgeKind.IMPORTS)


# ---- D5/types ---------------------------------------------------------------


def test_every_class_flavour_and_object_is_a_type(tmp_path: Path) -> None:
    types = _ids(_facts(tmp_path), NodeKind.TYPE)
    for name in (
        "CartRepository",  # interface
        "OfflineCartRepository",  # class
        "CartRegistry",  # object
        "SyncState",  # enum class
        "TopicEntity",  # data class
        "Outcome",  # sealed interface
        "Money",  # value class
        "Marker",  # annotation class
    ):
        assert f"java:com.shop.data.{name}" in types, name


def test_nested_type_is_qualified_by_its_outer_type(tmp_path: Path) -> None:
    types = _ids(_facts(tmp_path), NodeKind.TYPE)
    assert "java:com.shop.data.OfflineCartRepository.Nested" in types


def test_companion_members_fold_onto_the_enclosing_type(tmp_path: Path) -> None:
    """D5: a call site writes `OfflineCartRepository.create()`, never `Companion`."""
    batch = _facts(tmp_path)
    assert "java:com.shop.data.OfflineCartRepository.create" in _ids(batch, NodeKind.FUNCTION)
    assert "java:com.shop.data.OfflineCartRepository.ROUTE" in _ids(batch, NodeKind.FIELD)
    # The companion itself is not a type — neither under its own name nor `Companion`.
    types = _ids(batch, NodeKind.TYPE)
    assert "java:com.shop.data.OfflineCartRepository.Factory" not in types
    assert "java:com.shop.data.OfflineCartRepository.Companion" not in types


def test_enum_entries_are_fields_of_their_enum(tmp_path: Path) -> None:
    fields = _ids(_facts(tmp_path), NodeKind.FIELD)
    assert "java:com.shop.data.SyncState.IDLE" in fields
    assert "java:com.shop.data.SyncState.RUNNING" in fields


# ---- D4: top-level and extension functions ----------------------------------


def test_top_level_and_extension_functions_hang_off_the_module(tmp_path: Path) -> None:
    """D4: an extension is a free function; the receiver type does not own it."""
    batch = _facts(tmp_path)
    funcs = _ids(batch, NodeKind.FUNCTION)
    assert "java:com.shop.data.topLevel" in funcs
    assert "java:com.shop.data.slugify" in funcs
    # The receiver is recorded nowhere — no node, and no CONTAINS from `String`.
    assert "java:com.shop.data.String.slugify" not in funcs
    contains = _edges(batch, EdgeKind.CONTAINS)
    assert ("java:com.shop.data", "java:com.shop.data.slugify") in contains
    assert not any(src.endswith(".String") for src, _ in contains)


# ---- D6: fields -------------------------------------------------------------


def test_constructor_val_is_a_field_and_a_bare_parameter_is_not(tmp_path: Path) -> None:
    """D6: in Kotlin a constructor `val` *is* a property; a plain parameter is not."""
    fields = _ids(_facts(tmp_path), NodeKind.FIELD)
    assert "java:com.shop.data.OfflineCartRepository.dao" in fields
    assert "java:com.shop.data.OfflineCartRepository.attempts" in fields  # `var` counts too
    assert "java:com.shop.data.OfflineCartRepository.notALabel" not in fields


def test_body_properties_are_fields(tmp_path: Path) -> None:
    fields = _ids(_facts(tmp_path), NodeKind.FIELD)
    assert "java:com.shop.data.OfflineCartRepository.cache" in fields
    assert "java:com.shop.data.OfflineCartRepository.label" in fields


def test_top_level_property_is_not_a_field(tmp_path: Path) -> None:
    """A Field belongs to a Type — the corpus rule every front-end follows."""
    assert "java:com.shop.data.TOP_LEVEL_CONSTANT" not in _ids(_facts(tmp_path), NodeKind.FIELD)


def test_type_alias_declares_no_node(tmp_path: Path) -> None:
    batch = _facts(tmp_path)
    everything = {n.id for n in batch.nodes}
    assert "java:com.shop.data.Callback" not in everything


# ---- D7: inheritance --------------------------------------------------------


def test_one_implements_edge_for_both_supertype_spellings(tmp_path: Path) -> None:
    """Kotlin writes `: Base(), Iface` as one list and the graph keeps one edge kind."""
    implements = _edges(_facts(tmp_path), EdgeKind.IMPLEMENTS)
    src = "java:com.shop.data.OfflineCartRepository"
    assert (src, "java:com.shop.data.CartRepository") in implements  # same-package sibling
    assert (src, "java:BaseRepository") in implements  # repointed: nothing declares it


def test_finalize_repoints_an_undeclared_supertype_to_a_bare_external(tmp_path: Path) -> None:
    """A framework supertype must not be asserted into the file's own package."""
    batch = _facts(tmp_path, "package com.shop.ui\n\nclass Screen : ViewModel()\n", "Screen.kt")
    implements = _edges(batch, EdgeKind.IMPLEMENTS)
    assert ("java:com.shop.ui.Screen", "java:ViewModel") in implements
    assert ("java:com.shop.ui.Screen", "java:com.shop.ui.ViewModel") not in implements
    external = {n.id for n in batch.nodes if n.external}
    assert "java:ViewModel" in external


def test_imported_supertype_resolves_through_the_import(tmp_path: Path) -> None:
    src = "package com.shop.ui\n\nimport androidx.lifecycle.ViewModel\n\nclass Screen : ViewModel()\n"
    implements = _edges(_facts(tmp_path, src, "Screen.kt"), EdgeKind.IMPLEMENTS)
    assert ("java:com.shop.ui.Screen", "java:androidx.lifecycle.ViewModel") in implements


# ---- #396: a same-file single-line body collapses the parse, not IMPLEMENTS -----


def test_a_same_line_interface_body_no_longer_loses_the_whole_file(tmp_path: Path) -> None:
    """#396 as filed: `interface Iface { fun f() }` immediately followed by another

    single-line declaration in the same file. The bug wasn't in `IMPLEMENTS`
    resolution (same-file vs. cross-file makes no difference to `_resolve_type`) —
    it was that this exact shape is a `tree-sitter-kotlin` 1.1.0 scanner ambiguity
    whose `ERROR` loses every declaration after it, not just the `IMPLEMENTS` edge.
    `_recover_collapsed_parse` detects the collapse and moves the body's closing brace
    onto its own line before re-parsing.
    """
    batch = _facts(
        tmp_path,
        "package app\ninterface Iface { fun f() }\nclass C : Iface { override fun f() {} }\n",
        "All.kt",
    )
    ids = {n.id for n in batch.nodes}
    assert {"java:app.Iface", "java:app.Iface.f", "java:app.C", "java:app.C.f"} <= ids
    assert ("java:app.C", "java:app.Iface") in _edges(batch, EdgeKind.IMPLEMENTS)


def test_the_recovered_parse_reports_original_line_numbers(tmp_path: Path) -> None:
    """Splitting a line to unblock the parser must not corrupt provenance — a reader

    following `file:line` needs to land on the declaration in the *actual* file on
    disk, not in a buffer that only ever existed in memory.
    """
    batch = _facts(
        tmp_path,
        "package app\ninterface Iface { fun f() }\nclass C : Iface { override fun f() {} }\n",
        "All.kt",
    )
    by_id = {n.id: n for n in batch.nodes}
    assert by_id["java:app.Iface"].provenance is not None
    assert by_id["java:app.Iface"].provenance.line == 2
    assert by_id["java:app.C"].provenance is not None
    assert by_id["java:app.C"].provenance.line == 3


def test_a_normally_parsing_file_takes_no_recovery_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The recovery attempt only runs when the initial parse already has an `ERROR` —

    idiomatic, multi-line Kotlin (the overwhelming majority of real source) is
    completely untouched, same file bytes, same tree, same line numbers as always.
    The brace scanner is the first thing a real recovery calls, so making it raise
    proves the path was not taken rather than merely that its result was harmless.
    """

    def never(_source: bytes) -> list[tuple[int, int]]:
        raise AssertionError("recovery ran on a file that parses cleanly")

    monkeypatch.setattr(kotlin_extractor, "_same_line_brace_pairs", never)
    src = (
        "package app\n\ninterface Iface {\n    fun f()\n}\n\nclass C : Iface {\n    override fun f() {}\n}\n"
    )
    batch = _facts(tmp_path, src, "All.kt")
    assert ("java:app.C", "java:app.Iface") in _edges(batch, EdgeKind.IMPLEMENTS)
    by_id = {n.id: n for n in batch.nodes}
    assert by_id["java:app.Iface"].provenance is not None
    assert by_id["java:app.Iface"].provenance.line == 3
    assert by_id["java:app.C"].provenance is not None
    assert by_id["java:app.C"].provenance.line == 7


def test_an_unrecoverable_parse_error_still_degrades_to_nothing_not_a_crash(tmp_path: Path) -> None:
    """A genuine syntax error (not this one specific ambiguity) must not raise —

    recovery gives up cleanly and extraction proceeds exactly as it did before this
    fix existed: the module node, nothing else.
    """
    batch = _facts(tmp_path, "package app\nclass C {{{{ not valid kotlin ]][[\n", "Broken.kt")
    assert {n.id for n in batch.nodes} == {"java:app"}


def test_a_very_deep_expression_does_not_abort_extraction(tmp_path: Path) -> None:
    """A recursive ERROR walk raised `RecursionError` at ~600 terms, which
    `RepoCodeExtractor` does not catch — one generated file aborted the whole repo."""
    src = "package app\nval s = " + " + ".join(['"a"'] * 3000) + "\nclass After\n"
    batch = _facts(tmp_path, src, "Deep.kt")
    assert "java:app.After" in {n.id for n in batch.nodes}


def test_an_unrelated_syntax_error_spends_no_parse_on_distant_brace_pairs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only a brace pair overlapping an `ERROR` is tried. Trying every pair was
    quadratic — 190 s on 4,000 such lines, for a recovery that could never succeed."""
    real = kotlin_extractor._kotlin_parser()
    parses = 0

    class Counting:
        def parse(self, source: bytes) -> object:
            nonlocal parses
            parses += 1
            return real.parse(source)

    monkeypatch.setattr(kotlin_extractor, "_kotlin_parser", Counting)
    lines = "".join(f"fun f{k}(x: Int) {{ println(x + {k}) }}\n" for k in range(2000))
    _facts(tmp_path, "package app\n" + lines + "fun broken( {\n", "Many.kt")
    assert parses <= 1 + kotlin_extractor._RECOVERY_PARSE_BUDGET


def test_two_collapses_in_one_file_are_both_recovered(tmp_path: Path) -> None:
    src = (
        "package app\ninterface A { fun a() }\nclass B { fun b() {} }\nclass C : A { override fun a() {} }\n"
    )
    batch = _facts(tmp_path, src, "Two.kt")
    by_id = {n.id: n for n in batch.nodes}
    assert {"java:app.A", "java:app.B", "java:app.C"} <= set(by_id)
    lines = [p.line for p in (by_id[i].provenance for i in ("java:app.A", "java:app.B", "java:app.C")) if p]
    assert lines == [2, 3, 4]
    assert ("java:app.C", "java:app.A") in _edges(batch, EdgeKind.IMPLEMENTS)


def test_many_collapses_in_one_file_are_all_recovered(tmp_path: Path) -> None:
    """The old 20-pass cap made recovery all-or-nothing: 21 one-line bodies lost the file."""
    bodies = "".join(f"interface I{k} {{ fun f() }}\n" for k in range(25))
    batch = _facts(tmp_path, "package app\n" + bodies + "class Tail\n", "Many.kt")
    assert "java:app.Tail" in {n.id for n in batch.nodes}


def test_braces_inside_strings_and_comments_are_not_structure() -> None:
    src = b'val a = "{ }"\nval b = \'{\'\n// { }\n/* { } */\nval c = """{ }"""\nfun f() { g() }\n'
    pairs = kotlin_extractor._same_line_brace_pairs(src)
    assert [src[o : c + 1] for o, c in pairs] == [b"{ g() }"]


def test_facts_held_until_finalize_keep_original_lines_after_recovery(tmp_path: Path) -> None:
    """Compose routes are emitted in `finalize` from state held on the extractor, which
    the batch remap never reached — every such fact came out one line late."""
    src = (
        "package shop.nav\n"
        "import androidx.navigation.compose.composable\n"
        "interface Iface { fun f() }\n"
        "fun graph() {\n"
        '    composable(route = "home_route") { Home() }\n'
        "}\n"
        "fun Home() {}\n"
        "fun go() {\n"
        '    navigate("home_route")\n'
        "}\n"
    )
    batch = _facts(tmp_path, src, "Nav.kt")
    endpoint = next(n for n in batch.nodes if n.kind is NodeKind.ENDPOINT)
    assert endpoint.provenance is not None and endpoint.provenance.line == 5
    consumes = next(e for e in batch.edges if e.kind is EdgeKind.CONSUMES)
    assert consumes.provenance is not None and consumes.provenance.line == 9


# ---- suffixes and the .kts exclusion ----------------------------------------


def test_kts_build_scripts_are_not_kotlin_source(tmp_path: Path) -> None:
    """D11: a Gradle script is a DSL. Parsing it as source invents a module per script."""
    assert KotlinExtractor().suffixes == (".kt",)
    (tmp_path / "build.gradle.kts").write_text(
        'plugins { id("com.android.library") }\ndependencies { implementation(project(":core")) }\n'
    )
    batch = RepoCodeExtractor().extract(tmp_path)
    assert [n for n in batch.nodes if n.language == "kotlin"] == []


# ---- D8/§3.2: CALLS, the typed-receiver language ----------------------------

CALLS_KT = """\
package com.shop.data

import com.shop.net.ApiClient
import com.shop.util.formatMoney

class CartService(private val dao: CartDao, private val api: ApiClient) {

    private val label: String = "cart"

    fun checkout(topic: Topic, amount: Int) {
        dao.load()                    // typed constructor property → the main event
        api.fetch()                   // typed property, imported type
        topic.render()                // typed parameter
        this.audit()                  // explicit this
        audit()                       // bare sibling
        Registry.register()           // object / companion member
        Receipt("r1")                 // constructor
        topic.slugify()               // same-file extension, receiver matches
        formatMoney(amount)           // imported function
        helper()                      // same-package, another file → skipped
        val untyped = makeThing()
        untyped.go()                  // inferred receiver → never
        listOf(1).map { it.toLong() } // lambda/`it` → never
    }

    fun audit() {}

    companion object {
        fun boot() {}
    }
}

class Receipt(val id: String)

object Registry {
    fun register() {}
}

// Declared, because a receiver's type has to be *known* for the call through it to
// land: a type nothing in the repository declares is no longer invented under the
// caller's package (see `_DeferredCall`).
class CartDao {
    fun load() {}
}

class Topic {
    fun render() {}
}

fun Topic.slugify(): String = ""
"""


def _calls(batch: FactBatch) -> set[tuple[str, str]]:
    return _edges(batch, EdgeKind.CALLS)


def test_typed_receivers_resolve_exactly(tmp_path: Path) -> None:
    """D8, the reason P2 is early: Kotlin declares every property and parameter type."""
    calls = _calls(_facts(tmp_path, CALLS_KT, "CartService.kt"))
    caller = "java:com.shop.data.CartService.checkout"
    assert (caller, "java:com.shop.data.CartDao.load") in calls  # constructor property
    assert (caller, "java:com.shop.net.ApiClient.fetch") in calls  # through the import
    assert (caller, "java:com.shop.data.Topic.render") in calls  # typed parameter


def test_bare_this_and_static_calls_resolve(tmp_path: Path) -> None:
    calls = _calls(_facts(tmp_path, CALLS_KT, "CartService.kt"))
    caller = "java:com.shop.data.CartService.checkout"
    assert (caller, "java:com.shop.data.CartService.audit") in calls  # both `audit()` forms
    assert (caller, "java:com.shop.data.Registry.register") in calls
    assert (caller, "java:com.shop.data.Receipt") in calls  # instantiation is a call to the type
    assert (caller, "java:com.shop.util.formatMoney") in calls  # imported function


def test_extension_call_resolves_to_the_free_function_not_the_receiver(tmp_path: Path) -> None:
    """D4 again, from the call side: `topic.slugify()` is not a member of `Topic`."""
    calls = _calls(_facts(tmp_path, CALLS_KT, "CartService.kt"))
    caller = "java:com.shop.data.CartService.checkout"
    assert (caller, "java:com.shop.data.slugify") in calls
    assert (caller, "java:com.shop.data.Topic.slugify") not in calls


def test_inference_shaped_calls_are_never_emitted(tmp_path: Path) -> None:
    """The precision guard: no target is better than a guessed one."""
    calls = _calls(_facts(tmp_path, CALLS_KT, "CartService.kt"))
    targets = {dst for _, dst in calls}
    assert not any(t.endswith(".go") for t in targets)  # unannotated `val untyped`
    assert not any(t.endswith(".toLong") for t in targets)  # `it` inside a lambda
    # A same-package function declared in *another* file has no finalize backstop,
    # so it is skipped rather than guessed into existence (§3.2 row 1).
    assert "java:com.shop.data.helper" not in targets


def test_a_local_binding_shadows_a_bare_call(tmp_path: Path) -> None:
    """D9: unlike Java, a Kotlin local can shadow a call — so neither is claimed."""
    src = """\
package com.shop.data

class Runner {
    fun go() {
        val helper = ::other
        helper()
    }
    fun helper() {}
    fun other() {}
}
"""
    calls = _calls(_facts(tmp_path, src, "Runner.kt"))
    assert ("java:com.shop.data.Runner.go", "java:com.shop.data.Runner.helper") not in calls


def test_safe_call_resolves_like_a_plain_one(tmp_path: Path) -> None:
    calls = _calls(
        _repo_facts(
            tmp_path,
            {
                "CartDao.kt": """\
package com.shop.data

class CartDao {
    fun fetch() {}
}
""",
                "Repo.kt": """\
package com.shop.data

class Repo(private val dao: CartDao) {
    fun load() {
        dao?.fetch()
    }
}
""",
            },
        )
    )
    assert ("java:com.shop.data.Repo.load", "java:com.shop.data.CartDao.fetch") in calls


def test_companion_call_folds_onto_the_class(tmp_path: Path) -> None:
    """D5 from the call side: `CartService.boot()` names the class, not `Companion`."""
    calls = _calls(
        _repo_facts(
            tmp_path,
            {
                "CartService.kt": """\
package com.shop.data

class CartService {
    companion object {
        fun boot() {}
    }
}
""",
                "Boot.kt": """\
package com.shop.data

class Boot {
    fun run() {
        CartService.boot()
    }
}
""",
            },
        )
    )
    assert ("java:com.shop.data.Boot.run", "java:com.shop.data.CartService.boot") in calls


# ---- D2, the payoff ---------------------------------------------------------


def test_mixed_java_and_kotlin_share_one_namespace(tmp_path: Path) -> None:
    """The reason D2 exists: one graph for the normal Android layout, not two.

    A Kotlin class extending a Java interface declared in the same package must
    produce an ``IMPLEMENTS`` edge that lands on the Java node — which only works
    because both front-ends mint ``java:com.shop.model.*`` ids.
    """
    pytest.importorskip("tree_sitter_java", reason="install the 'java' extra")
    src = tmp_path / "src" / "main" / "java" / "com" / "shop" / "model"
    src.mkdir(parents=True)
    (src / "Priced.java").write_text("package com.shop.model;\n\npublic interface Priced {}\n")
    (src / "Cart.kt").write_text("package com.shop.model\n\nclass Cart : Priced\n")

    batch = RepoCodeExtractor().extract(tmp_path)
    by_id = {n.id: n for n in batch.nodes}
    assert by_id["java:com.shop.model.Priced"].language == "java"
    assert by_id["java:com.shop.model.Cart"].language == "kotlin"
    assert ("java:com.shop.model.Cart", "java:com.shop.model.Priced") in {
        (e.src, e.dst) for e in batch.edges if e.kind is EdgeKind.IMPLEMENTS
    }
    # One module, declared once, holding both files' declarations.
    assert by_id["java:com.shop.model"].kind is NodeKind.MODULE
