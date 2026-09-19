"""Kotlin front-end for the PKG extractor (the 11th language front-end).

Maps Kotlin source onto the universal ``facts`` vocabulary, like every other
front-end. Parsing is via tree-sitter (``tree-sitter-kotlin``), an OPTIONAL
dependency behind the ``kotlin`` extra; the import is lazy so the base install
stays stdlib-only and importing this module never fails.

**Ids share Java's ``java:`` prefix — this is deliberate, and load-bearing**
(docs/specs/kotlin-support-roadmap.md D2). Kotlin and Java share one JVM package
namespace: a Kotlin ``import com.x.Y`` names the same class whether ``Y`` is
declared in ``Y.kt`` or ``Y.java``, and it cannot be both. Sharing the prefix
means a mixed repository — which is the normal Android layout, 193 of the 263
files in the validation app sit under ``src/main/java/**`` — produces **one**
graph rather than two disjoint ones, ``FactBatch`` dedup upgrades whichever
front-end declared a placeholder first, and Java's dotted-prefix import join
works unchanged. Nodes carry ``language="kotlin"``, which is what ``pkg
accuracy`` and the capability matrix key on; the prefix is a namespace, not a
language tag. Precedent: ``ts:`` already covers ``.ts``, ``.tsx``, ``.js``, ``.jsx``.

Emits the high-confidence declaration subset, precision-first:

* ``Module`` — the ``package`` header, falling back to the repo-relative path
  for the 14 of 263 files that declare none (D3, the Java rule).
* ``Type`` — ``class`` in every flavour (data / sealed / enum / value /
  annotation / interface) and ``object``. A ``companion object``'s members fold
  onto the enclosing type (D5): call sites name the class, never ``Companion``.
* ``Function`` — members, and top-level functions **including extensions**. An
  extension ``fun T.name()`` is a free function under the package module and the
  receiver is recorded nowhere (D4): the receiver type does not own the
  extension, and attaching it would hang a ``CONTAINS`` edge off a type declared
  in another module or in the SDK.
* ``Field`` — ``val``/``var`` properties in a type body **and** ``val``/``var``
  primary-constructor parameters, because in Kotlin a constructor ``val`` *is* a
  property (D6). A bare constructor parameter without ``val``/``var`` is not a
  field. Top-level ``val``s are not fields either — a ``Field`` belongs to a
  ``Type``. Enum entries are fields of their enum.
* ``IMPORTS`` / ``CONTAINS`` / ``IMPLEMENTS``. Kotlin does not distinguish
  ``extends`` from ``implements`` syntactically and neither does the edge (D7);
  a same-package guess that nothing declares is repointed in ``finalize``, the
  C#/PHP pattern.

``.kts`` build scripts are **not** this front-end's business — they are a DSL
whose "functions" are Gradle configuration, and parsing them as Kotlin source
would invent a phantom component per module. They get a dedicated reader
(``gradle_extractor.py``, D11).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from orchestrator.pkg.extractor import rel_module_name
from orchestrator.pkg.facts import Edge, EdgeKind, FactBatch, Node, NodeKind, Provenance
from orchestrator.pkg.finalize_names import declared_ids, resolve_or_drop
from orchestrator.pkg.kotlin_di import read_module
from orchestrator.pkg.kotlin_http import (
    ClientState,
    PendingCall,
    base_url_path,
    join_to_endpoints,
    scan_type,
)
from orchestrator.pkg.kotlin_kmp import id_suffix as kmp_id_suffix
from orchestrator.pkg.kotlin_kmp import link_actuals, source_set_of
from orchestrator.pkg.kotlin_names import string_constants
from orchestrator.pkg.kotlin_nav import NavState
from orchestrator.pkg.kotlin_nav import collect_consts as collect_route_consts
from orchestrator.pkg.kotlin_nav import emit as emit_nav_routes
from orchestrator.pkg.kotlin_nav import scan_calls as scan_nav_calls
from orchestrator.pkg.kotlin_room import (
    read_dao,
    read_entity,
    read_relation_view,
    repoint_table_edges,
)
from orchestrator.pkg.kotlin_routes import (
    KtorState,
    read_controller,
    register_module,
    spring_resolver,
)
from orchestrator.pkg.kotlin_routes import emit as emit_ktor_routes
from orchestrator.pkg.kotlin_routes import scan_calls as scan_ktor_calls

if TYPE_CHECKING:
    from tree_sitter import Node as TSNode

# Kotlin's package header has no trailing semicolon, unlike Java's.
_PACKAGE_RE = re.compile(r"^\s*package\s+([\w.]+)", re.M)

#: Declarations that produce a ``Type``. ``class_declaration`` covers `class`,
#: `interface`, `data class`, `sealed class/interface`, `enum class`, `value
#: class` and `annotation class` — the grammar spells them all the same way and
#: puts the flavour in `modifiers`, which the graph does not record.
_TYPE_DECLS = frozenset({"class_declaration", "object_declaration"})

#: Bodies a type's members can live in. `enum class` uses its own body node.
_TYPE_BODIES = frozenset({"class_body", "enum_class_body"})

_LANG = "kotlin"

#: ``kotlin.*`` scope functions — extensions on their receiver, never a member of it.
#: A *certain* (imported) receiver type has no repo-declared member list to refuse
#: these against, so ``_settle_calls`` checks the member name against this set before
#: minting an external placeholder (#389; declared receivers are already covered by the
#: ``resolve_or_drop``/``declared_ids`` check above it).
#:
#: **The name alone is not enough**, and that was the first fix's defect: ``run``,
#: ``apply`` and ``use`` are also genuine members of real library types, so refusing on
#: the name cost ``java.lang.Runnable.run``, ``java.util.TimerTask.run``,
#: ``org.gradle.api.Project.apply`` and every ``java.time`` ``with`` — measured at two
#: dropped edges on a three-call probe. The set is therefore paired with
#: ``_passes_function``: a scope function is *given* a function, a same-named member is
#: not. Kotlin's own ``with(x) { }`` is absent deliberately — it is a top-level
#: function, so it reaches ``_settle_calls`` as a bare callee with ``certain=False`` and
#: is dropped a line earlier; listing it could only ever match a real member.
_SCOPE_FUNCTIONS = frozenset(
    {
        "let",
        "run",
        "also",
        "apply",
        "takeIf",
        "takeUnless",
        "use",
        "runCatching",
    }
)


@dataclass
class _ImportContext:
    """Kotlin imports needed for precise type resolution.

    ``by_simple`` maps a simple name to its fully-qualified one, honouring
    ``import a.b.C as D`` (the alias is what the file uses, so the alias is the
    key). ``wildcard_prefixes`` keeps ``import a.b.*`` for resolution only — a
    wildcard names no single symbol, so it gets no ``IMPORTS`` edge.
    """

    by_simple: dict[str, str] = field(default_factory=dict)
    wildcard_prefixes: set[str] = field(default_factory=set)


@dataclass(frozen=True)
class _DeferredCall:
    """A ``recv.name()`` held back until every declaration in the repository is known.

    A call through a *typed receiver* cannot be judged from one file. Two things are
    unknowable there, and both were fabrications before this existed
    (docs/specs/kotlin-support-roadmap.md §11):

    * **Which package the receiver's type is in.** ``_resolve_type`` falls back to the
      enclosing package for any bare name it cannot place, which is right for a sibling
      and wrong for everything else — including every one of Kotlin's default imports.
      ``s.uppercase()`` in ``package app.ui`` became a call to ``java:app.ui.String.uppercase``,
      a class that does not exist in any package.
    * **Whether the named member exists at all.** ``topic.let { }`` resolved onto
      ``java:app.data.Topic.let``, and ``let`` is not a member of ``Topic`` — it is one of
      the scope functions §3.2 lists under "never".

    Both used to reach the graph as an ``external`` placeholder node plus an edge, so
    ``pkg verify`` saw nothing dangling and reported clean. Deferring instead lets
    ``finalize`` ask the question that settles most of it: does the repository declare
    this? That question has no answer when the receiver's type is **imported rather
    than repo-declared** — ``modifier.let { }`` on an imported ``Modifier`` has nothing
    to check "does the repo declare this" against, so ``_settle_calls`` pairs the member
    name (``_SCOPE_FUNCTIONS``) with the call's *shape* (``takes_function_argument``)
    before minting a placeholder (#389).

    What this still cannot settle is named honestly rather than claimed closed: an
    imported receiver's real members are unknowable, so ``m.padding(8)`` is only known
    *not* to be ``Modifier.padding`` when the file imports the extension by that name
    (``imported_extension``). Where it does not — a wildcard import, or a genuine
    member — the receiver-member reading stands, and it may be a fabrication.
    """

    src: str
    #: Method ids to try, in priority order. The first one already grounded wins.
    candidates: tuple[str, ...]
    #: The type id each candidate hangs off, in the same order as ``candidates``.
    owners: tuple[str, ...]
    #: Whether the receiver's type was **read from the source** (an explicit import or a
    #: qualified name) rather than assumed. Only a certain type may back an external
    #: placeholder: its fully-qualified name is what the file actually says, so a call
    #: into a library lands rather than dangling. A guess has no such backstop.
    certain: bool
    #: Whether the call site hands the callee a function — a trailing lambda, or a lone
    #: lambda/callable-reference argument. A scope function always does; a same-named
    #: member such as ``Runnable.run()`` does not. See ``_passes_function``.
    takes_function_argument: bool
    #: The fully-qualified id of an extension this file imports under the called name,
    #: or ``""``. Written in the source, so it outranks the receiver-member guess as the
    #: external placeholder (see ``_settle_calls``).
    imported_extension: str
    provenance: Provenance


@dataclass
class _Pending:
    """A function body held back until every declaration in the file is known."""

    func_id: str
    owner: str | None  # enclosing type id; None for a top-level function
    body: TSNode
    params: dict[str, str]  # parameter name → its declared type, as written
    #: whether this is a `fun Route.x()` — a Ktor route module, so its body is
    #: already in route context even though no `routing { … }` encloses it (D16)
    route_module: bool = False
    #: the extension receiver type as written (`fun NavController.x()` → `NavController`),
    #: which is what `this` denotes inside the body of a top-level extension
    receiver: str = ""


@dataclass
class _FileContext:
    """The resolver table for one file — built in pass 1, read in pass 2.

    Kotlin declares the type of every property and every parameter, which is the
    whole reason typed-receiver resolution is P2 here rather than a late phase as
    it was for PHP and TypeScript: ``dao.getTopics()`` resolves *exactly* through
    the import map, with no inference anywhere. These tables are what make that a
    lookup instead of a guess.
    """

    package: str
    imports: _ImportContext
    #: type id → the member names it declares, companion members folded in (D5)
    type_members: dict[str, set[str]] = field(default_factory=dict)
    #: type id → {property name: its declared type as written} — the typed receivers
    field_types: dict[str, dict[str, str]] = field(default_factory=dict)
    #: names of plain top-level functions declared in this file
    top_level_funcs: set[str] = field(default_factory=set)
    #: extension name → the receiver type it extends, for `x.ext()` resolution (D4)
    extensions: dict[str, str] = field(default_factory=dict)
    #: simple names of types declared in this file, for constructor calls
    local_types: set[str] = field(default_factory=set)
    #: `const val NAME = "literal"` declared in this file — Room table names use them
    constants: dict[str, str] = field(default_factory=dict)
    pending: list[_Pending] = field(default_factory=list)
    #: the path from a literal Retrofit ``baseUrl(...)`` in this file, if any (D10)
    base_path: str = ""
    #: the Gradle source set this file sits in — `androidMain`, `main`, … (D17)
    source_set: str = ""

    def members_of(self, type_id: str | None) -> set[str]:
        return self.type_members.get(type_id or "", set())


class KotlinExtractor:
    """Kotlin front-end (tree-sitter). Install the ``kotlin`` extra to use it."""

    language: str = _LANG
    suffixes: tuple[str, ...] = (".kt",)

    def __init__(self) -> None:
        # Retrofit calls accumulate across the walk and are joined to endpoints in
        # ``finalize``, because the endpoint they call may be declared in another
        # file — or, for the cross-repo join, in another repository (D10).
        self._client = ClientState()
        #: Calls that matched no endpoint here. **A side-channel, never facts** —
        #: ``RepoCodeExtractor`` collects this duck-typed attribute and ``pkg joins``
        #: proposes them against other repositories' endpoints.
        self.unresolved_calls: list[PendingCall] = []
        # Compose routes are named once and imported, so a route constant is almost
        # always in a different file from the `composable` using it (D14).
        self._nav = NavState()
        # Ktor route modules are mounted by their caller, usually in another file,
        # so a route's full path is only known once the whole tree is read (D16).
        self._ktor = KtorState()
        # Calls through a typed receiver, judged in `finalize` — see `_DeferredCall`.
        self._deferred: list[_DeferredCall] = []

    def module_name(self, path: Path, root: Path) -> str:
        # Kotlin's module is the package declaration, which lives in the file and
        # is free of the directory layout Java enforces; fall back to the
        # repo-relative path when there is none (14 of 263 in the validation app).
        try:
            m = _PACKAGE_RE.search(path.read_text(encoding="utf-8"))
        except OSError:
            m = None
        return m.group(1) if m else rel_module_name(path, root)

    def extract(self, *, path: Path, module: str, rel: str) -> FactBatch:
        parser = _kotlin_parser()
        source = path.read_bytes()
        tree = parser.parse(source)
        batch = FactBatch()
        module_id = f"java:{module}" if module else "java:<root>"
        batch.add_node(Node(module_id, NodeKind.MODULE, module or rel, _LANG, Provenance(rel, 1)))

        imports = self._imports(tree.root_node, module_id, source, rel, batch)
        ctx = _FileContext(package=module, imports=imports, source_set=source_set_of(rel))
        # Retrofit puts the host in the builder, not the annotations, so the base
        # path (when it is a literal at all) has to be read before the interfaces.
        ctx.base_path = base_url_path(tree.root_node, source)
        ctx.constants = string_constants(tree.root_node, source)
        collect_route_consts(tree.root_node, source, self._nav, package=module)

        # Pass 1 — every declaration, and the resolver table that describes them.
        for node in tree.root_node.named_children:
            if node.type in _TYPE_DECLS:
                self._emit_type(node, module_id, ctx, source, rel, batch)
            elif node.type == "function_declaration":
                # Top level, so a free function or an extension — same node either
                # way (D4). The receiver, when there is one, is recorded nowhere.
                self._emit_function(node, module_id, None, ctx, source, rel, batch)
            elif node.type == "type_alias":
                # No node — it names no new declaration. Recorded so `Cb` resolves.
                alias = _field_text(node, "type", source)
                if alias:
                    ctx.local_types.add(alias)
            # A top-level `property_declaration` is intentionally not a Field: a
            # Field belongs to a Type (D6).

        # Pass 2 — calls, once every id in the file is known (D8, §3.2).
        for pend in ctx.pending:
            self._calls(pend, ctx, source, rel, batch)
            scan_nav_calls(
                pend.body,
                pend.func_id,
                source,
                rel,
                self._nav,
                package=module,
                imports=ctx.imports.by_simple,
            )
            scan_ktor_calls(
                pend.body,
                source,
                rel,
                self._ktor,
                owner=pend.func_id if pend.route_module else None,
                package=module,
                imports=ctx.imports.by_simple,
            )
        return batch

    # ---- declarations -------------------------------------------------------

    def _imports(
        self, root: TSNode, module_id: str, source: bytes, rel: str, batch: FactBatch
    ) -> _ImportContext:
        """Emit concrete ``IMPORTS`` edges; retain wildcards and aliases for resolution."""
        ctx = _ImportContext()
        for node in root.named_children:
            if node.type != "import":
                continue
            children = node.named_children
            if not children:
                continue
            fqn = _text(children[0], source)
            if not fqn:
                continue
            if _text(node, source).rstrip().endswith("*"):
                # `import a.b.*` — the qualified_identifier is the prefix, and no
                # single symbol is named, so resolution keeps it and the graph
                # gets no edge for a thing that was never imported.
                ctx.wildcard_prefixes.add(fqn)
                continue
            # `import a.b.C as D` puts the alias in a trailing identifier. The
            # alias is the name this file actually writes, so it is the key.
            alias = _text(children[1], source) if len(children) > 1 else ""
            ctx.by_simple[alias or fqn.rsplit(".", 1)[-1]] = fqn
            tid = f"java:{fqn}"
            batch.add_node(Node(tid, NodeKind.MODULE, fqn, _LANG, external=True))
            batch.add_edge(Edge(module_id, tid, EdgeKind.IMPORTS, Provenance(rel, node.start_point[0] + 1)))
        return ctx

    def _emit_type(
        self,
        node: TSNode,
        parent_id: str,
        ctx: _FileContext,
        source: bytes,
        rel: str,
        batch: FactBatch,
    ) -> None:
        name = _field_text(node, "name", source)
        if not name:
            return  # an anonymous `object : Foo {}` declares no named type
        # An `actual` declares the *same* package-qualified name as its `expect`, so
        # without the source set the two collide on one id and `FactBatch` keeps one
        # (D17). Members inherit the suffix through this id, so nothing is doubled.
        type_id = f"{parent_id}.{name}{kmp_id_suffix(node, source, ctx.source_set)}"
        ctx.local_types.add(name)
        ctx.type_members.setdefault(type_id, set())
        ctx.field_types.setdefault(type_id, {})
        line = node.start_point[0] + 1
        batch.add_node(
            Node(
                type_id,
                NodeKind.TYPE,
                name,
                _LANG,
                Provenance(rel, line, node.end_point[0] + 1),
            )
        )
        batch.add_edge(Edge(parent_id, type_id, EdgeKind.CONTAINS, Provenance(rel, line)))

        for base in _supertypes(node, source):
            target = self._resolve_type(base, ctx)
            if target is not None:
                batch.add_edge(Edge(type_id, target, EdgeKind.IMPLEMENTS, Provenance(rel, line)))

        self._emit_constructor_properties(node, type_id, ctx, source, rel, batch)
        for body in (c for c in node.named_children if c.type in _TYPE_BODIES):
            self._emit_members(body, type_id, ctx, source, rel, batch)

        # Framework readings (P3), after the members so a DAO method's Function id
        # already exists to hang READS/WRITES off. Each is a no-op on a type that
        # carries no such annotation, so a plain class costs one dictionary lookup.
        resolve = self._type_resolver(ctx)
        if not read_entity(node, type_id, resolve, source, rel, batch, constants=ctx.constants):
            # Only a class that is not itself an entity can be a Room *view* — a
            # query result shape holding `@Embedded` + `@Relation`.
            read_relation_view(node, resolve, source, rel, batch)
        read_dao(node, type_id, resolve, source, rel, batch)
        read_module(node, type_id, resolve, source, rel, batch)
        scan_type(
            node,
            type_id,
            source,
            rel,
            self._client,
            base_path=ctx.base_path,
            by_simple=ctx.imports.by_simple,
            wildcard_prefixes=ctx.imports.wildcard_prefixes,
        )
        read_controller(node, type_id, self._spring_resolver(ctx), source, rel, batch)

    def _type_resolver(self, ctx: _FileContext) -> Any:
        """A ``simple name → node id`` closure for the framework readers.

        They need to turn ``TopicEntity::class`` into an id without importing the
        resolver table's shape, so they get a function instead of the context.
        """

        def resolve(name: str) -> str | None:
            return self._resolve_type(name, ctx)

        return resolve

    def _spring_resolver(self, ctx: _FileContext) -> Any:
        """A ``simple annotation name → is it Spring's?`` predicate for this file.

        ``@GetMapping`` is nobody's annotation until an import says whose it is, and
        the validation repository imports it by wildcard, so both forms matter.
        """
        return spring_resolver(ctx.imports.by_simple, ctx.imports.wildcard_prefixes)

    def _emit_constructor_properties(
        self,
        node: TSNode,
        type_id: str,
        ctx: _FileContext,
        source: bytes,
        rel: str,
        batch: FactBatch,
    ) -> None:
        """``class Repo(private val dao: Dao)`` — a constructor ``val`` is a property (D6).

        This is not a convenience: in DI-heavy Kotlin the constructor property is
        the typed receiver every call in the class goes through, so leaving it out
        would drop most of the class's structure and, later, most of its call graph.
        A parameter without ``val``/``var`` is a plain argument and gets nothing.
        """
        ctor = next((c for c in node.named_children if c.type == "primary_constructor"), None)
        if ctor is None:
            return
        params = next((c for c in ctor.named_children if c.type == "class_parameters"), None)
        if params is None:
            return
        for param in params.named_children:
            if param.type != "class_parameter" or not _binds_property(param):
                continue
            pname = next((_text(c, source) for c in param.named_children if c.type == "identifier"), "")
            if pname:
                _add_member(batch, type_id, pname, NodeKind.FIELD, rel, param.start_point[0] + 1)
                ctx.type_members.setdefault(type_id, set()).add(pname)
                declared = next((_text(c, source) for c in param.named_children if c.type == "user_type"), "")
                if declared:
                    ctx.field_types.setdefault(type_id, {})[pname] = declared

    def _emit_members(
        self,
        body: TSNode,
        type_id: str,
        ctx: _FileContext,
        source: bytes,
        rel: str,
        batch: FactBatch,
    ) -> None:
        for member in body.named_children:
            line = member.start_point[0] + 1
            if member.type == "function_declaration":
                self._emit_function(member, type_id, type_id, ctx, source, rel, batch)
            elif member.type == "property_declaration":
                for pname in _property_names(member, source):
                    _add_member(batch, type_id, pname, NodeKind.FIELD, rel, line)
                    ctx.type_members.setdefault(type_id, set()).add(pname)
                declared = _declared_property_type(member, source)
                names = _property_names(member, source)
                if declared and len(names) == 1:
                    ctx.field_types.setdefault(type_id, {})[names[0]] = declared
            elif member.type == "enum_entry":
                ename = _field_text(member, "name", source) or next(
                    (_text(c, source) for c in member.named_children if c.type == "identifier"), ""
                )
                if ename:
                    _add_member(batch, type_id, ename, NodeKind.FIELD, rel, line)
                    ctx.type_members.setdefault(type_id, set()).add(ename)
            elif member.type == "companion_object":
                # Fold onto the enclosing type (D5). `A.make()` and a bare `make()`
                # inside `A` both name `java:pkg.A.make`; nothing at a call site
                # ever writes `Companion`, so a separate node would be a name the
                # graph invented. The companion's own name, where one is given, is
                # not a declaration of its own.
                for inner in (c for c in member.named_children if c.type in _TYPE_BODIES):
                    self._emit_members(inner, type_id, ctx, source, rel, batch)
            elif member.type in _TYPE_DECLS:
                self._emit_type(member, type_id, ctx, source, rel, batch)

    def _emit_function(
        self,
        node: TSNode,
        parent_id: str,
        owner: str | None,
        ctx: _FileContext,
        source: bytes,
        rel: str,
        batch: FactBatch,
    ) -> None:
        """A named function under ``parent_id``.

        ``override`` / ``suspend`` / ``operator`` / ``infix`` are modifiers the
        graph does not record, and ``@Composable`` is a normal function — a
        Compose screen is a function that returns Unit, not a new kind of thing.
        """
        name = _field_text(node, "name", source)
        if not name:
            return
        func_id = _add_member(
            batch,
            parent_id,
            name,
            NodeKind.FUNCTION,
            rel,
            node.start_point[0] + 1,
            suffix=kmp_id_suffix(node, source, ctx.source_set) if owner is None else "",
        )
        # Read regardless of nesting: a Ktor route module is normally top level, but
        # `override fun Routing.registerRoutes()` inside a class is the same thing.
        receiver = _extension_receiver(node, source)
        route_module = bool(receiver) and register_module(name, func_id, receiver, self._ktor)
        if owner is not None:
            ctx.type_members.setdefault(owner, set()).add(name)
        else:
            if receiver:
                # An extension: resolvable by name at a call site, and kept with
                # its receiver so `x.ext()` is only claimed when `x` fits (D4).
                ctx.extensions[name] = receiver
            else:
                ctx.top_level_funcs.add(name)

        body = next((c for c in node.named_children if c.type == "function_body"), None)
        if body is not None:
            ctx.pending.append(
                _Pending(func_id, owner, body, _parameter_types(node, source), route_module, receiver)
            )

    def _finalize_resolver(self, batch: FactBatch) -> Any:
        """A ``screen name → Function id`` closure over the finished batch.

        Compose screens are declared in one module and navigated to from another,
        so the ``EXPOSES`` target cannot be resolved while reading the file that
        declares the route — by then the import map of the *other* file is what
        would be needed. By ``finalize`` every declaration exists, so the screen is
        found by name. A name that matches several grounded functions resolves to
        nothing rather than picking one.
        """
        by_name: dict[str, list[str]] = {}
        for node in batch.nodes:
            if node.kind is NodeKind.FUNCTION and node.grounded:
                by_name.setdefault(node.name, []).append(node.id)

        def resolve(name: str) -> str | None:
            found = by_name.get(name, [])
            return found[0] if len(found) == 1 else None

        return resolve

    # ---- CALLS (D8, §3.2) ---------------------------------------------------

    def _calls(self, pend: _Pending, ctx: _FileContext, source: bytes, rel: str, batch: FactBatch) -> None:
        """Emit ``CALLS`` for the call sites in one body that resolve *exactly*.

        Kotlin is the typed-receiver language: because every property and every
        parameter carries a declared type, ``dao.getTopics()`` — the dominant
        shape in this style of code — resolves through the import map with no
        inference at all. That is why this is P2 and not a late phase.

        Everything that would need inference is skipped rather than guessed: a
        call on an unannotated ``val x = something()``, ``it.x()`` inside a
        lambda, a chained receiver, a callable reference, ``invoke`` on a lambda.
        A same-package function declared in *another* file is skipped too — there
        is no ``finalize`` backstop for a function id, so it would be a guess that
        never gets checked (§3.2 row 1).
        """
        scope = _Scope()
        for name, declared in pend.params.items():
            scope.bind(name, declared)
        scope.merge_fields(ctx.field_types.get(pend.owner or "", {}))
        _collect_bindings(pend.body, source, scope)

        for call in _call_sites(pend.body):
            line = call.start_point[0] + 1
            target = self._resolve_call(
                call, pend.owner, ctx, scope, source, line=line, rel=rel, this_type=pend.receiver
            )
            if target is None:
                continue
            if isinstance(target, _DeferredCall):
                # A typed receiver: whether this call is real is a whole-repository
                # question, so it is answered in `finalize` (see `_DeferredCall`).
                self._deferred.append(replace(target, src=pend.func_id, provenance=Provenance(rel, line)))
                continue
            # A resolved third-party callee — `Modifier.padding`, `Json.decodeFromString` —
            # is a real call to a real symbol this tree does not declare, so it gets an
            # **external placeholder**. Without one the edge dangles and `pkg verify`
            # reports an error for what is simply a call into a library (measured: 387
            # such edges across 161 distinct AndroidX/kotlinx symbols on the validation
            # app). `FactBatch` dedup upgrades the placeholder the moment a grounded
            # declaration for the same id shows up — from this front-end or from Java's,
            # which is the other half of what D2's shared namespace buys.
            batch.add_node(Node(target, NodeKind.FUNCTION, target.rsplit(".", 1)[-1], _LANG, external=True))
            batch.add_edge(
                Edge(
                    pend.func_id,
                    target,
                    EdgeKind.CALLS,
                    Provenance(rel, call.start_point[0] + 1),
                )
            )

    def _resolve_call(
        self,
        call: TSNode,
        owner: str | None,
        ctx: _FileContext,
        scope: _Scope,
        source: bytes,
        *,
        line: int,
        rel: str,
        this_type: str = "",
    ) -> str | _DeferredCall | None:
        callee = next(iter(call.named_children), None)
        if callee is None:
            return None
        # Read off the *call*, not the callee: the arguments are siblings of the
        # navigation expression, so this is the last point where both are in hand.
        passes_function = _passes_function(call)
        if callee.type == "identifier":
            return self._resolve_bare(
                _text(callee, source), owner, ctx, scope, passes_function=passes_function
            )
        if callee.type == "navigation_expression":
            return self._resolve_navigated(
                callee,
                owner,
                ctx,
                scope,
                source,
                line=line,
                rel=rel,
                this_type=this_type,
                passes_function=passes_function,
            )
        return None  # a chained or computed callee — inference, so never

    def _resolve_bare(
        self,
        name: str,
        owner: str | None,
        ctx: _FileContext,
        scope: _Scope,
        *,
        passes_function: bool,
    ) -> str | _DeferredCall | None:
        """``foo()`` with no receiver."""
        if not name or name in scope.bound:
            # D9: a Kotlin local *can* shadow a call — `val helper = ::other`
            # then `helper()` invokes the local through `invoke`, not the member.
            # Unlike Java, where variables and methods are separate namespaces,
            # this is a real ambiguity, so the call is not claimed for either.
            return None
        # A member of the enclosing type, or of any type enclosing that one — a
        # nested class can call its outer's members without qualifying them.
        holder: str | None = owner
        while holder:
            if name in ctx.members_of(holder):
                return f"{holder}.{name}"
            holder = holder.rsplit(".", 1)[0] if holder.count(".") > 1 else None
        if name in ctx.top_level_funcs or name in ctx.extensions:
            return f"java:{ctx.package}.{name}" if ctx.package else None
        if name in ctx.local_types:
            # A constructor call. The corpus rule is that instantiation is a call
            # to the type, so the target is the Type node, not an invented `.ctor`.
            return f"java:{ctx.package}.{name}" if ctx.package else None
        if name in ctx.imports.by_simple:
            # Imported — and the id is the import target whether the name is a
            # type or a function, which is exactly what D2's shared namespace
            # buys: the id unifies with the declaration in the other file.
            return f"java:{ctx.imports.by_simple[name]}"
        if ctx.package:
            # A bare name this file does not declare and does not import: in Kotlin
            # that is a same-package declaration in **another file**, which is how a
            # Ktor route module is mounted (`route("/v1") { orders() }`) and how any
            # multi-file package calls itself.
            #
            # §3.2 row 1 refused this outright, and gave the right reason for the code
            # as it then stood: "there is no `finalize` backstop for a function id, so
            # it would be a guess that never gets checked." There is one now — the same
            # one `_DeferredCall` uses — so the call can be *checked* instead of either
            # guessed or dropped. Nothing is emitted unless the repository declares it.
            candidates = tuple(
                f"java:{prefix}.{name}" for prefix in (ctx.package, *sorted(ctx.imports.wildcard_prefixes))
            )
            return _DeferredCall(
                src="",
                candidates=candidates,
                owners=candidates,
                certain=False,
                takes_function_argument=passes_function,
                imported_extension="",
                provenance=Provenance("", 0),
            )
        return None

    def _resolve_navigated(
        self,
        nav: TSNode,
        owner: str | None,
        ctx: _FileContext,
        scope: _Scope,
        source: bytes,
        *,
        line: int,
        rel: str,
        this_type: str = "",
        passes_function: bool,
    ) -> str | _DeferredCall | None:
        """``recv.foo()`` — the typed-receiver case, and the static/companion one."""
        parts = [c for c in nav.named_children]
        if len(parts) < 2:
            return None
        receiver, name_node = parts[0], parts[-1]
        if name_node.type != "identifier":
            return None
        name = _text(name_node, source)
        if not name:
            return None

        if receiver.type == "this_expression":
            if owner:
                return f"{owner}.{name}" if name in ctx.members_of(owner) else None
            # A top-level extension: `this` is its *receiver*, whose type the signature
            # states and the file imports. Found in review: `fun NavController.x() {
            # this.navigate(…) }` resolved to nothing at all, because only a member
            # function was considered to have a `this` worth resolving.
            return (
                self._deferred_call(
                    this_type, name, ctx, owner=None, line=line, rel=rel, passes_function=passes_function
                )
                if this_type
                else None
            )
        if receiver.type != "identifier":
            return None  # a chained or computed receiver — never
        recv = _text(receiver, source)
        if recv == "Companion" and owner:
            # D5's third call form, written from inside the class that owns the companion.
            # `Companion` is not a type this file declares, so without this it resolved as a
            # bare name under the enclosing package — the phantom `java:pkg.Companion.make`
            # that D5 exists to prevent. §6 assigns all three forms to the `companions`
            # corpus case and the fixture only ever contained two of them.
            return f"{owner}.{name}" if name in ctx.members_of(owner) else None
        recv_type = scope.type_of(recv)

        # An extension is claimed only when the receiver fits, or when the receiver's
        # type is unknown and so cannot contradict it. Attaching it to the receiver
        # *type* would be the D4 fabrication.
        if name in ctx.extensions and (recv_type is None or _bare_type(recv_type) == ctx.extensions[name]):
            return f"java:{ctx.package}.{name}" if ctx.package else None
        if recv_type is not None:
            # An extension **imported** from another file is written exactly like a member
            # call, and `ctx.extensions` only knows this file's. Offering the import as a
            # second candidate recovers the true, grounded target instead of losing it —
            # `test_extension_call_resolves_to_the_free_function_not_the_receiver` pinned
            # only the same-file half of D4. Member first, extension second, which is
            # Kotlin's own resolution order: a member always wins over an extension.
            imported = ctx.imports.by_simple.get(name)
            return self._deferred_call(
                recv_type,
                name,
                ctx,
                owner=None,
                line=line,
                rel=rel,
                also=(f"java:{imported}",) if imported else (),
                passes_function=passes_function,
            )
        if recv[:1].isupper():
            # `Type.foo()` — an object, a companion member folded onto the class
            # (D5), or an enum member. The Java rule, unchanged.
            return self._deferred_call(
                recv, name, ctx, owner=None, line=line, rel=rel, passes_function=passes_function
            )
        return None

    def _deferred_call(
        self,
        type_name: str,
        member: str,
        ctx: _FileContext,
        *,
        owner: str | None,
        line: int,
        rel: str,
        also: tuple[str, ...] = (),
        passes_function: bool,
    ) -> _DeferredCall | None:
        """Hold back ``<type_name>.<member>()`` for the whole-repository check.

        ``also`` are extra ids the call could name, tried *after* the receiver's own
        members — they are alternative readings of the same call site.

        They used to be barred from the external-placeholder fallback too, on the
        grounds that they are "not the reading the source states". That is right while
        the receiver is one the repository declares, and backwards once it is not: an
        imported receiver's members are **unknowable**, so ``Modifier.padding`` is a
        guess, while ``import androidx.compose.foundation.layout.padding`` is a
        fully-qualified name the file itself wrote. ``_settle_calls`` therefore prefers
        an ``also`` id for that one case, and ``imported_extension`` carries it.
        """
        owners, certain = self._type_candidates(type_name, ctx)
        if not owners and not also:
            return None
        return _DeferredCall(
            src=owner or "",
            candidates=tuple(f"{t}.{member}" for t in owners) + also,
            owners=owners or also,
            certain=certain and bool(owners),
            takes_function_argument=passes_function,
            imported_extension=also[0] if also else "",
            provenance=Provenance(rel, line),
        )

    def _type_candidates(self, simple_or_fqn: str, ctx: _FileContext) -> tuple[tuple[str, ...], bool]:
        """Every id a type name could denote, best first, and whether the source says so.

        ``certain`` means the name was *read*: an already-qualified name, or one an
        explicit ``import`` places. Everything else is assumption, and the candidates
        are then the enclosing package plus each ``import a.b.*`` prefix — a wildcard
        names no symbol on its own, but it is exactly where a bare name may come from,
        and offering it as a candidate is how the *true* target of ``dao.getTopics()``
        is found rather than invented under the caller's own package.
        """
        name = _bare_type(simple_or_fqn)
        if not name:
            return (), False
        if "." in name:
            return (f"java:{name}",), True
        if name in ctx.imports.by_simple:
            return (f"java:{ctx.imports.by_simple[name]}",), True
        guesses = [f"java:{ctx.package}.{name}"] if ctx.package else []
        guesses += [f"java:{prefix}.{name}" for prefix in sorted(ctx.imports.wildcard_prefixes)]
        return tuple(guesses), False

    # ---- resolution ---------------------------------------------------------

    def _resolve_type(self, simple_or_fqn: str, ctx: _FileContext) -> str | None:
        """Resolve a type name to a node id (precision-first, else ``None``)."""
        name = _bare_type(simple_or_fqn)
        if not name:
            return None
        if "." in name:
            # Already qualified, or a nested name (`Outer.Inner`) we take as read.
            return f"java:{name}"
        if name in ctx.imports.by_simple:
            return f"java:{ctx.imports.by_simple[name]}"
        if ctx.package:  # same-package sibling — a guess, checked again in finalize
            return f"java:{ctx.package}.{name}"
        return None

    def finalize(self, batch: FactBatch) -> FactBatch:
        """Repoint ``IMPLEMENTS`` edges whose base type was guessed into this package.

        ``_resolve_type`` has to answer per file: a bare ``: ViewModel`` gives no
        clue whether the base is a sibling declaration or ``androidx.lifecycle``.
        It assumes the enclosing package, which is right for a sibling and wrong
        for every framework supertype — and Android code inherits from the
        framework constantly.

        By the time this runs, every declaration in the repository is known, so
        the guess is checkable. A target matching no declared type is repointed at
        ``java:<BareName>`` with an **external** node: that asserts the name the
        source actually wrote instead of a package this front-end invented, and it
        lands the edge rather than leaving it dangling. Same shape as C# and PHP.

        **Only a guess is repointed.** The test for "was this a guess" is whether
        the target names a node at all: a supertype that resolved through an
        explicit ``import androidx.lifecycle.ViewModel`` already has a node — the
        external placeholder the import pass emitted — and its fully-qualified
        name is *read from the source*, not inferred. Repointing that to a bare
        ``java:ViewModel`` would throw away the one thing the file actually told
        us. A same-package guess, by contrast, emits no node anywhere, which is
        precisely what makes it identifiable here.
        """
        known = {n.id for n in batch.nodes}
        out = FactBatch()
        for node in batch.nodes:
            out.add_node(node)
        for edge in batch.edges:
            # `PROVIDES` rides along for the same reason and by the same test: a Hilt
            # binding's return type is resolved by `_resolve_type` too, so
            # `@Provides fun x(): Repo` in a module whose package does not declare
            # `Repo` used to mint the type `java:<this.package>.Repo` — a class in a
            # package that does not contain it, which `FactStore.injection_reach_of`
            # then walked in `blast_radius`.
            if (
                edge.kind not in (EdgeKind.IMPLEMENTS, EdgeKind.PROVIDES)
                or not edge.dst.startswith("java:")
                or edge.dst in known
            ):
                out.add_edge(edge)
                continue
            bare = edge.dst.rsplit(".", 1)[-1]
            target = f"java:{bare}"
            out.add_node(Node(target, NodeKind.TYPE, bare, _LANG, external=True))
            out.add_edge(Edge(edge.src, target, edge.kind, edge.provenance))

        # P3, and both passes need the whole tree for the same reason the IMPLEMENTS
        # repoint above does: a `@Query` names a *table* while an `@Entity` names a
        # *class*, and a Retrofit call names a path whose endpoint — if it exists at
        # all — is declared somewhere else entirely.
        out = repoint_table_edges(out)
        resolve_function = self._finalize_resolver(out)
        emit_nav_routes(self._nav, out, resolve_function)
        self._nav.clear()
        emit_ktor_routes(self._ktor, out, resolve_function)
        self._ktor.clear()
        link_actuals(out)
        self._settle_calls(out)
        # [0] is the unmatched calls; the join *count* is the other half of the return and
        # is what `test_kotlin_http` asserts, but nothing here needs it.
        self.unresolved_calls = join_to_endpoints(self._client, out)[0]
        return out

    def _settle_calls(self, batch: FactBatch) -> None:
        """Decide every held-back typed-receiver call against the finished repository.

        Four outcomes, and the middle two are the whole point:

        * A candidate the repository **declares** wins, first one in priority order.
          A wildcard-imported sibling lands here — ``import app.data.*`` then
          ``dao.getTopics()`` resolves to ``java:app.data.TopicDao.getTopics``, the real
          declaration, which the per-file guess used to replace with one under the
          *caller's* package.
        * Nothing grounded, and the receiver's type was **guessed** or is a type this
          repository declares: **drop**. A guessed id has no backstop, and a declared
          type that has no such member means the call is not to that type at all —
          ``topic.let { }`` on a repo-declared ``Topic`` being the common shape. This is
          the case that used to mint a placeholder and so hide itself from ``pkg verify``.
        * Nothing grounded, the type was read from an import, the repository does not
          declare it, and the call is a **scope function by name and by shape**: **drop**.
          An imported type has no declared-member list to refuse against, so
          ``modifier.let { }`` on an imported ``Modifier`` would otherwise mint a
          placeholder — the receiver's own fabrication, one level further out than the
          declared-type case above (#389).

          Both halves are load-bearing. Refusing on the name alone — the first fix for
          #389 — dropped ``r.run()`` on a ``Runnable`` and ``d.with(adjuster)`` on a
          ``LocalDate``, genuine members that merely share a scope function's name;
          measured, a three-call probe went from three ``CALLS`` to one. So the call
          must also *pass a function*, which is what a scope function is for and what
          ``Runnable.run()`` does not do.
        * Nothing grounded, the type was read from the source, and the repository does
          not declare it: a genuine call into a library, which keeps an external
          placeholder rather than dangling (measured: 387 such edges across 161
          AndroidX/kotlinx symbols on the validation app, and losing them would be a real
          recall regression).

          **Which id that placeholder gets is not obvious**, and the receiver-member
          form is usually wrong. ``m.padding(8)`` reads as a member of ``Modifier``, but
          ``padding`` is ``androidx.compose.foundation.layout.padding``, an extension —
          ``Modifier`` does not declare it, and Kotlin requires the file to import an
          extension in order to call it. So when this file imports something under the
          called name, that import is the better-evidenced target: it is a
          fully-qualified name the source actually wrote, where the member reading is a
          guess about a type nothing here can introspect.

        What survives all four is stated rather than hidden: a wildcard-imported
        extension binds no simple name, so ``m.padding(8)`` under ``import
        androidx.compose.foundation.layout.*`` still lands on the receiver-member id and
        may still be a fabrication. Narrowed, not closed.
        """
        declared = declared_ids(batch)
        for call in self._deferred:
            if resolve_or_drop(
                batch,
                call.src,
                call.candidates,
                EdgeKind.CALLS,
                call.provenance,
                declared=declared,
            ):
                continue
            if not call.certain or call.owners[0] in declared:
                continue
            member = call.candidates[0].rsplit(".", 1)[-1]
            if member in _SCOPE_FUNCTIONS and call.takes_function_argument:
                continue
            # The import outranks the receiver-member guess — see the docstring.
            target = call.imported_extension or call.candidates[0]
            batch.add_node(Node(target, NodeKind.FUNCTION, target.rsplit(".", 1)[-1], _LANG, external=True))
            batch.add_edge(Edge(call.src, target, EdgeKind.CALLS, call.provenance))
        self._deferred.clear()


# ---- scope, for typed receivers ---------------------------------------------


@dataclass
class _Scope:
    """What names a body binds, and which of them carry a declared type.

    Deliberately flat: one table for the whole function rather than one per
    block. That over-approximates ``bound``, so a name bound anywhere in the body
    silences a bare call to it everywhere in the body. The error is on the side
    of emitting nothing, which is the direction this front-end is allowed to be
    wrong in.
    """

    types: dict[str, str] = field(default_factory=dict)
    bound: set[str] = field(default_factory=set)

    def __post_init__(self) -> None:
        self.bound |= set(self.types)

    def merge_fields(self, fields: dict[str, str]) -> None:
        """Enclosing-type properties are in scope, but a parameter shadows one."""
        for name, declared in fields.items():
            self.types.setdefault(name, declared)

    def bind(self, name: str, declared: str = "") -> None:
        if not name:
            return
        self.bound.add(name)
        if declared:
            self.types[name] = declared
        else:
            # An unannotated `val x = something()` — the type would have to be
            # inferred, so the name is bound but deliberately untyped, and every
            # call on it is skipped rather than guessed.
            self.types.pop(name, None)

    def type_of(self, name: str) -> str | None:
        return self.types.get(name)


def _collect_bindings(body: TSNode, source: bytes, scope: _Scope) -> None:
    """Record every local binding in ``body``, with its declared type where given."""
    for node in _walk(body):
        if node.type == "property_declaration":
            declared = _declared_property_type(node, source)
            names = _property_names(node, source)
            for name in names:
                scope.bind(name, declared if len(names) == 1 else "")
        elif node.type in ("lambda_parameters", "function_value_parameters"):
            for child in node.named_children:
                if child.type in ("parameter", "variable_declaration"):
                    scope.bind(_declared_name_or_first(child, source))
        elif node.type == "for_statement":
            # `for (helper in fns)` binds `helper`, and a bound name silences a bare
            # call to it (D9). The grammar hangs the loop variable straight off the
            # `for_statement` rather than inside any parameter list, so it needs its
            # own branch — without it, `for (helper in fns) { helper() }` was resolved
            # to a *member* named `helper`, an edge to a function the loop never calls.
            for child in node.named_children:
                if child.type == "variable_declaration":
                    scope.bind(_declared_name_or_first(child, source))
        elif node.type == "catch_block":
            # `catch (report: Throwable)` binds `report` — same rule, and the grammar
            # puts the name as a bare `identifier` child of the catch.
            caught = next((c for c in node.named_children if c.type == "identifier"), None)
            if caught is not None:
                scope.bind(_text(caught, source))


#: Argument nodes that are *syntactically* a function, whatever their type.
_FUNCTION_ARGUMENTS = frozenset({"lambda_literal", "callable_reference"})


def _passes_function(call: TSNode) -> bool:
    """Whether this call site hands the callee a function.

    The discriminator between a scope function and a library member that happens to
    share its name. ``m.let { }`` passes one and ``r.run()`` does not, which is the only
    difference available to a front-end that cannot introspect ``Runnable``.

    Three shapes count, all read straight off the grammar: a trailing lambda is an
    ``annotated_lambda`` child of the ``call_expression``; the parenthesised forms
    ``x.let({ … })`` and ``x.let(::f)`` put a ``lambda_literal`` or
    ``callable_reference`` inside a lone ``value_argument``. Everything else — no
    arguments, a value, or more than one argument — is not the scope-function shape.

    Deliberately syntactic, and deliberately incomplete: ``x.also(fn)`` where ``fn`` is
    a variable holding a lambda reads exactly like a one-argument member call, and no
    amount of parsing separates them. That residue is recorded in
    ``corpus/kotlin/scope_functions/expected.json`` rather than hidden.
    """
    for child in call.named_children:
        if child.type == "annotated_lambda":
            return True
        if child.type == "value_arguments":
            args = [a for a in child.named_children if a.type == "value_argument"]
            if len(args) == 1 and any(g.type in _FUNCTION_ARGUMENTS for g in args[0].named_children):
                return True
    return False


def _call_sites(body: TSNode) -> list[TSNode]:
    """Every ``call_expression`` in a body, excluding nested type declarations.

    A nested class or object is its own scope with its own members; its calls are
    emitted against *its* functions, not the enclosing one.
    """
    out: list[TSNode] = []
    stack = list(body.named_children)
    while stack:
        node = stack.pop()
        if node.type in _TYPE_DECLS:
            continue
        if node.type == "call_expression":
            out.append(node)
        stack.extend(node.named_children)
    return out


def _walk(node: TSNode) -> list[TSNode]:
    out: list[TSNode] = []
    stack = [node]
    while stack:
        n = stack.pop()
        out.append(n)
        stack.extend(n.named_children)
    return out


def _parameter_types(func: TSNode, source: bytes) -> dict[str, str]:
    """``fun f(topic: Topic, onClick: () -> Unit)`` → ``{"topic": "Topic", "onClick": ""}``.

    **Every** parameter is returned, including the ones whose type is not a simple
    ``user_type``. The empty string means "bound here, but not usefully typed",
    and both halves matter: the type drives receiver resolution, and the *name*
    silences a bare call to it.

    Dropping the untyped ones was a real defect, caught by the invention oracle
    rather than by a test. Compose parameters are function-typed — ``onClick: ()
    -> Unit`` is a ``function_type``, not a ``user_type`` — so they were not bound
    at all, and a later bare ``onClick(...)`` resolved to an import of the same
    name instead of being skipped as shadowed.
    """
    params = next((c for c in func.named_children if c.type == "function_value_parameters"), None)
    if params is None:
        return {}
    out: dict[str, str] = {}
    for param in params.named_children:
        if param.type != "parameter":
            continue
        name = next((_text(c, source) for c in param.named_children if c.type == "identifier"), "")
        declared = next((_text(c, source) for c in param.named_children if c.type == "user_type"), "")
        if name:
            out[name] = declared
    return out


def _extension_receiver(func: TSNode, source: bytes) -> str:
    """The receiver of ``fun Topic.slugify()``, or ``""`` for a plain function.

    The grammar puts the receiver's ``user_type`` *before* the name identifier,
    so position is what tells an extension from a function with a return type.
    """
    name_node = func.child_by_field_name("name")
    if name_node is None:
        return ""
    for child in func.named_children:
        if child.type == "user_type" and child.end_byte < name_node.start_byte:
            return _bare_type(_text(child, source))
    return ""


def _declared_property_type(prop: TSNode, source: bytes) -> str:
    """The written type of ``val x: T = …``; ``""`` when it is inferred."""
    decl = next((c for c in prop.named_children if c.type == "variable_declaration"), None)
    if decl is None:
        return ""
    return next((_text(c, source) for c in decl.named_children if c.type == "user_type"), "")


def _declared_name_or_first(node: TSNode, source: bytes) -> str:
    return next((_text(c, source) for c in node.named_children if c.type == "identifier"), "")


def _bare_type(name: str) -> str:
    """``Flow<List<Topic>>`` → ``Flow``; a nullable ``Topic?`` → ``Topic``."""
    return name.split("<", 1)[0].strip().rstrip("?").strip()


# ---- module-level helpers ---------------------------------------------------


def _add_member(
    batch: FactBatch,
    parent_id: str,
    name: str,
    kind: NodeKind,
    rel: str,
    line: int,
    suffix: str = "",
) -> str:
    """Add a member node under ``parent_id`` plus its ``CONTAINS`` edge; return its id.

    ``suffix`` marks a declaration that shares its name with another in a different
    source set (D17); it is part of the id and never part of the ``name``, which stays
    what the source wrote.
    """
    member_id = f"{parent_id}.{name}{suffix}"
    batch.add_node(Node(member_id, kind, name, _LANG, Provenance(rel, line)))
    batch.add_edge(Edge(parent_id, member_id, EdgeKind.CONTAINS, Provenance(rel, line)))
    return member_id


def _binds_property(param: TSNode) -> bool:
    """Whether a ``class_parameter`` declares a property (``val``/``var``) (D6).

    The keyword is an anonymous token child, not a field, so this reads the token
    stream rather than asking for a name the grammar does not give.
    """
    return any(child.type in ("val", "var") for child in param.children)


def _property_names(prop: TSNode, source: bytes) -> list[str]:
    """Declared names in a ``property_declaration``, including destructuring.

    ``val (a, b) = pair`` declares two properties; the grammar gives one
    ``multi_variable_declaration`` holding both.
    """
    names: list[str] = []
    for child in prop.named_children:
        if child.type == "variable_declaration":
            names.append(_declared_name(child, source))
        elif child.type == "multi_variable_declaration":
            names.extend(
                _declared_name(inner, source)
                for inner in child.named_children
                if inner.type == "variable_declaration"
            )
    return [n for n in names if n]


def _declared_name(decl: TSNode, source: bytes) -> str:
    """The identifier of a ``variable_declaration`` (``name: Type`` → ``name``)."""
    return next((_text(c, source) for c in decl.named_children if c.type == "identifier"), "")


def _supertypes(node: TSNode, source: bytes) -> list[str]:
    """Supertype names from a ``delegation_specifiers`` clause.

    Kotlin writes one list for what Java splits into ``extends`` and
    ``implements``: ``class A : Base(), Iface`` — the superclass is the one with
    a constructor invocation, and the graph does not care which is which (D7).
    """
    out: list[str] = []
    specifiers = next((c for c in node.named_children if c.type == "delegation_specifiers"), None)
    if specifiers is None:
        return out
    for spec in specifiers.named_children:
        if spec.type != "delegation_specifier":
            continue
        for child in spec.named_children:
            if child.type == "user_type":
                out.append(_text(child, source))
                break
            if child.type == "constructor_invocation":
                inner = next((c for c in child.named_children if c.type == "user_type"), None)
                if inner is not None:
                    out.append(_text(inner, source))
                break
            if child.type == "explicit_delegation":
                # `: Iface by impl` — the interface is still implemented.
                inner = next((c for c in child.named_children if c.type == "user_type"), None)
                if inner is not None:
                    out.append(_text(inner, source))
                break
    return [n for n in out if n]


def _field_text(node: TSNode, field_name: str, source: bytes) -> str:
    child = node.child_by_field_name(field_name)
    return _text(child, source) if child is not None else ""


def _text(node: TSNode | None, source: bytes) -> str:
    if node is None:
        return ""
    return source[node.start_byte : node.end_byte].decode("utf-8", "replace").strip()


def _kotlin_parser() -> Any:
    try:
        import tree_sitter_kotlin
        from tree_sitter import Language, Parser
    except ImportError as exc:  # pragma: no cover - exercised only without the extra
        raise RuntimeError(
            "Kotlin extraction needs tree-sitter; install the extra: "
            "uv pip install 'tree-sitter>=0.21' 'tree-sitter-kotlin>=1.1.0'"
        ) from exc
    language = Language(tree_sitter_kotlin.language())
    try:
        return Parser(language)
    except TypeError:  # older tree-sitter API
        parser = Parser()
        parser.language = language
        return parser


__all__ = ["KotlinExtractor"]
