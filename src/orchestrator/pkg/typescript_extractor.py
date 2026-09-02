"""TypeScript front-end for the PKG extractor (G6: a third language).

Maps TypeScript source onto the same universal ``facts`` vocabulary the Python
and Java extractors use — so the knowledge graph stays language-neutral and a
new stack adds a front-end, not a reshape. Parsing is via tree-sitter (accurate
ASTs, unlike regex), an OPTIONAL dependency: install the ``typescript`` extra
(``uv pip install 'orchestrator[typescript]'``). The import is lazy so the base
install stays stdlib-only and importing this module never fails.

Emits the high-confidence declaration subset, precision-first like the other
front-ends: ``Module`` (the file, path-addressed — TS has no package namespace),
``Type`` (class/interface/``type`` alias/enum), ``Function`` (function decls,
exported arrow consts, class methods, interface method signatures), ``Field``
(class properties, interface members) nodes; ``IMPORTS``, ``CONTAINS``, and
``IMPLEMENTS`` (class ``extends``/``implements`` + interface ``extends``) edges.
``CALLS`` is emitted only where the callee resolves precisely (a second pass over
function/method bodies): a bare call to a module-level function or an imported
binding, ``this.method()`` calls to a sibling, and ``ns.func()`` calls through an
imported namespace. Instance calls on a typed variable (``obj.method()``) are
skipped — they'd need type inference, and a guessed edge poisons grounding.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from orchestrator.pkg.extractor import rel_module_name
from orchestrator.pkg.facts import Edge, EdgeKind, FactBatch, Node, NodeKind, Provenance

if TYPE_CHECKING:
    from tree_sitter import Node as TSNode

_TYPE_DECLS = frozenset(
    {
        "class_declaration",
        "abstract_class_declaration",
        "interface_declaration",
        "type_alias_declaration",
        "enum_declaration",
    }
)
_FUNC_CONST_DECLS = frozenset({"lexical_declaration", "variable_declaration"})


class TypeScriptExtractor:
    """TypeScript front-end (tree-sitter). Install the ``typescript`` extra to use it."""

    language: str = "typescript"
    suffixes: tuple[str, ...] = (".ts", ".tsx")

    def __init__(self) -> None:
        #: Member calls whose receiver type is known but whose target is unproven until the
        #: whole repository is in hand. Drained by `finalize`.
        self._pending_calls: list[_PendingCall] = []

    def finalize(self, batch: FactBatch) -> FactBatch:
        """Emit the deferred member calls whose target actually exists in the merged graph.

        A single file can resolve `h: Handler` to `ts:app/handler.Handler`, and cannot know
        whether that type has a `run`. Minting the id anyway is the fabrication that cost 497
        edges in Python and 47 across four front-ends. So the check is existence, here, once
        every file has been read: **if the node is not in the graph, no edge is drawn** — the
        same skip-rather-than-guess rule, moved to the only place with enough information to
        apply it.

        Clears the queue so a later extraction starts clean, as Go's finalize does.
        """
        pending = self._pending_calls
        self._pending_calls = []
        if not pending:
            return batch
        known = {n.id for n in batch.nodes}
        for call in pending:
            target = f"{call.type_id}.{call.method}"
            if target in known:
                batch.add_edge(Edge(call.caller, target, EdgeKind.CALLS, Provenance(call.rel, call.line)))
            # The receiver's own type is a call only when it was *constructed* here.
            if call.constructed and call.type_id in known:
                batch.add_edge(
                    Edge(call.caller, call.type_id, EdgeKind.CALLS, Provenance(call.rel, call.line))
                )
        return batch

    def module_name(self, path: Path, root: Path) -> str:
        # TS modules are path-addressed (no package decl): repo-relative path
        # without the extension, with ``index`` collapsed to its directory
        # (mirrors the Python ``__init__`` collapse).
        rel = rel_module_name(path, root)
        for suffix in self.suffixes:
            if rel.endswith(suffix):
                rel = rel[: -len(suffix)]
                break
        if rel.endswith("/index"):
            rel = rel[: -len("/index")]
        elif rel == "index":
            rel = ""
        return rel

    def extract(self, *, path: Path, module: str, rel: str) -> FactBatch:
        parser = _ts_parser(path.suffix)
        source = path.read_bytes()
        tree = parser.parse(source)
        batch = FactBatch()
        module_id = f"ts:{module}" if module else "ts:<root>"
        batch.add_node(Node(module_id, NodeKind.MODULE, module or rel, "typescript", Provenance(rel, 1)))

        decls = [_unwrap(node) for node in tree.root_node.named_children]
        imports, namespaces = self._imports(decls, module_id, source, rel, batch)
        local_types = {
            _field_text(d, "name", source) for d in decls if d is not None and d.type in _TYPE_DECLS
        }
        # Two-pass: collect bodies + a module-level callable registry during the
        # declaration walk, then resolve calls once every callable id is known.
        funcs: list[tuple[str, str | None, TSNode]] = []
        local_funcs: dict[str, str] = {}
        type_methods: dict[str, set[str]] = {}
        for node in decls:
            if node is None:
                continue
            if node.type in _TYPE_DECLS:
                self._emit_type(
                    node, module_id, imports, local_types, source, rel, batch, funcs, type_methods
                )
            elif node.type == "function_declaration":
                _emit_function(node, module_id, source, rel, batch, funcs, local_funcs)
            elif node.type in _FUNC_CONST_DECLS:
                self._emit_const_functions(node, module_id, source, rel, batch, funcs, local_funcs)
        # Express routes: top-level expression statements the declaration walk above skips.
        # Emitted here rather than in `finalize` because a mount (`app.use("/v1", r)`) and the
        # router it mounts are the same variable in the same module; a router imported from
        # another file keeps its local path, which `emit` documents as the deliberate exception.
        from orchestrator.pkg.typescript_routes import emit as _emit_routes
        from orchestrator.pkg.typescript_routes import scan_module as _scan_routes

        _emit_routes(_scan_routes(decls, source, rel, local_funcs), batch)

        for fid, type_id, body in funcs:
            _calls(
                fid,
                type_id,
                body,
                type_methods,
                local_funcs,
                imports,
                namespaces,
                source,
                rel,
                batch,
                local_types,
                module_id,
                self._pending_calls,
            )
        return batch

    def _imports(
        self, decls: list[TSNode | None], module_id: str, source: bytes, rel: str, batch: FactBatch
    ) -> tuple[dict[str, str], set[str]]:
        """Emit IMPORTS edges; return ({localName: specifier}, namespace-bound locals)."""
        by_local: dict[str, str] = {}
        namespaces: set[str] = set()
        for node in decls:
            if node is None or node.type != "import_statement":
                continue
            source_node = node.child_by_field_name("source")
            spec = _text(source_node, source).strip("\"'") if source_node is not None else ""
            if not spec:
                continue
            # A RELATIVE specifier names a module in this repo, so resolve it to that
            # module's id rather than recording the literal `../environment`. Left raw, the
            # same first-party module appeared once per importing directory as a separate
            # "external" module — `ts:../environment` and `ts:../../environments/environment`
            # alongside the real `ts:.../environments/environment` — so IMPORTS never joined
            # and `pkg verify` reported them as phantoms.
            resolved = _relative_module(spec, rel)
            mid = f"ts:{resolved}" if resolved is not None else f"ts:{spec}"
            batch.add_node(
                Node(mid, NodeKind.MODULE, resolved or spec, "typescript", external=resolved is None)
            )
            batch.add_edge(Edge(module_id, mid, EdgeKind.IMPORTS, Provenance(rel, node.start_point[0] + 1)))
            locals_, ns_locals = _imported_locals(node, source)
            for local in locals_:
                by_local[local] = spec
            namespaces.update(ns_locals)
        return by_local, namespaces

    def _emit_type(
        self,
        node: TSNode,
        parent_id: str,
        imports: dict[str, str],
        local_types: set[str],
        source: bytes,
        rel: str,
        batch: FactBatch,
        funcs: list[tuple[str, str | None, TSNode]],
        type_methods: dict[str, set[str]],
    ) -> None:
        name = _field_text(node, "name", source)
        if not name:
            return
        type_id = f"{parent_id}.{name}"
        line = node.start_point[0] + 1
        batch.add_node(
            Node(type_id, NodeKind.TYPE, name, "typescript", Provenance(rel, line, node.end_point[0] + 1))
        )
        batch.add_edge(Edge(parent_id, type_id, EdgeKind.CONTAINS, Provenance(rel, line)))

        for base in _supertypes(node, source):
            target = _resolve_type(base, imports, local_types, parent_id, rel)
            if target is not None:
                # A base type from a package (`implements OnInit` from @angular/core) needs
                # the same external node a call target does, or the edge dangles.
                _ensure_external(batch, target, kind=NodeKind.TYPE)
                batch.add_edge(Edge(type_id, target, EdgeKind.IMPLEMENTS, Provenance(rel, line)))

        body = node.child_by_field_name("body")
        if body is None:
            return
        for member in body.named_children:
            mline = member.start_point[0] + 1
            if member.type in ("method_definition", "method_signature"):
                mname = _field_text(member, "name", source)
                if mname:
                    fid = f"{type_id}.{mname}"
                    batch.add_node(Node(fid, NodeKind.FUNCTION, mname, "typescript", Provenance(rel, mline)))
                    batch.add_edge(Edge(type_id, fid, EdgeKind.CONTAINS, Provenance(rel, mline)))
                    type_methods.setdefault(type_id, set()).add(mname)
                    mbody = member.child_by_field_name("body")
                    if mbody is not None:
                        funcs.append((fid, type_id, mbody))
            elif member.type in ("public_field_definition", "property_signature"):
                fname = _field_text(member, "name", source)
                if fname:
                    fid = f"{type_id}.{fname}"
                    batch.add_node(Node(fid, NodeKind.FIELD, fname, "typescript", Provenance(rel, mline)))
                    batch.add_edge(Edge(type_id, fid, EdgeKind.CONTAINS, Provenance(rel, mline)))

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
        """``export const f = () => {}`` / ``const f = function () {}`` → a Function node."""
        for declarator in node.named_children:
            if declarator.type != "variable_declarator":
                continue
            value = declarator.child_by_field_name("value")
            if value is None or value.type not in ("arrow_function", "function_expression", "function"):
                continue
            name = _field_text(declarator, "name", source)
            if not name:
                continue
            line = declarator.start_point[0] + 1
            fid = f"{module_id}.{name}"
            batch.add_node(Node(fid, NodeKind.FUNCTION, name, "typescript", Provenance(rel, line)))
            batch.add_edge(Edge(module_id, fid, EdgeKind.CONTAINS, Provenance(rel, line)))
            local_funcs[name] = fid
            fbody = value.child_by_field_name("body")
            if fbody is not None:
                funcs.append((fid, None, fbody))


def _emit_function(
    node: TSNode,
    module_id: str,
    source: bytes,
    rel: str,
    batch: FactBatch,
    funcs: list[tuple[str, str | None, TSNode]],
    local_funcs: dict[str, str],
) -> None:
    name = _field_text(node, "name", source)
    if not name:
        return
    line = node.start_point[0] + 1
    fid = f"{module_id}.{name}"
    batch.add_node(Node(fid, NodeKind.FUNCTION, name, "typescript", Provenance(rel, line)))
    batch.add_edge(Edge(module_id, fid, EdgeKind.CONTAINS, Provenance(rel, line)))
    local_funcs[name] = fid
    body = node.child_by_field_name("body")
    if body is not None:
        funcs.append((fid, None, body))


# Call-resolution boundaries: a nested *named* scope (function/method/class) is
# collected separately, so don't attribute its calls to the enclosing function.
# Anonymous arrows / function expressions are closures — we descend into them.
_CALL_SCOPE_STOP = frozenset(
    {"function_declaration", "method_definition", "class_declaration", "abstract_class_declaration"}
)


def _ensure_external(batch: FactBatch, target: str, *, kind: NodeKind = NodeKind.FUNCTION) -> None:
    """Give a package-keyed target a node, so the edge lands somewhere.

    A call to an imported third-party symbol resolves to ``ts:<specifier>:<name>`` — an
    honest id naming a real npm package — but no node was ever emitted for it, so the edge
    dangled. Measured on a real Angular codebase: **854 dangling edges**, almost all of them
    `rxjs/operators:takeUntil`, `@angular/core:OnInit`, `jquery:$` and friends.

    This is the shape Python already uses (``py:ValueError`` exists as an external ``Type``):
    the node is `external`, so it is ungrounded and excluded from every count that claims to
    describe first-party code, while `pkg verify` stops reporting a dangling edge that was
    never wrong — only unlanded.

    Repo-local ids (``ts:path/to/mod.name``) are left alone: those *should* resolve to a real
    declaration, and inventing a node for one would paper over a genuine resolution miss.
    """
    # `ts:<specifier>:<name>` — the package form has a second colon; repo-local ids do not.
    if target.count(":") < 2:
        return
    _, spec, name = target.split(":", 2)
    batch.add_node(Node(target, kind, name, "typescript", external=True))
    batch.add_node(Node(f"ts:{spec}", NodeKind.MODULE, spec, "typescript", external=True))


def _relative_module(spec: str, rel: str) -> str | None:
    """Module id path for a relative specifier, or None when it names a package.

    Mirrors `_import_target`'s path handling so an IMPORTS edge and a CALLS edge to the same
    module agree on its id — they disagreed before, which is why the join failed.
    """
    if not spec.startswith("."):
        return None
    import posixpath

    joined = posixpath.normpath(posixpath.join(posixpath.dirname(rel), spec))
    for suffix in (".ts", ".tsx"):
        if joined.endswith(suffix):
            joined = joined[: -len(suffix)]
            break
    if joined.endswith("/index"):
        joined = joined[: -len("/index")]
    return joined


def _import_target(spec: str, name: str, rel: str) -> str:
    """Resolve an imported call target to a node id.

    A **relative** specifier (``./core``) is resolved against the importing
    file's path to the definition's module id (``ts:core.name``), so cross-file
    calls connect to the real symbol. A **package** specifier (``react``) stays
    an external, specifier-keyed id (``ts:react:name``) — its definition isn't in
    this repo.
    """
    if not spec.startswith("."):
        return f"ts:{spec}:{name}"
    import posixpath

    joined = posixpath.normpath(posixpath.join(posixpath.dirname(rel), spec))
    for suffix in (".ts", ".tsx"):
        if joined.endswith(suffix):
            joined = joined[: -len(suffix)]
            break
    if joined.endswith("/index"):
        joined = joined[: -len("/index")]
    elif joined == "index":
        joined = ""
    return f"ts:{joined}.{name}" if joined else f"ts:{name}"


def _calls(
    caller: str,
    type_id: str | None,
    body: TSNode,
    type_methods: dict[str, set[str]],
    local_funcs: dict[str, str],
    imports: dict[str, str],
    namespaces: set[str],
    source: bytes,
    rel: str,
    batch: FactBatch,
    local_types: set[str],
    module_id: str,
    pending: list[_PendingCall],
) -> None:
    """Emit CALLS for precisely-resolvable ``call_expression`` sites in a body."""
    siblings = type_methods.get(type_id or "", set())
    bound = _bound_names(body, source)
    typed, constructors = _typed_locals(body, source)
    stack = list(body.named_children)
    while stack:
        n = stack.pop()
        if n.type in _CALL_SCOPE_STOP:
            continue
        if n.type == "call_expression":
            fn = n.child_by_field_name("function")
            line = n.start_point[0] + 1
            if fn is not None and fn.type == "identifier" and line >= bound.get(_text(fn, source), 1 << 30):
                # A call through a name this function bound itself — a parameter, a local, or a
                # closure argument. `local_funcs` and `imports` still hold the FILE-level
                # binding of that name, so resolving it would emit an edge to a definition the
                # call never reaches. The callee here is decided by whoever supplied the value.
                stack.extend(n.named_children)
                continue
            target = _resolve_callee(fn, type_id, siblings, local_funcs, imports, namespaces, rel, source)
            if target is not None:
                _ensure_external(batch, target)
                batch.add_edge(Edge(caller, target, EdgeKind.CALLS, Provenance(rel, n.start_point[0] + 1)))
            elif fn is not None and fn.type == "member_expression":
                _defer_member_call(
                    caller, fn, typed, constructors, imports, local_types, module_id, rel, source, pending
                )
        stack.extend(n.named_children)


@dataclass(frozen=True)
class _PendingCall:
    """A ``receiver.method()`` whose receiver type is known but whose target is not yet proven."""

    caller: str
    type_id: str
    method: str
    rel: str
    line: int
    #: Whether the receiver was *constructed* at the call site. `new Handler().run()` reaches
    #: the constructor and the method; `h.run()` on an annotated parameter reaches only the
    #: method — the caller received the instance, it did not build one. Emitting the type edge
    #: for both cost 0.20 precision the moment it was measured.
    constructed: bool


def _typed_locals(body: TSNode, src: bytes) -> tuple[dict[str, str], dict[str, bool]]:
    """``{identifier: type name}`` for the receivers a function body states the type of.

    Three forms, all of them **in the same tree as the call** — no inference, no symbol table:

    ===========================  ===========================================
    `function f(h: Handler)`     the parameter's `type_annotation`
    `let h: Handler`             the declarator's `type_annotation`
    `const h = new Handler()`    the `new_expression`'s constructor name
    ===========================  ===========================================

    Everything else — a call's return value, a destructured binding, a field of an object
    literal — is left out on purpose. Each needs an answer this pass does not have, and a
    receiver typed by guess is the fabricated edge that `_resolve_call` exists to refuse.
    """
    found: dict[str, str] = {}
    constructed: dict[str, bool] = {}
    # Parameters are siblings of the body, not inside it — walking only the body finds every
    # local and no annotated parameter, which is the single most common typed receiver there is.
    params = body.parent.child_by_field_name("parameters") if body.parent is not None else None
    stack = list(body.named_children) + (list(params.named_children) if params is not None else [])
    # The whole body, function scopes included: a nested arrow sees the outer `const`, and
    # stopping at the boundary would drop the receivers most likely to appear in a callback.
    while stack:
        n = stack.pop()
        if n.type in ("required_parameter", "optional_parameter", "variable_declarator"):
            name_node = n.child_by_field_name("pattern") or n.child_by_field_name("name")
            name = _text(name_node, src) if name_node is not None and name_node.type == "identifier" else ""
            if name:
                annotated = n.child_by_field_name("type")
                value = n.child_by_field_name("value")
                built = value is not None and value.type == "new_expression"
                if annotated is not None:
                    # `: Handler` — the annotation node keeps its colon.
                    found.setdefault(name, _text(annotated, src).lstrip(":").strip())
                    constructed.setdefault(name, built)
                elif built and value is not None:
                    ctor = value.child_by_field_name("constructor")
                    if ctor is not None:
                        found.setdefault(name, _text(ctor, src))
                        constructed.setdefault(name, True)
        stack.extend(n.named_children)
    return found, constructed


def _defer_member_call(
    caller: str,
    fn: TSNode,
    typed: dict[str, str],
    constructors: dict[str, bool],
    imports: dict[str, str],
    local_types: set[str],
    module_id: str,
    rel: str,
    source: bytes,
    pending: list[_PendingCall],
) -> None:
    """Record ``receiver.method()`` for the whole-repo pass, when the receiver's type is stated.

    **Deferred rather than emitted**, and that is the point. The type resolves here — `Handler`
    is an import or a sibling — but whether `Handler` *has* a `run` cannot be known from one
    file, and minting `ts:app/handler.Handler.run` unchecked is precisely the invented edge that
    cost 497 fabrications in Python. `finalize` sees the merged graph and emits only where the
    target node actually exists.
    """
    obj = fn.child_by_field_name("object")
    prop = fn.child_by_field_name("property")
    pname = _text(prop, source) if prop is not None else ""
    if obj is None or not pname:
        return

    constructed = False
    if obj.type == "new_expression":  # `new Handler().run()` — the receiver IS the constructor
        ctor = obj.child_by_field_name("constructor")
        base = _text(ctor, source) if ctor is not None else ""
        constructed = True
    elif obj.type == "identifier":
        name = _text(obj, source)
        base = typed.get(name, "")
        # A local whose type came from `new T()` was constructed in this body; one whose type
        # came from an annotation was handed in.
        constructed = bool(base) and constructors.get(name, False)
    else:
        return
    if not base:
        return

    type_id = _resolve_type(base, imports, local_types, module_id, rel)
    if type_id is not None:
        pending.append(_PendingCall(caller, type_id, pname, rel, fn.start_point[0] + 1, constructed))


def _pattern_names(node: TSNode | None, src: bytes) -> list[str]:
    """Names a binding pattern binds — never the contents of a default value.

    `const { arg: slotName = makeDefault() } = x` binds `slotName`. Sweeping the pattern for
    identifiers also picks up `makeDefault`, and then every later call to it is skipped as
    shadowed — a silently dropped edge, which is the failure this whole change exists to avoid
    in the other direction.
    """
    if node is None:
        return []
    if node.type in ("identifier", "shorthand_property_identifier_pattern"):
        return [_text(node, src)]
    if node.type == "pair_pattern":
        return _pattern_names(node.child_by_field_name("value"), src)
    if node.type == "assignment_pattern":
        return _pattern_names(node.child_by_field_name("left"), src)
    if node.type in ("object_pattern", "array_pattern", "rest_pattern"):
        return [n for c in node.named_children for n in _pattern_names(c, src)]
    return []


def _params_of(node: TSNode | None, src: bytes) -> list[str]:
    """Parameter names of a callable. Direct children only, for one specific reason.

    A parameter typed ``(v: string) => void`` carries its own ``formal_parameters`` under the
    ``type`` field. ``v`` is bound nowhere, and treating it as a local would drop real calls.
    """
    if node is None:
        return []
    single = node.child_by_field_name("parameter")  # `x => …`
    if single is not None:
        return _pattern_names(single, src)
    params = node.child_by_field_name("parameters")
    if params is None:
        return []
    out: list[str] = []
    for child in params.named_children:
        pattern = child.child_by_field_name("pattern")
        out.extend(_pattern_names(pattern if pattern is not None else child, src))
    return out


def _bound_names(body: TSNode, src: bytes) -> dict[str, int]:
    """Names this function binds, each with the first line it is in scope.

    A callee in this map is reached through a parameter, a local or a closure argument, so no
    file-level id names it. The line matters: TypeScript's temporal dead zone makes a call
    *above* a `const` an error rather than a call to an outer function, but keeping the line
    costs nothing and keeps this helper honest about what it is asserting.

    The walk mirrors `_calls_in_body` exactly — same `_CALL_SCOPE_STOP` boundaries — because a
    binding set that covers more or less ground than the call walk would either drop real edges
    or miss shadowed ones. Anonymous arrows are descended into by both: `hook.forEach(h => h())`
    is where vue/core's one fabricated edge came from.
    """
    bound: dict[str, int] = {}

    def bind(name: str, line: int) -> None:
        if name and line < bound.get(name, 1 << 30):
            bound[name] = line

    for name in _params_of(body.parent, src):
        bind(name, body.parent.start_point[0] + 1 if body.parent is not None else 1)

    stack = list(body.named_children)
    while stack:
        n = stack.pop()
        if n.type in _CALL_SCOPE_STOP:
            continue
        after = n.end_point[0] + 2  # a declaration is in scope from the line after it ends
        if n.type == "variable_declarator":
            for name in _pattern_names(n.child_by_field_name("name"), src):
                bind(name, after)
        elif n.type in ("for_in_statement", "for_statement"):
            for name in _pattern_names(n.child_by_field_name("left"), src):
                bind(name, n.start_point[0] + 1)
        elif n.type == "catch_clause":
            for name in _pattern_names(n.child_by_field_name("parameter"), src):
                bind(name, n.start_point[0] + 1)
        elif n.type in ("arrow_function", "function_expression", "generator_function"):
            for name in _params_of(n, src):
                bind(name, n.start_point[0] + 1)
        stack.extend(n.named_children)
    return bound


def _resolve_callee(
    fn: TSNode | None,
    type_id: str | None,
    siblings: set[str],
    local_funcs: dict[str, str],
    imports: dict[str, str],
    namespaces: set[str],
    rel: str,
    source: bytes,
) -> str | None:
    """The callee's node id, or ``None`` when it can't be resolved precisely."""
    if fn is None:
        return None
    if fn.type == "identifier":
        name = _text(fn, source)
        if name in local_funcs:  # module-level function / arrow const
            return local_funcs[name]
        if name in imports:  # imported binding → resolve to its definition module
            return _import_target(imports[name], name, rel)
        return None
    if fn.type == "member_expression":
        obj = fn.child_by_field_name("object")
        prop = fn.child_by_field_name("property")
        pname = _text(prop, source) if prop is not None else ""
        if obj is None or not pname:
            return None
        if obj.type == "this":  # this.method() → sibling
            return f"{type_id}.{pname}" if type_id and pname in siblings else None
        if obj.type == "identifier":  # ns.func() through an imported NAMESPACE only
            # A named binding is a value, not a module: `items.forEach()` calls an Array
            # method, not an export of the module `items` came from. Resolving it produced
            # `ts:<module>.forEach` — a node that does not and should not exist.
            oname = _text(obj, source)
            if oname in namespaces and oname in imports:
                return _import_target(imports[oname], pname, rel)
    return None


def _unwrap(node: TSNode) -> TSNode | None:
    """Top-level decls are wrapped in ``export_statement``; return the inner declaration."""
    if node.type != "export_statement":
        return node
    decl = node.child_by_field_name("declaration")
    if decl is not None:
        return decl
    for child in node.named_children:
        if (
            child.type in _TYPE_DECLS
            or child.type in _FUNC_CONST_DECLS
            or child.type == "function_declaration"
        ):
            return child
    return None


def _imported_locals(import_stmt: TSNode, source: bytes) -> tuple[list[str], list[str]]:
    """Local names from an import, and which of them are NAMESPACE imports.

    The distinction is load-bearing. `X.foo()` only means "the export `foo` of X's module"
    when X was bound by `import * as X`. For a named binding — `import { items }` — X is a
    *value*, and `items.forEach()` is an Array method, not an export. Treating the two alike
    resolved `sidenavMenuItems.forEach(...)` to `ts:<module>.forEach`, a node that does not
    and should not exist.
    """
    locals_: list[str] = []
    namespaces: list[str] = []
    for clause in import_stmt.named_children:
        if clause.type != "import_clause":
            continue
        for child in clause.named_children:
            if child.type == "identifier":  # default import
                locals_.append(_text(child, source))
            elif child.type == "namespace_import":  # * as ns
                ident = child.named_children[-1] if child.named_children else None
                if ident is not None:
                    locals_.append(_text(ident, source))
                    namespaces.append(_text(ident, source))
            elif child.type == "named_imports":
                for spec in child.named_children:
                    if spec.type != "import_specifier":
                        continue
                    alias = spec.child_by_field_name("alias")
                    name = alias if alias is not None else spec.child_by_field_name("name")
                    if name is not None:
                        locals_.append(_text(name, source))
    return [n for n in locals_ if n], [n for n in namespaces if n]


def _supertypes(node: TSNode, source: bytes) -> list[str]:
    """``extends`` + ``implements`` type names of a class/interface declaration."""
    out: list[str] = []
    for child in node.named_children:
        if child.type == "class_heritage":
            for clause in child.named_children:
                if clause.type == "extends_clause":
                    value = clause.child_by_field_name("value")
                    out.extend(_text(v, source) for v in ([value] if value else clause.named_children))
                elif clause.type == "implements_clause":
                    out.extend(_text(t, source) for t in clause.named_children)
        elif child.type == "extends_type_clause":  # interface extends
            out.extend(_text(t, source) for t in child.named_children)
    return [n for n in out if n]


def _resolve_type(
    base: str, imports: dict[str, str], local_types: set[str], module_id: str, rel: str = ""
) -> str | None:
    """Resolve a base type name to a node id (precision-first, else None)."""
    name = base.split("<", 1)[0].strip()  # drop generics: Repo<T> → Repo
    if not name or "." in name:  # namespaced base (ns.Base) — won't second-guess
        return None
    if name in imports:
        # Route through `_import_target` so a RELATIVE specifier resolves to the repo-local
        # module id. Building `ts:{spec}:{name}` from the raw text gave `ts:./serializable:
        # Serializable` — two colons, which reads as a package id, so an external module node
        # `ts:./serializable` was minted alongside the real first-party one.
        return _import_target(imports[name], name, rel)
    if name in local_types:  # same-module sibling type
        return f"{module_id}.{name}"
    return None


def _field_text(node: TSNode, field: str, source: bytes) -> str:
    child = node.child_by_field_name(field)
    return _text(child, source) if child is not None else ""


def _text(node: TSNode | None, source: bytes) -> str:
    if node is None:
        return ""
    return source[node.start_byte : node.end_byte].decode("utf-8", "replace").strip()


def _ts_parser(suffix: str) -> Any:
    try:
        import tree_sitter_typescript
        from tree_sitter import Language, Parser
    except ImportError as exc:  # pragma: no cover - exercised only without the extra
        raise RuntimeError(
            "TypeScript extraction needs tree-sitter; install the extra: "
            "uv pip install 'tree-sitter>=0.21' 'tree-sitter-typescript>=0.21'"
        ) from exc
    raw = (
        tree_sitter_typescript.language_tsx()
        if suffix == ".tsx"
        else tree_sitter_typescript.language_typescript()
    )
    language = Language(raw)
    try:
        return Parser(language)
    except TypeError:  # older tree-sitter API
        parser = Parser()
        parser.language = language
        return parser


__all__ = ["TypeScriptExtractor"]
