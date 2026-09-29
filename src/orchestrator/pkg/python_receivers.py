"""Python calls through a variable: ``recv.m()`` lands on the method its declared type means (B35).

The per-file pass resolves a call only when the callee is named (``f()``, ``mod.f()``) or is a
sibling method called through ``self``. Every other attribute call was dropped, so a method
called through a parameter, a local or a stored attribute had no graph callers at all: on this
repository ``FactStore.impact_of`` and ``LLMCodegenAdapter.refine`` both answered "0 callers" to
``blast_radius``. Java and C# learned this in 3.47.0 (``typed_receivers``); this is the Python
port, with Python's own scoping.

**A call is judged once every module is known**, never per file: whether a written type means a
class this repository declares — often through a package re-export — and whether that class, or
the class it inherits from, declares the member are whole-repository questions. So
:meth:`ReceiverScan.scan` records facts while each file is read, and :func:`resolve_receivers`
settles them in ``PythonExtractor.finalize``, after re-exports.

The rules, precision-first — a wrong edge is worse than a missing one (SSPN-48, D1–D7):

- **A receiver's type comes only from what the code declares** (D1): a parameter annotation, a
  local assigned from a constructor (``s = Store()``), a class-body annotation or assignment,
  or ``self.attr = …`` from a constructor or from a typed parameter of the same method. Python
  scopes a name to its whole function, so a name bound once has one type everywhere in it; a
  name bound twice, to different types or to anything unreadable (a call's result, a loop
  target, a comprehension or lambda variable, ``global``), refuses. ``None`` assigned alongside
  one type does not count against it.
- **``Optional[X]`` and ``X | None`` mean ``X``** (D2) — ``None`` has no methods. A union of two
  classes refuses.
- **The type must be a class this repository declares.** An imported name that is not declared
  here ends the lookup — no edge to an invented external member (typed-receivers D11, B1).
- **The member is found on the class, then up a single-inheritance chain** (D3): the first class
  that declares it wins. The lookup refuses on reaching a class with two or more bases (their
  order is Python's MRO, which this pass does not rank), or a base this repository does not
  declare (it may define the member). A Protocol or ABC receiver lands on the declared member
  (D4), true to the source.
- **Fields follow the same chain**: the receiver class's own ``self.attr``, else an ancestor's.
  Two agreeing records are one type; disagreeing ones refuse.

Out of scope, refused by omission (D5): chains (``build().run()``, ``a.b.m()``), properties,
``super().m()``, ``cls.m()``, closures over an enclosing function's locals, module-level
variables. :func:`resolve_receivers` also repoints the per-file pass's ``Class.inherited()``
edge from an invented ``Class.inherited`` placeholder to the ancestor that declares it, or drops
it when no in-repo class does (D6): the graph must not assert a symbol that does not exist.
"""

from __future__ import annotations

import ast
from collections.abc import Callable
from dataclasses import dataclass, field

from orchestrator.pkg.facts import Edge, EdgeKind, FactBatch, Node, NodeKind, Provenance

#: A binding whose type cannot be read: it shadows, and a call through it refuses.
_UNREADABLE = "<unreadable>"
#: ``x = None`` — compatible with exactly one other type (Optional by assignment).
_NONE = "<none>"

_OPTIONAL = frozenset({"py:typing.Optional", "py:typing_extensions.Optional"})
_UNION = frozenset({"py:typing.Union", "py:typing_extensions.Union"})


@dataclass(frozen=True)
class _Call:
    caller: str
    member: str
    rel: str
    line: int
    receiver: str = ""  # candidate type id (unresolved), for a local or parameter
    field_of: str = ""  # class id, for ``self.<field>.m()``
    field_name: str = ""
    self_of: str = ""  # class id, for an inherited ``self.m()``


@dataclass
class ReceiverScan:
    """Facts one front-end accumulates across a walk; consumed by :func:`resolve_receivers`."""

    calls: list[_Call] = field(default_factory=list)
    # class id -> written bases as candidate ids ("" for a base this pass cannot read)
    bases: dict[str, list[str]] = field(default_factory=dict)
    # class id -> field name -> every recorded type source (candidate id, _NONE or _UNREADABLE)
    fields: dict[str, dict[str, list[str]]] = field(default_factory=dict)

    def clear(self) -> None:
        self.calls.clear()
        self.bases.clear()
        self.fields.clear()

    # ---- per file -------------------------------------------------------------------------

    def scan(
        self,
        tree: ast.Module,
        *,
        module_id: str,
        rel: str,
        imports: dict[str, str],
        names: dict[str, str],
    ) -> None:
        _FileScan(self, module_id=module_id, rel=rel, imports=imports, names=names).walk(tree.body, module_id)


class _FileScan:
    def __init__(
        self, out: ReceiverScan, *, module_id: str, rel: str, imports: dict[str, str], names: dict[str, str]
    ) -> None:
        self.out = out
        self.module_id = module_id
        self.rel = rel
        self.imports = imports
        self.names = names
        # PEP 695 type parameters in scope (`def f[T](x: T)`, `class Box[T]`): a written name that
        # is one of them means the parameter, never a class of the same name (typed-receivers B3).
        self._type_params: list[frozenset[str]] = []

    # -- the tree, with the parent ids the per-file pass gives every def --------------------

    def walk(
        self, body: list[ast.stmt], parent: str, cls: str | None = None, methods: frozenset[str] = frozenset()
    ) -> None:
        for stmt in body:
            if isinstance(stmt, ast.ClassDef):
                type_id = f"{parent}.{stmt.name}"
                self._type_params.append(_type_param_names(stmt))
                self._class(stmt, type_id)
                own = frozenset(
                    s.name for s in _flat(stmt.body) if isinstance(s, ast.FunctionDef | ast.AsyncFunctionDef)
                )
                self.walk(stmt.body, type_id, type_id, own)
                self._type_params.pop()
            elif isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef):
                func_id = f"{parent}.{stmt.name}"
                self._type_params.append(_type_param_names(stmt))
                self._function(stmt, func_id, cls, methods)
                # Nested defs belong to this function; `self` inside them is not the class's.
                self.walk(stmt.body, func_id)
                self._type_params.pop()
            else:
                for inner in _sub_bodies(stmt):
                    self.walk([inner], parent, cls, methods)

    # -- a class: its bases and the declared types of its fields ----------------------------

    def _class(self, node: ast.ClassDef, type_id: str) -> None:
        self.out.bases[type_id] = [self.type_of(b, allow_generic=True) or "" for b in node.bases]
        fields = self.out.fields.setdefault(type_id, {})
        for stmt in node.body:
            if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                fields.setdefault(stmt.target.id, []).append(self.type_of(stmt.annotation) or _UNREADABLE)
            elif isinstance(stmt, ast.Assign):
                for tgt in stmt.targets:
                    if isinstance(tgt, ast.Name):
                        fields.setdefault(tgt.id, []).append(self._value_type(stmt.value, {}))
        for method in _flat(node.body):
            if not isinstance(method, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            scope = self._bindings(method)
            for stmt in _statements(method.body):
                if isinstance(stmt, ast.AnnAssign) and (attr := _self_attr(stmt.target)):
                    fields.setdefault(attr, []).append(self.type_of(stmt.annotation) or _UNREADABLE)
                elif isinstance(stmt, ast.Assign):
                    for tgt in stmt.targets:
                        for leaf in _targets(tgt):
                            if attr := _self_attr(leaf):
                                source = self._value_type(stmt.value, scope) if leaf is tgt else _UNREADABLE
                                fields.setdefault(attr, []).append(source)
                elif isinstance(stmt, ast.AugAssign) and (attr := _self_attr(stmt.target)):
                    fields.setdefault(attr, []).append(_UNREADABLE)

    # -- a function: its own names, and the receiver calls in its body ----------------------

    def _function(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
        func_id: str,
        cls: str | None,
        methods: frozenset[str],
    ) -> None:
        scope = self._bindings(node)
        for expr in _expressions(node.body):
            for call in (n for n in ast.walk(expr) if isinstance(n, ast.Call)):
                func = call.func
                if not isinstance(func, ast.Attribute):
                    continue
                recv, line = func.value, getattr(call, "lineno", node.lineno)
                if isinstance(recv, ast.Name):
                    if recv.id == "self":
                        # Own methods are resolved per file; an inherited one is decided here.
                        if cls is not None and func.attr not in methods:
                            self.out.calls.append(_Call(func_id, func.attr, self.rel, line, self_of=cls))
                        continue
                    source = scope.get(recv.id)
                    if source and source not in (_UNREADABLE, _NONE):
                        self.out.calls.append(_Call(func_id, func.attr, self.rel, line, receiver=source))
                elif cls is not None and (attr := _self_attr(recv)):
                    self.out.calls.append(
                        _Call(func_id, func.attr, self.rel, line, field_of=cls, field_name=attr)
                    )

    def _bindings(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> dict[str, str]:
        """Each name this function binds -> its one readable type (a candidate id), or
        ``_UNREADABLE``. A name absent from the result is not the function's own."""
        sources: dict[str, list[str]] = {}
        args = node.args
        for a in [*args.posonlyargs, *args.args, *args.kwonlyargs]:
            sources.setdefault(a.arg, []).append(self.type_of(a.annotation) or _UNREADABLE)
        for extra in (args.vararg, args.kwarg):
            if extra is not None:
                sources.setdefault(extra.arg, []).append(_UNREADABLE)  # *args / **kwargs: a tuple, a dict
        for stmt in _statements(node.body):
            if isinstance(stmt, ast.Assign):
                for tgt in stmt.targets:
                    if isinstance(tgt, ast.Name):
                        sources.setdefault(tgt.id, []).append(self._value_type(stmt.value, {}))
                    else:
                        for leaf in _targets(tgt):
                            if isinstance(leaf, ast.Name):
                                sources.setdefault(leaf.id, []).append(_UNREADABLE)
            elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                sources.setdefault(stmt.target.id, []).append(self.type_of(stmt.annotation) or _UNREADABLE)
            elif isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                sources.setdefault(stmt.name, []).append(_UNREADABLE)
            elif isinstance(stmt, ast.Global | ast.Nonlocal):
                for name in stmt.names:
                    sources.setdefault(name, []).append(_UNREADABLE)
            for name in _other_bound_names(stmt):
                sources.setdefault(name, []).append(_UNREADABLE)
        # Names bound inside expressions anywhere in the body — comprehension targets, lambda
        # parameters, `:=` — are a different scope or a rebinding; either way, not readable.
        for expr in _expressions(node.body):
            for n in ast.walk(expr):
                if isinstance(n, ast.comprehension):
                    for leaf in _targets(n.target):
                        if isinstance(leaf, ast.Name):
                            sources.setdefault(leaf.id, []).append(_UNREADABLE)
                elif isinstance(n, ast.Lambda):
                    la = n.args
                    lambda_args = [*la.posonlyargs, *la.args, *la.kwonlyargs, la.vararg, la.kwarg]
                    for param in (p for p in lambda_args if p is not None):
                        sources.setdefault(param.arg, []).append(_UNREADABLE)
                elif isinstance(n, ast.NamedExpr) and isinstance(n.target, ast.Name):
                    sources.setdefault(n.target.id, []).append(_UNREADABLE)
        return {name: _agree(found) for name, found in sources.items()}

    def _value_type(self, value: ast.expr, scope: dict[str, str]) -> str:
        """The type an assigned value has, when it is written: a constructor call, ``None``, or
        (for ``self.attr = param``) a typed parameter of the same method."""
        if isinstance(value, ast.Constant) and value.value is None:
            return _NONE
        if isinstance(value, ast.Call):
            return self.type_of(value.func) or _UNREADABLE
        if isinstance(value, ast.Name) and value.id in scope:
            return scope[value.id]
        return _UNREADABLE

    # -- a written type -> a candidate id, resolved against the repository in finalize -------

    def type_of(self, expr: ast.expr | None, *, allow_generic: bool = False) -> str | None:
        if expr is None:
            return None
        if isinstance(expr, ast.Constant) and isinstance(expr.value, str):
            try:
                parsed = ast.parse(expr.value, mode="eval").body
            except SyntaxError:
                return None
            return self.type_of(parsed)
        if isinstance(expr, ast.Name):
            if any(expr.id in params for params in self._type_params):
                return None
            imported, defined = self.imports.get(expr.id), self.names.get(expr.id)
            if imported and defined:
                return None  # bound twice at module level: nobody can say which
            return imported or defined
        if isinstance(expr, ast.Attribute):
            head = self.type_of(expr.value)
            return f"{head}.{expr.attr}" if head else None
        if isinstance(expr, ast.BinOp) and isinstance(expr.op, ast.BitOr):
            return self._one_arm([expr.left, expr.right])
        if isinstance(expr, ast.Subscript):
            outer = self.type_of(expr.value)
            inner = expr.slice
            if outer in _OPTIONAL:
                return self._one_arm([inner])
            if outer in _UNION:
                return self._one_arm(list(inner.elts) if isinstance(inner, ast.Tuple) else [inner])
            # `Generic[T]` as a base is still the base `Generic`; as an annotation, `list[X]` is a
            # list, not an X — so only a base keeps the subscripted name.
            return outer if allow_generic else None
        return None

    def _one_arm(self, arms: list[ast.expr]) -> str | None:
        flat: list[ast.expr] = []
        while arms:
            arm = arms.pop()
            if isinstance(arm, ast.BinOp) and isinstance(arm.op, ast.BitOr):
                arms += [arm.left, arm.right]
            else:
                flat.append(arm)
        real = [a for a in flat if not _is_none(a)]
        return self.type_of(real[0]) if len(real) == 1 else None


# ---- whole repository --------------------------------------------------------------------


def resolve_receivers(
    batch: FactBatch, scan: ReceiverScan, resolve: Callable[[str], str | None]
) -> FactBatch:
    """Add a ``CALLS`` edge for every recorded call that resolves, and repoint or drop the
    per-file pass's invented ``Class.member`` placeholders (D6). ``resolve`` maps a candidate id
    through re-exports to the grounded id it names, or ``None``. Clears ``scan``."""
    nodes = {n.id: n for n in batch.nodes}
    declared = {i for i, n in nodes.items() if n.kind is NodeKind.TYPE and n.grounded and i.startswith("py:")}
    members: dict[str, set[str]] = {}
    for e in batch.edges:
        if e.kind is EdgeKind.CONTAINS and e.src in declared:
            child = nodes.get(e.dst)
            if child is not None and child.kind is NodeKind.FUNCTION and child.grounded:
                members.setdefault(e.src, set()).add(child.name)
    index = _Index(declared, members, scan, resolve)

    added: list[Edge] = []
    for call in scan.calls:
        if call.self_of:
            start: str | None = call.self_of
        elif call.field_of:
            start = index.field_type(call.field_of, call.field_name)
        else:
            start = index.type_of(call.receiver)
        owner = index.owner(start, call.member) if start is not None else None
        if owner is not None:
            added.append(
                Edge(call.caller, f"{owner}.{call.member}", EdgeKind.CALLS, Provenance(call.rel, call.line))
            )

    result = _repoint_phantoms(batch, nodes, index)
    for edge in added:
        result.add_edge(edge)
    scan.clear()
    return result


class _Index:
    def __init__(
        self,
        declared: set[str],
        members: dict[str, set[str]],
        scan: ReceiverScan,
        resolve: Callable[[str], str | None],
    ) -> None:
        self.declared = declared
        self.members = members
        self.scan = scan
        self._resolve = resolve
        self._bases: dict[str, list[str | None]] = {}

    def type_of(self, candidate: str) -> str | None:
        """The declared class a candidate id names, or ``None`` — external, or not a class."""
        if not candidate:
            return None
        found = self._resolve(candidate)
        return found if found in self.declared else None

    def bases(self, type_id: str) -> list[str | None]:
        """In-repo base ids; ``None`` for a base this repository does not declare or we cannot read."""
        if type_id not in self._bases:
            self._bases[type_id] = [self.type_of(b) for b in self.scan.bases.get(type_id, [])]
        return self._bases[type_id]

    def chain(self, type_id: str) -> list[str | None]:
        """``type_id`` and its single-inheritance ancestors; a trailing ``None`` means the walk
        stopped somewhere a member could still be defined (two bases, or an external one)."""
        out: list[str | None] = []
        cur: str | None = type_id
        while cur is not None and cur not in out:
            out.append(cur)
            bases = self.bases(cur)
            if not bases:
                return out
            if len(bases) > 1 or bases[0] is None:
                out.append(None)
                return out
            cur = bases[0]
        return out

    def owner(self, type_id: str, member: str) -> str | None:
        for t in self.chain(type_id):
            if t is None:
                return None
            if member in self.members.get(t, ()):
                return t
        return None

    def field_type(self, type_id: str, name: str) -> str | None:
        for t in self.chain(type_id):
            if t is None:
                return None
            found = self.scan.fields.get(t, {}).get(name)
            if found:
                agreed = _agree(found)
                return None if agreed in (_UNREADABLE, _NONE) else self.type_of(agreed)
        return None


def _repoint_phantoms(batch: FactBatch, nodes: dict[str, Node], index: _Index) -> FactBatch:
    """``Store.make()`` where ``make`` is inherited was emitted per file as a call to an invented
    ``Store.make``. Land it on the ancestor that declares it, or drop it (D6)."""
    alias: dict[str, str | None] = {}
    for node_id, node in nodes.items():
        if not node_id.startswith("py:") or not node.external:
            continue
        owner_path, _, member = node_id.rpartition(".")
        cls = index.type_of(owner_path) if owner_path else None
        if cls is None or member in index.members.get(cls, ()):
            continue
        if member in index.scan.fields.get(cls, {}):
            continue  # a class attribute holding a callable: not a method, not invented
        alias[node_id] = index.owner(cls, member)
    if not alias:
        return batch
    result = FactBatch()
    for node in batch.nodes:
        if node.id not in alias:
            result.add_node(node)
    for edge in batch.edges:
        if edge.dst in alias:
            target = alias[edge.dst]
            if target is None or edge.kind is not EdgeKind.CALLS:
                continue
            result.add_edge(
                Edge(edge.src, f"{target}.{edge.dst.rpartition('.')[2]}", edge.kind, edge.provenance)
            )
        else:
            result.add_edge(edge)
    return result


# ---- small AST helpers ---------------------------------------------------------------------


def _agree(sources: list[str]) -> str:
    """One readable type, or ``_UNREADABLE``: ``None`` alongside exactly one type is that type."""
    kinds = set(sources)
    if _UNREADABLE in kinds:
        return _UNREADABLE
    typed = kinds - {_NONE}
    if len(typed) == 1:
        return typed.pop()
    return _NONE if not typed else _UNREADABLE


def _type_param_names(node: ast.AST) -> frozenset[str]:
    return frozenset(getattr(p, "name", "") for p in getattr(node, "type_params", []) or [])


def _is_none(expr: ast.expr) -> bool:
    return (isinstance(expr, ast.Constant) and expr.value is None) or (
        isinstance(expr, ast.Name) and expr.id == "None"
    )


def _self_attr(expr: ast.expr) -> str | None:
    """``attr`` for ``self.attr``, else ``None``."""
    if isinstance(expr, ast.Attribute) and isinstance(expr.value, ast.Name) and expr.value.id == "self":
        return expr.attr
    return None


def _targets(expr: ast.expr) -> list[ast.expr]:
    if isinstance(expr, ast.Tuple | ast.List):
        return [leaf for e in expr.elts for leaf in _targets(e)]
    if isinstance(expr, ast.Starred):
        return _targets(expr.value)
    return [expr]


def _sub_bodies(stmt: ast.stmt) -> list[ast.stmt]:
    out: list[ast.stmt] = []
    for attr in ("body", "orelse", "finalbody"):
        out.extend(getattr(stmt, attr, []) or [])
    for handler in getattr(stmt, "handlers", []) or []:
        out.extend(handler.body)
    for case in getattr(stmt, "cases", []) or []:
        out.extend(case.body)
    return out


def _flat(body: list[ast.stmt]) -> list[ast.stmt]:
    """A class body's statements, through ``if``/``try`` blocks, not into nested defs."""
    out: list[ast.stmt] = []
    for stmt in body:
        out.append(stmt)
        if not isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            out.extend(_flat(_sub_bodies(stmt)))
    return out


def _statements(body: list[ast.stmt]) -> list[ast.stmt]:
    """Every statement of a function's own scope: through compound statements, never into a
    nested def or class (their own scope) — though the def itself is listed, as a binding."""
    return _flat(body)


def _expressions(body: list[ast.stmt]) -> list[ast.AST]:
    """The expression parts of a function's own statements, the way the per-file pass reads
    them: a compound statement's header, a simple statement whole — never a nested def or class."""
    out: list[ast.AST] = []
    for stmt in _statements(body):
        if isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            continue
        if _sub_bodies(stmt) or isinstance(
            stmt, ast.If | ast.For | ast.AsyncFor | ast.While | ast.With | ast.AsyncWith | ast.Try
        ):
            for attr in ("test", "iter", "subject"):
                value = getattr(stmt, attr, None)
                if isinstance(value, ast.expr):
                    out.append(value)
            for item in getattr(stmt, "items", []) or []:
                out.append(item.context_expr)
            for handler in getattr(stmt, "handlers", []) or []:
                if handler.type is not None:
                    out.append(handler.type)
        else:
            out.append(stmt)
    return out


def _other_bound_names(stmt: ast.stmt) -> list[str]:
    """Names a statement binds that carry no readable type."""
    names: list[str] = []
    if isinstance(stmt, ast.For | ast.AsyncFor):
        names += [leaf.id for leaf in _targets(stmt.target) if isinstance(leaf, ast.Name)]
    elif isinstance(stmt, ast.With | ast.AsyncWith):
        for item in stmt.items:
            if item.optional_vars is not None:
                names += [leaf.id for leaf in _targets(item.optional_vars) if isinstance(leaf, ast.Name)]
    elif isinstance(stmt, ast.AugAssign) and isinstance(stmt.target, ast.Name):
        names.append(stmt.target.id)
    elif isinstance(stmt, ast.Import | ast.ImportFrom):
        names += [(a.asname or a.name).split(".")[0] for a in stmt.names]
    elif isinstance(stmt, ast.Delete):
        names += [leaf.id for t in stmt.targets for leaf in _targets(t) if isinstance(leaf, ast.Name)]
    elif isinstance(stmt, ast.Try):
        names += [h.name for h in stmt.handlers if h.name]
    elif isinstance(stmt, ast.Match):
        names += [
            n.name
            for c in stmt.cases
            for n in ast.walk(c.pattern)
            if isinstance(n, ast.MatchAs | ast.MatchStar) and n.name
        ]
    return names


__all__ = ["ReceiverScan", "resolve_receivers"]
