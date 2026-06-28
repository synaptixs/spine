"""C++ front-end for the PKG extractor (Track 3: a sixth language, a C superset).

C++ reuses Track 2's machinery — the translation-unit model, the `#include` graph
(with the same in-repo resolution + repo-wide header index), header/source merge,
and the declarator/`call`/field helpers — and layers the object model on top:
classes, namespaces, (multiple) inheritance, member functions, and templates.

Emitted (precision-first):
- ``Module`` — the file (``.cpp``/``.cc``/``.cxx``/``.hpp``/``.hh``/``.hxx``).
- ``Type`` — ``class`` / ``struct`` / ``union`` / ``enum``, namespace-qualified
  (``cpp:geo::Circle``); a ``template`` emits its ``Type``/``Function`` but
  instantiations are not resolved.
- ``Field`` — data members, enum constants, and file/global variables.
- ``Function`` — free functions AND member functions. **Header/source merge:** an
  in-class declaration is an ``external`` placeholder upgraded by the definition
  (inline or the out-of-line ``ReturnType Class::method() {…}``), keyed by the
  qualified name so the two collapse onto one node regardless of file order.
- ``IMPORTS`` — ``#include`` (reuses the C resolver).
- ``CONTAINS`` — module → free function / type / global, and type → member.
- ``IMPLEMENTS`` — a class's ``base_class_clause`` (multiple bases → multiple edges).
- ``CALLS`` — free-function and ``Namespace::func`` calls (name-keyed; overloads
  collapse), plus an unqualified / ``this->`` call to a sibling method. Member
  calls on other objects need type inference and are not resolved (documented).
- ``REFERENCES`` — a data member whose type is another class/struct (the data edge).

``.h`` headers stay with the C front-end (most are C-compatible); a C++ project that
uses ``.h`` for class headers will have those parsed as C — classes there are not
captured. Preprocessor caveat carries over: parsing is pre-expansion.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from orchestrator.pkg.c_extractor import (
    _PREPROC_CONTAINERS,
    _declarator_name,
    _declared_names,
    _field_text,
    _function_declarator,
    _has_body,
    _member_type_name,
    _resolve_include,
    _string_content,
    _text,
)
from orchestrator.pkg.extractor import rel_module_name
from orchestrator.pkg.facts import Edge, EdgeKind, FactBatch, Node, NodeKind, Provenance

if TYPE_CHECKING:
    from tree_sitter import Node as TSNode

_TYPE_SPECIFIERS = ("class_specifier", "struct_specifier", "union_specifier", "enum_specifier")


class CppExtractor:
    """C++ front-end (tree-sitter). Install the ``cpp`` extra to use it."""

    language: str = "cpp"
    suffixes: tuple[str, ...] = (".cpp", ".cc", ".cxx", ".hpp", ".hh", ".hxx")

    def module_name(self, path: Path, root: Path) -> str:
        return rel_module_name(path, root)

    def extract(self, *, path: Path, module: str, rel: str) -> FactBatch:
        parser = _cpp_parser()
        source = path.read_bytes()
        tree = parser.parse(source)
        batch = FactBatch()
        module_id = f"cpp:{module}" if module else f"cpp:{rel}"
        batch.add_node(Node(module_id, NodeKind.MODULE, module or rel, "cpp", Provenance(rel, 1)))

        # Collected during the walk for the CALLS pass: every function/method body with
        # its node id, plus each type's own method names (for sibling-call resolution).
        funcs: list[tuple[str, str | None, TSNode]] = []  # (func_id, enclosing_type_id, body)
        free_funcs: dict[str, str] = {}
        type_methods: dict[str, set[str]] = {}
        ctx = _Ctx(module_id, path, source, rel, batch, funcs, free_funcs, type_methods)

        self._walk(tree.root_node.named_children, "", ctx)
        for fid, type_id, body in funcs:
            self._calls(fid, type_id, body, ctx)
        return batch

    # --- walk ----------------------------------------------------------------

    def _walk(self, nodes: list[TSNode], ns: str, ctx: _Ctx, tparams: frozenset[str] = frozenset()) -> None:
        for node in nodes:
            t = node.type
            if t in _PREPROC_CONTAINERS:
                self._walk(node.named_children, ns, ctx, tparams)
            elif t == "namespace_definition":
                inner = _join(ns, _field_text(node, "name", ctx.source))
                body = node.child_by_field_name("body")
                if body is not None:
                    self._walk(body.named_children, inner, ctx)
            elif t == "template_declaration":
                child = next(
                    (
                        c
                        for c in node.named_children
                        if c.type in (*_TYPE_SPECIFIERS, "function_definition", "declaration")
                    ),
                    None,
                )
                if child is not None:
                    self._walk([child], ns, ctx, _template_params(node, ctx.source))
            elif t == "preproc_include":
                self._include(node, ctx)
            elif t in _TYPE_SPECIFIERS:
                name = _field_text(node, "name", ctx.source)
                if name and _has_body(node):
                    self._emit_type(node, ns, ctx, tparams)
            elif t == "function_definition":
                self._function_def(node, ns, ctx)
            elif t == "declaration":
                self._declaration(node, ns, ctx)

    # --- includes (reuse the C resolver) -------------------------------------

    def _include(self, node: TSNode, ctx: _Ctx) -> None:
        target = node.child_by_field_name("path")
        if target is None:
            return
        line = node.start_point[0] + 1
        if target.type == "system_lib_string":
            name = _text(target, ctx.source).strip("<>")
            ctx.batch.add_node(Node(f"cpp:{name}", NodeKind.MODULE, name, "cpp", external=True))
            ctx.batch.add_edge(
                Edge(ctx.module_id, f"cpp:{name}", EdgeKind.IMPORTS, Provenance(ctx.rel, line))
            )
            return
        raw = _string_content(target, ctx.source)
        if not raw:
            return
        resolved = _resolve_include(ctx.path, ctx.rel, raw)
        if resolved is not None:
            tid = f"cpp:{resolved}"
            ctx.batch.add_node(Node(tid, NodeKind.MODULE, resolved, "cpp", Provenance(resolved, 1)))
        else:
            tid = f"cpp:{raw}"
            ctx.batch.add_node(Node(tid, NodeKind.MODULE, raw, "cpp", external=True))
        ctx.batch.add_edge(Edge(ctx.module_id, tid, EdgeKind.IMPORTS, Provenance(ctx.rel, line)))

    # --- types + members -----------------------------------------------------

    def _emit_type(self, spec: TSNode, ns: str, ctx: _Ctx, tparams: frozenset[str] = frozenset()) -> None:
        name = _field_text(spec, "name", ctx.source)
        qualified = _join(ns, name)
        type_id = f"cpp:{qualified}"
        line = spec.start_point[0] + 1
        ctx.batch.add_node(
            Node(type_id, NodeKind.TYPE, name, "cpp", Provenance(ctx.rel, line, spec.end_point[0] + 1))
        )
        ctx.batch.add_edge(Edge(ctx.module_id, type_id, EdgeKind.CONTAINS, Provenance(ctx.rel, line)))
        ctx.type_methods.setdefault(type_id, set())

        for base in _base_types(spec, ctx.source):
            target = base if "::" in base else _join(ns, base)
            ctx.batch.add_edge(Edge(type_id, f"cpp:{target}", EdgeKind.IMPLEMENTS, Provenance(ctx.rel, line)))

        body = spec.child_by_field_name("body")
        if body is None:
            return
        if spec.type == "enum_specifier":
            for e in body.named_children:
                if e.type == "enumerator":
                    en = _field_text(e, "name", ctx.source)
                    if en:
                        _member(type_id, en, NodeKind.FIELD, e.start_point[0] + 1, ctx)
            return
        for member in body.named_children:
            mline = member.start_point[0] + 1
            if member.type == "function_definition":  # inline method definition
                self._member_function(member, type_id, ns, ctx, is_definition=True)
            elif member.type == "field_declaration":
                fdeclr = _function_declarator(member.child_by_field_name("declarator"))
                if fdeclr is not None:  # a member-function declaration
                    self._member_function(member, type_id, ns, ctx, is_definition=False)
                else:  # data member(s)
                    mtype = _member_type_name(member, ctx.source)
                    for fname in _declared_names(member, ctx.source):
                        _member(type_id, fname, NodeKind.FIELD, mline, ctx)
                    if mtype is not None and mtype != _qual_name(type_id) and mtype not in tparams:
                        tgt = mtype if "::" in mtype else _join(ns, mtype)
                        ctx.batch.add_edge(
                            Edge(type_id, f"cpp:{tgt}", EdgeKind.REFERENCES, Provenance(ctx.rel, mline))
                        )
            elif member.type in _TYPE_SPECIFIERS and _has_body(member):
                self._emit_type(member, qualified, ctx, tparams)  # nested type

    def _member_function(
        self, node: TSNode, type_id: str, ns: str, ctx: _Ctx, *, is_definition: bool
    ) -> None:
        fdeclr = _function_declarator(node.child_by_field_name("declarator"))
        name = _declarator_name(fdeclr, ctx.source) if fdeclr else ""
        if not name:
            return
        mid = f"{type_id}::{name}"
        line = node.start_point[0] + 1
        ctx.batch.add_node(
            Node(mid, NodeKind.FUNCTION, name, "cpp", Provenance(ctx.rel, line), external=not is_definition)
        )
        ctx.batch.add_edge(Edge(type_id, mid, EdgeKind.CONTAINS, Provenance(ctx.rel, line)))
        ctx.type_methods.setdefault(type_id, set()).add(name)
        if is_definition:
            body = node.child_by_field_name("body")
            if body is not None:
                ctx.funcs.append((mid, type_id, body))

    # --- free functions + out-of-line definitions + globals ------------------

    def _function_def(self, node: TSNode, ns: str, ctx: _Ctx) -> None:
        fdeclr = _function_declarator(node.child_by_field_name("declarator"))
        if fdeclr is None:
            return
        name_node = fdeclr.child_by_field_name("declarator")
        body = node.child_by_field_name("body")
        if name_node is not None and name_node.type == "qualified_identifier":
            # Out-of-line member definition: `RetType A::B::method() { … }`. Prefix the
            # enclosing namespace so a partial qualifier inside `namespace geo {…}`
            # (`Circle::area`) merges with the in-class declaration (`geo::Circle::area`).
            full = _join(ns, _qualified_text(name_node, ctx.source))
            type_q, _, method = full.rpartition("::")
            type_id = f"cpp:{type_q}"
            mid = f"{type_id}::{method}"
            line = node.start_point[0] + 1
            ctx.batch.add_node(Node(mid, NodeKind.FUNCTION, method, "cpp", Provenance(ctx.rel, line)))
            ctx.batch.add_edge(Edge(type_id, mid, EdgeKind.CONTAINS, Provenance(ctx.rel, line)))
            ctx.type_methods.setdefault(type_id, set()).add(method)
            if body is not None:
                ctx.funcs.append((mid, type_id, body))
            return
        # A free function definition.
        name = _declarator_name(fdeclr, ctx.source)
        if not name:
            return
        fid = f"cpp:{_join(ns, name)}"
        line = node.start_point[0] + 1
        ctx.batch.add_node(Node(fid, NodeKind.FUNCTION, name, "cpp", Provenance(ctx.rel, line)))
        ctx.batch.add_edge(Edge(ctx.module_id, fid, EdgeKind.CONTAINS, Provenance(ctx.rel, line)))
        ctx.free_funcs[name] = fid
        if body is not None:
            ctx.funcs.append((fid, None, body))

    def _declaration(self, node: TSNode, ns: str, ctx: _Ctx) -> None:
        type_node = node.child_by_field_name("type")
        declarators = [
            c
            for c in node.named_children
            if c.type
            in (
                "identifier",
                "pointer_declarator",
                "array_declarator",
                "init_declarator",
                "function_declarator",
            )
        ]
        if not declarators and type_node is not None and type_node.type in _TYPE_SPECIFIERS:
            if _field_text(type_node, "name", ctx.source) and _has_body(type_node):
                self._emit_type(type_node, ns, ctx)
            return
        for d in declarators:
            fdeclr = _function_declarator(d)
            if fdeclr is not None:  # free-function prototype → placeholder
                name = _declarator_name(fdeclr, ctx.source)
                if name:
                    fid = f"cpp:{_join(ns, name)}"
                    line = node.start_point[0] + 1
                    ctx.batch.add_node(
                        Node(fid, NodeKind.FUNCTION, name, "cpp", Provenance(ctx.rel, line), external=True)
                    )
                    ctx.batch.add_edge(Edge(ctx.module_id, fid, EdgeKind.CONTAINS, Provenance(ctx.rel, line)))
                    ctx.free_funcs.setdefault(name, fid)
                continue
            vname = _declarator_name(d, ctx.source)
            if vname:
                vid = f"cpp:{_join(ns, vname)}"
                line = d.start_point[0] + 1
                ctx.batch.add_node(Node(vid, NodeKind.FIELD, vname, "cpp", Provenance(ctx.rel, line)))
                ctx.batch.add_edge(Edge(ctx.module_id, vid, EdgeKind.CONTAINS, Provenance(ctx.rel, line)))

    # --- CALLS (3.3) ---------------------------------------------------------

    def _calls(self, caller: str, type_id: str | None, body: TSNode, ctx: _Ctx) -> None:
        siblings = ctx.type_methods.get(type_id or "", set())
        stack = list(body.named_children)
        while stack:
            n = stack.pop()
            if n.type == "call_expression":
                fn = n.child_by_field_name("function")
                target = _resolve_callee(fn, ctx.source, siblings, type_id, ctx.free_funcs)
                if target is not None:
                    ctx.batch.add_edge(
                        Edge(caller, target, EdgeKind.CALLS, Provenance(ctx.rel, n.start_point[0] + 1))
                    )
            stack.extend(n.named_children)


class _Ctx:
    """Threaded extraction state (keeps method signatures short)."""

    def __init__(
        self,
        module_id: str,
        path: Path,
        source: bytes,
        rel: str,
        batch: FactBatch,
        funcs: list[tuple[str, str | None, Any]],
        free_funcs: dict[str, str],
        type_methods: dict[str, set[str]],
    ) -> None:
        self.module_id = module_id
        self.path = path
        self.source = source
        self.rel = rel
        self.batch = batch
        self.funcs = funcs
        self.free_funcs = free_funcs
        self.type_methods = type_methods


# --- helpers ---------------------------------------------------------------


def _member(type_id: str, name: str, kind: NodeKind, line: int, ctx: _Ctx) -> None:
    mid = f"{type_id}::{name}"
    ctx.batch.add_node(Node(mid, kind, name, "cpp", Provenance(ctx.rel, line)))
    ctx.batch.add_edge(Edge(type_id, mid, EdgeKind.CONTAINS, Provenance(ctx.rel, line)))


def _resolve_callee(
    fn: TSNode | None, source: bytes, siblings: set[str], type_id: str | None, free_funcs: dict[str, str]
) -> str | None:
    """The called function's node id, or ``None`` when it can't be resolved precisely."""
    if fn is None:
        return None
    if fn.type == "identifier":
        name = _text(fn, source)
        if type_id and name in siblings:
            return f"{type_id}::{name}"  # unqualified sibling-method call
        return free_funcs.get(name, f"cpp:{name}")  # free function (name-keyed)
    if fn.type == "qualified_identifier":
        return f"cpp:{_qualified_text(fn, source)}"  # Namespace::func or Class::method
    if fn.type == "field_expression":
        # obj.method() / this->method() — only resolvable to a sibling on `this`.
        arg = fn.child_by_field_name("argument")
        field = fn.child_by_field_name("field")
        if arg is not None and arg.type == "this" and field is not None and type_id:
            name = _text(field, source)
            if name in siblings:
                return f"{type_id}::{name}"
    return None


def _qualified_text(node: TSNode, source: bytes) -> str:
    """A qualified identifier / type name with all whitespace removed. Out-of-line
    definitions can wrap the line between `::` and the name (`connection::\\n  read`)
    or around `<...>`; the raw source slice would carry that whitespace into the node
    id/name, so collapse it (qualified ids have no semantic whitespace)."""
    return "".join(_text(node, source).split())


def _template_params(node: TSNode, source: bytes) -> frozenset[str]:
    """The type-parameter names of a ``template_declaration`` (``T``, ``U``) — excluded
    from REFERENCES so a member of template type isn't read as a concrete type."""
    plist = node.child_by_field_name("parameters")
    if plist is None:
        plist = next((c for c in node.named_children if c.type == "template_parameter_list"), None)
    if plist is None:
        return frozenset()
    names: set[str] = set()
    for p in plist.named_children:
        ident = next((c for c in p.named_children if c.type == "type_identifier"), None)
        if ident is not None:
            names.add(_text(ident, source))
    return frozenset(names)


def _base_types(spec: TSNode, source: bytes) -> list[str]:
    """Base-class names from a ``base_class_clause`` (drops access specifiers/virtual)."""
    clause = next((c for c in spec.named_children if c.type == "base_class_clause"), None)
    if clause is None:
        return []
    out: list[str] = []
    for c in clause.named_children:
        if c.type in ("type_identifier", "qualified_identifier", "template_type"):
            out.append(_qualified_text(c, source).split("<", 1)[0])
    return out


def _join(ns: str, name: str) -> str:
    if ns and name:
        return f"{ns}::{name}"
    return name or ns


def _qual_name(type_id: str) -> str:
    return type_id.split(":", 1)[1] if ":" in type_id else type_id


def _cpp_parser() -> Any:
    try:
        import tree_sitter_cpp
        from tree_sitter import Language, Parser
    except ImportError as exc:  # pragma: no cover - exercised only without the extra
        raise RuntimeError(
            "C++ extraction needs tree-sitter; install the extra: "
            "uv pip install 'tree-sitter>=0.21' 'tree-sitter-cpp>=0.21'"
        ) from exc
    language = Language(tree_sitter_cpp.language())
    try:
        return Parser(language)
    except TypeError:  # older tree-sitter API
        parser = Parser()
        parser.language = language
        return parser


__all__ = ["CppExtractor"]
