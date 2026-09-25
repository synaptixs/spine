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

**Blazor components** (``.razor``) are read too: :mod:`orchestrator.pkg.razor` rewrites
one into line-aligned C# — ``@using``/``@namespace``/``@inject`` in place, markup blanked,
``@code`` opened as a ``partial class`` named after the file — and it takes the path above
from there, so every symbol carries its true ``.razor`` line. A component's module is its
``@namespace`` when it declares one, else the namespace its ``.razor.cs`` code-behind declares,
else its path, exactly as an unnamespaced ``.cs``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from orchestrator.pkg.csharp_di import REGISTRATIONS, Binding, emit_provides, factory_creation, type_arguments
from orchestrator.pkg.extractor import rel_module_name
from orchestrator.pkg.facts import Edge, EdgeKind, FactBatch, Node, NodeKind, Provenance
from orchestrator.pkg.razor import (
    code_behind_namespace,
    component_class_name,
    component_namespace,
    razor_to_csharp,
)
from orchestrator.pkg.typed_receivers import (
    STOP,
    UNREADABLE,
    DeferredCall,
    ReceiverState,
    Scope,
    TypeRef,
    resolve_bases,
    resolve_calls,
)

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
    parent: _TypeRec | None = None  # the enclosing type of a nested one
    decl: _NsDecl | None = None  # the innermost namespace declaration it sits in
    type_params: frozenset[str] = frozenset()  # its own `<T, U>` — names that are never in-repo types
    bases: list[tuple[str, str]] = field(default_factory=list)  # (as written, provisional id)


class CSharpExtractor:
    """C# front-end (tree-sitter). Install the ``csharp`` extra to use it."""

    language: str = "csharp"
    # `.razor` too: a Blazor component is C# plus markup, and `pkg.razor` rewrites it into
    # line-aligned C# before the parser sees it — the same shape as `php_extractor` branching on
    # `.blade.php` by filename, with the opposite verdict (a component holds real logic).
    suffixes: tuple[str, ...] = (".cs", ".razor")

    def __init__(self) -> None:
        # `recv.m()` calls wait for `finalize`: whether `recv`'s type is declared here, which
        # type it is and whether it has `m` are whole-repository questions (`typed_receivers`).
        self._receivers = ReceiverState("csharp")
        # `services.AddScoped<IFoo, Foo>()` — resolved against every declaration in `finalize`.
        self._bindings: list[Binding] = []
        self._projects: dict[str, bool] = {}  # directory -> holds a .csproj

    def module_name(self, path: Path, root: Path) -> str:
        # C#'s closest thing to a package is the (first) namespace, which lives in
        # the file; fall back to the repo-relative path when there's none. A component
        # declares it as `@namespace`, and most do not — then, like an unnamespaced .cs
        # file, its module is its path.
        try:
            # utf-8-sig strips a leading BOM, which is common in .NET files and would
            # otherwise defeat the ^namespace match. errors="replace" for the same reason
            # `extract` decodes that way: one stray byte must not drop the file from the graph.
            text = path.read_text(encoding="utf-8-sig", errors="replace")
        except OSError:
            return rel_module_name(path, root)
        if path.suffix.lower() == ".razor":
            return component_namespace(text) or code_behind_namespace(path) or rel_module_name(path, root)
        m = _NAMESPACE_RE.search(text)
        return m.group(1) if m else rel_module_name(path, root)

    def finalize(self, batch: FactBatch) -> FactBatch:
        """Repoint `IMPLEMENTS` edges whose base type was provisionally placed in the wrong
        namespace.

        `_resolve_type` has to guess at per-file time: a bare `class Foo : IEqualityComparer`
        gives no clue whether the base is first-party or framework. It assumes the enclosing
        namespace, which is right for a sibling type and wrong for everything from `System.*`.

        By the time this runs every declaration in the repo is known, so the guess is
        checkable. A target that matches no declared type is repointed at `csharp:<BareName>`
        and given an **external** node — asserting the name we read rather than a namespace we
        invented, and landing the edge so it stops dangling.

        Measured on a real ASP.NET codebase: 77 `IMPLEMENTS` edges pointed at types that did
        not exist — `IEqualityComparer`, `Exception`, `ControllerBase`, `ClientBase`.
        """
        # First the bases a `using` or the namespace chain places in this repository (B21) —
        # without it a class and its interface in sibling namespaces were never linked.
        batch = resolve_bases(batch, self._receivers)
        declared = {n.id for n in batch.nodes if n.kind is NodeKind.TYPE and not n.external}
        repointed = FactBatch()
        for node in batch.nodes:
            repointed.add_node(node)
        for edge in batch.edges:
            if (
                edge.kind is not EdgeKind.IMPLEMENTS
                or not edge.dst.startswith("csharp:")
                or edge.dst in declared
            ):
                repointed.add_edge(edge)
                continue
            bare = edge.dst.rsplit(".", 1)[-1]
            target = f"csharp:{bare}"
            repointed.add_node(Node(target, NodeKind.TYPE, bare, "csharp", external=True))
            repointed.add_edge(Edge(edge.src, target, EdgeKind.IMPLEMENTS, edge.provenance))
        # Before `resolve_calls`, which clears the shared state (`global using`s included).
        emit_provides(repointed, self._bindings, self._receivers)
        resolve_calls(repointed, self._receivers)
        return repointed

    def extract(self, *, path: Path, module: str, rel: str) -> FactBatch:
        source = path.read_bytes()
        project = self._project_of(path, rel)
        if path.suffix.lower() == ".razor":
            text = source.decode("utf-8-sig", errors="replace")
            # Line-aligned, so every provenance below is a true `.razor` line number.
            rewritten = razor_to_csharp(text, rel, namespace=code_behind_namespace(path))
            batch = self._extract_source(rewritten.encode("utf-8"), module=module, rel=rel, project=project)
            return _span_component(
                batch, text, rel, namespace=component_namespace(text) or code_behind_namespace(path)
            )
        return self._extract_source(source, module=module, rel=rel, project=project)

    def _project_of(self, path: Path, rel: str) -> str:
        """The directory of the nearest `.csproj` above ``path`` (repo-relative): the unit a
        `global using` applies to. Two projects in one repository do not share them."""
        root = Path(str(path)[: -len(rel)]) if rel and str(path).endswith(rel) else path.parent
        cur = path.parent
        while True:
            key = str(cur)
            if key not in self._projects:
                self._projects[key] = any(cur.glob("*.csproj"))
            if self._projects[key] or cur == root or cur.parent == cur:
                return cur.relative_to(root).as_posix() if self._projects[key] and cur != root else ""
            cur = cur.parent

    def _extract_source(self, source: bytes, *, module: str, rel: str, project: str = "") -> FactBatch:
        parser = _csharp_parser()
        tree = parser.parse(source)
        batch = FactBatch()
        module_id = f"csharp:{module}" if module else "csharp:<root>"
        batch.add_node(Node(module_id, NodeKind.MODULE, module or rel, "csharp", Provenance(rel, 1)))

        types: list[_TypeRec] = []
        decls: list[tuple[int, int, _NsDecl]] = []
        self._usings(tree.root_node, module_id, source, rel, batch)
        self._walk(tree.root_node.named_children, module_id, "", source, rel, batch, types, None, decls)
        # Phase 1.3 — framework + call edges, computed once the full type set is known.
        _framework_edges(types, module_id, tree.root_node, source, rel, batch)
        # B21: calls through typed receivers, and DI bindings — both settled in `finalize`.
        unit = _unit_scope(tree.root_node, source, project, self._receivers)
        for rec in types:  # the innermost namespace declaration each type sits in
            rec.decl = max(
                ((start, d) for start, end, d in decls if start <= rec.node.start_byte < end),
                key=lambda sd: sd[0],
                default=(0, None),
            )[1]
        _record_receiver_calls(types, unit, source, rel, self._receivers)
        _record_bindings(tree.root_node, types, decls, unit, source, rel, self._bindings)
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
        decl: _NsDecl | None = None,
        decls: list[tuple[int, int, _NsDecl]] | None = None,
    ) -> None:
        """Walk siblings, descending into namespaces and emitting top-level types.

        Handles both block ``namespace N { ... }`` and file-scoped ``namespace N;``
        (whose types are subsequent siblings — so the namespace sticks for the rest).
        Each namespace declaration is recorded with the ``using``s written inside it (B21): C#
        consults them at that declaration's level, not the file's.
        """
        current_ns = namespace
        for i, node in enumerate(nodes):
            if node.type == "file_scoped_namespace_declaration":
                current_ns = _join_ns(namespace, _field_text(node, "name", source))
                decl = _ns_decl(current_ns, namespace, nodes[i + 1 :], decl, source)
                if decls is not None:
                    decls.append((node.start_byte, 1 << 62, decl))
            elif node.type == "namespace_declaration":
                ns = _join_ns(namespace, _field_text(node, "name", source))
                body = node.child_by_field_name("body")
                if body is not None:
                    inner = _ns_decl(ns, namespace, body.named_children, decl, source)
                    if decls is not None:
                        decls.append((node.start_byte, node.end_byte, inner))
                    self._walk(body.named_children, module_id, ns, source, rel, batch, types, inner, decls)
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
        # One CONTAINS per parent/child per file. `Edge.key()` carries provenance, so a partial
        # class declared twice in one file — every Razor component, whose `@inject` and `@code`
        # blocks each open one — emitted the same containment twice, at two lines.
        if not any(t.type_id == type_id for t in types):
            batch.add_edge(Edge(contains_parent, type_id, EdgeKind.CONTAINS, Provenance(rel, line)))
        parent = next((t for t in types if t.type_id == parent_type_id), None) if parent_type_id else None
        rec = _TypeRec(
            type_id=type_id,
            name=name,
            namespace=namespace,
            node=node,
            parent=parent,
            type_params=_type_params(node, source),
        )
        if node.type == "interface_declaration":
            self._receivers.interfaces.add(type_id)
        self._receivers.type_params[type_id] = rec.type_params
        types.append(rec)

        if node.type == "delegate_declaration":
            return  # a delegate is a leaf named type — no bases, members, or param-fields

        for base in _base_types(node, source):
            target = _resolve_type(base, namespace)
            if target is not None:
                batch.add_edge(Edge(type_id, target, EdgeKind.IMPLEMENTS, Provenance(rel, line)))
                rec.bases.append((base, target))

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
            bound = _bound_names(mnode, source)
            for callee, line, bare in _calls_in(mnode, source):
                # A local, parameter or lambda argument shadows the sibling method: C# resolves
                # a simple name to the innermost declaration, so `Handle()` under a parameter
                # named `Handle` invokes the delegate, not the method. `this.Handle()` is an
                # explicit member access and cannot be shadowed, which is why `bare` is carried
                # rather than inferred — skipping it too would drop real edges.
                if bare and line >= bound.get(callee, 1 << 30):
                    continue
                target = method_ids.get(callee)
                if target is not None:
                    batch.add_edge(Edge(mid, target, EdgeKind.CALLS, Provenance(rel, line)))


# --- typed receivers (B21) --------------------------------------------------


@dataclass(frozen=True)
class _NsDecl:
    """One namespace declaration and the ``using``s written inside it. C# resolves a simple name
    level by level: this namespace's types, then these usings and aliases, then the namespaces it
    implicitly opens (``namespace A.B`` opens ``A``), then the enclosing declaration."""

    full: str
    outer: str  # the namespace of the enclosing declaration ('' at the top)
    usings: tuple[str, ...]
    aliases: tuple[tuple[str, str], ...]
    parent: _NsDecl | None


@dataclass(frozen=True)
class _Unit:
    """The compilation unit's own ``using``s and aliases, and the project its ``global using``s
    (collected repository-wide into ``ReceiverState.global_prefixes[project]``) belong to."""

    usings: tuple[str, ...]
    aliases: tuple[tuple[str, str], ...]
    project: str


def _parse_using(node: TSNode, source: bytes) -> tuple[str, str, str]:
    """``(kind, alias, target)`` — kind is ``static``, ``global``, ``alias`` or ``plain``."""
    words = {c.type for c in node.children}
    if "static" in words or not node.named_children:
        return "static", "", ""
    target = _text(node.named_children[-1], source).removeprefix("global::")
    alias = node.child_by_field_name("name")
    if alias is not None:
        return "alias", _text(alias, source), target
    return ("global" if "global" in words else "plain"), "", target


def _ns_decl(full: str, outer: str, members: list[TSNode], parent: _NsDecl | None, source: bytes) -> _NsDecl:
    usings: list[str] = []
    aliases: list[tuple[str, str]] = []
    for node in members:
        if node.type == "using_directive":
            kind, alias, target = _parse_using(node, source)
            if kind == "alias":
                aliases.append((alias, target))
            elif kind in ("plain", "global") and target:
                usings.append(target)
    return _NsDecl(full, outer, tuple(sorted(set(usings))), tuple(sorted(aliases)), parent)


def _unit_scope(root: TSNode, source: bytes, project: str, state: ReceiverState) -> _Unit:
    """The file's top-level ``using``s — those before any file-scoped namespace, which belong to
    that namespace instead. ``global using`` joins the project-wide set."""
    usings: list[str] = []
    aliases: list[tuple[str, str]] = []
    for node in root.named_children:
        if node.type == "file_scoped_namespace_declaration":
            break
        if node.type != "using_directive":
            continue
        kind, alias, target = _parse_using(node, source)
        if kind == "global" and target:
            state.global_prefixes.setdefault(project, set()).add(target)
        elif kind == "alias":
            aliases.append((alias, target))
        elif kind == "plain" and target:
            usings.append(target)
    return _Unit(tuple(sorted(set(usings))), tuple(sorted(aliases)), project)


_GENERIC_CALLABLES = frozenset({"method_declaration", "local_function_statement", "constructor_declaration"})


def _params_at(node: TSNode, source: bytes, stop: TSNode | None = None) -> frozenset[str]:
    """Every type parameter in force at ``node``: its enclosing method's and every local
    function's between them (B30, D5) — ``TItem Make<TItem>() => new TItem()`` inside a method
    means the local function's ``TItem``, never an in-repo class of that name. Walks up to ``stop``
    (the method) when given, else to the enclosing type."""
    names: set[str] = set()
    cur: TSNode | None = node
    while cur is not None:
        if cur.type in _GENERIC_CALLABLES:
            names |= _type_params(cur, source)
        if cur == stop or cur.type in _TYPE_DECLS:
            break
        cur = cur.parent
    return frozenset(names)


def _type_params(node: TSNode, source: bytes) -> frozenset[str]:
    """``<T, U>`` of a type or method declaration."""
    names: set[str] = set()
    for child in node.named_children:
        if child.type == "type_parameter_list":
            for tp in child.named_children:
                ident = tp.child_by_field_name("name") or next(
                    (c for c in tp.named_children if c.type == "identifier"), None
                )
                if ident is not None:
                    names.add(_text(ident, source))
    return frozenset(names)


def _strip_type(text: str) -> str | None:
    """``global::A.List<B<C>>?`` → ``A.List``; None for arrays, pointers, tuples, keywords."""
    name = text.removeprefix("global::").strip()
    while True:  # balanced generic arguments, innermost first: `Outer<X>.Inner<Y>` keeps both names
        stripped = re.sub(r"<[^<>]*>", "", name)
        if stripped == name:
            break
        name = stripped
    name = name.rstrip("?").strip()
    if not name or any(ch in name for ch in "[]*(), ") or not (name[0].isalpha() or name[0] == "_"):
        return None
    return name


def _type_ref_in(
    text: str,
    rec: _TypeRec | None,
    decl: _NsDecl | None,
    unit: _Unit,
    extra_params: frozenset[str] = frozenset(),
) -> TypeRef | None:
    """The candidate ids a written C# type name can denote, in the compiler's lookup order:
    types nested in (or inherited by) the enclosing types; for each namespace declaration from
    the innermost out, its namespace's types then its usings and aliases, then the namespaces it
    implicitly opens; the global namespace; then the file's usings and aliases together with the
    project's ``global using``s. A type parameter is never an in-repo type. ``global::A.B``
    names the global namespace's ``A.B`` and nothing else: no enclosing type, namespace or using
    is consulted, and a miss there is not retried anywhere nearer."""
    name = _strip_type(text)
    if name is None:
        return None
    if text.strip().startswith("global::"):
        return TypeRef(((f"csharp:{name}", STOP),), project=unit.project)
    head, _, rest = name.partition(".")
    suffix = f".{rest}" if rest else ""
    params = set(extra_params)
    cur_rec = rec
    while cur_rec is not None:
        params |= cur_rec.type_params
        cur_rec = cur_rec.parent
    if head in params:
        return None
    groups: list[tuple[str, ...]] = []
    d = decl
    while d is not None:
        opened = [p for p in d.full[len(d.outer) :].split(".") if p] if d.full.startswith(d.outer) else []
        for k in range(len(opened), 0, -1):
            ns = ".".join(p for p in (d.outer, *opened[:k]) if p)
            groups.append((f"csharp:{ns}.{head}{suffix}",))
            if k == len(opened):
                alias = dict(d.aliases).get(head)
                if alias is not None:
                    groups.append((f"csharp:{alias}{suffix}", STOP))
                elif d.usings:
                    groups.append(tuple(f"csharp:{u}.{head}{suffix}" for u in d.usings))
        d = d.parent
    groups.append((f"csharp:{head}{suffix}",))  # the global namespace
    alias = dict(unit.aliases).get(head)
    if alias is not None:
        groups.append((f"csharp:{alias}{suffix}", STOP))
    if rest:
        # `Outer.Inner`: the head the full way, then `Inner` among its member types (B30, D4); the
        # groups above are the namespace-qualified reading, for when the head is no type
        head_ref = _type_ref_in(head, rec, decl, unit, extra_params)
        return TypeRef(
            tuple(groups),
            simple=f"{head}{suffix}",
            using_prefixes=unit.usings,
            project=unit.project,
            head=head_ref,
            rest=tuple(rest.split(".")),
        )
    chain: list[str] = []
    cur_rec = rec
    while cur_rec is not None:
        chain.append(cur_rec.type_id)
        cur_rec = cur_rec.parent
    return TypeRef(
        tuple(groups),
        simple=f"{head}{suffix}",
        using_prefixes=unit.usings,
        enclosing=tuple(chain),
        nested=f"{head}{suffix}",
        project=unit.project,
    )


def _type_ref(
    text: str, rec: _TypeRec, unit: _Unit, extra_params: frozenset[str] = frozenset()
) -> TypeRef | None:
    return _type_ref_in(text, rec, rec.decl, unit, extra_params)


def _type_node_ref(
    node: TSNode | None, rec: _TypeRec, unit: _Unit, source: bytes, extra_params: frozenset[str] = frozenset()
) -> TypeRef | None:
    if node is None or node.type in (
        "implicit_type",
        "predefined_type",
        "array_type",
        "tuple_type",
        "pointer_type",
    ):
        return None
    return _type_ref(_text(node, source), rec, unit, extra_params)


_BLOCKS = frozenset(
    {
        "block",
        "switch_section",
        "method_declaration",
        "constructor_declaration",
        "local_function_statement",
        "lambda_expression",
        "anonymous_method_expression",
        "accessor_declaration",
        "arrow_expression_clause",
    }
)


def _block_of(node: TSNode) -> TSNode:
    cur = node.parent
    while cur is not None and cur.type not in _BLOCKS:
        cur = cur.parent
    return cur if cur is not None else node


def _method_scope(mnode: TSNode, rec: _TypeRec, unit: _Unit, source: bytes) -> Scope:
    """Every binding a method makes, with the block it is in force in (R1).

    Every binding form is collected, typed or not: a name missed here would fall through to a
    field of the same name and resolve to *its* type — the one way this pass could invent."""
    scope = Scope(before_decl_refuses=True)  # C#: a local is in scope for its whole block
    # The method's own type parameters, and each local function's over its byte range (B30, D5) —
    # indexed once: walking `parent` per declaration is quadratic in nesting (review 1, S1).
    own = _type_params(mnode, source)
    local_functions: list[tuple[int, int, frozenset[str]]] = []
    pending = list(mnode.named_children)
    while pending:
        n = pending.pop()
        pending.extend(n.named_children)
        if n.type == "local_function_statement":
            local_functions.append((n.start_byte, n.end_byte, _type_params(n, source)))

    def params_at(node: TSNode) -> frozenset[str]:
        found = set(own)
        for start, end, params in local_functions:
            if start <= node.start_byte < end:
                found |= params
        return frozenset(found)

    def typed(node: TSNode | None) -> object:
        ref = _type_node_ref(node, rec, unit, source, params_at(node)) if node is not None else None
        return ref if ref is not None else UNREADABLE

    def bind(name_node: TSNode | None, ref: object, where: TSNode, decl: TSNode) -> None:
        if name_node is not None:
            scope.bind(_text(name_node, source), ref, where.start_byte, where.end_byte, decl.start_byte)

    def bind_all(node: TSNode | None, where: TSNode) -> None:  # every identifier in a pattern, untyped
        stack = [node] if node is not None else []
        while stack:
            n = stack.pop()
            if n.type == "identifier":
                bind(n, UNREADABLE, where, n)
            stack.extend(n.named_children)

    for param in (mnode.child_by_field_name("parameters") or mnode).named_children:
        if param.type == "parameter":
            bind(param.child_by_field_name("name"), typed(param.child_by_field_name("type")), mnode, mnode)

    stack = [c for c in mnode.named_children if c.type != "parameter_list"]
    while stack:
        n = stack.pop()
        stack.extend(n.named_children)
        t = n.type
        if t in ("lambda_expression", "anonymous_method_expression"):
            params = n.child_by_field_name("parameters")
            if params is not None and params.type != "parameter_list":
                bind(params, UNREADABLE, n, n)  # `x => …`
            elif params is not None:
                for p in params.named_children:
                    bind(p.child_by_field_name("name"), UNREADABLE, n, n)  # D13: refuse typed or not
        elif t == "local_function_statement":
            bind(n.child_by_field_name("name"), UNREADABLE, _block_of(n), n)
            for p in (n.child_by_field_name("parameters") or n).named_children:
                if p.type == "parameter":
                    bind(p.child_by_field_name("name"), typed(p.child_by_field_name("type")), n, n)
        elif t == "variable_declaration":
            container = n.parent
            if container is not None and container.type == "local_declaration_statement":
                where = _block_of(container)
            else:
                where = container or n
            ty = n.child_by_field_name("type")
            for d in n.named_children:
                if d.type != "variable_declarator":
                    continue
                name = d.child_by_field_name("name")
                if name is None:
                    bind_all(d.named_children[0] if d.named_children else None, where)  # `var (a, b) = …`
                elif ty is not None and ty.type == "implicit_type":
                    init = next((c for c in d.named_children if c.type == "object_creation_expression"), None)
                    ref = typed(init.child_by_field_name("type")) if init is not None else UNREADABLE
                    bind(name, ref, where, d)
                else:
                    bind(name, typed(ty), where, d)
        elif t == "foreach_statement":
            left = n.child_by_field_name("left")
            if left is not None and left.type == "identifier":
                bind(left, typed(n.child_by_field_name("type")), n, n)
            else:
                bind_all(left, n)
        elif t == "catch_declaration":
            bind(n.child_by_field_name("name"), typed(n.child_by_field_name("type")), n.parent or n, n)
        elif t in ("declaration_expression", "declaration_pattern"):
            bind(n.child_by_field_name("name"), typed(n.child_by_field_name("type")), _block_of(n), n)
        elif t in ("single_variable_designation", "parenthesized_variable_designation"):
            bind_all(n, _block_of(n))
        elif t in ("from_clause", "let_clause", "join_clause", "join_into_clause", "query_continuation"):
            query = n.parent
            while query is not None and query.type != "query_expression":
                query = query.parent
            named = n.child_by_field_name("name") or next(
                (c for c in n.named_children if c.type == "identifier"), None
            )
            bind(named, UNREADABLE, query or n, n)
    return scope


def _record_fields(rec: _TypeRec, unit: _Unit, source: bytes, state: ReceiverState) -> None:
    """Fields and properties with their declared types — and a primary-constructor or record's
    positional parameters, which are fields in all but name."""
    for child in rec.node.named_children:
        if child.type == "parameter_list":
            for p in child.named_children:
                if p.type == "parameter":
                    ref = _type_node_ref(p.child_by_field_name("type"), rec, unit, source)
                    state.add_field(rec.type_id, _field_text(p, "name", source), ref)
    body = rec.node.child_by_field_name("body")
    for member in body.named_children if body is not None else ():
        if member.type == "property_declaration":
            ref = _type_node_ref(member.child_by_field_name("type"), rec, unit, source)
            state.add_field(rec.type_id, _field_text(member, "name", source), ref)
        elif member.type in ("field_declaration", "event_field_declaration"):
            for decl in member.named_children:
                if decl.type != "variable_declaration":
                    continue
                ref = _type_node_ref(decl.child_by_field_name("type"), rec, unit, source)
                for d in decl.named_children:
                    if d.type == "variable_declarator":
                        state.add_field(rec.type_id, _field_text(d, "name", source), ref)


def _record_bindings(
    root: TSNode,
    types: list[_TypeRec],
    decls: list[tuple[int, int, _NsDecl]],
    unit: _Unit,
    source: bytes,
    rel: str,
    out: list[Binding],
) -> None:
    """Every two-type DI registration in the file (`csharp_di`), resolved from where it is
    written: inside a type, or at the top level of a `Program.cs`."""
    stack = [root]
    while stack:
        node = stack.pop()
        stack.extend(node.named_children)
        if node.type != "invocation_expression":
            continue
        found = type_arguments(node)
        if found is None or found[0] not in REGISTRATIONS or len(found[1]) not in (1, 2):
            continue
        written = [_text(a, source) for a in found[1]]
        if len(written) == 1:
            built = factory_creation(node)  # `AddScoped<IAudit>(sp => new DbAudit())` (B22, D9)
            if built is None:
                continue
            written.append(_text(built, source))
        enclosing = max(
            (t for t in types if t.node.start_byte <= node.start_byte and node.end_byte <= t.node.end_byte),
            key=lambda t: t.node.start_byte,
            default=None,
        )
        if enclosing is not None:
            decl = enclosing.decl
        else:
            decl = max(
                ((start, d) for start, end, d in decls if start <= node.start_byte < end),
                key=lambda sd: sd[0],
                default=(0, None),
            )[1]
        # a generic method's own `<TImpl>` is never the in-repo class of that name (B30, D5)
        params = _params_at(node, source)
        iface, impl = (_type_ref_in(w, enclosing, decl, unit, params) for w in written)
        if iface is not None and impl is not None:
            out.append(Binding(impl, iface, rel, node.start_point[0] + 1))


def _qualified_name(node: TSNode, source: bytes) -> str | None:
    """``A.B.Helper`` for a receiver that is a chain of plain identifiers, else None."""
    if node.type == "identifier":
        return _text(node, source)
    if node.type == "member_access_expression":
        head = node.child_by_field_name("expression")
        name = node.child_by_field_name("name")
        if head is not None and name is not None and name.type == "identifier":
            prefix = _qualified_name(head, source)
            return f"{prefix}.{_text(name, source)}" if prefix else None
    return None


def _creation(
    node: TSNode, rec: _TypeRec, unit: _Unit, source: bytes, method_params: frozenset[str]
) -> TypeRef | None:
    """The type a creation instantiates (B22): ``new T(…)`` and ``new T { … }`` name it; a
    target-typed ``new(…)`` only when its declaration writes the type on the same line —
    ``Foo x = new();`` (D3). Anywhere else (``return new();``, an argument, an assignment) the
    type comes from inference, and is refused. ``new T[n]`` is an ``array_creation_expression``
    and never read: it runs no constructor of ``T``."""
    if node.type == "object_creation_expression":
        written = node.child_by_field_name("type")
        if written is not None and written.type == "nullable_type":
            return None  # `new S?()` is a null `Nullable<S>` — no constructor of `S` runs
        return _type_node_ref(written, rec, unit, source, method_params)
    parent = node.parent
    if parent is not None and parent.type == "equals_value_clause":
        parent = parent.parent
    if parent is None or parent.type != "variable_declarator" or parent.parent is None:
        return None
    declaration = parent.parent
    if declaration.type != "variable_declaration":
        return None
    return _type_node_ref(declaration.child_by_field_name("type"), rec, unit, source, method_params)


def _record_receiver_calls(
    types: list[_TypeRec], unit: _Unit, source: bytes, rel: str, state: ReceiverState
) -> None:
    """Defer every ``recv.m()`` / ``Type.m()`` call — and every ``new T(…)`` (B22) — in this file
    to ``finalize`` (B21)."""
    for rec in types:
        _record_fields(rec, unit, source, state)
        for i, (written, provisional) in enumerate(rec.bases):
            ref = _type_ref_in(written, rec.parent, rec.decl, unit)
            state.add_base(rec.type_id, provisional, ref)
            # Only a class's (or record's) first base can be a class; the rest are interfaces.
            if i == 0 and rec.node.type in ("class_declaration", "record_declaration"):
                state.add_class_base(rec.type_id, ref)
        for _name, mid, mnode in rec.methods:
            scope = _method_scope(mnode, rec, unit, source)
            # The type parameters in force travel down the walk: a local function adds its own
            # (B30, D5). Recomputing them per node from the ancestors was quadratic in nesting.
            own = _type_params(mnode, source)
            stack = [(c, own) for c in mnode.named_children if c.type != "parameter_list"]
            while stack:
                n, method_params = stack.pop()
                inner = (
                    method_params | _type_params(n, source)
                    if n.type == "local_function_statement"
                    else method_params
                )
                stack.extend((c, inner) for c in n.named_children)
                if n.type in ("object_creation_expression", "implicit_object_creation_expression"):
                    created = _creation(n, rec, unit, source, method_params)
                    if created is not None:
                        state.calls.append(
                            DeferredCall(mid, "", rel, n.start_point[0] + 1, receiver=created, creates=True)
                        )
                    continue
                if n.type != "invocation_expression":
                    continue
                fn = n.child_by_field_name("function")
                if fn is None or fn.type != "member_access_expression":
                    continue  # `Foo()` is the sibling pass's; `x?.Foo()` is out of scope
                obj, member_node = fn.child_by_field_name("expression"), fn.child_by_field_name("name")
                if obj is None or member_node is None:
                    continue
                if member_node.type == "generic_name":
                    member = _generic_head(member_node, source)
                else:
                    member = _text(member_node, source)
                line = n.start_point[0] + 1
                at = n.start_byte
                call: DeferredCall | None = None
                if obj.type == "identifier":
                    recv = _text(obj, source)
                    bound = scope.lookup(recv, at)
                    if isinstance(bound, TypeRef):
                        call = DeferredCall(mid, member, rel, line, receiver=bound)
                    elif bound is None:
                        call = DeferredCall(
                            mid,
                            member,
                            rel,
                            line,
                            field_of=rec.type_id,
                            field_name=recv,
                            static=_type_ref(recv, rec, unit, method_params),
                        )
                elif obj.type == "member_access_expression":
                    head = obj.child_by_field_name("expression")
                    if head is not None and head.type in ("this", "this_expression"):
                        field_name = _field_text(obj, "name", source)
                        call = DeferredCall(
                            mid, member, rel, line, field_of=rec.type_id, field_name=field_name
                        )
                    else:
                        qualified = _qualified_name(obj, source)
                        first = qualified.split(".", 1)[0] if qualified else ""
                        if qualified and scope.lookup(first, at) is None:
                            # `Status.Kind.Parse()`: static only if `Status` is not a field (B7).
                            call = DeferredCall(
                                mid,
                                member,
                                rel,
                                line,
                                field_of=rec.type_id,
                                field_name=first,
                                static=_type_ref(qualified, rec, unit, method_params),
                                chain_head=True,
                            )
                elif obj.type == "generic_name":  # `Cache<T>.Get()` — a static call on a generic type
                    ref = _type_ref(_generic_head(obj, source), rec, unit, method_params)
                    call = DeferredCall(mid, member, rel, line, receiver=ref)
                if call is not None and (call.receiver is not None or call.field_of is not None):
                    state.calls.append(call)


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
    """Best-effort base-type id. A simple name is *provisionally* placed in its own namespace.

    "Assume same namespace" is a guess, and on real code it is usually wrong: `IEqualityComparer`,
    `Exception` and `DisplayNameAttribute` are `System.*` types, but a per-file pass cannot know
    whether a bare base name is first-party or framework — that needs every declaration in the
    repo, which only exists after the walk.

    So the guess is made here and **corrected in `finalize`**: any target that turns out not to
    be a declared type is repointed at an external node keyed by the bare name, which asserts
    the name (which we read) and not the namespace (which we invented).
    """
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


def _calls_in(mnode: TSNode, source: bytes) -> list[tuple[str, int, bool]]:
    """``(callee_name, line, bare)`` for unqualified / ``this.`` calls inside a method.

    ``bare`` is True only for the ``Foo(...)`` form. ``this.Foo(...)`` resolves to the member
    whatever else is in scope, so only the bare form can be shadowed by a local.
    """
    out: list[tuple[str, int, bool]] = []
    stack = list(mnode.named_children)
    while stack:
        node = stack.pop()
        if node.type == "invocation_expression":
            fn = node.child_by_field_name("function")
            name = _unqualified_call_name(fn, source)
            if name:
                out.append((name, node.start_point[0] + 1, fn is not None and fn.type == "identifier"))
        stack.extend(node.named_children)
    return out


def _bound_names(mnode: TSNode, source: bytes) -> dict[str, int]:
    """Names this method binds, each with the first line it is in scope.

    C# forbids using a simple name as an outer meaning in a block where it later denotes a
    local, so the line rarely changes an answer here — it is kept so the helper asserts what it
    actually knows, and so the four front-ends read the same way.

    Lambdas are descended into by the call walk, so their parameters bind too: a call inside
    ``xs.Select(Handle => Handle())`` is attributed to the enclosing method.
    """
    bound: dict[str, int] = {}

    def bind(node: TSNode | None, line: int) -> None:
        if node is None:
            return
        name = _text(node, source)
        if name and line < bound.get(name, 1 << 30):
            bound[name] = line

    def bind_params(plist: TSNode | None, line: int) -> None:
        if plist is None:
            return
        if plist.type == "identifier":  # `x => …`, which carries no parameter_list
            bind(plist, line)
            return
        for param in plist.named_children:
            bind(param.child_by_field_name("name") or param, line)

    bind_params(mnode.child_by_field_name("parameters"), mnode.start_point[0] + 1)

    stack = list(mnode.named_children)
    while stack:
        n = stack.pop()
        if n.type == "variable_declarator":
            bind(n.child_by_field_name("name") or n, n.end_point[0] + 2)
        elif n.type == "foreach_statement":
            bind(n.child_by_field_name("left"), n.start_point[0] + 1)
        elif n.type == "catch_declaration":
            bind(n.child_by_field_name("name"), n.start_point[0] + 1)
        elif n.type in ("lambda_expression", "anonymous_method_expression"):
            bind_params(n.child_by_field_name("parameters"), n.start_point[0] + 1)
        elif n.type == "local_function_statement":
            bind(n.child_by_field_name("name"), n.start_point[0] + 1)
            bind_params(n.child_by_field_name("parameters"), n.start_point[0] + 1)
        stack.extend(n.named_children)
    return bound


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


def _span_component(batch: FactBatch, text: str, rel: str, *, namespace: str) -> FactBatch:
    """The component's ``Type`` spans the whole file.

    Blazor's generated class *is* the file — the markup is the render method's body — but the
    rewrite opens the class on the ``@inject`` line and again at ``@code``, and ``add_node``
    keeps the first, so the ``Type`` reported a one-line span at ``@inject``. Then
    ``GroundedRetriever.enclosing_symbol`` on a line inside ``@code`` found nothing, and a diff
    there had no blast radius. Widened here, once the parse is done, from a fact the file
    states: its length.
    """
    from dataclasses import replace

    from orchestrator.pkg.razor import _lines

    stem = component_class_name(rel)
    type_id = f"csharp:{_join_ns(namespace, stem)}"
    total = len(_lines(text)) or 1
    if all(n.id != type_id for n in batch.nodes):
        return batch
    widened = FactBatch()
    for node in batch.nodes:
        if node.id == type_id and node.provenance is not None:
            node = replace(node, provenance=Provenance(rel, 1, total))
        widened.add_node(node)
    for edge in batch.edges:
        widened.add_edge(edge)
    return widened


__all__ = ["CSharpExtractor"]
