"""Java front-end for the PKG extractor (G6: a second language).

Maps Java source onto the same universal ``facts`` vocabulary the Python
extractor uses — so the knowledge graph is language-neutral and a second
stack adds a front-end, not a reshape. Parsing is via tree-sitter (accurate
ASTs, unlike regex), which is an OPTIONAL dependency: install the ``java``
extra (``uv pip install 'orchestrator[java]'``). The import is lazy so the
base install stays stdlib-only and importing this module never fails.

Emits the high-confidence declaration subset, precision-first like the Python
front-end: ``Module`` (the package), ``Type`` (class/interface/enum/record),
``Function`` (method/constructor), ``Field`` nodes; ``IMPORTS``, ``CONTAINS``,
and ``IMPLEMENTS`` (extends + implements) edges; JAX-RS / Jakarta REST
annotations become ``Endpoint`` nodes with ``EXPOSES`` edges to their handler
methods, and so do Spring MVC ``@GetMapping``/``@RequestMapping`` handlers, read
through the shared ``jvm_routes`` module the Kotlin front-end also uses (D16 of
docs/specs/kotlin-support-roadmap.md) — Spring is the framework most Java services
actually use, and this front-end was blind to it until P6. ``CALLS`` is emitted only
where the callee resolves precisely (a
second pass over method bodies): unqualified / ``this.`` calls to a sibling
method, and ``Type.method()`` calls whose ``Type`` resolves via imports or the
same package. Calls through a *typed* receiver — a parameter, field (own, inherited,
or an enclosing class's), typed local or ``var x = new T()`` — are deferred to
``finalize`` and settled by ``typed_receivers`` once every declaration is known (B21);
a receiver whose type is not written (a lambda parameter, ``var x = call()``) refuses,
because a guessed edge poisons grounding. Overloads collapse onto one id (no arity in ids).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from orchestrator.pkg.extractor import rel_module_name
from orchestrator.pkg.facts import Edge, EdgeKind, FactBatch, Node, NodeKind, Provenance
from orchestrator.pkg.jvm_routes import (
    MAPPING_ANNOTATIONS,
    METHOD_ARGUMENT,
    PATH_ARGUMENTS,
    RouteAnnotation,
    class_prefix,
    emit_endpoints,
    is_controller,
    literal_path,
    resolves_into_spring,
)
from orchestrator.pkg.typed_receivers import (
    DeferredCall,
    ReceiverState,
    TypeRef,
    resolve_bases,
    resolve_calls,
)

if TYPE_CHECKING:
    from tree_sitter import Node as TSNode

_PACKAGE_RE = re.compile(r"^\s*package\s+([\w.]+)\s*;", re.M)
_TYPE_DECLS = frozenset(
    {"class_declaration", "interface_declaration", "enum_declaration", "record_declaration"}
)
_HTTP_VERB_ANNOTATIONS = frozenset({"GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"})
_JAX_RS_PACKAGES = frozenset({"jakarta.ws.rs", "javax.ws.rs"})


@dataclass(frozen=True)
class _ImportContext:
    """Java imports needed for precise type and annotation resolution."""

    by_simple: dict[str, str]
    wildcard_prefixes: frozenset[str]


class JavaExtractor:
    """Java front-end (tree-sitter). Install the ``java`` extra to use it."""

    language: str = "java"
    suffixes: tuple[str, ...] = (".java",)

    def __init__(self) -> None:
        # `recv.m()` waits for `finalize`: the receiver's type, and whether it declares `m`,
        # are whole-repository questions (`typed_receivers`, B21).
        self._receivers = ReceiverState("java")

    def finalize(self, batch: FactBatch) -> FactBatch:
        """Settle the deferred typed-receiver calls, once every declaration is known.

        Bases a wildcard import or a nested name brought into scope are repointed first, so a
        member inherited through one is found (the per-file pass placed them in the package).
        """
        batch = resolve_bases(batch, self._receivers)
        resolve_calls(batch, self._receivers)
        return batch

    def module_name(self, path: Path, root: Path) -> str:
        # Java's module is the package declaration, which lives in the file
        # (not the path); fall back to the repo-relative path when unpackaged.
        try:
            m = _PACKAGE_RE.search(path.read_text(encoding="utf-8"))
        except OSError:
            m = None
        return m.group(1) if m else rel_module_name(path, root)

    def extract(self, *, path: Path, module: str, rel: str) -> FactBatch:
        parser = _java_parser()
        source = path.read_bytes()
        tree = parser.parse(source)
        batch = FactBatch()
        module_id = f"java:{module}" if module else "java:<root>"
        batch.add_node(Node(module_id, NodeKind.MODULE, module or rel, "java", Provenance(rel, 1)))

        imports = self._imports(tree.root_node, module_id, source, rel, batch)
        # Two-pass: collect method bodies + a per-type method registry during the
        # declaration walk, then resolve calls once every method id is known.
        funcs: list[tuple[str, str, TSNode]] = []
        type_methods: dict[str, set[str]] = {}
        for node in tree.root_node.named_children:
            if node.type in _TYPE_DECLS:
                self._emit_type(node, module_id, module, imports, source, rel, batch, funcs, type_methods)
        for fid, type_id, body in funcs:
            self._calls(fid, type_id, body, type_methods, imports, module, source, rel, batch)
        return batch

    def _imports(
        self, root: TSNode, module_id: str, source: bytes, rel: str, batch: FactBatch
    ) -> _ImportContext:
        """Emit concrete IMPORTS edges and retain wildcard prefixes for annotations."""
        by_simple: dict[str, str] = {}
        wildcard_prefixes: set[str] = set()
        for node in root.named_children:
            if node.type != "import_declaration":
                continue
            children = node.named_children
            if children and children[-1].type == "asterisk":
                if len(children) >= 2:
                    wildcard_prefixes.add(_text(children[-2], source))
                continue
            fqn = _text(children[-1], source) if children else ""
            if not fqn:
                continue
            tid = f"java:{fqn}"
            batch.add_node(Node(tid, NodeKind.MODULE, fqn, "java", external=True))
            batch.add_edge(Edge(module_id, tid, EdgeKind.IMPORTS, Provenance(rel, node.start_point[0] + 1)))
            by_simple[fqn.rsplit(".", 1)[-1]] = fqn
        return _ImportContext(by_simple, frozenset(wildcard_prefixes))

    def _emit_type(
        self,
        node: TSNode,
        parent_id: str,
        package: str,
        imports: _ImportContext,
        source: bytes,
        rel: str,
        batch: FactBatch,
        funcs: list[tuple[str, str, TSNode]],
        type_methods: dict[str, set[str]],
    ) -> None:
        name = _field_text(node, "name", source)
        if not name:
            return
        type_id = f"{parent_id}.{name}"
        line = node.start_point[0] + 1
        batch.add_node(
            Node(type_id, NodeKind.TYPE, name, "java", Provenance(rel, line, node.end_point[0] + 1))
        )
        batch.add_edge(Edge(parent_id, type_id, EdgeKind.CONTAINS, Provenance(rel, line)))
        if parent_id in type_methods:  # a nested / inner type: its enclosing type is in scope (D13)
            self._receivers.outer[type_id] = parent_id

        for base in _supertypes(node, source):
            target = self._resolve_type(base, package, imports)
            if target is not None:
                batch.add_edge(Edge(type_id, target, EdgeKind.IMPLEMENTS, Provenance(rel, line)))
                ref = _java_type_ref(
                    base, parent_id if parent_id in type_methods else None, package, imports, self._receivers
                )
                if ref is not None:
                    self._receivers.base_refs[(type_id, target)] = ref

        body = node.child_by_field_name("body")
        if body is None:
            return
        class_path = _path_annotation(node, source, imports)
        # Spring (D16). ``None`` means "not a controller, or a class prefix that is
        # not a literal" — both silence every method, for the same reason: a method
        # path without its real prefix names a route that does not exist.
        class_routes = _spring_annotations(node, imports, source)
        spring_prefix = class_prefix(class_routes) if is_controller(class_routes) else None
        methods = type_methods.setdefault(type_id, set())
        for member in body.named_children:
            mline = member.start_point[0] + 1
            if member.type in ("method_declaration", "constructor_declaration"):
                mname = _field_text(member, "name", source)
                if mname:
                    fid = f"{type_id}.{mname}"
                    batch.add_node(Node(fid, NodeKind.FUNCTION, mname, "java", Provenance(rel, mline)))
                    batch.add_edge(Edge(type_id, fid, EdgeKind.CONTAINS, Provenance(rel, mline)))
                    methods.add(mname)
                    if member.type == "method_declaration":
                        _jax_rs_endpoints(member, fid, class_path, imports, source, rel, batch)
                        if spring_prefix is not None:
                            emit_endpoints(
                                _spring_annotations(member, imports, source),
                                method_id=fid,
                                prefix=spring_prefix,
                                language="java",
                                rel=rel,
                                batch=batch,
                            )
                    mbody = member.child_by_field_name("body")
                    if mbody is not None:
                        funcs.append((fid, type_id, mbody))
            elif member.type == "field_declaration":
                fref = _java_type_node_ref(
                    member.child_by_field_name("type"), type_id, package, imports, self._receivers, source
                )
                for fname in _field_names(member, source):
                    fid = f"{type_id}.{fname}"
                    batch.add_node(Node(fid, NodeKind.FIELD, fname, "java", Provenance(rel, mline)))
                    batch.add_edge(Edge(type_id, fid, EdgeKind.CONTAINS, Provenance(rel, mline)))
                    self._receivers.add_field(type_id, fname, fref)
            elif member.type in _TYPE_DECLS:
                self._emit_type(member, type_id, package, imports, source, rel, batch, funcs, type_methods)

    def _calls(
        self,
        caller: str,
        type_id: str,
        body: TSNode,
        type_methods: dict[str, set[str]],
        imports: _ImportContext,
        package: str,
        source: bytes,
        rel: str,
        batch: FactBatch,
    ) -> None:
        """Emit CALLS for precisely-resolvable ``method_invocation`` sites in a body, and defer
        the ones through a typed receiver to ``finalize`` (B21)."""
        siblings = type_methods.get(type_id, set())
        scope = _method_scope(body.parent or body, type_id, package, imports, self._receivers, source)
        stack = list(body.named_children)
        while stack:
            n = stack.pop()
            if n.type in _TYPE_DECLS:
                continue  # nested/local class — a separate scope, not this method's calls
            if n.type == "method_invocation":
                line = n.start_point[0] + 1
                target = self._resolve_call(n, type_id, siblings, imports, package, source)
                if target is not None:
                    batch.add_edge(Edge(caller, target, EdgeKind.CALLS, Provenance(rel, line)))
                else:
                    deferred = _deferred_call(n, caller, type_id, scope, rel, line, source)
                    if deferred is not None:
                        self._receivers.calls.append(deferred)
            stack.extend(n.named_children)

    def _resolve_call(
        self,
        inv: TSNode,
        type_id: str,
        siblings: set[str],
        imports: _ImportContext,
        package: str,
        source: bytes,
    ) -> str | None:
        """The callee's node id, or ``None`` when it can't be resolved precisely."""
        name = _field_text(inv, "name", source)
        if not name:
            return None
        obj = inv.child_by_field_name("object")
        if obj is None or obj.type == "this":  # foo() / this.foo() → sibling method
            return f"{type_id}.{name}" if name in siblings else None
        if obj.type == "identifier":
            recv = _text(obj, source)
            # A capitalized receiver is a Type (static call); lowercase is a
            # variable (instance call) we can't resolve without type inference.
            if recv[:1].isupper():
                resolved = self._resolve_type(recv, package, imports)
                if resolved is not None:
                    return f"{resolved}.{name}"
        return None

    def _resolve_type(self, simple_or_fqn: str, package: str, imports: _ImportContext) -> str | None:
        """Resolve a base type name to a node id (precision-first, else None)."""
        name = simple_or_fqn.split("<", 1)[0].strip()  # drop generics: List<T> → List
        if not name or "." in name:  # a qualified base we won't second-guess
            return f"java:{name}" if "." in name else None
        if name in imports.by_simple:
            return f"java:{imports.by_simple[name]}"
        if package:  # same-package sibling type
            return f"java:{package}.{name}"
        return None


# --- typed receivers (B21) --------------------------------------------------

_UNREADABLE = object()  # bound, but with no type we can read — a receiver that refuses
_PRIMITIVES = frozenset({"int", "long", "short", "byte", "char", "boolean", "float", "double", "void", "var"})


def _java_type_ref(
    text: str, enclosing: str | None, package: str, imports: _ImportContext, state: ReceiverState
) -> TypeRef | None:
    """The candidate ids a written Java type name can denote, in the compiler's order: a type
    nested in an enclosing type, a single-type import, the same package, then on-demand imports
    as one level. A qualified ``Outer.Inner`` resolves its head the same way."""
    name = re.sub(r"<.*>", "", text).strip()
    if not name or name in _PRIMITIVES or any(ch in name for ch in "[]|&?@ ") or not name[0].isalpha():
        return None
    head, _, rest = name.partition(".")
    suffix = f".{rest}" if rest else ""
    groups: list[tuple[str, ...]] = []
    chain: list[str] = []
    cur = enclosing
    while cur is not None:
        chain.append(cur)
        cur = state.outer.get(cur)
    if head in imports.by_simple:
        groups.append((f"java:{imports.by_simple[head]}{suffix}",))
    if package:
        groups.append((f"java:{package}.{head}{suffix}",))
    groups.append(tuple(f"java:{w}.{head}{suffix}" for w in sorted(imports.wildcard_prefixes)))
    if rest:
        groups.append((f"java:{name}",))  # already fully qualified
    return TypeRef(tuple(g for g in groups if g), enclosing=tuple(chain), nested=f"{head}{suffix}")


def _java_type_node_ref(
    node: TSNode | None,
    enclosing: str,
    package: str,
    imports: _ImportContext,
    state: ReceiverState,
    source: bytes,
) -> TypeRef | None:
    if node is None or node.type in (
        "integral_type",
        "floating_point_type",
        "boolean_type",
        "void_type",
        "array_type",
    ):
        return None
    return _java_type_ref(_text(node, source), enclosing, package, imports, state)


def _method_scope(
    method: TSNode, type_id: str, package: str, imports: _ImportContext, state: ReceiverState, source: bytes
) -> dict[str, object]:
    """``name → TypeRef`` for everything a method binds, ``_UNREADABLE`` where the type is not
    written (``var x = call()``, a lambda parameter, a union catch, a varargs array, a pattern).

    Every binding form is collected, typed or not: a name missed here would fall through to a
    field of the same name and resolve to *its* type — the one way this pass could invent."""
    scope: dict[str, object] = {}

    def bind(name_node: TSNode | None, ref: object) -> None:
        name = _text(name_node, source) if name_node is not None else ""
        if name:
            prior = scope.get(name, ref)
            scope[name] = ref if prior == ref else _UNREADABLE

    def typed(node: TSNode | None) -> object:
        ref = _java_type_node_ref(node, type_id, package, imports, state, source)
        return ref if ref is not None else _UNREADABLE

    stack = [method]
    while stack:
        n = stack.pop()
        t = n.type
        if t in _TYPE_DECLS and n is not method:
            continue  # a local class — its own scope
        if t == "lambda_expression":
            params = n.child_by_field_name("parameters")
            idents = [params] if params is not None and params.type == "identifier" else []
            idents += [
                c for c in (params.named_children if params is not None else []) if c.type == "identifier"
            ]
            for p in params.named_children if params is not None else []:
                if p.type in ("formal_parameter", "spread_parameter"):
                    idents.append(p.child_by_field_name("name") or p)
            for ident in idents:
                bind(ident, _UNREADABLE)  # D13: lambda parameters refuse, typed or not
        elif t == "formal_parameter":
            bind(n.child_by_field_name("name"), typed(n.child_by_field_name("type")))
        elif t == "spread_parameter":  # varargs: an array
            for d in n.named_children:
                if d.type == "variable_declarator":
                    bind(d.child_by_field_name("name"), _UNREADABLE)
        elif t in ("local_variable_declaration", "field_declaration"):
            ty = n.child_by_field_name("type")
            for d in n.named_children:
                if d.type != "variable_declarator":
                    continue
                if ty is not None and _text(ty, source) == "var":
                    value = d.child_by_field_name("value")
                    new = value if value is not None and value.type == "object_creation_expression" else None
                    bind(
                        d.child_by_field_name("name"),
                        typed(new.child_by_field_name("type")) if new is not None else _UNREADABLE,
                    )
                else:
                    bind(
                        d.child_by_field_name("name"),
                        typed(ty) if not d.child_by_field_name("dimensions") else _UNREADABLE,
                    )
        elif t in ("enhanced_for_statement", "resource"):
            ty = n.child_by_field_name("type")
            bind(
                n.child_by_field_name("name"),
                typed(ty) if ty is not None and _text(ty, source) != "var" else _UNREADABLE,
            )
        elif t == "catch_formal_parameter":
            catch_type = next((c for c in n.named_children if c.type == "catch_type"), None)
            text = _text(catch_type, source) if catch_type is not None else ""
            ref = _java_type_ref(text, type_id, package, imports, state) if text and "|" not in text else None
            bind(n.child_by_field_name("name"), ref if ref is not None else _UNREADABLE)
        elif t == "instanceof_expression" and n.child_by_field_name("name") is not None:
            bind(n.child_by_field_name("name"), typed(n.child_by_field_name("right")))
        elif t in ("record_pattern", "type_pattern", "record_pattern_component"):
            for c in n.named_children:
                if c.type == "identifier":
                    bind(c, _UNREADABLE)
        stack.extend(n.named_children)
    return scope


def _deferred_call(
    inv: TSNode, caller: str, type_id: str, scope: dict[str, object], rel: str, line: int, source: bytes
) -> DeferredCall | None:
    """A ``recv.m()`` through a variable or field, for ``finalize`` — or None when out of scope."""
    name = _field_text(inv, "name", source)
    obj = inv.child_by_field_name("object")
    if not name or obj is None:
        return None
    if obj.type == "identifier":
        recv = _text(obj, source)
        if recv[:1].isupper() and recv not in scope:
            return None  # a type name — the static path's, not a receiver
        if recv in scope:
            bound = scope[recv]
            return (
                DeferredCall(caller, name, rel, line, receiver=bound) if isinstance(bound, TypeRef) else None
            )
        return DeferredCall(caller, name, rel, line, field_of=type_id, field_name=recv)
    if obj.type == "field_access" and _field_text(obj, "object", source) == "this":
        return DeferredCall(
            caller, name, rel, line, field_of=type_id, field_name=_field_text(obj, "field", source)
        )
    return None


def _jax_rs_endpoints(
    method: TSNode,
    method_id: str,
    class_path: str | None,
    imports: _ImportContext,
    source: bytes,
    rel: str,
    batch: FactBatch,
) -> None:
    """Emit JAX-RS/Jakarta REST endpoints for a verb-annotated handler.

    Fully qualified annotations work directly; unqualified annotations must
    resolve through an explicit or wildcard ``javax.ws.rs``/``jakarta.ws.rs``
    import. This deliberately excludes clients such as Retrofit. A ``@Path``
    without an HTTP verb is a sub-resource locator and is skipped.
    ``@Produces``/``@Consumes`` are not captured, and cross-file
    ``@ApplicationPath`` resolution is out of scope. A missing class ``@Path``
    remains an empty prefix so method-only paths requested by Discussion #54
    are represented; application/inheritance reachability is not inferred.
    """
    annotations = _annotations(method, source)
    verbs = list(
        dict.fromkeys(
            _simple_annotation_name(name)
            for name, _ in annotations
            if _simple_annotation_name(name) in _HTTP_VERB_ANNOTATIONS
            and _is_jax_rs_annotation(name, imports)
        )
    )
    if not verbs:
        return
    method_path = next(
        (
            _annotation_string_arg(annotation, source)
            for name, annotation in annotations
            if _simple_annotation_name(name) == "Path" and _is_jax_rs_annotation(name, imports)
        ),
        "",
    )
    if class_path is None or method_path is None:
        return  # a non-literal @Path cannot be grounded precisely
    full_path = _join_path(class_path, method_path)
    line = method.start_point[0] + 1
    provenance = Provenance(rel, line)
    for verb in verbs:
        endpoint_id = f"java:endpoint:{verb} {full_path}"
        batch.add_node(
            Node(
                endpoint_id,
                NodeKind.ENDPOINT,
                f"{verb} {full_path}",
                "java",
                provenance,
            )
        )
        batch.add_edge(Edge(endpoint_id, method_id, EdgeKind.EXPOSES, provenance))


def _spring_annotations(node: TSNode, imports: _ImportContext, source: bytes) -> list[RouteAnnotation]:
    """Read this declaration's Spring annotations into ``jvm_routes``'s neutral form.

    The grammar-specific half of D16: Java spells an annotation ``annotation`` /
    ``marker_annotation`` with a ``name`` field and ``element_value_pair`` arguments,
    where Kotlin nests a ``user_type`` or a ``constructor_invocation``. Everything
    the two readings then *mean* is in ``jvm_routes``.
    """
    out: list[RouteAnnotation] = []
    for name, annotation in _annotations(node, source):
        if not resolves_into_spring(
            name, by_simple=imports.by_simple, wildcard_prefixes=imports.wildcard_prefixes
        ):
            continue
        simple = _simple_annotation_name(name)
        line = annotation.start_point[0] + 1
        if simple not in MAPPING_ANNOTATIONS:
            out.append(RouteAnnotation(name=simple, line=line))
            continue
        out.append(
            RouteAnnotation(
                name=simple,
                path=_spring_path(annotation, source),
                methods=_spring_methods(annotation, source),
                line=line,
            )
        )
    return out


def _spring_path(annotation: TSNode, source: bytes) -> str | None:
    """``""`` when the annotation names no path, ``None`` when it is not a literal."""
    arguments = annotation.child_by_field_name("arguments")
    if arguments is None:
        return ""  # a marker `@GetMapping`, which maps the class prefix itself
    positional: TSNode | None = None
    for child in arguments.named_children:
        if child.type == "element_value_pair":
            if _text(child.child_by_field_name("key"), source) in PATH_ARGUMENTS:
                return _spring_literal(child.child_by_field_name("value"), source)
        elif positional is None:
            positional = child
    return "" if positional is None else _spring_literal(positional, source)


def _spring_literal(node: TSNode | None, source: bytes) -> str | None:
    """A string literal's text, taking the first element of a ``{...}`` array."""
    if node is None:
        return ""
    if node.type == "element_value_array_initializer":
        items = node.named_children
        if not items:
            return ""
        node = items[0]
    return literal_path(_string_literal(node, source)) if node.type == "string_literal" else None


def _spring_methods(annotation: TSNode, source: bytes) -> tuple[str, ...]:
    """``method = {RequestMethod.GET}`` → ``("GET",)``; ``()`` when absent."""
    arguments = annotation.child_by_field_name("arguments")
    if arguments is None:
        return ()
    for child in arguments.named_children:
        if child.type != "element_value_pair":
            continue
        if _text(child.child_by_field_name("key"), source) != METHOD_ARGUMENT:
            continue
        value = child.child_by_field_name("value")
        if value is None:
            return ()
        items = value.named_children if value.type == "element_value_array_initializer" else [value]
        return tuple(verb for item in items if (verb := _text(item, source).rsplit(".", 1)[-1]))
    return ()


def _path_annotation(node: TSNode, source: bytes, imports: _ImportContext) -> str | None:
    """Return ``@Path`` text, empty when absent, or ``None`` when non-literal."""
    return next(
        (
            _annotation_string_arg(annotation, source)
            for name, annotation in _annotations(node, source)
            if _simple_annotation_name(name) == "Path" and _is_jax_rs_annotation(name, imports)
        ),
        "",
    )


def _annotations(node: TSNode, source: bytes) -> list[tuple[str, TSNode]]:
    """Return declaration annotations as ``(possibly-qualified name, node)``."""
    modifiers = next((child for child in node.named_children if child.type == "modifiers"), None)
    if modifiers is None:
        return []
    annotations: list[tuple[str, TSNode]] = []
    for child in modifiers.named_children:
        if child.type not in ("annotation", "marker_annotation"):
            continue
        name = _field_text(child, "name", source)
        if name:
            annotations.append((name, child))
    return annotations


def _simple_annotation_name(name: str) -> str:
    """Return the final segment of a possibly-qualified annotation name."""
    return name.rsplit(".", 1)[-1]


def _is_jax_rs_annotation(name: str, imports: _ImportContext) -> bool:
    """Whether an annotation name resolves precisely to javax/jakarta JAX-RS."""
    simple = _simple_annotation_name(name)
    if "." in name:
        return name.rsplit(".", 1)[0] in _JAX_RS_PACKAGES
    imported = imports.by_simple.get(simple)
    if imported is not None:
        return imported in {f"{package}.{simple}" for package in _JAX_RS_PACKAGES}
    return bool(imports.wildcard_prefixes & _JAX_RS_PACKAGES)


def _annotation_string_arg(annotation: TSNode, source: bytes) -> str | None:
    """Return a direct string or ``value = "..."`` argument, else ``None``."""
    arguments = annotation.child_by_field_name("arguments")
    if arguments is None:
        return None
    for child in arguments.named_children:
        candidate = child.child_by_field_name("value") if child.type == "element_value_pair" else child
        if candidate is not None and candidate.type == "string_literal":
            return _string_literal(candidate, source)
    return None


def _string_literal(node: TSNode, source: bytes) -> str | None:
    """Decode Java string escapes useful in URI paths while preserving templates."""
    value = _text(node, source)
    if len(value) < 2 or not value.startswith('"') or not value.endswith('"'):
        return None
    inner = value[1:-1]
    escapes = {
        "b": "\b",
        "t": "\t",
        "n": "\n",
        "f": "\f",
        "r": "\r",
        '"': '"',
        "'": "'",
        "\\": "\\",
    }

    def replace_escape(match: re.Match[str]) -> str:
        unicode_digits = match.group(1)
        if unicode_digits is not None:
            return chr(int(unicode_digits, 16))
        escaped = match.group(2)
        return escapes.get(escaped or "", match.group(0))

    return re.sub(
        r"\\(?:u([0-9a-fA-F]{4})|([btnfr\"'\\]))",
        replace_escape,
        inner,
    )


def _join_path(prefix: str, suffix: str) -> str:
    """Join class and method JAX-RS paths into one normalized absolute path."""
    parts = [part.strip("/") for part in (prefix, suffix) if part and part.strip("/")]
    return "/" + "/".join(parts) if parts else "/"


def _supertypes(node: TSNode, source: bytes) -> list[str]:
    """The extends + implements type names of a class/interface declaration."""
    out: list[str] = []
    superclass = node.child_by_field_name("superclass")
    if superclass is not None:
        out.extend(_type_names(superclass, source))
    interfaces = node.child_by_field_name("interfaces")
    if interfaces is not None:
        out.extend(_type_names(interfaces, source))
    # An interface's own `extends A, B` is an `extends_interfaces` child with no field name; it was
    # missed, so `interface Table extends DatabaseObject` had no IMPLEMENTS and a member Table
    # inherits (`table.getName()`) could not be found (B21).
    for child in node.named_children:
        if child.type == "extends_interfaces":
            out.extend(_type_names(child, source))
    return out


def _type_names(node: TSNode, source: bytes) -> list[str]:
    """Type identifiers under a superclass/super_interfaces node."""
    names: list[str] = []
    for child in node.named_children:
        if child.type in ("type_identifier", "scoped_type_identifier", "generic_type"):
            names.append(_text(child, source))
        elif child.type in ("type_list", "interface_type_list"):
            names.extend(_text(t, source) for t in child.named_children)
    return [n for n in names if n]


def _field_names(field_decl: TSNode, source: bytes) -> list[str]:
    """Declared names in a (possibly multi) field declaration."""
    names: list[str] = []
    for child in field_decl.named_children:
        if child.type == "variable_declarator":
            n = child.child_by_field_name("name")
            if n is not None:
                names.append(_text(n, source))
    return names


def _field_text(node: TSNode, field: str, source: bytes) -> str:
    child = node.child_by_field_name(field)
    return _text(child, source) if child is not None else ""


def _text(node: TSNode | None, source: bytes) -> str:
    if node is None:
        return ""
    return source[node.start_byte : node.end_byte].decode("utf-8", "replace").strip()


def _java_parser() -> Any:
    try:
        import tree_sitter_java
        from tree_sitter import Language, Parser
    except ImportError as exc:  # pragma: no cover - exercised only without the extra
        raise RuntimeError(
            "Java extraction needs tree-sitter; install the extra: "
            "uv pip install 'tree-sitter>=0.21' 'tree-sitter-java>=0.21'"
        ) from exc
    language = Language(tree_sitter_java.language())
    try:
        return Parser(language)
    except TypeError:  # older tree-sitter API
        parser = Parser()
        parser.language = language
        return parser


__all__ = ["JavaExtractor"]
