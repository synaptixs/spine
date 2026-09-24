"""Python re-exports: land a call made through a re-export on the symbol that defines it.

``from orchestrator.pkg import FactStore; FactStore(b)`` is resolved per file, from the import
*text*, to ``py:orchestrator.pkg.FactStore`` — an ``external`` placeholder, because the class is
defined in ``orchestrator.pkg.store`` and the package only re-binds it. Measured on this
repository before the fix: 141 in-repo symbols had such a phantom twin, and 1,271 ``CALLS`` edges
(5.5% of all) plus an ``IMPLEMENTS`` landed on them — so ``blast_radius FactStore`` missed 110
callers and called the class third-party. ``pkg verify`` passed; no corpus case had the shape.

**Why here, and not in** :func:`~orchestrator.pkg.import_link.link_imports`: that join sees only
the finished graph, and the graph has already lost what resolution needs. ``from .x import a as b``
records its IMPORTS edge under ``a`` (the name ``b`` a caller uses is gone), and ``from .x import
*`` records no names at all. The front-end has both while it reads each file, so it keeps a
per-module binding table (:func:`collect_exports`) and :func:`resolve_reexports` follows it in
``PythonExtractor.finalize``, once every module is known.

The rules are Python's own, applied precision-first:

- **Any module re-exports**, not only a package ``__init__``: every name a module binds at module
  level is importable from it. Chains are followed (``app`` → ``app.sub`` → ``app.sub.deep``),
  cycle-safe.
- **Only module-level bindings count.** An import inside a function binds a local. Statements
  under a module-level ``if``/``try``/``with`` do bind — including ``if TYPE_CHECKING:``.
- **``from x import *``** binds x's literal ``__all__`` when it has one, and otherwise every
  name x binds that does not start with an underscore (following x's own stars). Its answer
  for a name is three-valued — binds it, does not, or *unknown*: a star from outside the
  scanned tree, or one whose ``__all__`` is not a single literal, could bind anything.
- **Source order decides, and only an unconditional binding overrides.** Python runs a module
  top to bottom, so the last binding of a name directly in the module body replaces everything
  before it (``from .a import h`` then ``from .b import h`` is ``b.h``); a binding under an
  ``if``/``try``/``with`` after it may run instead, so it stays a candidate. What is left must
  all agree. A later unknown star, an assignment, a ``del``, a ``for`` or ``with … as`` target
  or an ``except … as`` name is a binding nobody can follow — so ``from .mine import array``
  followed by ``from numpy import *`` refuses, and ``try: from .fast import f / except
  ImportError: from .slow import f`` refuses because the two disagree. A refused call stays on
  the placeholder, where ``pkg verify``'s ``phantom-symbol`` warning counts it. A module-level
  ``__getattr__`` (PEP 562) binds nothing statically and is left alone for the same reason.
- **A member of a re-exported class resolves too** (``from app import Store; Store.get()`` →
  ``py:app.Store.get``): the class part is resolved, then the member must exist on it.

Every edge kind is repointed, IMPORTS included — an import through a re-export names the defining
symbol, exactly as a direct import already does — and the orphaned placeholders are dropped.
Results are memoised with an in-progress set as the cycle guard (a re-export cycle binds
nothing), so a diamond of star imports costs one visit per name, not one per path. Iteration is
sorted throughout: the same tree gives the same graph.
"""

from __future__ import annotations

import ast
from collections.abc import Callable
from dataclasses import dataclass, field

from orchestrator.pkg.facts import Edge, FactBatch

# `from x import *` with an `__all__` we cannot read binds a set we cannot know.
_UNKNOWABLE: tuple[str, ...] = ("<non-literal __all__>",)

# What a star import answers for one name: it binds it, it does not, or nobody can tell.
_YES, _NO, _UNKNOWN = "yes", "no", "unknown"
# An event whose value cannot be followed: `x = ...`, `del x`, `for x in ...`, `with ... as x`.
_REBOUND = "<rebound>"


@dataclass(frozen=True)
class _Binding:
    line: int
    unconditional: bool  # directly in the module body — not under an if/try/with/for
    target: str  # "py:<id>", or _REBOUND


@dataclass
class ModuleExports:
    """What one module binds at module level, for another module to import from it.

    Bindings are kept as source-ordered events, not a set, because Python executes a module top
    to bottom: a later unconditional binding overrides everything before it, and that is the
    only reason a name bound more than once can still be resolved.
    """

    bindings: dict[str, list[_Binding]] = field(default_factory=dict)
    stars: list[tuple[int, bool, str]] = field(default_factory=list)  # (line, unconditional, "py:<m>")
    all_names: tuple[str, ...] | None = None  # literal `__all__`, None when absent, else _UNKNOWABLE
    defined: set[str] = field(default_factory=set)  # top-level def / class names

    def bind(self, name: str, line: int, unconditional: bool, target: str) -> None:
        self.bindings.setdefault(name, []).append(_Binding(line, unconditional, target))


def _module_level(body: list[ast.stmt], depth: int = 0) -> list[tuple[ast.stmt, bool]]:
    """Statements that bind at module level, each with whether it runs unconditionally.

    Descends into if/try/with/for/while/match — their names bind in the module — never into a
    def or class, whose names are local to it.
    """
    out: list[tuple[ast.stmt, bool]] = []
    for stmt in body:
        out.append((stmt, depth == 0))
        nested: list[ast.stmt] = []
        if isinstance(stmt, ast.Match):
            for case in stmt.cases:
                nested.extend(case.body)
        elif not isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            for attr in ("body", "orelse", "finalbody"):
                nested.extend(getattr(stmt, attr, []) or [])
            for handler in getattr(stmt, "handlers", []) or []:
                nested.extend(handler.body)
        out.extend(_module_level(nested, depth + 1))
    return out


def _rebound_names(stmt: ast.stmt) -> set[str]:
    """Names a non-import statement assigns or deletes in the module's own scope."""
    names: set[str] = set()
    stack: list[ast.AST] = [stmt]
    while stack:
        node = stack.pop()
        if node is not stmt and isinstance(
            node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef | ast.Lambda | ast.stmt
        ):
            continue  # a nested scope, or a nested statement `_module_level` visits on its own
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store | ast.Del):
            names.add(node.id)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            names.add(node.name)
        stack.extend(ast.iter_child_nodes(node))
    return names


def _literal_all(value: ast.expr) -> tuple[str, ...]:
    if isinstance(value, ast.List | ast.Tuple) and all(
        isinstance(e, ast.Constant) and isinstance(e.value, str) for e in value.elts
    ):
        return tuple(str(e.value) for e in value.elts if isinstance(e, ast.Constant))
    return _UNKNOWABLE


def collect_exports(tree: ast.Module, import_base: Callable[[ast.ImportFrom], str]) -> ModuleExports:
    """One module's re-export table. ``import_base`` resolves a (possibly relative) ``from``."""
    out = ModuleExports()
    all_assignments = 0
    for stmt, unconditional in _module_level(tree.body):
        line = stmt.lineno
        if isinstance(stmt, ast.Import):
            for a in stmt.names:
                # `import a.b` binds `a` (the package); `import a.b as c` binds `c` to `a.b`.
                top = a.name.split(".")[0]
                out.bind(a.asname or top, line, unconditional, f"py:{a.name if a.asname else top}")
        elif isinstance(stmt, ast.ImportFrom):
            base = import_base(stmt)
            for a in stmt.names:
                if base.startswith("."):
                    # Climbed out of the scanned tree: a binding, but never one we can follow.
                    out.bind(a.asname or a.name, line, unconditional, _REBOUND)
                elif a.name == "*":
                    out.stars.append((line, unconditional, f"py:{base}"))
                else:
                    target = f"py:{base}.{a.name}" if base else f"py:{a.name}"
                    out.bind(a.asname or a.name, line, unconditional, target)
        elif isinstance(stmt, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            out.defined.add(stmt.name)
        else:
            for name in _rebound_names(stmt):
                if name == "__all__":
                    continue
                out.bind(name, line, unconditional, _REBOUND)
            if isinstance(stmt, ast.Assign | ast.AnnAssign | ast.AugAssign):
                targets = stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target]
                if any(_is_all(t) for t in targets):
                    all_assignments += 1
                    value = stmt.value if not isinstance(stmt, ast.AugAssign) else None
                    out.all_names = _literal_all(value) if value is not None else _UNKNOWABLE
    if all_assignments > 1:
        out.all_names = _UNKNOWABLE  # built up piecewise, or chosen by a branch — not one literal
    return out


def _is_all(target: ast.expr) -> bool:
    return isinstance(target, ast.Name) and target.id == "__all__"


class _Resolver:
    def __init__(self, exports: dict[str, ModuleExports], grounded: set[str]) -> None:
        self._exports = exports
        self._grounded = grounded
        self._memo: dict[str, str | None] = {}
        self._active: set[str] = set()
        self._star_memo: dict[tuple[str, str], str] = {}
        self._star_active: set[tuple[str, str]] = set()

    def resolve(self, target: str) -> str | None:
        """The grounded id ``target`` denotes through re-exports, or None when it cannot be told."""
        if target in self._grounded:
            return target
        if target in self._memo:
            return self._memo[target]
        if target in self._active:
            return None  # a re-export cycle binds nothing
        self._active.add(target)
        try:
            result = self._resolve(target)
        finally:
            self._active.discard(target)
        self._memo[target] = result
        return result

    def _resolve(self, target: str) -> str | None:
        module, _, name = target.rpartition(".")
        if not module or not name:
            return None
        exports = self._exports.get(module)
        if exports is None:
            # Not a first-party module: perhaps a member of a re-exported symbol
            # (`py:app.Store.get`, where `py:app.Store` re-exports a class).
            owner = self.resolve(module)
            member = f"{owner}.{name}" if owner is not None else None
            return member if member in self._grounded else None
        # Every event that may bind `name`, in source order. A star that does not bind it is
        # no event at all; one we cannot see into is an event whose value nobody can follow.
        events: list[tuple[int, bool, str | None]] = [
            (b.line, b.unconditional, None if b.target == _REBOUND else b.target)
            for b in exports.bindings.get(name, ())
        ]
        for line, unconditional, star in exports.stars:
            answer = self._star_binds(star, name)
            if answer == _YES:
                events.append((line, unconditional, f"{star}.{name}"))
            elif answer == _UNKNOWN:
                events.append((line, unconditional, None))
        events.sort(key=lambda e: e[0])
        # The last unconditional event overrides everything before it; conditional events
        # after it may still run instead. What is left must all agree — never guessed.
        last = max((i for i, e in enumerate(events) if e[1]), default=0)
        live = events[last:]
        if not live:
            return None
        results = {self.resolve(t) if t is not None else None for _, _, t in live}
        return results.pop() if len(results) == 1 else None

    def _star_binds(self, module: str, name: str) -> str:
        """Would ``from <module> import *`` bind ``name``? yes / no / unknown."""
        key = (module, name)
        if key in self._star_memo:
            return self._star_memo[key]
        if key in self._star_active:
            return _NO  # a star cycle adds nothing the rest of the cycle does not already bind
        self._star_active.add(key)
        try:
            answer = self._star_answer(module, name)
        finally:
            self._star_active.discard(key)
        self._star_memo[key] = answer
        return answer

    def _star_answer(self, module: str, name: str) -> str:
        source = self._exports.get(module)
        if source is None or source.all_names is _UNKNOWABLE:
            return _UNKNOWN  # outside the tree, or an `__all__` we cannot read
        if source.all_names is not None:
            return _YES if name in source.all_names else _NO
        if name.startswith("_"):
            return _NO
        if name in source.defined or name in source.bindings:
            return _YES
        answers = {self._star_binds(star, name) for _, _, star in source.stars}
        if _YES in answers:
            return _YES
        return _UNKNOWN if _UNKNOWN in answers else _NO


def resolve_reexports(batch: FactBatch, exports: dict[str, ModuleExports]) -> FactBatch:
    """Repoint every edge whose target is a Python re-export placeholder; drop the placeholders."""
    grounded = {n.id for n in batch.nodes if n.grounded}
    resolver = _Resolver(exports, grounded)
    alias: dict[str, str] = {}
    for node in sorted(batch.nodes, key=lambda n: n.id):
        if node.external and node.id.startswith("py:"):
            real = resolver.resolve(node.id)
            if real is not None and real != node.id:
                alias[node.id] = real
    if not alias:
        return batch
    result = FactBatch()
    for node in batch.nodes:
        if node.id not in alias:
            result.add_node(node)
    for edge in batch.edges:
        src, dst = alias.get(edge.src, edge.src), alias.get(edge.dst, edge.dst)
        result.add_edge(
            edge if (src, dst) == (edge.src, edge.dst) else Edge(src, dst, edge.kind, edge.provenance)
        )
    return result


__all__ = ["ModuleExports", "collect_exports", "resolve_reexports"]
