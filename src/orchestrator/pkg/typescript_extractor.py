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
CALLS is intentionally NOT emitted — TS call resolution needs type/alias
inference, and a guessed edge would poison grounding; better to omit than emit
noise (same stance as the Java front-end).
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
        for node in decls:
            if node is None:
                continue
            if node.type in _TYPE_DECLS:
                self._emit_type(node, module_id, imports, local_types, source, rel, batch)
            elif node.type == "function_declaration":
                _emit_function(node, module_id, source, rel, batch)
            elif node.type in _FUNC_CONST_DECLS:
                self._emit_const_functions(node, module_id, source, rel, batch)
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
            elif member.type in ("public_field_definition", "property_signature"):
                fname = _field_text(member, "name", source)
                if fname:
                    fid = f"{type_id}.{fname}"
                    batch.add_node(Node(fid, NodeKind.FIELD, fname, "typescript", Provenance(rel, mline)))
                    batch.add_edge(Edge(type_id, fid, EdgeKind.CONTAINS, Provenance(rel, mline)))

    def _emit_const_functions(
        self, node: TSNode, module_id: str, source: bytes, rel: str, batch: FactBatch
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


def _emit_function(node: TSNode, module_id: str, source: bytes, rel: str, batch: FactBatch) -> None:
    name = _field_text(node, "name", source)
    if not name:
        return
    line = node.start_point[0] + 1
    fid = f"{module_id}.{name}"
    batch.add_node(Node(fid, NodeKind.FUNCTION, name, "typescript", Provenance(rel, line)))
    batch.add_edge(Edge(module_id, fid, EdgeKind.CONTAINS, Provenance(rel, line)))


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
