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
        imports = self._imports(decls, module_id, source, rel, batch)
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
        for fid, type_id, body in funcs:
            _calls(fid, type_id, body, type_methods, local_funcs, imports, source, rel, batch)
        return batch

    def _imports(
        self, decls: list[TSNode | None], module_id: str, source: bytes, rel: str, batch: FactBatch
    ) -> dict[str, str]:
        """Emit IMPORTS edges; return {localName: moduleSpecifier} for heritage resolution."""
        by_local: dict[str, str] = {}
        for node in decls:
            if node is None or node.type != "import_statement":
                continue
            source_node = node.child_by_field_name("source")
            spec = _text(source_node, source).strip("\"'") if source_node is not None else ""
            if not spec:
                continue
            mid = f"ts:{spec}"
            batch.add_node(Node(mid, NodeKind.MODULE, spec, "typescript", external=True))
            batch.add_edge(Edge(module_id, mid, EdgeKind.IMPORTS, Provenance(rel, node.start_point[0] + 1)))
            for local in _imported_locals(node, source):
                by_local[local] = spec
        return by_local

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
            target = _resolve_type(base, imports, local_types, parent_id)
            if target is not None:
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
    source: bytes,
    rel: str,
    batch: FactBatch,
) -> None:
    """Emit CALLS for precisely-resolvable ``call_expression`` sites in a body."""
    siblings = type_methods.get(type_id or "", set())
    stack = list(body.named_children)
    while stack:
        n = stack.pop()
        if n.type in _CALL_SCOPE_STOP:
            continue
        if n.type == "call_expression":
            fn = n.child_by_field_name("function")
            target = _resolve_callee(fn, type_id, siblings, local_funcs, imports, rel, source)
            if target is not None:
                batch.add_edge(Edge(caller, target, EdgeKind.CALLS, Provenance(rel, n.start_point[0] + 1)))
        stack.extend(n.named_children)


def _resolve_callee(
    fn: TSNode | None,
    type_id: str | None,
    siblings: set[str],
    local_funcs: dict[str, str],
    imports: dict[str, str],
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
        if obj.type == "identifier":  # ns.func() through an imported namespace
            oname = _text(obj, source)
            if oname in imports:
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


def _imported_locals(import_stmt: TSNode, source: bytes) -> list[str]:
    """Local binding names introduced by an import (default, namespace, named/aliased)."""
    locals_: list[str] = []
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
            elif child.type == "named_imports":
                for spec in child.named_children:
                    if spec.type != "import_specifier":
                        continue
                    alias = spec.child_by_field_name("alias")
                    name = alias if alias is not None else spec.child_by_field_name("name")
                    if name is not None:
                        locals_.append(_text(name, source))
    return [n for n in locals_ if n]


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


def _resolve_type(base: str, imports: dict[str, str], local_types: set[str], module_id: str) -> str | None:
    """Resolve a base type name to a node id (precision-first, else None)."""
    name = base.split("<", 1)[0].strip()  # drop generics: Repo<T> → Repo
    if not name or "." in name:  # namespaced base (ns.Base) — won't second-guess
        return None
    if name in imports:  # imported symbol → external node keyed by its module specifier
        return f"ts:{imports[name]}:{name}"
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
