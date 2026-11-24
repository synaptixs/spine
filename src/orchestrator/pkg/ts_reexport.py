"""TS/JS re-exports: which declaration a name exported *through* a module really is (B121).

A barrel declares nothing. `src/index.ts` saying `export { ok } from './result'` makes
`import { ok } from '../src'` name `ts:src.ok`, an id no file declares, so the call was dropped
as dangling and its caller lost: 401 calls on neverthrow, 2,289 on vue-core, almost all of them
tests, so `regression_gaps` called every barrel-exported function untested. CommonJS has the same
shape: `module.exports = require('./lib/express')` makes the root module stand for another one,
and `express.json()` from a test named `ts:<root>.json`.

Python solved this class in `python_reexport`; this is the TS/JS counterpart. Each front-end
records what a module re-exports while it reads the file (:func:`collect`), into the run's table
(:class:`ReexportTable`, on ``ExtractionRun.reexports``), and the finalizers ask the table what a
name really is before the existence check drops it (:meth:`ReexportTable.resolve`).

**What it reads**, all from the re-exporting file's own syntax, no inference:

=============================================  ====================================
`export { a, b as c } from './x'`              `c` is `x`'s `b`
`export * from './x'`                          every name `x` exports, except `default`
`export { default as X } from './x'`           `X` is `x`'s default declaration
`export * as ns from './x'`                    `ns` is the module `x`
`module.exports = require('./x')`              the module is `x`: every name, and its default
`module.exports = { a: require('./x').a }`     `a` is `x`'s `a`
`module.exports = { m: require('./x') }`       `m` is the module `x`
=============================================  ====================================

**Precision rules**, the same skip-rather-than-guess stance as the rest of the graph:

- A readable CommonJS export map decides what its module exports: a declared function is not a
  CommonJS export, so `module.exports = { run: helper }` makes `run` be `helper`, and a name the
  map does not list is not exported (#703 review).
- An ES module's own *exported* declaration wins over `export *` (ES semantics). Only what the
  module exports counts: `function parse` beside `export { parse } from './parser'` is legal and
  the export is the parser's, so a module with ESM export statements answers only for the names
  it lists (``ReexportTable.local_exports``).
- An explicit `export { … } from` wins over `export *` (ES semantics again).
- A name that two `export *` statements reach at *different* declarations is ambiguous by the
  spec (a module importing it is a SyntaxError), so it resolves to nothing.
- Chains are followed to a depth of :data:`MAX_DEPTH`, with a visited set, so a cycle
  (`a` re-exports `*` from `b` and `b` from `a`) ends with no answer instead of not ending.
- The answer is only ever an id that exists in the merged graph: a name the table cannot land
  on a node stays unresolved, and the existence check drops it as before.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tree_sitter import Node as TSNode

#: How many re-export hops a name may take before resolution gives up. Real barrels nest two or
#: three deep (`src/index.ts` → `src/core/index.ts` → `src/core/result.ts`); eight is room, and a
#: bound at all is what makes a cycle finite.
MAX_DEPTH = 8

#: ``exported`` of a record for `export * from` (every name the source exports).
STAR = "*"
#: ``imported`` of a record that stands for the source module itself (`export * as ns`).
WHOLE = "*"


@dataclass
class ReexportTable:
    """What every module of one extraction re-exports, filled during the walk, read at finalize."""

    #: ``module id -> [(exported name, source module id, imported name)]`` in source order.
    #: ``exported`` is :data:`STAR` for `export * from`; ``imported`` is :data:`WHOLE` when the
    #: name stands for the whole source module, and ``"default"`` for its default export.
    records: dict[str, list[tuple[str, str, str]]] = field(default_factory=dict)
    #: ``module id -> id of the declaration it exports as default`` (`export default function f`).
    defaults: dict[str, str] = field(default_factory=dict)

    def add(self, module: str, exported: str, source: str, imported: str) -> None:
        self.records.setdefault(module, []).append((exported, source, imported))

    #: ``module id -> {exported name: local name}`` for what a module exports from its own
    #: declarations (`export function f`, `export { a as b }`). A module listed here exports
    #: nothing else of its own: an unexported local that shares a forwarded name is not the export.
    local_exports: dict[str, dict[str, str]] = field(default_factory=dict)

    def resolve(
        self,
        module: str,
        name: str,
        known: Collection[str],
        exports: Mapping[str, object] | None = None,
    ) -> str | None:
        """The id ``name`` exported from ``module`` really is: a declaration, or a module (for a
        name that stands for a whole module), or None when nothing read says.

        ``exports`` is the run's CommonJS export maps (``ExtractionRun.exports``). Where a module's
        map is not opaque it **decides** what the module exports: a declared function is not a
        CommonJS export, so `module.exports = { run: helper }` makes `run` be `helper`, whatever
        else is called `run` in the file, and a name the map does not list is not exported.
        """
        return self._resolve(module, name, known, exports or {}, 0, set())

    def _resolve(
        self,
        module: str,
        name: str,
        known: Collection[str],
        exports: Mapping[str, object],
        depth: int,
        seen: set[tuple[str, str]],
    ) -> str | None:
        if depth > MAX_DEPTH or (module, name) in seen:
            return None
        seen.add((module, name))
        records = self.records.get(module, ())
        entry = exports.get(module)
        tier = getattr(entry, "tier", None)
        if name in getattr(entry, "dead", ()):
            return None  # written onto an object the module no longer exports
        if entry is not None and tier != "opaque":
            # The CommonJS map decides: a listed name is what it maps to, an unlisted one is not
            # exported. A listed value the map cannot name (`{ json: require('./b').json }`) may
            # still be forwarded by a record below; nothing else is consulted.
            names = getattr(entry, "names", {})
            if name == "default":
                default = getattr(entry, "default", None)
                return default if isinstance(default, str) and default in known else None
            if not isinstance(names, dict) or name not in names:
                return None
            mapped = names[name]
            if isinstance(mapped, str) and mapped in known:
                return mapped
            return self._explicit(module, name, records, known, exports, depth, seen)
        if name == "default":
            declared = self.defaults.get(module)
            if declared is not None and declared in known:
                return declared
        else:
            own = self._own(module, name)
            if own is not None and own in known:
                return own
        hit = self._explicit(module, name, records, known, exports, depth, seen)
        if hit is not None or name == "default" or any(e == name for e, _, _ in records):
            return hit  # an explicit forward answers alone; `export *` never forwards a default
        found = {
            hit
            for exported, source, _ in records
            if exported == STAR
            for hit in [self._resolve(source, name, known, exports, depth + 1, set(seen))]
            if hit is not None
        }
        return found.pop() if len(found) == 1 else None  # two different answers: ambiguous

    def _own(self, module: str, name: str) -> str | None:
        """The module's own exported declaration of ``name``: by its export list when the module
        has one, else by name (a module whose exports were never read, as before)."""
        exported = self.local_exports.get(module)
        if exported is None:
            return f"{module}.{name}"
        local = exported.get(name)
        return f"{module}.{local}" if local is not None else None

    def _explicit(
        self,
        module: str,
        name: str,
        records: Collection[tuple[str, str, str]],
        known: Collection[str],
        exports: Mapping[str, object],
        depth: int,
        seen: set[tuple[str, str]],
    ) -> str | None:
        for exported, source, imported in records:
            if exported == name:
                if imported == WHOLE:
                    return source if source in known else None
                return self._resolve(source, imported, known, exports, depth + 1, seen)
        return None

    def route(
        self,
        target: str,
        modules: Collection[str],
        known: Collection[str],
        exports: Mapping[str, object] | None = None,
    ) -> str | None:
        """``ts:m.name.rest`` → the existing id it reaches through ``m``'s re-exports, or None.

        ``m`` is the longest prefix that is a module, the way
        `typescript_extractor._export_router` finds a module. A name that stands for a whole
        module (`ns.ok()` after `export * as ns`) continues into that module.
        """
        module = target
        while module not in modules and "." in module:
            module = module.rpartition(".")[0]
        rest = target[len(module) + 1 :] if module != target else ""
        for _ in range(MAX_DEPTH):
            if not rest:
                return None
            head, _, rest = rest.partition(".")
            hit = self.resolve(module, head, known, exports)
            if hit is None:
                return None
            if hit not in modules:
                landed = f"{hit}.{rest}" if rest else hit
                return landed if landed in known else None
            module = hit  # a whole module: the rest of the id is a member of it
        return None


def collect(
    roots: list[TSNode | None],
    source: bytes,
    module_id: str,
    module_of: Callable[[str], str | None],
    table: ReexportTable,
) -> None:
    """Record what one file re-exports. ``roots`` are its top-level statements (unwrapped is fine:
    only `export_statement` and `module.exports = …` statements are read); ``module_of(spec)``
    is the module id a relative specifier names, or None for a package."""
    for node in roots:
        if node is None:
            continue
        if node.type == "export_statement":
            _esm(node, source, module_id, module_of, table)
        elif node.type == "expression_statement":
            _cjs(node, source, module_id, module_of, table)


def _text(node: TSNode, source: bytes) -> str:
    return source[node.start_byte : node.end_byte].decode("utf-8", "replace")


def _spec(node: TSNode | None, source: bytes) -> str:
    return _text(node, source).strip("\"'`") if node is not None and node.type == "string" else ""


def _declared(declaration: TSNode, source: bytes) -> list[str]:
    """Names an exported declaration binds: its own name, or each declarator's."""
    name = declaration.child_by_field_name("name")
    if name is not None:
        return [_text(name, source)]
    out: list[str] = []
    for declarator in declaration.named_children:
        bound = declarator.child_by_field_name("name") if declarator.type == "variable_declarator" else None
        if bound is not None and bound.type == "identifier":
            out.append(_text(bound, source))
    return out


def _esm(
    node: TSNode,
    source: bytes,
    module_id: str,
    module_of: Callable[[str], str | None],
    table: ReexportTable,
) -> None:
    # Any ESM export statement means the module's exports are read from its own syntax, so its
    # own exports are exactly what is listed (possibly nothing, for a pure barrel).
    own = table.local_exports.setdefault(module_id, {})
    from_node = node.child_by_field_name("source")
    if from_node is None:
        is_default = any(c.type == "default" for c in node.children)
        declaration = node.child_by_field_name("declaration")
        if declaration is not None and is_default:
            name = declaration.child_by_field_name("name")
            if name is not None:  # `export default function build` / `class Store`
                table.defaults[module_id] = f"{module_id}.{_text(name, source)}"
        elif is_default:
            value = node.child_by_field_name("value")
            if value is not None and value.type == "identifier":  # `export default build;`
                table.defaults[module_id] = f"{module_id}.{_text(value, source)}"
        elif declaration is not None:  # `export function f`, `export const a = …, b = …`
            for local in _declared(declaration, source):
                own[local] = local
        else:  # `export { a, b as c }`
            for clause in node.named_children:
                if clause.type != "export_clause":
                    continue
                for spec in clause.named_children:
                    name, alias = spec.child_by_field_name("name"), spec.child_by_field_name("alias")
                    if spec.type == "export_specifier" and name is not None:
                        own[_text(alias if alias is not None else name, source)] = _text(name, source)
        return
    target = module_of(_spec(from_node, source))
    if target is None or target == module_id:
        return  # a package, or a module forwarding itself
    for child in node.named_children:
        if child.type == "export_clause":
            for spec in child.named_children:
                if spec.type != "export_specifier":
                    continue
                name = spec.child_by_field_name("name")
                alias = spec.child_by_field_name("alias")
                if name is None:
                    continue
                imported = _text(name, source)
                table.add(module_id, _text(alias if alias is not None else name, source), target, imported)
            return
        if child.type == "namespace_export":
            ns = next((c for c in child.named_children if c.type in ("identifier", "string")), None)
            if ns is not None:
                table.add(module_id, _text(ns, source).strip("\"'"), target, WHOLE)
            return
    if any(c.type == "*" for c in node.children):
        table.add(module_id, STAR, target, STAR)


def _required(node: TSNode | None, source: bytes, module_of: Callable[[str], str | None]) -> str | None:
    """The module `require('./x')` names, for a ``call_expression`` that is exactly that."""
    if node is None or node.type != "call_expression":
        return None
    fn = node.child_by_field_name("function")
    args = node.child_by_field_name("arguments")
    if fn is None or fn.type != "identifier" or _text(fn, source) != "require" or args is None:
        return None
    named = args.named_children
    return module_of(_spec(named[0], source)) if len(named) == 1 else None


def _cjs(
    node: TSNode,
    source: bytes,
    module_id: str,
    module_of: Callable[[str], str | None],
    table: ReexportTable,
) -> None:
    expr = node.named_children[0] if node.named_children else None
    if expr is None or expr.type != "assignment_expression":
        return
    left, right = expr.child_by_field_name("left"), expr.child_by_field_name("right")
    if left is None or right is None or _text(left, source).replace(" ", "") != "module.exports":
        return
    whole = _required(right, source, module_of)
    if whole is not None and whole != module_id:
        table.add(module_id, STAR, whole, STAR)  # every name of `x`
        table.add(module_id, "default", whole, "default")  # and what `require` returns itself
        return
    if right.type != "object":
        return
    for pair in right.named_children:
        if pair.type != "pair":
            continue
        key, value = pair.child_by_field_name("key"), pair.child_by_field_name("value")
        if key is None or value is None or key.type not in ("property_identifier", "string"):
            continue
        exported = _text(key, source).strip("\"'")
        module = _required(value, source, module_of)
        if module is not None:  # `{ m: require('./x') }`
            table.add(module_id, exported, module, WHOLE)
            continue
        if value.type == "member_expression":  # `{ a: require('./x').a }`
            obj, prop = value.child_by_field_name("object"), value.child_by_field_name("property")
            module = _required(obj, source, module_of)
            if module is not None and prop is not None:
                table.add(module_id, exported, module, _text(prop, source))
