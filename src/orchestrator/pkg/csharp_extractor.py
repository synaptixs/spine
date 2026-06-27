"""C# / .NET front-end for the PKG extractor (Track 1: a fourth language).

Maps C# source onto the same universal ``facts`` vocabulary the Python/Java/TS
extractors use — so the knowledge graph stays language-neutral. Parsing is via
tree-sitter (accurate ASTs), an OPTIONAL dependency: install the ``csharp`` extra
(``uv pip install 'synaptixs-spine[csharp]'``). The import is lazy so the base
install stays stdlib-only and importing this module never fails.

Emits the high-confidence declaration subset, precision-first like the Java
front-end: ``Module`` (the file, named by its namespace), ``Type`` (class /
interface / struct / enum / record / delegate), ``Function`` (method /
constructor / operator), ``Field`` (field / property / event / enum member /
positional record param); ``IMPORTS`` (``using``), ``CONTAINS``, and
``IMPLEMENTS`` (base list) edges. ``CALLS`` is intentionally NOT emitted yet —
C# call resolution needs overload/type inference, and a guessed edge would poison
grounding (Phase 1.3).

Node ids are namespace-qualified (``csharp:Namespace.Type``) so partial classes
split across files collapse onto one node.
"""

from __future__ import annotations

import re
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

        self._usings(tree.root_node, module_id, source, rel, batch)
        self._walk(tree.root_node.named_children, module_id, "", source, rel, batch)
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
                    self._walk(body.named_children, module_id, ns, source, rel, batch)
            elif node.type in _TYPE_DECLS:
                self._emit_type(node, module_id, None, current_ns, source, rel, batch)

    def _emit_type(
        self,
        node: TSNode,
        module_id: str,
        parent_type_id: str | None,
        namespace: str,
        source: bytes,
        rel: str,
        batch: FactBatch,
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
                    self._add_member(type_id, mname, NodeKind.FUNCTION, mline, rel, batch)
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
                self._emit_type(member, module_id, type_id, namespace, source, rel, batch)

    @staticmethod
    def _add_member(type_id: str, name: str, kind: NodeKind, line: int, rel: str, batch: FactBatch) -> None:
        mid = f"{type_id}.{name}"
        batch.add_node(Node(mid, kind, name, "csharp", Provenance(rel, line)))
        batch.add_edge(Edge(type_id, mid, EdgeKind.CONTAINS, Provenance(rel, line)))


def _join_ns(prefix: str, name: str) -> str:
    if prefix and name:
        return f"{prefix}.{name}"
    return name or prefix


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


def _field_text(node: TSNode, field: str, source: bytes) -> str:
    child = node.child_by_field_name(field)
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
