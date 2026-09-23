"""JavaScript front-end for the PKG extractor — the TypeScript front-end, taught CommonJS.

A subclass rather than a new parser (javascript-support-roadmap D1). Every TypeScript-only
construct the parent reads — `interface`, `type`, `enum`, generics, `implements`, `as` — is a
clean no-op on JavaScript, and the TSX grammar parses plain JavaScript and JSX alike, so what
JavaScript actually needs is narrow and listed below. It rides the ``typescript`` extra: it
needs `tree_sitter_typescript` and nothing else, exactly as the Gradle reader rides ``kotlin``.

**Ids are TypeScript's, the language tag is not** (D4). A JavaScript module mints
``ts:path/to/file``, the same namespace TypeScript uses, because the two share one module
resolution: a `.ts` file importing `./util` reaches `util.js` and the reverse, and a separate
prefix would make every such edge resolve to nothing. Nodes are tagged ``javascript`` all the
same — that string, not the prefix, is what `pkg accuracy` and the capability matrix count by.
Kotlin is the precedent: ``kotlin`` nodes, ``java:`` ids.

What JavaScript adds over the parent:

* **CommonJS imports.** The parent reads `import` statements only, so on a CommonJS repository
  every module was an island — no ``IMPORTS``, and with no imports no cross-file ``CALLS`` or
  ``IMPLEMENTS`` either. Read here: `const m = require('./m')` (a *namespace*: `m.f()` is the
  export `f`), `const {a} = require('./m')`, `const a = require('./m').a`, and a bare
  `require('./m')` for its side effects.
* **CommonJS exports.** `exports.f = …`, `module.exports.f = …`, `module.exports = {f, g() {}}`,
  a named `module.exports = function f() {}`, and `obj.f = …` where `obj` *is* the exports
  object (`var app = exports = module.exports = {}`, or `module.exports = res`) — the form the
  classic CommonJS library uses most (see `_export_aliases`). The parent sees none of these —
  they are assignments, not declarations — so a CommonJS module's public surface was empty.
* **Express handlers named as members** — `app.get('/', site.index)` — bound to the export, so
  the ``EXPOSES`` edge exists (see `_route_handler`). The route reader it shares with TypeScript
  also now reads `var app = module.exports = express()`, a chained router binding.
* **The data layer** — Sequelize models as ``Entity``/``Field`` nodes and ``REFERENCES`` edges,
  read by :mod:`orchestrator.pkg.js_orm`.
* **An existence check on what it resolved.** See :meth:`JavaScriptExtractor.finalize`.

Precision-first, as every front-end. Two left-out shapes are declared as corpus ``known_gaps``
and measured — a renamed destructuring (`{run: go} = require(…)`, since resolution names the
target by the local) and a call through an untyped receiver (D8: JavaScript states no types for
the parent's typed-receiver rule to read). The rest are refused or excluded rather than guessed,
and are not measured by the corpus: calling a whole module (`m()` after `m = require(…)`), an
anonymous `module.exports = function () {}`, class expressions, `require` inside a function,
dynamic `import()`, and routes registered inside a function body. How much of each module's
exports this pass has read — readable, names-known or opaque — decides how far calls into it are
trusted (see `_export_surface`).

Declared, measured, and left as they are:

- **A file reaches its own members**, exported or not, so `const self = require('./m')` inside
  `m.js` resolves `self.f()` to an unexported `f`.
- **`delete exports.f` is a read.** The export it removes is still in the map, and `m.f()` is
  kept.
- **A TypeScript default import of a CommonJS default resolves by its local name.** The parent
  cannot tell `import Base from './base'` from a named import, so the default slot is matched by
  name there: `import B from './base'` over `module.exports = Base` loses `extends B`. The same
  import in a JavaScript file is read (see `ExtractionRun.whole`).
- **Inside a function, a `let` in an inner block counts for the whole function** when the
  reference scan decides what is the module's own (`_declares`), so a reference to the real
  alias elsewhere in that function is skipped as shadowed. It needs a local spelled like the
  alias. (At the top level, a block's `let`/`const`/`class` and a `for` header's `let`/`const`
  are scoped exactly; a `var` belongs to the module wherever it is written.)
- **A write in a function is taken to run after `module.exports` is replaced**, so it stays
  live: `(function () { exports.g = g; })(); module.exports = exports = { f };` exports `g`
  here, though the function ran first. The scan does not follow calls.
- **Handing `module` to a function is a use** (`require('./logger')(module)` reads `module.id`);
  a callee that writes `module.exports` through its parameter is not seen.
- **The exports object copied to a name makes the surface opaque**, even where the copy can no
  longer reach the exported object: `var api = exports; api.g = g; module.exports = { f }` is
  resolved by name.
- **Reference scanning is quadratic in nesting depth**: each reference walks its ancestors.
  Five hundred nested functions take about 10 s; ordinary files, including a 446 KB one, are
  linear (a per-file cache answers each scope once).
- **The reference finder reads spellings, not aliasing.** It follows `exports`, `module.exports`,
  the aliases a chain or `module.exports = x` names, top-level `this`, and any bare `module`; a
  spelling it cannot follow (`{ exports }`, `const mod = module`, `module['exports']`) makes the
  surface opaque. The exports object reached some other way — returned from a helper, pulled
  out of another object — is not seen.
- **An alias declared twice is opaque, and its later writes can still invent**: after
  `var app = module.exports = {…}; var app = {}; app.g = g`, `m.g()` is resolved by name. The
  same holds for the second `var app` inside a top-level `if` block, and for an alias reassigned
  inside a callback — `var` is the module's there too, and the scan does not order the writes.
- **An opaque map keeps only the names certain statements wrote** — plain top-level
  `<exports>.f = …` in order. A write under an `if`, in a loop or in a function may be onto a
  binding this pass misread, and kept, it became a rename; so a true export written there is
  lost too, and a call to it is resolved by name like any other on an opaque module.
- **A file that declares its own `module`** (`var module = { exports: {} }`) exports nothing
  through it, and its `module.exports` writes are read as a local's. A `var module` inside a
  class `static {}` block is the block's, but the surface then misreads the file's real
  `module.exports = …` and over-exports.
- **A whole-module binding is matched by its local name.** `class X extends log` after
  `const log = require('./log')` over `module.exports = Log; Log.log = log` names the member
  `log` — a function — because a member the export map names wins over the whole module.
- **A TypeScript named import of a CommonJS default is resolved as a member**:
  `import { Base } from './base'` over `module.exports = Base` reaches `Base`, though `Base` is
  not a member of itself.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING

from orchestrator.pkg.extractor import ExportMap
from orchestrator.pkg.facts import Edge, EdgeKind, FactBatch, Node, NodeKind, Provenance
from orchestrator.pkg.typescript_extractor import (
    _FUNC_CONST_DECLS,
    TypeScriptExtractor,
    _export_router,
    _field_text,
    _import_target,
    _pattern_names,
    _relative_module,
    _text,
)

if TYPE_CHECKING:
    from tree_sitter import Node as TSNode

_LANG = "javascript"
_SUFFIXES = (".js", ".jsx", ".mjs", ".cjs")
_FUNCTION_VALUES = frozenset({"arrow_function", "function_expression", "function", "generator_function"})
#: Edge kinds this front-end resolves by name, and so the ones `finalize` checks landed.
_CHECKED = frozenset({EdgeKind.CALLS, EdgeKind.IMPLEMENTS, EdgeKind.EXPOSES, EdgeKind.REFERENCES})
#: Edges that name a module *member*, and so are routed through a CommonJS module's export map.
_THROUGH_EXPORTS = frozenset({EdgeKind.CALLS, EdgeKind.IMPLEMENTS, EdgeKind.EXPOSES})
#: A name a call site can actually spell as `m.name`. A string key like `'a-b'` cannot.
_IDENTIFIER = re.compile(r"^[A-Za-z_$][\w$]*$")


class JavaScriptExtractor(TypeScriptExtractor):
    """JavaScript front-end (tree-sitter, via the ``typescript`` extra)."""

    language: str = _LANG
    suffixes: tuple[str, ...] = _SUFFIXES

    def __init__(self) -> None:
        super().__init__()
        #: The file being read: what `module.exports` is, and which names are it. Set by the
        #: `_imports` pre-pass — the one per-file hook that sees every declaration before any is
        #: emitted — and read by `_emit_statement`, `_route_handler` and `_emit_module`.
        self._surface = _Surface()
        #: The file being read: ``{exported name: the node it is, or None}`` — what `require`
        #: returns, as far as the file says.
        self._file_exports: dict[str, str | None] = {}
        #: The file being read: every ``<exports object>.name = …`` it makes, exported or not. A
        #: same-file read of `exports.show` is the function, even after the export was undone.
        self._file_members: dict[str, str | None] = {}
        #: The file being read: ``{local: imported name}``, None for a whole-module or default
        #: import. Handed to the data-layer reader, which must know *which* export a local is.
        self._import_names: dict[str, str | None] = {}
        #: Every module's Sequelize model bindings: ``module id -> {name: model}``, where a name
        #: is a variable (`const Booking = sequelize.define('gig', …)`) or an `exports.X` it was
        #: assigned to. Drained by `finalize`, which settles imported association ends with it.
        self._models: dict[str, dict[str, str]] = {}

    def extract(self, *, path: Path, module: str, rel: str) -> FactBatch:
        if any(path.with_suffix(s).is_file() for s in (".ts", ".tsx")):
            # `foo.js` beside `foo.ts` is `tsc`'s output. Both map to `ts:foo`, the walk reaches
            # the `.js` first, and the first grounded node wins — so read, it took over the
            # TypeScript module's nodes and provenance, and `explain_symbol` pointed at the build.
            # Generated code is skipped by name everywhere else; this is the same rule, by sibling,
            # and it covers `.mjs`/`.cjs` too: whatever wrote them, they collide on the same id.
            return FactBatch()
        return _retag(super().extract(path=path, module=module, rel=rel))

    def finalize(self, batch: FactBatch) -> FactBatch:
        """The parent's deferred member calls, then two checks on what was resolved by name.

        JavaScript resolves more calls *by name* than TypeScript does — every `m.f()` through a
        `require` binding, every `app.get('/', site.index)` — and a name is a claim, not a fact.

        1. **Through the export map** (see `_export_surface` for its three tiers). A cross-file
           edge into a module names `ts:m.<name>…`, and what the module exports under that name is
           known only from its own file: `module.exports = { run: helper }` makes `m.run` *be*
           `helper`, whatever else is called `run` there. So the edge is rewritten to the real
           target, or dropped when the module never writes that name to its exports — whether the
           map is exact (*readable*) or the set of names it may export (*names-known*). An
           *opaque* module is left to step 2 alone, except for names known to be dead — enforcing
           a map it only half read dropped true edges into `Object.assign` exports.
        2. **Existence.** A ``CALLS``, ``IMPLEMENTS``, ``EXPOSES`` or ``REFERENCES`` edge from a
           JavaScript file whose source or target no one declares is dropped rather than left
           dangling — the parent's skip-rather-than-guess rule, extended to what this front-end
           adds.

        The export maps live on the run (:class:`~orchestrator.pkg.extractor.ExtractionRun`), not on
        this front-end, because TypeScript files call into them too. Deferred member calls —
        `new Handler().run()`, from a `.js` or a `.ts` file — are routed by the parent's
        `finalize` before its existence check; the edges it routed are recorded on the run and
        skipped here, so the result does not depend on which finalizer runs first.
        """
        from orchestrator.pkg.js_orm import settle

        run = self._run
        models, self._models = self._models, {}
        routed = _through_exports(batch, _export_router(batch, run), run.routed)
        return _drop_unlanded(settle(super().finalize(routed), run.exports, models))

    def _imports(
        self, decls: list[TSNode | None], module_id: str, source: bytes, rel: str, batch: FactBatch
    ) -> tuple[dict[str, str], set[str]]:
        by_local, namespaces = super()._imports(decls, module_id, source, rel, batch)
        self._file_exports, self._file_members = {}, {}
        self._import_names = _esm_import_names(decls, source)
        whole_modules: set[str] = set()
        for node in decls:
            if node is None:
                continue
            if node.type in _FUNC_CONST_DECLS:
                for declarator in node.named_children:
                    if declarator.type != "variable_declarator":
                        continue
                    bound = _require_binding(declarator, source)
                    if bound is None:
                        continue
                    spec, locals_, is_namespace = bound
                    _import_edge(spec, module_id, rel, declarator.start_point[0] + 1, batch)
                    for local in locals_:
                        by_local[local] = spec
                        self._import_names[local] = None if is_namespace else local
                    if is_namespace:
                        namespaces.update(locals_)
                        whole_modules.update(locals_)
            elif node.type == "expression_statement" and node.named_children:
                side_effect = _require_spec(node.named_children[0], source)
                if side_effect is not None:  # `require('./polyfill')` — imported for its side effects
                    _import_edge(side_effect, module_id, rel, node.start_point[0] + 1, batch)
        # A whole-module `require` binding is a namespace for `m.f()` and is not callable as one:
        # the parent reads this, per file, and only this front-end ever fills it.
        self._uncallable = frozenset(whole_modules)
        # A whole-module binding used as a value — `extends B`, `new B()` — is the module's
        # default, whatever it is called; so is an ESM default import. The parent resolves either
        # by the local name, so the run is told which ids came from one (see `_export_router`).
        defaults = whole_modules | {
            n for n, imported in _esm_import_names(decls, source).items() if imported is None
        }
        for local in defaults:
            if local in by_local:
                self._run.whole.add((rel, _import_target(by_local[local], local, rel)))
        esm = rel.endswith(".mjs") or any(
            d is not None
            and (
                d.type == "import_statement" or (d.parent is not None and d.parent.type == "export_statement")
            )
            for d in decls
        )
        self._surface = _export_surface(decls, source, module_id, frozenset(by_local), esm)
        return by_local, namespaces

    def _emit_module(
        self, root: TSNode, module_id: str, source: bytes, rel: str, batch: FactBatch, imports: dict[str, str]
    ) -> None:
        """The data layer (see :mod:`js_orm`), then this file's export map for `finalize`."""
        from orchestrator.pkg.js_orm import scan

        objects = frozenset({"exports", "module.exports"} | self._surface.aliases)
        bindings = scan(root, module_id, source, rel, batch, imports, self._import_names, objects)
        if bindings:
            self._models[module_id] = bindings
        surface = self._surface
        if not surface.cjs:
            return
        exported = self._file_exports
        if surface.alias_object_span is not None:  # `const api = {find, create}; module.exports = api`
            obj = root.descendant_for_byte_range(*surface.alias_object_span)
            if obj is not None and obj.type == "object":
                for member in obj.named_children:
                    _emit_object_member(member, module_id, source, rel, batch, [], exported)
        certain = dict(exported)  # plain top-level `<ref>.f = …` statements, read in order
        for name, target in surface.extra:
            exported.setdefault(name, target)
        # An ESM `export` beside CommonJS exports is a surface this pass does not merge.
        mixed = any(child.type == "export_statement" for child in root.named_children)
        if not mixed:
            default = f"{module_id}.{surface.default}" if surface.default else None
            # An opaque map keeps only what certain statements wrote: a write under a loop or in a
            # function may be onto a binding this pass misread, and kept, it became a rename.
            names = dict(exported) if surface.tier != _OPAQUE else certain
            dead = surface.dead - set(names)
            self._run.exports[module_id] = ExportMap(rel, names, surface.tier, default, dead)

    def _route_handler(
        self, module_id: str, imports: dict[str, str], namespaces: set[str], source: bytes, rel: str
    ) -> Callable[[TSNode], str | None] | None:
        """Bind `app.get('/', site.index)` — the CommonJS way to route to another file's export.

        Two spellings: a member of a `require` namespace (`site.index`, where
        `site = require('./site')`), routed through that module's export map in `finalize`; and a
        member of this module's own exports object (`exports.index`, or an alias of it), which is
        whatever this file assigned there — read as it stands, so an export undone later is still
        the function this line refers to.
        """
        surface, members = self._surface, self._file_members

        def resolve(member: TSNode) -> str | None:
            owner = member.child_by_field_name("object")
            prop = _field_text(member, "property", source)
            if owner is None or not _IDENTIFIER.match(prop):
                return None
            name = _text(owner, source)
            if surface.is_exports_object(name, owner.type):
                return members.get(prop)
            if owner.type == "identifier" and name in namespaces and name in imports:
                return _import_target(imports[name], prop, rel)
            return None

        return resolve

    def _emit_const_functions(
        self,
        node: TSNode,
        module_id: str,
        source: bytes,
        rel: str,
        batch: FactBatch,
        funcs: list[tuple[str, str | None, TSNode]],
        local_funcs: dict[str, str],
    ) -> None:
        """The parent's function-valued consts, then a literal at the end of a *declared* chain.

        `var api = module.exports = { run }` makes the literal the exports object, as the
        statement form does; read only as a statement, that map was left empty and enforced.
        """
        super()._emit_const_functions(node, module_id, source, rel, batch, funcs, local_funcs)
        for declarator in node.named_children:
            value = declarator.child_by_field_name("value")
            if value is None:
                continue
            lefts, right = _chain_nodes(value)
            chained = any(_text(left, source) == "module.exports" for left in lefts)
            if chained and right is not None and self._surface.is_object(right):
                for member in right.named_children:
                    _emit_object_member(member, module_id, source, rel, batch, funcs, self._file_exports)

    def _emit_statement(
        self,
        node: TSNode,
        module_id: str,
        source: bytes,
        rel: str,
        batch: FactBatch,
        funcs: list[tuple[str, str | None, TSNode]],
        local_funcs: dict[str, str],
    ) -> None:
        """CommonJS exports — the assignments that are a CommonJS module's public surface.

        What *counts* as exported is decided by `_export_surface`; every assignment onto the
        exports object still gets its node, because the function it assigns is real whether or
        not a later line undoes the export. `exports.a = exports.b = f` exports both names.
        """
        if node.type != "expression_statement" or not node.named_children:
            return
        expr = node.named_children[0]
        if expr.type != "assignment_expression":
            return
        lefts, right = _chain_nodes(expr)
        if right is None:
            return
        line = node.start_point[0] + 1
        surface = self._surface
        emitted = False
        for left in lefts:
            if left.type != "member_expression":
                continue
            if _text(left, source) == "module.exports":
                if surface.is_object(right):
                    for member in right.named_children:
                        _emit_object_member(member, module_id, source, rel, batch, funcs, self._file_exports)
                elif right.type in _FUNCTION_VALUES and surface.single:
                    # The default export. Its function is real and gets a node; it is not a
                    # *member* a caller can name as `m.build`, so it joins no export map.
                    name = _field_text(right, "name", source)
                    if name and not emitted:
                        _emit_export(name, right, module_id, rel, line, batch, funcs)
                        emitted = True
                continue
            owner = left.child_by_field_name("object")
            prop = _field_text(left, "property", source)
            if owner is None or not _IDENTIFIER.match(prop):
                continue
            owner_text = _text(owner, source)
            if not surface.is_exports_object(owner_text, owner.type):
                continue
            if right.type in _FUNCTION_VALUES:
                target: str | None = f"{module_id}.{prop}"
                _emit_export(prop, right, module_id, rel, line, batch, funcs)
            else:
                target = _value_target(right, module_id, source)
            self._file_members[prop] = target
            if surface.names_exports(owner_text, owner.type, node.start_byte):
                self._file_exports[prop] = target


def _esm_import_names(decls: list[TSNode | None], source: bytes) -> dict[str, str | None]:
    """``{local: imported name}`` for ESM imports; None for a default or namespace import."""
    out: dict[str, str | None] = {}
    for node in decls:
        if node is None or node.type != "import_statement":
            continue
        for clause in node.named_children:
            if clause.type != "import_clause":
                continue
            for child in clause.named_children:
                if child.type == "identifier":  # default import
                    out[_text(child, source)] = None
                elif child.type == "namespace_import" and child.named_children:
                    out[_text(child.named_children[-1], source)] = None
                elif child.type == "named_imports":
                    for spec in child.named_children:
                        if spec.type != "import_specifier":
                            continue
                        imported = _field_text(spec, "name", source)
                        local = _field_text(spec, "alias", source) or imported
                        if local:
                            out[local] = imported
    return out


#: The three tiers a CommonJS surface is read into — by what this pass *has read* of it.
_READABLE, _NAMES, _OPAQUE = "readable", "names-known", "opaque"


@dataclass(frozen=True)
class _Surface:
    """What a CommonJS file's `module.exports` is, as far as the source leaves no doubt."""

    #: Top-level names that *are* the exports object (`var app = … = module.exports = {}`).
    aliases: frozenset[str] = frozenset()
    #: Whether `exports.f = …` still reaches `module.exports`.
    exports_ok: bool = True
    #: Where `exports` is first rebound on its own (`exports = o`, `var exports = …`), or -1: a
    #: write through `exports` before it lands on the exported object, one after it does not.
    rebound_at: int = -1
    #: Whether `module.exports.f = …` / `alias.f = …` names the object that is exported.
    members_ok: bool = True
    #: The byte span of the object literal `module.exports` was set to, when it was. A span and
    #: not the node: tree-sitter returns a fresh wrapper each time a node is reached.
    object_span: tuple[int, int] | None = None
    #: The span of the object literal an *alias* was declared as (`const api = {…}`).
    alias_object_span: tuple[int, int] | None = None
    #: A declared class or named function that *is* `module.exports` — its default, not a member.
    default: str | None = None
    #: Where the single `module.exports = …` sits, or -1 when there is none.
    offset: int = -1
    #: Whether `module.exports` is assigned exactly once.
    single: bool = False
    #: Whether the file exports anything the CommonJS way at all.
    cjs: bool = False
    #: What this pass has read of the surface — see `_export_surface`.
    tier: str = _READABLE
    #: Exported names the top-level statements do not show, with the node each is: a write under
    #: a branch or in a function, a subscript, a merge, the keys of a frozen or ambiguous literal.
    extra: tuple[tuple[str, str | None], ...] = ()
    #: Names written only through `exports` after `module.exports` replaced it — never exported.
    dead: frozenset[str] = frozenset()

    @property
    def readable(self) -> bool:
        return self.tier == _READABLE

    def is_object(self, node: TSNode) -> bool:
        return self.object_span == (node.start_byte, node.end_byte)

    def is_exports_object(self, owner: str, owner_type: str) -> bool:
        """Whether ``owner`` is lexically the exports object — exported or not, as it turns out."""
        return owner in ("exports", "module.exports") or (
            owner_type == "identifier" and owner in self.aliases
        )

    def names_exports(self, owner: str, owner_type: str, at: int) -> bool:
        """Whether `<owner>.f = …` at byte ``at`` puts `f` on the object `require` returns.

        Order matters for `exports` and `module.exports`: a member set *before* `module.exports`
        is replaced was set on the object that got replaced. An alias is order-free — it is the
        exported object itself, whenever it is augmented.
        """
        if owner == "exports":
            return self.exports_ok and at > self.offset and (self.rebound_at < 0 or at < self.rebound_at)
        if owner == "module.exports":
            return self.members_ok and at > self.offset
        return owner_type == "identifier" and owner in self.aliases and self.members_ok


def _chain(node: TSNode, source: bytes) -> tuple[list[str], TSNode | None]:
    """``a = b = c = V`` → (``["a", "b", "c"]``, ``V``)."""
    lefts, final = _chain_nodes(node)
    return [_text(left, source) for left in lefts], final


def _chain_nodes(node: TSNode) -> tuple[list[TSNode], TSNode | None]:
    lefts: list[TSNode] = []
    current: TSNode | None = node
    while current is not None and current.type == "assignment_expression":
        left = current.child_by_field_name("left")
        if left is None:
            return lefts, None
        lefts.append(left)
        current = current.child_by_field_name("right")
    return lefts, current


def _walk(node: TSNode) -> list[TSNode]:
    out: list[TSNode] = []
    stack = [node]
    while stack:
        current = stack.pop()
        out.append(current)
        stack.extend(current.named_children)
    return out


#: What lies between a top-level statement and an assignment *it* makes — `a = b = c`, or
#: `var x = a = b`. Anything else between them (a function, a branch, a loop) makes it nested.
_STATEMENT_CHAIN = frozenset(
    {
        "expression_statement",
        "assignment_expression",
        "variable_declarator",
        "lexical_declaration",
        "variable_declaration",
    }
)


def _nested(node: TSNode, top: TSNode) -> bool:
    """Whether ``node`` sits inside something other than the top-level statement's own chain.

    The top-level statement itself counts: in a brace-less `if (c) exports.f = f;` the
    assignment's statement sits directly under the `if`, so a walk that stopped on reaching the
    top-level node never saw a branch at all.
    """
    if top.type not in _STATEMENT_CHAIN:
        return True
    current = node.parent
    while current is not None and (current.start_byte, current.end_byte) != (top.start_byte, top.end_byte):
        if current.type not in _STATEMENT_CHAIN:
            return True
        current = current.parent
    return False


def _plain_object(obj: TSNode) -> bool:
    """An object literal whose every member this pass names: no spread, no computed key.

    `__proto__: p` is not a member either: it sets the prototype, and every member `p` has
    becomes callable on the export without being written in this file.
    """
    for child in obj.named_children:
        if child.type in ("shorthand_property_identifier", "comment"):
            continue
        if child.type not in ("pair", "method_definition"):
            return False  # a spread, or anything this pass does not read
        key = child.child_by_field_name("key" if child.type == "pair" else "name")
        if key is None or key.type == "computed_property_name" or key.text in (b"__proto__", b'"__proto__"'):
            return False
    return True


def _literal_names(obj: TSNode, module_id: str, source: bytes) -> dict[str, str | None]:
    """``{key: the node it is}`` for a plain object literal, without minting anything."""
    out: dict[str, str | None] = {}
    for member in obj.named_children:
        if member.type == "shorthand_property_identifier":
            name = _text(member, source)
            out[name] = f"{module_id}.{name}"
        elif member.type == "method_definition":
            name = _field_text(member, "name", source)
            out[name] = f"{module_id}.{name}"
        elif member.type == "pair":
            name = _field_text(member, "key", source).strip("\"'")
            value = member.child_by_field_name("value")
            if value is not None and value.type in _FUNCTION_VALUES:
                out[name] = f"{module_id}.{name}"
            elif value is not None:
                out[name] = _value_target(value, module_id, source)
    return {k: v for k, v in out.items() if _IDENTIFIER.match(k)}


def _descriptor_target(desc: TSNode | None, module_id: str, source: bytes) -> str | None:
    """The node a property descriptor's `value:` is — `{ value: find }` → `ts:m.find`."""
    if desc is None or desc.type != "object":
        return None
    for member in desc.named_children:
        if member.type == "pair" and _field_text(member, "key", source).strip("\"'") == "value":
            value = member.child_by_field_name("value")
            return _value_target(value, module_id, source) if value is not None else None
        if member.type == "shorthand_property_identifier" and _text(member, source) == "value":
            return None
    return None


def _object_create(node: TSNode | None, source: bytes, local: frozenset[str] = frozenset()) -> bool:
    """`Object.create(proto)` — a fresh object whose members are the ones the file assigns.

    Not when the prototype is declared in this file, or when a second argument adds properties:
    then members the file never writes onto the exports are callable on them.
    """
    if node is None or node.type != "call_expression":
        return False
    if _text(node.child_by_field_name("function"), source) != "Object.create":
        return False
    args = node.child_by_field_name("arguments")
    named = [a for a in args.named_children if a.type != "comment"] if args is not None else []
    return len(named) == 1 and _text(named[0], source).split(".", 1)[0] not in local


#: Where a binding can be introduced over part of a file — and where `this` stops being `exports`.
_FUNCTION_NODES = frozenset(
    {
        "function_declaration",
        "generator_function_declaration",
        "function_expression",
        "function",
        "generator_function",
        "arrow_function",
        "method_definition",
    }
)
_PATTERNS = frozenset(
    {"object_pattern", "array_pattern", "pair_pattern", "assignment_pattern", "rest_pattern"}
)
_FREEZES = frozenset({"Object.freeze", "Object.seal", "Object.preventExtensions"})
_MERGES = frozenset(
    {"Object.assign", "Object.defineProperty", "Object.defineProperties", "Reflect.defineProperty"}
)


def _param_names(fn: TSNode, source: bytes) -> list[str]:
    params = fn.child_by_field_name("parameters")
    if params is None:
        return _pattern_names(fn.child_by_field_name("parameter"), source)
    out: list[str] = []
    for param in params.named_children:
        if param.type in ("required_parameter", "optional_parameter"):
            param = param.child_by_field_name("pattern") or param
        out.extend(_pattern_names(param, source))
    return out


def _declares(scope: TSNode, name: str, source: bytes) -> bool:
    """Whether ``scope`` binds ``name`` itself, anywhere in it but inside a nested function.

    Over the whole function, so a `let` in an inner block counts for all of it: that can only
    hide a reference to the exports object, and a hidden one errs the same way as an
    unrecognised one would not — toward a narrower map. Accepted, and rare: it needs a local
    spelled like the exports alias.
    """
    stack = list(scope.named_children)
    while stack:
        node = stack.pop()
        if node.type in ("function_declaration", "generator_function_declaration", "class_declaration"):
            if _field_text(node, "name", source) == name:
                return True
            if node.type != "class_declaration":
                continue
        elif node.type in _FUNCTION_NODES:
            continue
        elif node.type == "variable_declarator" and name in _pattern_names(
            node.child_by_field_name("name"), source
        ):
            return True
        stack.extend(node.named_children)
    return False


def _loop_declares(loop: TSNode, name: str, source: bytes) -> bool:
    """Whether a `for` header binds ``name`` with `let`/`const` for the loop: `for (const api of
    xs)`, `for (let api = …;;)`. A header `var` belongs to the function or the module."""
    if loop.type == "for_in_statement":
        kind = loop.child_by_field_name("kind")
        return (
            kind is not None
            and _text(kind, source) in ("let", "const")
            and name in _pattern_names(loop.child_by_field_name("left"), source)
        )
    init = loop.child_by_field_name("initializer")
    if init is None or init.type != "lexical_declaration":
        return False
    return any(name in _pattern_names(d.child_by_field_name("name"), source) for d in init.named_children)


def _block_declares(block: TSNode, name: str, source: bytes) -> bool:
    """Whether a block binds ``name`` itself with `let`, `const` or `class` — not `var`, which
    belongs to the enclosing function (or module) wherever it is written."""
    for child in block.named_children:
        if child.type == "class_declaration" and _field_text(child, "name", source) == name:
            return True
        if child.type == "lexical_declaration":
            for declarator in child.named_children:
                if name in _pattern_names(declarator.child_by_field_name("name"), source):
                    return True
    return False


def _shadowed(
    ref: TSNode, name: str, source: bytes, cache: dict[tuple[int, int, str], bool] | None = None
) -> bool:
    """Whether ``name`` at ``ref`` is a binding of an enclosing function or block, not the module's.

    A block counts even at the top level: `{ const api = {}; api.run = helper }` writes onto its
    own `api`. So does the file itself for `module`, which only the wrapper otherwise binds.
    ``cache`` holds each (scope, name) answer for one file — without it, every reference in a
    function re-walked the whole function: 14 s on a 47 KB express-style file.
    """
    memo = cache if cache is not None else {}
    current = ref.parent
    while current is not None:
        key = (current.start_byte, current.end_byte, name)
        if key in memo:
            if memo[key]:
                return True
        elif current.type in _FUNCTION_NODES:
            body = current.child_by_field_name("body")
            # a named function *expression* binds its own name inside itself
            own = current.type in ("function_expression", "function", "generator_function") and (
                _field_text(current, "name", source) == name
            )
            memo[key] = (
                own
                or name in _param_names(current, source)
                or (body is not None and _declares(body, name, source))
            )
            if memo[key]:
                return True
        elif current.type in ("for_statement", "for_in_statement"):
            memo[key] = _loop_declares(current, name, source)
            if memo[key]:
                return True
        elif current.type == "catch_clause":
            memo[key] = name in _pattern_names(current.child_by_field_name("parameter"), source)
            if memo[key]:
                return True
        elif current.type == "statement_block" and (
            current.parent is None or current.parent.type not in _FUNCTION_NODES
        ):
            memo[key] = _block_declares(current, name, source)
            if memo[key]:
                return True
        elif current.type == "program" and name == "module":
            # `var module = { exports: {} }` at the top level: every `module` in the file is that
            # one, and the real module's exports stay `{}`
            memo[key] = _file_declares(current, name, source)
            if memo[key]:
                return True
        current = current.parent
    return False


def _file_declares(program: TSNode, name: str, source: bytes) -> bool:
    """Whether the file itself binds ``name``: a `var` anywhere outside a function (a top-level
    `for (var …)` too), or a `let`/`const`/`class`/`function` written directly at the top level.
    A `{ let module = … }` block binds it for the block alone, not the file."""
    for child in program.named_children:
        if child.type in ("function_declaration", "generator_function_declaration", "class_declaration"):
            if _field_text(child, "name", source) == name:
                return True
        elif child.type == "lexical_declaration" and any(
            name in _pattern_names(d.child_by_field_name("name"), source) for d in child.named_children
        ):
            return True
    stack = list(program.named_children)
    while stack:
        node = stack.pop()
        if node.type in _FUNCTION_NODES or node.type == "class_body":
            continue
        if node.type == "variable_declaration" and any(
            name in _pattern_names(d.child_by_field_name("name"), source) for d in node.named_children
        ):
            return True
        if (
            node.type == "for_in_statement"
            and _field_text(node, "kind", source) == "var"
            and name in _pattern_names(node.child_by_field_name("left"), source)
        ):
            return True
        stack.extend(node.named_children)
    return False


def _top_level_this(node: TSNode) -> bool:
    """Top-level `this` is `exports`; inside any function but an arrow, or a class, it is not."""
    current = node.parent
    while current is not None:
        if (
            current.type in _FUNCTION_NODES and current.type != "arrow_function"
        ) or current.type == "class_body":
            return False
        current = current.parent
    return True


def _itself(value: TSNode | None, source: bytes) -> bool:
    """`module.exports = exports`: the object it already is — no replacement."""
    return value is not None and value.type == "identifier" and _text(value, source) == "exports"


def _in_function(node: TSNode, top: TSNode) -> bool:
    """Whether ``node`` sits inside a function below the top-level statement ``top``."""
    current = node.parent
    while current is not None and (current.start_byte, current.end_byte) != (top.start_byte, top.end_byte):
        if current.type in _FUNCTION_NODES:
            return True
        current = current.parent
    return top.type in _FUNCTION_NODES


def _same(a: TSNode | None, b: TSNode) -> bool:
    return a is not None and (a.start_byte, a.end_byte, a.type) == (b.start_byte, b.end_byte, b.type)


@dataclass(frozen=True)
class _Write:
    """A member the source writes onto an exports reference: `<ref>.name = value`, and where."""

    owner: str
    name: str
    value: TSNode | None
    #: ``member`` (`<ref>.f = …`), ``subscript`` (`<ref>['f'] = …`) or ``merge`` (`Object.assign`…).
    kind: str


def _reference(ref: TSNode, owner: str, source: bytes) -> list[_Write] | None:
    """What one reference to the exports object does — or None when it is not a recognised form.

    The recognised forms (the allowlist, js-review-followup D6): a member write `<ref>.f = …` or
    `<ref>['f'] = …` with a string key; a member read `<ref>.f`; a link in the assignment chain
    that makes `module.exports` (`a = exports = module.exports = …`); `Object.assign(<ref>, {…})`,
    `Object.defineProperty(<ref>, 'f', …)`, `Reflect.defineProperty(<ref>, 'f', …)` and
    `Object.defineProperties(<ref>, {…})` with literal names; `typeof exports`; the declaration
    of an alias or of the default; calling or constructing it (`new User()` on the default).
    Everything else — the object handed to a function, copied to
    another name, destructured into, compared — is not, and the caller makes the surface opaque.
    """
    parent = ref.parent
    if parent is None:
        return None
    kind = parent.type
    if kind in ("member_expression", "subscript_expression") and _same(
        parent.child_by_field_name("object"), ref
    ):
        above = parent.parent
        if above is None:
            return []
        if above.type in _PATTERNS or (
            above.type == "pair" and above.parent is not None and above.parent.type == "object_pattern"
        ):
            return None  # `({ f: exports.f } = …)` — written by destructuring
        written = above.type in ("assignment_expression", "augmented_assignment_expression") and _same(
            above.child_by_field_name("left"), parent
        )
        written = written or above.type == "update_expression"
        if not written:
            return []  # a read: `exports.f`, `exports.f()`, `delete exports.f` (a declared gap)
        if kind == "member_expression":
            name = _field_text(parent, "property", source)
            if name == "__proto__":
                return None  # sets the prototype: every member of it is callable, none written
        else:
            index = parent.child_by_field_name("index")
            if index is None or index.type != "string":
                return None  # `exports[k] = …` — a name this pass cannot know
            name = _text(index, source).strip("\"'`")
        value = None
        if above.type == "assignment_expression":
            _, value = _chain(above, source)
        return [_Write(owner, name, value, "member" if kind == "member_expression" else "subscript")]
    if kind == "assignment_expression":
        if _same(parent.child_by_field_name("left"), ref):
            targets, _ = _chain(parent, source)
            return [] if owner in ("exports", "module.exports") or "module.exports" in targets else None
        # `module.exports = api`: the value that becomes the exports object; and
        # `module.exports.Post = Post`, the default stored on itself — a value, not a write.
        left = parent.child_by_field_name("left")
        if _text(left, source) == "module.exports":
            return []
        holder = (
            left.child_by_field_name("object")
            if left is not None and left.type == "member_expression"
            else None
        )
        return (
            []
            if holder is not None and _text(holder, source) in ("exports", "module.exports", owner)
            else None
        )
    if kind in (
        "variable_declarator",
        "function_declaration",
        "class_declaration",
        "generator_function_declaration",
    ):
        return [] if _same(parent.child_by_field_name("name"), ref) else None
    if kind == "unary_expression" and _text(parent, source).startswith("typeof"):
        return []
    if kind == "spread_element":
        return []  # `{ ...exports }` copies the members out: a read, not a write
    if kind in ("call_expression", "new_expression") and _same(
        parent.child_by_field_name("function" if kind == "call_expression" else "constructor"), ref
    ):
        return []  # `new User()` / `create()` on the default: a use, not a write
    if kind == "arguments" and parent.parent is not None:
        call = parent.parent
        fn = _text(call.child_by_field_name("function"), source)
        args = [a for a in parent.named_children if a.type != "comment"]
        if fn not in _MERGES or not args or not _same(args[0], ref):
            return None
        return _merge(fn, args, owner, source)
    return None


def _merge(fn: str, args: list[TSNode], owner: str, source: bytes) -> list[_Write] | None:
    """The members `Object.assign` / `defineProperty` / `defineProperties` write, or None."""
    if fn == "Object.assign":
        if not all(a.type == "object" and _plain_object(a) for a in args[1:]):
            return None
        writes: list[_Write] = []
        for obj in args[1:]:
            for member in obj.named_children:
                if member.type == "shorthand_property_identifier":
                    writes.append(_Write(owner, _text(member, source), member, "merge"))
                elif member.type == "pair":
                    key = _field_text(member, "key", source).strip("\"'")
                    writes.append(_Write(owner, key, member.child_by_field_name("value"), "merge"))
                elif member.type == "method_definition":
                    writes.append(_Write(owner, _field_text(member, "name", source), member, "merge"))
        return writes
    if fn == "Object.defineProperties":
        if len(args) < 2 or args[1].type != "object" or not _plain_object(args[1]):
            return None
        return [
            _Write(owner, _field_text(m, "key", source).strip("\"'"), m.child_by_field_name("value"), "merge")
            for m in args[1].named_children
            if m.type == "pair"
        ]
    if len(args) < 2 or args[1].type != "string":
        return None
    name = _text(args[1], source).strip("\"'`")
    if name == "__esModule":  # Babel's marker on every file it emits — no member anyone calls
        return []
    return [_Write(owner, name, args[2] if len(args) > 2 else None, "merge")]


def _references(
    decls: list[TSNode | None], source: bytes, aliases: frozenset[str], esm: bool = False
) -> tuple[list[tuple[_Write, TSNode]], bool, bool]:
    """Every reference to the exports object: ``(the writes among them, each with its top-level
    statement; whether every one was recognised; whether there was any)``.

    A reference inside a function that binds the same name is that function's own and is
    skipped — `function tag(api) { return api }` says nothing about the module's `api`.
    """
    writes: list[tuple[_Write, TSNode]] = []
    seen, recognised = False, True
    cache: dict[tuple[int, int, str], bool] = {}
    declared: dict[str, int] = {}  # how many top-level declarations name each alias
    for top in decls:
        if top is None:
            continue
        for node in _walk(top):
            if node.type == "shorthand_property_identifier":
                # `{ exports }` stores the exports object in another one; a write through it is
                # a spelling the finder cannot follow
                text = _text(node, source)
                if (text == "exports" or text in aliases) and not _shadowed(node, text, source, cache):
                    seen, recognised = True, False
                continue
            if node.type == "identifier" and _text(node, source) == "module":
                if _shadowed(node, "module", source, cache) or _module_use(node, source):
                    continue
                seen, recognised = True, False  # `const mod = module`, `module['exports']`
                continue
            if node.type == "identifier":
                owner = _text(node, source)
                if owner != "exports" and owner not in aliases:
                    continue
                if _shadowed(node, owner, source, cache):
                    continue
                parent = node.parent
                if (
                    parent is not None
                    and parent.type == "variable_declarator"
                    and _same(parent.child_by_field_name("name"), node)
                ):
                    declared[owner] = declared.get(owner, 0) + 1
            elif node.type == "member_expression":
                if _text(node, source) != "module.exports":
                    continue
                if _shadowed(node, "module", source, cache):
                    continue
                owner = "module.exports"
            elif node.type == "this":
                if esm or not _top_level_this(node):
                    continue  # an ES module's top-level `this` is `undefined`
                owner = "this"  # top-level `this` is the object `exports` starts as
            else:
                continue
            seen = True
            found = _reference(node, owner, source)
            if found is None:
                recognised = False
                continue
            writes.extend((w, top) for w in found)
    if any(count > 1 for count in declared.values() if count):
        recognised = False  # an alias declared twice names two objects, and this pass cannot order them
    return writes, recognised, seen


def _module_use(node: TSNode, source: bytes) -> bool:
    """Whether a bare `module` is a use that cannot reach its exports under another name:
    `module.exports` (read by the finder itself), a read of another member (`module.hot`),
    `typeof module`, a comparison (`require.main === module`), or an argument to a call —
    `require('./logger')(module)` reads `module.id`, and treating it as opaque handed every
    unexported function of an ordinary module to a name lookup. (A callee that *writes*
    `module.exports` through its parameter is not seen: declared in the module docstring.)"""
    parent = node.parent
    if parent is None:
        return False
    if parent.type == "member_expression" and _same(parent.child_by_field_name("object"), node):
        return True
    if parent.type == "unary_expression" and _text(parent, source).startswith("typeof"):
        return True
    if parent.type == "arguments":
        return True
    operator = parent.child_by_field_name("operator") if parent.type == "binary_expression" else None
    # `module || {}` yields the module itself: only a comparison is harmless
    return operator is not None and _text(operator, source) in ("===", "!==", "==", "!=", "instanceof")


def _write_target(write: _Write, module_id: str, source: bytes) -> str | None:
    value = write.value
    if value is None:
        return None
    if value.type in _FUNCTION_VALUES or value.type == "method_definition":
        return f"{module_id}.{write.name}"
    if value.type == "shorthand_property_identifier":
        return f"{module_id}.{_text(value, source)}"
    if value.type == "object":  # a property descriptor, from `defineProperty`
        return _descriptor_target(value, module_id, source)
    return _value_target(value, module_id, source)


def _export_surface(
    decls: list[TSNode | None],
    source: bytes,
    module_id: str,
    imported: frozenset[str] = frozenset(),
    esm: bool = False,
) -> _Surface:
    """Which object `module.exports` is, which top-level names are it — and how much of it is read.

    The classic CommonJS library names the exports object once and augments it; express's `lib/`
    spells 43 of its 49 exported functions this way:

        var app = exports = module.exports = {};     // a declaration, chained through
        app.init = function init() { … };

        var res = Object.create(http.ServerResponse.prototype);
        module.exports = res;                        // an assignment, naming a declared object
        res.send = function send(body) { … };

    Either way `require('./application').init` *is* `app.init`. But every one of these facts can
    be undone by the same file: `module.exports` replaced without rebinding `exports` (so
    `exports.f = …` exports nothing), `exports` rebound on its own, an alias reassigned.

    **Three tiers, each defined by what was read** (js-review-followup §3.1):

    - **readable** — every reference to the exports object is a recognised form (`_reference`,
      an allowlist: a shape nobody thought of is not recognised), and the value of
      `module.exports` is one this pass reads in full: a plain object literal, a declared
      function or class, a function value, `Object.create` of a prototype from elsewhere, or a
      name declared as one of those. The export map is exact.
    - **names-known** — every reference is recognised, but a write sits under a branch, in a
      function or in a merge; or the value is `Object.freeze`/`Object.seal` of a plain literal;
      or `module.exports` is assigned inside a branch (UMD) and every value it is given is a
      plain literal. The map is the set of names the file *may* export — the union of every one
      it visibly writes. (Assigned twice at the top level is not ambiguous: straight-line code,
      the last assignment wins, and writes onto the objects it replaced are dead.)
    - **opaque** — some reference is not recognised (`const e = exports`, `mixin(exports, …)`,
      `exports[k] = …`, an alias reassigned), or the value hides its names (`make()`, a
      re-export). No map: calls into the module are resolved by name and kept if their target
      exists.

    A readable or names-known map is enforced alike — a call to a name the file never writes
    reaches nothing — which is the point of the middle tier: `Object.freeze({ f })` beside a
    private `secret` no longer hands `m.secret()` to a name lookup. An unrecognised reference is
    never read as readable — among the spellings the finder follows (see the module docstring for
    what it cannot see); a reader that cannot tell must not decide.
    """
    chains: list[tuple[list[str], TSNode | None, str | None, int]] = []
    rebound_at = -1  # where `exports` is first rebound on its own; writes after it go elsewhere

    def rebound(at: int) -> None:
        nonlocal rebound_at
        rebound_at = at if rebound_at < 0 else min(rebound_at, at)

    reassigned: set[str] = set()
    member_exports = False
    declared: dict[str, TSNode] = {}  # top-level `const x = <value>`
    callables: set[str] = set()  # top-level `class X` / `function X`
    # a file that declares its own `module` never writes the real one: its exports stay `{}`
    own = next((_shadowed(node, "module", source) for node in decls if node is not None), False)
    cjs_owner = "exports." if own else ("exports.", "module.exports.")
    for node in decls:
        if node is None:
            continue
        if node.type in ("class_declaration", "function_declaration"):
            name = node.child_by_field_name("name")
            if name is not None:
                callables.add(_text(name, source))
        elif node.type in _FUNC_CONST_DECLS:
            for declarator in node.named_children:
                name = declarator.child_by_field_name("name")
                value = declarator.child_by_field_name("value")
                if name is None or value is None or name.type != "identifier":
                    continue
                declared[_text(name, source)] = value  # `var exports = …`: rebound in the walk below
                targets, final = _chain(value, source)
                if "module.exports" in targets and not own and not _itself(final, source):
                    chains.append((targets, final, _text(name, source), declarator.end_byte))
                elif "exports" in targets:
                    rebound(declarator.start_byte)
        elif node.type == "expression_statement" and node.named_children:
            expr = node.named_children[0]
            if expr.type != "assignment_expression":
                continue
            targets, final = _chain(expr, source)
            if "module.exports" in targets and not own and not _itself(final, source):
                chains.append((targets, final, None, expr.end_byte))
            elif "exports" in targets:
                rebound(expr.start_byte)
            else:
                reassigned.update(t for t in targets if _IDENTIFIER.match(t))
                member_exports = member_exports or any(t.startswith(cjs_owner) for t in targets)
    # Every `module.exports = …` in the file, at any depth: one below the top level (UMD's
    # `if (typeof module …)`) makes the exported object a matter of which branch ran.
    values: list[TSNode | None] = []
    for node in decls:
        if node is None:
            continue
        for sub in _walk(node):
            if (
                sub.type == "variable_declarator"
                and _text(sub.child_by_field_name("name"), source) == "exports"
                and not _in_function(sub, node)
            ):
                rebound(sub.start_byte)  # `var exports = {}`, in a top-level block too, rebinds it
            left = sub.child_by_field_name("left") if sub.type == "assignment_expression" else None
            if (
                left is not None
                and _text(left, source) == "module.exports"
                and not _shadowed(left, "module", source)  # a `module` parameter's is not the module's
                and not _itself(_chain(sub, source)[1], source)
            ):
                values.append(_chain(sub, source)[1])
    local = frozenset(callables | set(declared))

    def named_by(chain: tuple[list[str], TSNode | None, str | None, int]) -> set[str]:
        _, final, alias, _ = chain
        out = {alias} if alias else set()
        if final is not None and final.type == "identifier":
            out.add(_text(final, source))
        return out

    aliases = {name for chain in chains for name in named_by(chain)}
    writes, recognised, seen = _references(decls, source, frozenset(aliases), esm)
    # Reassigned at the top level only, `module.exports` is straight-line code: the last
    # assignment wins, and whatever the earlier ones named is an object nobody exports.
    replaced: set[str] = set()
    replaced_keys: set[str] = set()  # the members of an object literal a later assignment replaced
    ambiguous = len(values) > len(chains)  # a `module.exports = …` below the top level
    if len(chains) > 1 and not ambiguous:
        replaced = aliases - named_by(chains[-1])
        aliases -= replaced
        for _, final, _, _ in chains[:-1]:
            if final is not None and final.type == "object":
                replaced_keys.update(_literal_names(final, module_id, source))
        chains = chains[-1:]

    if ambiguous:
        # Ambiguous: which object is exported depends on execution. Every name any assignment
        # writes is one it may export; if any value hides its names, none is known.
        union: dict[str, str | None] = {}
        hidden = not recognised
        for value in values:
            if value is None or value.type != "object" or not _plain_object(value):
                hidden = True
            else:
                union.update(_literal_names(value, module_id, source))
        rebinds = any("exports" in chain[0] for chain in chains)
        dead: set[str] = set()
        for write, _ in writes:
            if not _IDENTIFIER.match(write.name):
                continue
            if write.owner in ("exports", "this") and not rebinds:
                dead.add(write.name)  # `exports` still names the object `module.exports` replaced
            else:
                union.setdefault(write.name, _write_target(write, module_id, source))
        return _Surface(
            aliases=frozenset(aliases),
            exports_ok=False,
            members_ok=False,
            cjs=True,
            tier=_OPAQUE if hidden else _NAMES,
            extra=() if hidden else tuple(sorted(union.items())),
            dead=frozenset(dead - set(union)),
        )

    tier = _READABLE
    if not chains:
        # `var module = …` is CommonJS whatever it writes: its exports are `exports` alone
        surface = _Surface(
            rebound_at=rebound_at, cjs=member_exports or seen or (own and not esm), members_ok=not own
        )
    else:
        targets, final, alias, offset = chains[0]
        alias_object_span: tuple[int, int] | None = None
        default: str | None = None
        frozen: dict[str, str | None] = {}
        sealed = False
        tier = _OPAQUE
        if final is None:
            pass
        elif final.type == "identifier":
            named = _text(final, source)
            value = declared.get(named)
            if named in imported:
                pass  # `module.exports = require('./x')` in two steps: a re-export
            elif named in callables:
                default, tier = named, _READABLE
            elif value is not None and value.type == "object":
                if _plain_object(value):
                    alias_object_span, tier = (value.start_byte, value.end_byte), _READABLE
            elif value is not None and (
                value.type in _FUNCTION_VALUES or _object_create(value, source, local)
            ):
                tier = _READABLE
            elif value is not None:
                # `const Post = sequelize.define(…); module.exports = Post`: its names are hidden,
                # but `require` returns that binding — the default slot, not a member of it.
                default = named
        elif final.type == "object":
            tier = _READABLE if _plain_object(final) else _OPAQUE
        elif final.type in _FUNCTION_VALUES:
            tier = _READABLE
            default = _field_text(final, "name", source) or None
        elif _object_create(final, source, local):
            tier = _READABLE
        elif (
            final.type == "call_expression"
            and _text(final.child_by_field_name("function"), source) in _FREEZES
        ):
            args = final.child_by_field_name("arguments")
            named_args = [a for a in args.named_children if a.type != "comment"] if args is not None else []
            if len(named_args) == 1 and named_args[0].type == "object" and _plain_object(named_args[0]):
                frozen, tier = _literal_names(named_args[0], module_id, source), _NAMES
                sealed = True  # a member written onto it afterwards is silently dropped
        is_object = final is not None and final.type == "object"
        surface = _Surface(
            aliases=frozenset(aliases - reassigned),
            exports_ok="exports" in targets,
            rebound_at=rebound_at,
            members_ok=not sealed,
            object_span=(final.start_byte, final.end_byte) if final is not None and is_object else None,
            alias_object_span=alias_object_span,
            default=default,
            offset=offset,
            single=True,
            cjs=True,
            extra=tuple(sorted(frozen.items())),
        )
    if not recognised:
        tier = _OPAQUE

    # The writes the top-level statements do not show: `_emit_statement` records a plain
    # `<ref>.f = …` there, in order; the rest land here, and any that is not certain to run
    # makes the map a set of names the file *may* export.
    extra = dict(surface.extra)
    dead = set(replaced_keys)
    for write, top in writes:
        if not _IDENTIFIER.match(write.name):
            continue
        # A write that runs before `module.exports` is replaced — at the top level, under a branch
        # or not, but never inside a function, which may run after — lands on the object that
        # gets replaced. Top-level `this` is always that first object.
        early = (
            write.value is not None
            and write.value.start_byte < surface.offset
            and not _in_function(write.value, top)
        )
        # a write through `exports` after it was rebound — or in a function, which runs after
        after_rebind = surface.rebound_at >= 0 and (
            write.value is None
            or _in_function(write.value, top)
            or write.value.start_byte > surface.rebound_at
        )
        stale = (
            (write.owner == "exports" and (not surface.exports_ok or early or after_rebind))
            or (write.owner == "this" and surface.offset >= 0)  # `this` is `module.exports`'s first object
            or (write.owner == "module.exports" and (early or not surface.members_ok))
            or write.owner in replaced
        )
        if stale:
            dead.add(write.name)  # written onto an object nobody exports
            continue
        live = write.owner in ("exports", "this", "module.exports") or write.owner in surface.aliases
        if not live:
            continue
        nested = write.kind == "merge" or write.value is None or _nested(write.value, top)
        if (
            write.kind == "member"
            and not nested
            and top.type == "expression_statement"
            and write.owner != "this"
        ):
            continue  # a plain top-level `<ref>.f = …`: `_emit_statement` reads it, in order
        extra.setdefault(write.name, _write_target(write, module_id, source))
        if nested and tier == _READABLE:
            tier = _NAMES  # only a top-level `<ref>['f'] = …` is certain to run
    return replace(
        surface,
        tier=tier,
        extra=tuple(sorted(extra.items())),
        dead=frozenset(dead - set(extra)),
        cjs=surface.cjs or bool(extra) or bool(dead),
    )


def _value_target(value: TSNode, module_id: str, source: bytes) -> str | None:
    """The node an exported *value* is: `exports.run = helper` → `ts:m.helper`; anything else unknown.

    A module-level function or class is minted `<module>.<name>`, so an identifier's id is known
    without looking it up — and `finalize`'s existence check drops it if the name is a local
    variable rather than a declaration.
    """
    if value.type == "identifier" and _IDENTIFIER.match(_text(value, source)):
        return f"{module_id}.{_text(value, source)}"
    return None


def _through_exports(
    batch: FactBatch,
    route: Callable[[str, str | None], str | None],
    done: set[tuple[str, str, EdgeKind]],
) -> FactBatch:
    """Route cross-file edges into readable CommonJS modules through each module's export map.

    ``done`` holds the edges another finalizer already routed (TypeScript's deferred calls, when
    its finalizer ran first); they are final, and routing one twice would drop it.
    """
    out = FactBatch()
    for node in batch.nodes:
        out.add_node(node)
    for edge in batch.edges:
        if edge.kind in _THROUGH_EXPORTS and (edge.src, edge.dst, edge.kind) not in done:
            target = route(edge.dst, edge.provenance.file if edge.provenance is not None else None)
            if target is None:
                continue
            if target != edge.dst:
                edge = Edge(edge.src, target, edge.kind, edge.provenance)
        out.add_edge(edge)
    return out


def _require_spec(node: TSNode | None, source: bytes) -> str | None:
    """The specifier of a `require('…')` call, or ``None``.

    A literal string argument only. `require(path.join(dir, name))` names a module this pass
    cannot know, and a guessed specifier is an invented edge.
    """
    if node is None or node.type != "call_expression":
        return None
    fn = node.child_by_field_name("function")
    if fn is None or fn.type != "identifier" or _text(fn, source) != "require":
        return None
    args = node.child_by_field_name("arguments")
    named = [a for a in args.named_children if a.type != "comment"] if args is not None else []
    if len(named) != 1 or named[0].type != "string":
        return None
    return _text(named[0], source).strip("\"'") or None


def _require_binding(declarator: TSNode, source: bytes) -> tuple[str, list[str], bool] | None:
    """``(specifier, local names, is-namespace)`` for a declarator whose value is a `require`.

    Locals are bound only where resolution will name the right target. Resolution names an
    imported callee by its *local* name, so `{run: go} = require('./m')` would send `go()` to
    `ts:m.go` — a function the module need not have. Such a binding still records the import;
    it just binds nothing.
    """
    name = declarator.child_by_field_name("name")
    value = declarator.child_by_field_name("value")
    if name is None or value is None:
        return None
    spec = _require_spec(value, source)
    if spec is not None:
        if name.type == "identifier":  # const m = require('./m') — the whole module
            return spec, [_text(name, source)], True
        if name.type == "object_pattern":  # const {a, b} = require('./m')
            return spec, _destructured(name, source), False
        return spec, [], False
    if value.type == "member_expression":  # const a = require('./m').a
        spec = _require_spec(value.child_by_field_name("object"), source)
        if spec is None:
            return None
        local = _text(name, source) if name.type == "identifier" else ""
        member = _field_text(value, "property", source)
        return spec, ([local] if local and local == member else []), False
    return None


def _destructured(pattern: TSNode, source: bytes) -> list[str]:
    """Locals a `{…} = require(…)` binds under the export's own name."""
    out: list[str] = []
    for child in pattern.named_children:
        if child.type == "shorthand_property_identifier_pattern":  # {a}
            out.append(_text(child, source))
        elif child.type == "pair_pattern":  # {a: a} is fine; {a: b} is not
            key = _field_text(child, "key", source)
            value = child.child_by_field_name("value")
            if value is not None and value.type == "identifier" and _text(value, source) == key:
                out.append(key)
        elif child.type in ("object_assignment_pattern", "assignment_pattern"):  # {a = fallback}
            left = child.child_by_field_name("left")
            if left is not None and left.type == "shorthand_property_identifier_pattern":
                out.append(_text(left, source))
    return [n for n in out if n]


def _import_edge(spec: str, module_id: str, rel: str, line: int, batch: FactBatch) -> None:
    """The module node and ``IMPORTS`` edge a `require` implies — the parent's shape exactly."""
    resolved = _relative_module(spec, rel)
    mid = f"ts:{resolved}" if resolved is not None else f"ts:{spec}"
    batch.add_node(Node(mid, NodeKind.MODULE, resolved or spec, _LANG, external=resolved is None))
    batch.add_edge(Edge(module_id, mid, EdgeKind.IMPORTS, Provenance(rel, line)))


def _emit_object_member(
    member: TSNode,
    module_id: str,
    source: bytes,
    rel: str,
    batch: FactBatch,
    funcs: list[tuple[str, str | None, TSNode]],
    exported: dict[str, str | None],
) -> None:
    """One member of `module.exports = {…}`.

    A shorthand `{helper}` adds nothing: `helper` is a local function, already a node under the
    id a caller's `m.helper()` resolves to. A renamed `{run: helper}` is left alone for the
    opposite reason — `m.run()` names `ts:m.run`, which no declaration carries, and `finalize`
    drops the edge rather than inventing the node.
    """
    line = member.start_point[0] + 1
    if member.type == "pair":
        value = member.child_by_field_name("value")
        key = _field_text(member, "key", source).strip("\"'")
        if value is None or not _IDENTIFIER.match(key):
            return
        if value.type in _FUNCTION_VALUES:
            _emit_export(key, value, module_id, rel, line, batch, funcs)
            exported[key] = f"{module_id}.{key}"
        else:
            exported[key] = _value_target(value, module_id, source)  # {run: helper} → helper
    elif member.type == "method_definition":  # {run() {…}}
        name = _field_text(member, "name", source)
        if _IDENTIFIER.match(name):
            _emit_export(name, member, module_id, rel, line, batch, funcs)
            exported[name] = f"{module_id}.{name}"
    elif member.type == "shorthand_property_identifier":  # {helper}
        name = _text(member, source)
        exported[name] = f"{module_id}.{name}"


def _emit_export(
    name: str,
    fn: TSNode,
    module_id: str,
    rel: str,
    line: int,
    batch: FactBatch,
    funcs: list[tuple[str, str | None, TSNode]],
) -> None:
    if not _IDENTIFIER.match(name):
        return
    fid = f"{module_id}.{name}"
    batch.add_node(Node(fid, NodeKind.FUNCTION, name, _LANG, Provenance(rel, line)))
    batch.add_edge(Edge(module_id, fid, EdgeKind.CONTAINS, Provenance(rel, line)))
    body = fn.child_by_field_name("body")
    if body is not None:
        funcs.append((fid, None, body))


def _retag(batch: FactBatch) -> FactBatch:
    """Tag every node this file produced ``javascript`` — externals included, as Kotlin does."""
    out = FactBatch()
    for node in batch.nodes:
        out.add_node(node if node.language == _LANG else replace(node, language=_LANG))
    for edge in batch.edges:
        out.add_edge(edge)
    return out


def _drop_unlanded(batch: FactBatch) -> FactBatch:
    known = {n.id for n in batch.nodes}

    def unlanded(edge: Edge) -> bool:
        # Both ends: a CALLS or EXPOSES source is always a node this file emitted, but a
        # REFERENCES source is a model *named* in an association (`foo.hasMany(bar)`), and a
        # name is no more a fact at the source end than at the destination. (That no call lands
        # on a *module* is the last finalizer's rule — see `typescript_extractor._off_modules`.)
        return (
            edge.kind in _CHECKED
            and (edge.dst not in known or edge.src not in known)
            and edge.provenance is not None
            and edge.provenance.file.endswith(_SUFFIXES)
        )

    edges = list(batch.edges)
    kept = [e for e in edges if not unlanded(e)]
    if len(kept) == len(edges):
        return batch
    out = FactBatch()
    for node in batch.nodes:
        out.add_node(node)
    for edge in kept:
        out.add_edge(edge)
    return out


__all__ = ["JavaScriptExtractor"]
