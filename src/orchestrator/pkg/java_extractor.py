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
and ``IMPLEMENTS`` (extends + implements) edges. ``CALLS`` is emitted only where
the callee resolves precisely (a second pass over method bodies): unqualified /
``this.`` calls to a sibling method, and ``Type.method()`` calls whose ``Type``
resolves via imports or the same package. Instance calls on a typed variable
(``obj.method()``) are skipped — they'd need type inference, and a guessed edge
poisons grounding. Overloads collapse onto one id (no arity in ids).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from orchestrator.pkg.extractor import rel_module_name
from orchestrator.pkg.facts import Edge, EdgeKind, FactBatch, Node, NodeKind, Provenance

if TYPE_CHECKING:
    from tree_sitter import Node as TSNode

_PACKAGE_RE = re.compile(r"^\s*package\s+([\w.]+)\s*;", re.M)
_TYPE_DECLS = frozenset(
    {"class_declaration", "interface_declaration", "enum_declaration", "record_declaration"}
)


class JavaExtractor:
    """Java front-end (tree-sitter). Install the ``java`` extra to use it."""

    language: str = "java"
    suffixes: tuple[str, ...] = (".java",)

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
    ) -> dict[str, str]:
        """Emit IMPORTS edges; return {SimpleName: fully.qualified.Name}."""
        by_simple: dict[str, str] = {}
        for node in root.named_children:
            if node.type != "import_declaration":
                continue
            fqn = _text(node.named_children[-1], source) if node.named_children else ""
            if not fqn or fqn.endswith("*"):
                continue
            tid = f"java:{fqn}"
            batch.add_node(Node(tid, NodeKind.MODULE, fqn, "java", external=True))
            batch.add_edge(Edge(module_id, tid, EdgeKind.IMPORTS, Provenance(rel, node.start_point[0] + 1)))
            by_simple[fqn.rsplit(".", 1)[-1]] = fqn
        return by_simple

    def _emit_type(
        self,
        node: TSNode,
        parent_id: str,
        package: str,
        imports: dict[str, str],
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

        for base in _supertypes(node, source):
            target = self._resolve_type(base, package, imports)
            if target is not None:
                batch.add_edge(Edge(type_id, target, EdgeKind.IMPLEMENTS, Provenance(rel, line)))

        body = node.child_by_field_name("body")
        if body is None:
            return
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
                    mbody = member.child_by_field_name("body")
                    if mbody is not None:
                        funcs.append((fid, type_id, mbody))
            elif member.type == "field_declaration":
                for fname in _field_names(member, source):
                    fid = f"{type_id}.{fname}"
                    batch.add_node(Node(fid, NodeKind.FIELD, fname, "java", Provenance(rel, mline)))
                    batch.add_edge(Edge(type_id, fid, EdgeKind.CONTAINS, Provenance(rel, mline)))
            elif member.type in _TYPE_DECLS:
                self._emit_type(member, type_id, package, imports, source, rel, batch, funcs, type_methods)

    def _calls(
        self,
        caller: str,
        type_id: str,
        body: TSNode,
        type_methods: dict[str, set[str]],
        imports: dict[str, str],
        package: str,
        source: bytes,
        rel: str,
        batch: FactBatch,
    ) -> None:
        """Emit CALLS for precisely-resolvable ``method_invocation`` sites in a body."""
        siblings = type_methods.get(type_id, set())
        stack = list(body.named_children)
        while stack:
            n = stack.pop()
            if n.type in _TYPE_DECLS:
                continue  # nested/local class — a separate scope, not this method's calls
            if n.type == "method_invocation":
                target = self._resolve_call(n, type_id, siblings, imports, package, source)
                if target is not None:
                    batch.add_edge(
                        Edge(caller, target, EdgeKind.CALLS, Provenance(rel, n.start_point[0] + 1))
                    )
            stack.extend(n.named_children)

    def _resolve_call(
        self,
        inv: TSNode,
        type_id: str,
        siblings: set[str],
        imports: dict[str, str],
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

    def _resolve_type(self, simple_or_fqn: str, package: str, imports: dict[str, str]) -> str | None:
        """Resolve a base type name to a node id (precision-first, else None)."""
        name = simple_or_fqn.split("<", 1)[0].strip()  # drop generics: List<T> → List
        if not name or "." in name:  # a qualified base we won't second-guess
            return f"java:{name}" if "." in name else None
        if name in imports:
            return f"java:{imports[name]}"
        if package:  # same-package sibling type
            return f"java:{package}.{name}"
        return None


def _supertypes(node: TSNode, source: bytes) -> list[str]:
    """The extends + implements type names of a class/interface declaration."""
    out: list[str] = []
    superclass = node.child_by_field_name("superclass")
    if superclass is not None:
        out.extend(_type_names(superclass, source))
    interfaces = node.child_by_field_name("interfaces")
    if interfaces is not None:
        out.extend(_type_names(interfaces, source))
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
