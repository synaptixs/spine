"""C# / .NET front-end for the PKG extractor (Track 1: a fourth language).

Maps C# source onto the same universal ``facts`` vocabulary the Python/Java/TS
extractors use — so the knowledge graph stays language-neutral. Parsing is via
tree-sitter (accurate ASTs), an OPTIONAL dependency: install the ``csharp`` extra
(``uv pip install 'synaptixs-spine[csharp]'``). The import is lazy so the base
install stays stdlib-only and importing this module never fails.

Phase 1.1 (comprehension) emits the high-confidence declaration subset,
precision-first like the Java front-end: ``Module`` (the file, named by its
namespace), ``Type`` (class / interface / struct / enum / record / delegate),
``Function`` (method / constructor / operator), ``Field`` (field / property /
event / enum member / positional record param); ``IMPORTS`` (``using``),
``CONTAINS``, and ``IMPLEMENTS`` (base list) edges.

Phase 1.3 (framework edges) adds, on top of the declarations:
- **ASP.NET Core** routes → ``Endpoint`` + ``EXPOSES`` (route→handler): attribute
  controllers (``[HttpGet]``/``[HttpPost]``/… + a class/method ``[Route]`` prefix)
  and Minimal-API ``app.MapGet("/path", …)`` registrations.
- **EF Core** entities → ``Entity`` + ``REFERENCES`` (entity→entity): a class with
  ``[Table]`` or referenced by a ``DbContext``'s ``DbSet<T>``; navigation
  properties whose (element) type is another entity become a data edge.
- **CALLS** — conservative, intra-type only: an unqualified or ``this.`` call that
  resolves to a sibling method in the same type. Cross-type / overloaded
  resolution needs type inference and would poison grounding, so it is not
  attempted (precision-first).

Node ids are namespace-qualified (``csharp:Namespace.Type``) so partial classes
split across files collapse onto one node. Entity nodes use a parallel
``csharp:entity:Namespace.Type`` id so the data graph is distinct from the type
graph.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from orchestrator.pkg.extractor import rel_module_name
from orchestrator.pkg.facts import Edge, EdgeKind, FactBatch, Node, NodeKind, Provenance

if TYPE_CHECKING:
    from tree_sitter import Node as TSNode

_NAMESPACE_RE = re.compile(r"^\s*namespace\s+([\w.]+)", re.M)
_TYPE_DECLS = frozenset(
    {
        "class_declaration",
        "interface_declaration",
        "struct_declaration",
        "enum_declaration",
        "record_declaration",
        "record_struct_declaration",
        "delegate_declaration",  # a named type, but a leaf (no bases/members)
    }
)

# ASP.NET Core action attributes → HTTP verb (attribute-routed controllers).
_HTTP_VERB_ATTRS = {
    "HttpGet": "GET",
    "HttpPost": "POST",
    "HttpPut": "PUT",
    "HttpDelete": "DELETE",
    "HttpPatch": "PATCH",
    "HttpHead": "HEAD",
    "HttpOptions": "OPTIONS",
}
# Minimal-API endpoint registrations (``app.MapGet(...)``) → HTTP verb.
_MINIMAL_API_MAPS = {
    "MapGet": "GET",
    "MapPost": "POST",
    "MapPut": "PUT",
    "MapDelete": "DELETE",
    "MapPatch": "PATCH",
    "MapMethods": "ANY",
}


@dataclass
class _TypeRec:
    """A collected type declaration — the working set the framework pass reasons over."""

    type_id: str
    name: str
    namespace: str
    node: TSNode
    methods: list[tuple[str, str, TSNode]] = field(default_factory=list)  # (name, id, node)


class CSharpExtractor:
    """C# front-end (tree-sitter). Install the ``csharp`` extra to use it."""

    language: str = "csharp"
    suffixes: tuple[str, ...] = (".cs",)

    def module_name(self, path: Path, root: Path) -> str:
        # C#'s closest thing to a package is the (first) namespace, which lives in
        # the file; fall back to the repo-relative path when there's none.
        try:
            # utf-8-sig strips a leading BOM, which is common in .NET files and would
            # otherwise defeat the ^namespace match.
            m = _NAMESPACE_RE.search(path.read_text(encoding="utf-8-sig"))
        except OSError:
            m = None
        return m.group(1) if m else rel_module_name(path, root)

    def extract(self, *, path: Path, module: str, rel: str) -> FactBatch:
        parser = _csharp_parser()
        source = path.read_bytes()
        tree = parser.parse(source)
        batch = FactBatch()
        module_id = f"csharp:{module}" if module else "csharp:<root>"
        batch.add_node(Node(module_id, NodeKind.MODULE, module or rel, "csharp", Provenance(rel, 1)))

        types: list[_TypeRec] = []
        self._usings(tree.root_node, module_id, source, rel, batch)
        self._walk(tree.root_node.named_children, module_id, "", source, rel, batch, types)
        # Phase 1.3 — framework + call edges, computed once the full type set is known.
        _framework_edges(types, module_id, tree.root_node, source, rel, batch)
        return batch

    def _usings(self, root: TSNode, module_id: str, source: bytes, rel: str, batch: FactBatch) -> None:
        """Emit IMPORTS edges to each ``using`` target namespace."""
        for node in root.named_children:
            if node.type != "using_directive" or not node.named_children:
                continue
            target = _text(node.named_children[-1], source)
            if not target:
                continue
            tid = f"csharp:{target}"
            batch.add_node(Node(tid, NodeKind.MODULE, target, "csharp", external=True))
            batch.add_edge(Edge(module_id, tid, EdgeKind.IMPORTS, Provenance(rel, node.start_point[0] + 1)))

    def _walk(
        self,
        nodes: list[TSNode],
        module_id: str,
        namespace: str,
        source: bytes,
        rel: str,
        batch: FactBatch,
        types: list[_TypeRec],
    ) -> None:
        """Walk siblings, descending into namespaces and emitting top-level types.

        Handles both block ``namespace N { ... }`` and file-scoped ``namespace N;``
        (whose types are subsequent siblings — so the namespace sticks for the rest).
        """
        current_ns = namespace
        for node in nodes:
            if node.type == "file_scoped_namespace_declaration":
                current_ns = _join_ns(namespace, _field_text(node, "name", source))
            elif node.type == "namespace_declaration":
                ns = _join_ns(namespace, _field_text(node, "name", source))
                body = node.child_by_field_name("body")
                if body is not None:
                    self._walk(body.named_children, module_id, ns, source, rel, batch, types)
            elif node.type in _TYPE_DECLS:
                self._emit_type(node, module_id, None, current_ns, source, rel, batch, types)

    def _emit_type(
        self,
        node: TSNode,
        module_id: str,
        parent_type_id: str | None,
        namespace: str,
        source: bytes,
        rel: str,
        batch: FactBatch,
        types: list[_TypeRec],
    ) -> None:
        name = _field_text(node, "name", source)
        if not name:
            return
        # Top-level types key on the (tree) namespace so partial classes split across
        # files merge; nested types key on the enclosing type id.
        if parent_type_id is None:
            type_id = f"csharp:{_join_ns(namespace, name)}"
            contains_parent = module_id
        else:
            type_id = f"{parent_type_id}.{name}"
            contains_parent = parent_type_id
        line = node.start_point[0] + 1
        batch.add_node(
            Node(type_id, NodeKind.TYPE, name, "csharp", Provenance(rel, line, node.end_point[0] + 1))
        )
        batch.add_edge(Edge(contains_parent, type_id, EdgeKind.CONTAINS, Provenance(rel, line)))
        rec = _TypeRec(type_id=type_id, name=name, namespace=namespace, node=node)
        types.append(rec)

        if node.type == "delegate_declaration":
            return  # a delegate is a leaf named type — no bases, members, or param-fields

        for base in _base_types(node, source):
            target = _resolve_type(base, namespace)
            if target is not None:
                batch.add_edge(Edge(type_id, target, EdgeKind.IMPLEMENTS, Provenance(rel, line)))

        # Positional record parameters (`record Money(decimal Amount, ...)`) are
        # effectively properties → emit as FIELD.
        for child in node.named_children:
            if child.type == "parameter_list":
                for param in child.named_children:
                    if param.type == "parameter":
                        pname = _field_text(param, "name", source)
                        if pname:
                            self._add_member(
                                type_id, pname, NodeKind.FIELD, param.start_point[0] + 1, rel, batch
                            )

        body = node.child_by_field_name("body")
        if body is None:
            return
        for member in body.named_children:
            mline = member.start_point[0] + 1
            if member.type in ("method_declaration", "constructor_declaration"):
                mname = _field_text(member, "name", source)
                if mname:
                    mid = self._add_member(type_id, mname, NodeKind.FUNCTION, mline, rel, batch)
                    rec.methods.append((mname, mid, member))
            elif member.type == "indexer_declaration":  # `public T this[int i] { ... }`
                self._add_member(type_id, "this[]", NodeKind.FIELD, mline, rel, batch)
            elif member.type == "property_declaration":
                pname = _field_text(member, "name", source)
                if pname:
                    self._add_member(type_id, pname, NodeKind.FIELD, mline, rel, batch)
            elif member.type in ("field_declaration", "event_field_declaration"):
                for fname in _field_names(member, source):
                    self._add_member(type_id, fname, NodeKind.FIELD, mline, rel, batch)
            elif member.type == "event_declaration":  # property-style event (add/remove)
                ename = _field_text(member, "name", source)
                if ename:
                    self._add_member(type_id, ename, NodeKind.FIELD, mline, rel, batch)
            elif member.type == "operator_declaration":
                op = _field_text(member, "operator", source)
                if op:
                    self._add_member(type_id, f"operator{op}", NodeKind.FUNCTION, mline, rel, batch)
            elif member.type == "conversion_operator_declaration":
                ty = _field_text(member, "type", source)
                if ty:
                    self._add_member(type_id, f"operator {ty}", NodeKind.FUNCTION, mline, rel, batch)
            elif member.type == "enum_member_declaration":
                ename = _field_text(member, "name", source) or _text(member, source)
                if ename:
                    self._add_member(type_id, ename, NodeKind.FIELD, mline, rel, batch)
            elif member.type in _TYPE_DECLS:
                self._emit_type(member, module_id, type_id, namespace, source, rel, batch, types)

    @staticmethod
    def _add_member(type_id: str, name: str, kind: NodeKind, line: int, rel: str, batch: FactBatch) -> str:
        mid = f"{type_id}.{name}"
        batch.add_node(Node(mid, kind, name, "csharp", Provenance(rel, line)))
        batch.add_edge(Edge(type_id, mid, EdgeKind.CONTAINS, Provenance(rel, line)))
        return mid


# --- Phase 1.3: framework + call edges -------------------------------------


def _framework_edges(
    types: list[_TypeRec],
    module_id: str,
    root: TSNode,
    source: bytes,
    rel: str,
    batch: FactBatch,
) -> None:
    """Emit Endpoint/EXPOSES, Entity/REFERENCES and intra-type CALLS edges.

    Run as a post-pass so entity references can resolve against the full type set
    and call targets against each type's own methods."""
    by_qualified: dict[str, _TypeRec] = {}
    by_simple: dict[str, list[_TypeRec]] = {}
    for rec in types:
        by_qualified[_join_ns(rec.namespace, rec.name)] = rec
        by_simple.setdefault(rec.name, []).append(rec)

    def resolve(name: str, namespace: str) -> _TypeRec | None:
        """Best-effort type-name → record. Same-namespace first, then a unique
        simple-name match (precision-first: ambiguous names resolve to nothing)."""
        simple = _last_segment(name)
        if "." in name and name in by_qualified:
            return by_qualified[name]
        if namespace and _join_ns(namespace, simple) in by_qualified:
            return by_qualified[_join_ns(namespace, simple)]
        cands = by_simple.get(simple, [])
        return cands[0] if len(cands) == 1 else None

    _endpoint_edges(types, source, rel, batch)
    _minimal_api_edges(root, module_id, source, rel, batch)
    _entity_edges(types, resolve, source, rel, batch)
    _call_edges(types, source, rel, batch)


def _endpoint_edges(types: list[_TypeRec], source: bytes, rel: str, batch: FactBatch) -> None:
    """Attribute-routed controllers → Endpoint + EXPOSES (route→handler).

    The route is sourced, in order, from the HTTP-verb attribute's own argument
    (``[HttpGet("x")]``), then a sibling method-level ``[Route("x")]`` (the very
    common ``[Route(...)] + [HttpGet]`` split), then the class-level ``[Route]``
    prefix. A method-level ``[Route]`` with no verb attribute (responds to all
    verbs) is emitted as ``ANY`` — but only on a controller, so a stray ``[Route]``
    on a plain class doesn't masquerade as an endpoint."""
    for rec in types:
        class_route = ""
        for aname, anode in _attributes(rec.node, source):
            if aname == "Route":
                class_route = _attr_string_arg(anode, source)
                break
        controller = _is_controller(rec, source)
        for _mname, mid, mnode in rec.methods:
            verb, http_route, method_route = "", "", ""
            for aname, anode in _attributes(mnode, source):
                if aname in _HTTP_VERB_ATTRS:
                    verb = _HTTP_VERB_ATTRS[aname]
                    http_route = _attr_string_arg(anode, source)
                elif aname == "Route":
                    method_route = _attr_string_arg(anode, source)
            if not verb:
                if not (controller and method_route):
                    continue
                verb = "ANY"  # [Route]-only action handles every HTTP method
            full = _join_route(class_route, http_route or method_route)
            line = mnode.start_point[0] + 1
            eid = f"csharp:endpoint:{verb} {full}"
            batch.add_node(Node(eid, NodeKind.ENDPOINT, f"{verb} {full}", "csharp", Provenance(rel, line)))
            batch.add_edge(Edge(eid, mid, EdgeKind.EXPOSES, Provenance(rel, line)))


def _is_controller(rec: _TypeRec, source: bytes) -> bool:
    """A type is a controller if it's named ``*Controller``, carries
    ``[ApiController]``, or derives from a ``*Controller`` / ``ControllerBase`` base
    (covers custom bases like ``BaseController``)."""
    if rec.name.endswith("Controller"):
        return True
    if any(aname == "ApiController" for aname, _ in _attributes(rec.node, source)):
        return True
    return any(
        _last_segment(b).endswith("Controller") or _last_segment(b) == "ControllerBase"
        for b in _base_types(rec.node, source)
    )


def _minimal_api_edges(root: TSNode, module_id: str, source: bytes, rel: str, batch: FactBatch) -> None:
    """Minimal-API ``app.MapGet("/path", handler)`` → Endpoint + EXPOSES (→module).

    The handler is usually an inline lambda (no named symbol), so EXPOSES points at
    the module the route is registered in."""
    stack = [root]
    while stack:
        node = stack.pop()
        if node.type == "invocation_expression":
            fn = node.child_by_field_name("function")
            verb = _MINIMAL_API_MAPS.get(_member_name(fn, source)) if fn is not None else None
            if verb:
                args = node.child_by_field_name("arguments")
                route = _find_string(args, source) if args is not None else ""
                if route:
                    line = node.start_point[0] + 1
                    eid = f"csharp:endpoint:{verb} {route}"
                    batch.add_node(
                        Node(eid, NodeKind.ENDPOINT, f"{verb} {route}", "csharp", Provenance(rel, line))
                    )
                    batch.add_edge(Edge(eid, module_id, EdgeKind.EXPOSES, Provenance(rel, line)))
        stack.extend(node.named_children)


def _entity_edges(
    types: list[_TypeRec],
    resolve: Any,
    source: bytes,
    rel: str,
    batch: FactBatch,
) -> None:
    """EF Core entities → Entity nodes + entity→entity REFERENCES (nav properties)."""
    entities: dict[str, _TypeRec] = {}
    # 1. [Table]-annotated classes are entities.
    for rec in types:
        if any(aname == "Table" for aname, _ in _attributes(rec.node, source)):
            entities[rec.type_id] = rec
    # 2. A DbContext's DbSet<T> registers T as an entity.
    for rec in types:
        if not any(_last_segment(b).endswith("DbContext") for b in _base_types(rec.node, source)):
            continue
        body = rec.node.child_by_field_name("body")
        if body is None:
            continue
        for member in body.named_children:
            if member.type != "property_declaration":
                continue
            ty = member.child_by_field_name("type")
            if ty is None or ty.type != "generic_name" or _generic_head(ty, source) != "DbSet":
                continue
            for arg in _type_arg_names(ty, source):
                tgt = resolve(arg, rec.namespace)
                if tgt is not None:
                    entities[tgt.type_id] = tgt

    for rec in entities.values():
        eid = _entity_id(rec)
        line = rec.node.start_point[0] + 1
        batch.add_node(Node(eid, NodeKind.ENTITY, rec.name, "csharp", Provenance(rel, line)))

    # Navigation properties whose (element) type is another entity → REFERENCES.
    for rec in entities.values():
        body = rec.node.child_by_field_name("body")
        if body is None:
            continue
        src_eid = _entity_id(rec)
        for member in body.named_children:
            if member.type != "property_declaration":
                continue
            ty = member.child_by_field_name("type")
            if ty is None:
                continue
            for nm in _ref_type_names(ty, source):
                tgt = resolve(nm, rec.namespace)
                if tgt is not None and tgt.type_id in entities and tgt.type_id != rec.type_id:
                    batch.add_edge(
                        Edge(
                            src_eid,
                            _entity_id(tgt),
                            EdgeKind.REFERENCES,
                            Provenance(rel, member.start_point[0] + 1),
                        )
                    )


def _call_edges(types: list[_TypeRec], source: bytes, rel: str, batch: FactBatch) -> None:
    """Intra-type CALLS: an unqualified / ``this.`` call to a sibling method."""
    for rec in types:
        method_ids = {name: mid for name, mid, _ in rec.methods}
        for _name, mid, mnode in rec.methods:
            for callee, line in _calls_in(mnode, source):
                target = method_ids.get(callee)
                if target is not None:
                    batch.add_edge(Edge(mid, target, EdgeKind.CALLS, Provenance(rel, line)))


# --- helpers ---------------------------------------------------------------


def _entity_id(rec: _TypeRec) -> str:
    return f"csharp:entity:{_join_ns(rec.namespace, rec.name)}"


def _join_ns(prefix: str, name: str) -> str:
    if prefix and name:
        return f"{prefix}.{name}"
    return name or prefix


def _join_route(prefix: str, suffix: str) -> str:
    """Combine a class ``[Route]`` prefix with a method route into a normalized path."""
    parts = [p.strip("/") for p in (prefix, suffix) if p and p.strip("/")]
    return "/" + "/".join(parts) if parts else "/"


def _last_segment(name: str) -> str:
    """Last dotted segment, generics dropped (``A.B.IList<T>`` → ``IList``)."""
    return name.rsplit(".", 1)[-1].split("<", 1)[0].strip()


def _resolve_type(name: str, namespace: str) -> str | None:
    """Best-effort base-type id (precision-first). Simple names assume same namespace."""
    name = name.split("<", 1)[0].strip()  # drop generics: IList<T> → IList
    if not name:
        return None
    if "." in name:
        return f"csharp:{name}"
    return f"csharp:{_join_ns(namespace, name)}" if namespace else f"csharp:{name}"


def _base_types(node: TSNode, source: bytes) -> list[str]:
    """Type names from a declaration's ``base_list`` (base class + interfaces)."""
    for child in node.named_children:
        if child.type == "base_list":
            return [
                _text(c, source)
                for c in child.named_children
                if c.type in ("identifier", "qualified_name", "generic_name")
            ]
    return []


def _field_names(field_decl: TSNode, source: bytes) -> list[str]:
    """Declared names in a (possibly multi) field declaration."""
    names: list[str] = []
    for child in field_decl.named_children:
        if child.type != "variable_declaration":
            continue
        for declarator in child.named_children:
            if declarator.type == "variable_declarator" and declarator.named_children:
                names.append(_text(declarator.named_children[0], source))
    return [n for n in names if n]


def _attributes(node: TSNode, source: bytes) -> list[tuple[str, TSNode]]:
    """``(name, attribute_node)`` for each attribute in the node's attribute lists."""
    out: list[tuple[str, TSNode]] = []
    for child in node.named_children:
        if child.type != "attribute_list":
            continue
        for attr in child.named_children:
            if attr.type == "attribute":
                name = _last_segment(_field_text(attr, "name", source))
                if name:
                    out.append((name, attr))
    return out


def _attr_string_arg(attr: TSNode, source: bytes) -> str:
    """First string-literal argument of an attribute (``[Route("api/x")]`` → ``api/x``)."""
    for child in attr.named_children:
        if child.type == "attribute_argument_list":
            return _find_string(child, source)
    return ""


def _find_string(node: TSNode, source: bytes) -> str:
    """Depth-first first string-literal value under ``node`` (quotes stripped)."""
    if node.type in ("string_literal", "verbatim_string_literal", "raw_string_literal"):
        for child in node.named_children:
            if child.type == "string_literal_content":
                return _text(child, source)
        return _text(node, source).strip('@"')
    for child in node.named_children:
        found = _find_string(child, source)
        if found:
            return found
    return ""


def _member_name(fn: TSNode | None, source: bytes) -> str:
    """The invoked member's simple name (``app.MapGet`` → ``MapGet``; ``Foo`` → ``Foo``)."""
    if fn is None:
        return ""
    if fn.type == "identifier":
        return _text(fn, source)
    if fn.type == "member_access_expression":
        name = fn.child_by_field_name("name")
        return _text(name, source) if name is not None else ""
    if fn.type == "generic_name":
        return _generic_head(fn, source)
    return ""


def _calls_in(mnode: TSNode, source: bytes) -> list[tuple[str, int]]:
    """``(callee_name, line)`` for unqualified / ``this.`` calls inside a method."""
    out: list[tuple[str, int]] = []
    stack = list(mnode.named_children)
    while stack:
        node = stack.pop()
        if node.type == "invocation_expression":
            name = _unqualified_call_name(node.child_by_field_name("function"), source)
            if name:
                out.append((name, node.start_point[0] + 1))
        stack.extend(node.named_children)
    return out


def _unqualified_call_name(fn: TSNode | None, source: bytes) -> str:
    """The sibling-method name for ``Foo(...)`` or ``this.Foo(...)``; '' otherwise."""
    if fn is None:
        return ""
    if fn.type == "identifier":
        return _text(fn, source)
    if fn.type == "member_access_expression":
        obj = fn.child_by_field_name("expression")
        name = fn.child_by_field_name("name")
        if obj is not None and obj.type in ("this", "this_expression") and name is not None:
            return _text(name, source)
    return ""


def _generic_head(node: TSNode, source: bytes) -> str:
    """The container identifier of a ``generic_name`` (``DbSet<T>`` → ``DbSet``)."""
    for child in node.named_children:
        if child.type == "identifier":
            return _text(child, source)
    return ""


def _type_arg_names(node: TSNode, source: bytes) -> list[str]:
    """Type-argument names of a ``generic_name`` (``DbSet<Order>`` → ``["Order"]``)."""
    out: list[str] = []
    for child in node.named_children:
        if child.type == "type_argument_list":
            out.extend(_text(arg, source) for arg in child.named_children)
    return out


def _ref_type_names(ty: TSNode, source: bytes) -> list[str]:
    """Candidate referenced type names in a property type (collections unwrapped,
    primitives skipped) — ``Customer`` → ``["Customer"]``, ``List<Item>`` →
    ``["Item"]``, ``int`` → ``[]``."""
    if ty.type in ("identifier", "qualified_name"):
        return [_text(ty, source)]
    if ty.type == "generic_name":
        return _type_arg_names(ty, source)
    if ty.type in ("nullable_type", "array_type"):
        out: list[str] = []
        for child in ty.named_children:
            out.extend(_ref_type_names(child, source))
        return out
    return []


def _field_text(node: TSNode, fld: str, source: bytes) -> str:
    child = node.child_by_field_name(fld)
    return _text(child, source) if child is not None else ""


def _text(node: TSNode | None, source: bytes) -> str:
    if node is None:
        return ""
    return source[node.start_byte : node.end_byte].decode("utf-8", "replace").strip()


def _csharp_parser() -> Any:
    try:
        import tree_sitter_c_sharp
        from tree_sitter import Language, Parser
    except ImportError as exc:  # pragma: no cover - exercised only without the extra
        raise RuntimeError(
            "C# extraction needs tree-sitter; install the extra: "
            "uv pip install 'tree-sitter>=0.21' 'tree-sitter-c-sharp>=0.21'"
        ) from exc
    language = Language(tree_sitter_c_sharp.language())
    try:
        return Parser(language)
    except TypeError:  # older tree-sitter API
        parser = Parser()
        parser.language = language
        return parser


__all__ = ["CSharpExtractor"]
