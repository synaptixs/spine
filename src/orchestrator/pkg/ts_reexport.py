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

- A module's own declaration of a name wins over anything it re-exports (ES semantics, and a
  barrel cannot both declare and forward one name).
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

    def resolve(
        self,
        module: str,
        name: str,
        known: Collection[str],
        cjs_names: Callable[[str, str], str | None] | None = None,
    ) -> str | None:
        """The id ``name`` exported from ``module`` really is: a declaration, or a module (for a
        name that stands for a whole module), or None when nothing read says.

        ``cjs_names(module, name)`` is the CommonJS export map's answer for a module whose own
        source names its exports (`module.exports = { json }`), so a forward into such a module
        lands where that map says.
        """
        return self._resolve(module, name, known, cjs_names, 0, set())

    def _resolve(
        self,
        module: str,
        name: str,
        known: Collection[str],
        cjs_names: Callable[[str, str], str | None] | None,
        depth: int,
        seen: set[tuple[str, str]],
    ) -> str | None:
        if depth > MAX_DEPTH or (module, name) in seen:
            return None
        seen.add((module, name))
        if name == "default":
            declared = self.defaults.get(module)
            if declared is not None and declared in known:
                return declared
        else:
            own = f"{module}.{name}"
            if own in known:
                return own
        if cjs_names is not None:
            mapped = cjs_names(module, name)
            if mapped is not None and mapped in known:
                return mapped
        records = self.records.get(module, ())
        for exported, source, imported in records:
            if exported == name:
                if imported == WHOLE:
                    return source if source in known else None
                return self._resolve(source, imported, known, cjs_names, depth + 1, seen)
        if name == "default":
            return None  # `export *` never forwards a default
        found = {
            hit
            for exported, source, _ in records
            if exported == STAR
            for hit in [self._resolve(source, name, known, cjs_names, depth + 1, set(seen))]
            if hit is not None
        }
        return found.pop() if len(found) == 1 else None  # two different answers: ambiguous

    def route(
        self,
        target: str,
        modules: Collection[str],
        known: Collection[str],
        cjs_names: Callable[[str, str], str | None] | None = None,
    ) -> str | None:
        """``ts:m.name.rest`` → the existing id it reaches through ``m``'s re-exports, or None.

        ``m`` is the longest prefix that is a module with re-export records (or a CommonJS map),
        the way `typescript_extractor._export_router` finds a module. A name that stands for a
        whole module (`ns.ok()` after `export * as ns`) continues into that module.
        """
        module = target
        while module not in modules and "." in module:
            module = module.rpartition(".")[0]
        rest = target[len(module) + 1 :] if module != target else ""
        for _ in range(MAX_DEPTH):
            if not rest:
                return None
            head, _, rest = rest.partition(".")
            hit = self.resolve(module, head, known, cjs_names)
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


def _esm(
    node: TSNode,
    source: bytes,
    module_id: str,
    module_of: Callable[[str], str | None],
    table: ReexportTable,
) -> None:
    from_node = node.child_by_field_name("source")
    if from_node is None:
        declaration = node.child_by_field_name("declaration")
        if declaration is not None and any(c.type == "default" for c in node.children):
            name = declaration.child_by_field_name("name")
            if name is not None:  # `export default function build` / `class Store`
                table.defaults[module_id] = f"{module_id}.{_text(name, source)}"
        elif any(c.type == "default" for c in node.children):
            value = node.child_by_field_name("value")
            if value is not None and value.type == "identifier":  # `export default build;`
                table.defaults[module_id] = f"{module_id}.{_text(value, source)}"
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


def cjs_lookup(exports: Mapping[str, object]) -> Callable[[str, str], str | None]:
    """``(module, name) -> node id`` from the run's CommonJS export maps (``ExtractionRun.exports``),
    for :meth:`ReexportTable.resolve`. Only an exact answer counts: a name mapped to a value the
    map cannot name (None) is no answer."""

    def lookup(module: str, name: str) -> str | None:
        entry = exports.get(module)
        if name == "default":
            default = getattr(entry, "default", None)
            return default if isinstance(default, str) else None
        names = getattr(entry, "names", None)
        if not isinstance(names, dict):
            return None
        hit = names.get(name)
        return hit if isinstance(hit, str) else None

    return lookup
