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
        import_base: Callable[[ast.ImportFrom], str],
        source: str,
    ) -> None:
        _SCOPE_MEMO.clear()
        try:
            _FileScan(self, tree, module_id=module_id, rel=rel, import_base=import_base, source=source).walk(
                tree.body, module_id
            )
        finally:
            _SCOPE_MEMO.clear()


class _FileScan:
    """One file. Every name is read with the scope Python would read it in, and a name is typed
    only when *every* binding of it is one this pass understands — the census counts all of them
    generically (each ``ast.Name`` in store/del context, plus the binders that carry a bare
    string), so a binding form nobody listed refuses instead of slipping through (review 1, A)."""

    def __init__(
        self,
        out: ReceiverScan,
        tree: ast.Module,
        *,
        module_id: str,
        rel: str,
        import_base: Callable[[ast.ImportFrom], str],
        source: str,
    ) -> None:
        self.out = out
        self.module_id = module_id
        self.rel = rel
        self.import_base = import_base
        # Both need a whole-tree walk, so skip it when the source cannot contain either keyword.
        self._nonlocal_below: dict[int, set[str]] = {}
        global_names: set[str] = set()
        if "nonlocal" in source or "global" in source:
            _collect_nonlocals(tree, [], self._nonlocal_below, global_names)
        self.module = _module_scope(tree, module_id, import_base, global_names)
        # PEP 695 type parameters in scope (`def f[T](x: T)`, `class Box[T]`): a written name that
        # is one of them means the parameter, never a class of the same name (typed-receivers B3).
        self._type_params: list[frozenset[str]] = []
        # Enclosing scopes, innermost last: ("func" | "class", each name it binds -> what the name
        # means there when every binding is one import of one target, else None). A function body
        # sees every enclosing function's names but no class's; a class body, a method signature
        # and a class-body annotation also see the class they are written in.
        self._frames: list[tuple[str, dict[str, str | None]]] = []
        self._bindings_memo: dict[int, dict[str, str]] = {}
        self._meanings_memo: dict[int, dict[str, str | None]] = {}

    # -- the tree, with the parent ids the per-file pass gives every def --------------------

    def walk(
        self, body: list[ast.stmt], parent: str, cls: str | None = None, methods: frozenset[str] = frozenset()
    ) -> None:
        for stmt in body:
            if isinstance(stmt, ast.ClassDef):
                type_id = f"{parent}.{stmt.name}"
                self._type_params.append(_type_param_names(stmt))
                # Bases are evaluated before the class body exists; its annotations inside it.
                self.out.bases[type_id] = [self.type_of(b, allow_generic=True) or "" for b in stmt.bases]
                self._frames.append(("class", _scope_meanings(stmt.body, self.import_base)))
                self._class(stmt, type_id)
                own = frozenset(
                    s.name for s in _flat(stmt.body) if isinstance(s, ast.FunctionDef | ast.AsyncFunctionDef)
                )
                self.walk(stmt.body, type_id, type_id, own)
                self._frames.pop()
                self._type_params.pop()
            elif isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef):
                func_id = f"{parent}.{stmt.name}"
                self._enter(stmt)
                self._function(stmt, func_id, cls, methods)
                # Nested defs belong to this function; `self` inside them is not the class's.
                self.walk(stmt.body, func_id)
                self._leave()
            else:
                for inner in _sub_bodies(stmt):
                    self.walk([inner], parent, cls, methods)

    def _enter(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self._type_params.append(_type_param_names(node))
        key = id(node)
        if key not in self._meanings_memo:
            self._meanings_memo[key] = _function_meanings(node, self.import_base)
        self._frames.append(("func", self._meanings_memo[key]))

    def _leave(self) -> None:
        self._frames.pop()
        self._type_params.pop()

    # -- a class: the declared types of its fields ------------------------------------------

    def _class(self, node: ast.ClassDef, type_id: str) -> None:
        fields = self.out.fields.setdefault(type_id, {})
        # Class-body names: an `x = Store()` or `x: Store` is typed; any other binding of the name
        # at class level (a loop target, an import, a nested class) makes it unreadable.
        typed: dict[int, str] = {}
        for stmt in _statements(node.body):
            if isinstance(stmt, ast.Assign):
                for tgt in stmt.targets:
                    if isinstance(tgt, ast.Name):
                        typed[id(tgt)] = self._value_type(stmt.value, {})
            elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                typed[id(stmt.target)] = self.type_of(stmt.annotation) or _UNREADABLE
        stored, strings, _ = _scope_bindings(node.body, self.import_base)
        methods = {s.name for s in _flat(node.body) if isinstance(s, ast.FunctionDef | ast.AsyncFunctionDef)}
        for name_node in stored:
            fields.setdefault(name_node.id, []).append(typed.get(id(name_node), _UNREADABLE))
        for name in strings:
            if name not in methods:  # a `def` in the class body is a member, not a field
                fields.setdefault(name, []).append(_UNREADABLE)
        # `self.<attr>`: only a plain `self.x = …` / `self.x: T = …` in a method's own body is typed;
        # every other store of it anywhere in a method — a nested def, `with … as self.x`, a loop,
        # a tuple, `del`, `setattr(self, "x", …)` — makes the field unreadable.
        for method in _flat(node.body):
            if not isinstance(method, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            self._enter(method)
            scope = self._bindings(method)
            self_typed: dict[int, str] = {}
            for stmt in _statements(method.body):
                if isinstance(stmt, ast.AnnAssign) and _self_attr(stmt.target):
                    self_typed[id(stmt.target)] = self.type_of(stmt.annotation) or _UNREADABLE
                elif isinstance(stmt, ast.Assign):
                    for tgt in stmt.targets:
                        if _self_attr(tgt):
                            self_typed[id(tgt)] = self._value_type(stmt.value, scope)
            self._leave()
            for n in ast.walk(method):
                if (
                    isinstance(n, ast.Attribute)
                    and isinstance(n.ctx, ast.Store | ast.Del)
                    and (attr := _self_attr(n))
                ):
                    fields.setdefault(attr, []).append(self_typed.get(id(n), _UNREADABLE))
                elif (
                    isinstance(n, ast.Call)
                    and isinstance(n.func, ast.Name)
                    and n.func.id in {"setattr", "delattr"}
                    and len(n.args) >= 2
                    and isinstance(n.args[0], ast.Name)
                    and n.args[0].id == "self"
                    and isinstance(n.args[1], ast.Constant)
                    and isinstance(n.args[1].value, str)
                ):
                    fields.setdefault(n.args[1].value, []).append(_UNREADABLE)

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
        ``_UNREADABLE``. A name absent from the result is not the function's own. Memoised: a
        method is read once for its class's fields and once for its own calls."""
        key = id(node)
        if key not in self._bindings_memo:
            self._bindings_memo[key] = self._read_bindings(node)
        return self._bindings_memo[key]

    def _read_bindings(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> dict[str, str]:
        sources: dict[str, list[str]] = {}
        args = node.args
        # A signature's annotations are evaluated where the `def` runs, not inside the function:
        # `def f(box: box.Box)` means the module's `box`, though the parameter shadows it within —
        # and for a method, the class body's names are visible there (review 1, H8).
        own = self._frames.pop()
        for a in [*args.posonlyargs, *args.args, *args.kwonlyargs]:
            sources.setdefault(a.arg, []).append(self.type_of(a.annotation) or _UNREADABLE)
        self._frames.append(own)
        for extra in (args.vararg, args.kwarg):
            if extra is not None:
                sources.setdefault(extra.arg, []).append(_UNREADABLE)  # *args / **kwargs: a tuple, a dict
        # The only typed forms: `x = Ctor(...)` / `x = None` and `x: T = ...` as whole statements.
        typed: dict[int, str] = {}
        for stmt in _statements(node.body):
            if isinstance(stmt, ast.Assign):
                for tgt in stmt.targets:
                    if isinstance(tgt, ast.Name):
                        typed[id(tgt)] = self._value_type(stmt.value, {})
            elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                typed[id(stmt.target)] = self.type_of(stmt.annotation) or _UNREADABLE
        # Every binding of every name, generically; one we did not type makes the name unreadable.
        stored, strings, _ = _scope_bindings(node.body, self.import_base)
        for name_node in stored:
            sources.setdefault(name_node.id, []).append(typed.get(id(name_node), _UNREADABLE))
        for name in strings:
            sources.setdefault(name, []).append(_UNREADABLE)
        # A nested def that declares `nonlocal x` may rebind this function's `x` (review 1, H6).
        for name in self._nonlocal_below.get(id(node), ()):
            sources.setdefault(name, []).append(_UNREADABLE)
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

    def _enclosing(self, name: str) -> tuple[bool, str | None]:
        """Whether an enclosing scope binds ``name``, and what it means there — every function
        frame counts, a class frame only when it is the innermost one (a function body does not
        see its class's names). A local ``from x import Store`` is a readable binding; any other
        local binding shadows the module's name with something this pass cannot type."""
        for depth, (kind, names) in enumerate(reversed(self._frames)):
            if kind == "class" and depth:
                continue
            if name in names:
                return True, names[name]
        return False, None

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
            local, meaning = self._enclosing(expr.id)
            return meaning if local else self.module.get(expr.id)
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
        if call.caller not in nodes:
            # A def the per-file pass emits no node for (one inside a `match` case, say): an edge
            # from it would dangle, which `pkg verify` rightly fails (review 1, H4).
            continue
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
        """The nearest class declaring ``member`` as a method. An attribute of that name on a
        class at or before it — ``m = helper``, ``self.m = lambda: …`` — is what Python runs
        instead, and its value is not a method we can name, so the lookup refuses (review 1)."""
        for t in self.chain(type_id):
            if t is None:
                return None
            if member in self.scan.fields.get(t, {}):
                return None
            if member in self.members.get(t, ()):
                return t
        return None

    def field_type(self, type_id: str, name: str) -> str | None:
        for t in self.chain(type_id):
            if t is None:
                return None
            if name in self.members.get(t, ()):
                return None  # a method or property of that name on the chain shadows an inherited field
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


def _collect_nonlocals(
    node: ast.AST, enclosing: list[int], out: dict[int, set[str]], global_names: set[str]
) -> None:
    """Record each ``nonlocal`` name against every function enclosing the def that declares it
    (the declaring function binds nothing by it), and every ``global`` name."""
    for child in ast.iter_child_nodes(node):
        if isinstance(child, ast.Nonlocal):
            for fid in enclosing[:-1]:
                out.setdefault(fid, set()).update(child.names)
        elif isinstance(child, ast.Global):
            global_names.update(child.names)
        if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
            _collect_nonlocals(child, [*enclosing, id(child)], out, global_names)
        else:
            _collect_nonlocals(child, enclosing, out, global_names)


def _module_scope(
    tree: ast.Module,
    module_id: str,
    import_base: Callable[[ast.ImportFrom], str],
    global_names: set[str],
) -> dict[str, str]:
    """What each module-level name means, when every binding of it agrees (review 1, H3).

    Only module-level bindings count — an import inside a function binds a local there, which
    the function's own frame shadows. Every binding is counted: an import (``try``/``except
    ImportError`` fallbacks included), a ``def`` or ``class``, any other store; two that name
    different things, or one this pass cannot read, leave the name out. ``global x`` in any
    function counts as a binding nobody can read."""
    found: dict[str, set[str]] = {}

    def bind(name: str, target: str) -> None:
        found.setdefault(name, set()).add(target)

    for stmt in _statements(tree.body):
        if isinstance(stmt, ast.Import):
            for a in stmt.names:
                top = a.name.split(".")[0]
                bind(a.asname or top, f"py:{a.name if a.asname else top}")
        elif isinstance(stmt, ast.ImportFrom):
            base = import_base(stmt)
            for a in stmt.names:
                if a.name == "*":
                    continue  # a star binds names nobody listed here: re-export resolution owns it
                bind(a.asname or a.name, f"py:{base}.{a.name}" if base else f"py:{a.name}")
        elif isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            bind(stmt.name, f"{module_id}.{stmt.name}")
    stored, strings, _ = _scope_bindings(tree.body, import_base)
    for name_node in stored:
        bind(name_node.id, _UNREADABLE)
    for name in strings:
        if name not in found:  # imports and defs are bound above with their meaning
            bind(name, _UNREADABLE)
    for name in global_names:
        bind(name, _UNREADABLE)
    return {name: next(iter(t)) for name, t in found.items() if len(t) == 1 and _UNREADABLE not in t}


def _scope_bindings(
    body: list[ast.stmt], import_base: Callable[[ast.ImportFrom], str] | None = None
) -> tuple[list[ast.Name], list[str], list[tuple[str, str]]]:
    """Every binding in one scope: the ``ast.Name`` nodes it stores to or deletes (a loop, a
    ``with … as``, a walrus, a ``type`` alias, a comprehension or lambda inside it — counted,
    conservatively, as this scope's), and the names bound by a bare string: an import alias, a
    nested ``def``/``class``, an ``except … as``, a ``match`` capture, ``global``/``nonlocal``, a
    lambda parameter. With ``import_base``, each import alias's target too. Nested ``def`` and
    ``class`` bodies are their own scopes and not entered."""
    key = (id(body), import_base is not None)
    if key in _SCOPE_MEMO and _SCOPE_MEMO[key][0] is body:
        return _SCOPE_MEMO[key][1]
    stored: list[ast.Name] = []
    strings: list[str] = []
    imports: list[tuple[str, str]] = []
    pending: list[ast.AST] = list(body)
    while pending:
        node = pending.pop()
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            strings.append(node.name)
            continue
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store | ast.Del):
            stored.append(node)
        elif isinstance(node, ast.Import | ast.ImportFrom):
            for name, target in _import_bindings(node, import_base):
                strings.append(name)
                if target:
                    imports.append((name, target))
        elif isinstance(node, ast.ExceptHandler | ast.MatchAs | ast.MatchStar) and node.name:
            strings.append(node.name)
        elif isinstance(node, ast.MatchMapping) and node.rest:
            strings.append(node.rest)
        elif isinstance(node, ast.Global | ast.Nonlocal):
            strings += node.names
        elif isinstance(node, ast.Lambda):
            la = node.args
            strings += [p.arg for p in [*la.posonlyargs, *la.args, *la.kwonlyargs, la.vararg, la.kwarg] if p]
        pending.extend(ast.iter_child_nodes(node))
    result = (stored, strings, imports)
    _SCOPE_MEMO[key] = (body, result)
    return result


#: One file's scope walks, keyed by the identity of the statement list walked (checked against
#: the list itself, so a reused id never answers for another body). Cleared per file.
_SCOPE_MEMO: dict[
    tuple[int, bool], tuple[list[ast.stmt], tuple[list[ast.Name], list[str], list[tuple[str, str]]]]
] = {}


def _import_bindings(
    node: ast.Import | ast.ImportFrom, import_base: Callable[[ast.ImportFrom], str] | None
) -> list[tuple[str, str]]:
    """``(name, target)`` per alias an import binds; the target is empty without ``import_base``
    for a ``from`` import. A star binds names nobody listed here: re-export resolution owns it."""
    out: list[tuple[str, str]] = []
    if isinstance(node, ast.Import):
        for a in node.names:
            top = a.name.split(".")[0]
            out.append((a.asname or top, f"py:{a.name if a.asname else top}"))
    else:
        base = import_base(node) if import_base is not None else None
        for a in node.names:
            if a.name == "*":
                continue
            target = "" if base is None else (f"py:{base}.{a.name}" if base else f"py:{a.name}")
            out.append((a.asname or a.name, target))
    return out


def _scope_meanings(
    body: list[ast.stmt], import_base: Callable[[ast.ImportFrom], str]
) -> dict[str, str | None]:
    """Each name a scope binds -> its import target when *every* binding of it is an import of
    that one target (`from x import Store` inside a function), else ``None``."""
    stored, strings, imports = _scope_bindings(body, import_base)
    counts: dict[str, int] = {}
    for name in [n.id for n in stored] + strings:
        counts[name] = counts.get(name, 0) + 1
    targets: dict[str, set[str]] = {}
    import_counts: dict[str, int] = {}
    for name, target in imports:
        targets.setdefault(name, set()).add(target)
        import_counts[name] = import_counts.get(name, 0) + 1
    out: dict[str, str | None] = {}
    for name, count in counts.items():
        found = targets.get(name, set())
        out[name] = next(iter(found)) if len(found) == 1 and import_counts.get(name) == count else None
    return out


def _function_meanings(
    node: ast.FunctionDef | ast.AsyncFunctionDef, import_base: Callable[[ast.ImportFrom], str]
) -> dict[str, str | None]:
    """Every name a function binds in its own scope — what shadows an outer name inside it."""
    args = node.args
    meanings = _scope_meanings(node.body, import_base)
    for a in [*args.posonlyargs, *args.args, *args.kwonlyargs, args.vararg, args.kwarg]:
        if a is not None:
            meanings[a.arg] = None  # a parameter is bound once more, by the call: not import-only
    return meanings


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


__all__ = ["ReceiverScan", "resolve_receivers"]
