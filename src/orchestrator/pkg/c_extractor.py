"""C front-end for the PKG extractor (Track 2: a fifth language, new model).

C is procedural — no classes, namespaces, inheritance, or templates — so the model
the Python/Java/TS/C# front-ends use (a type that contains its methods) doesn't
apply. The unit here is the **translation unit (the file)**: the module contains
free functions, types, and globals directly.

Maps C source onto the universal ``facts`` vocabulary. Parsing is via tree-sitter
(``tree-sitter-c``), an OPTIONAL dependency — install the ``c`` extra. The import is
lazy so the base install stays stdlib-only.

Emitted (precision-first):
- ``Module`` — the file (``.c`` or ``.h``), keyed by its repo-relative path.
- ``Type`` — ``struct`` / ``union`` / ``enum`` (and ``typedef`` of one); ``Field`` —
  struct/union members, enum constants, and file/global variables.
- ``Function`` — free functions. **Header/source merge:** a node is keyed by name
  (C has external linkage — a name is globally unique), so a prototype in a ``.h``
  and the definition in a ``.c`` collapse onto one node. A prototype is emitted as
  an ``external`` placeholder and the definition as grounded, so the definition's
  provenance always wins (the FactBatch dedup upgrades the placeholder), regardless
  of file order — and a header-only declaration (never defined in-repo) correctly
  stays external. ``static`` symbols have internal linkage, so they're keyed by
  ``file::name`` to avoid merging same-named statics across files.
- ``IMPORTS`` — ``#include``. A ``"local.h"`` include is resolved in-repo (relative
  to the including file, then the repo root) → a grounded edge to that file's
  module; a ``<system.h>`` include (or an unresolved one) → an external module node.
- ``CONTAINS`` — module → function / type / global, and type → member.
- ``CALLS`` — from each call expression to the called function (name-keyed, so
  cross-file calls resolve; C has no overloading). Unresolved externals (libc,
  macros) point at an ungrounded id — a deliberate, documented trade-off.
- ``REFERENCES`` — a struct/union member whose type is another struct/union (the C
  data edge), name-keyed like CALLS.

Preprocessor caveat: parsing is pre-expansion. Heavy macro use yields partial facts
(we never run ``cpp``); this is documented, not worked around.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from orchestrator.pkg.extractor import rel_module_name
from orchestrator.pkg.facts import Edge, EdgeKind, FactBatch, Node, NodeKind, Provenance

if TYPE_CHECKING:
    from tree_sitter import Node as TSNode

_TYPE_SPECIFIERS = ("struct_specifier", "union_specifier", "enum_specifier")
# Preprocessor conditionals (notably the ``#ifndef`` include guard) wrap the real
# declarations; flatten them so guarded types/functions are still seen.
_PREPROC_CONTAINERS = ("preproc_ifdef", "preproc_if", "preproc_else", "preproc_elif")
# Declarator wrappers around the actual name (a returned/declared pointer, array, …).
_DECLARATOR_WRAPPERS = (
    "pointer_declarator",
    "array_declarator",
    "init_declarator",
    "function_declarator",
    "parenthesized_declarator",
)


class CExtractor:
    """C front-end (tree-sitter). Install the ``c`` extra to use it."""

    language: str = "c"
    suffixes: tuple[str, ...] = (".c", ".h")

    def module_name(self, path: Path, root: Path) -> str:
        # The translation unit is the file; its name is the repo-relative path.
        return rel_module_name(path, root)

    def extract(self, *, path: Path, module: str, rel: str) -> FactBatch:
        parser = _c_parser()
        source = path.read_bytes()
        tree = parser.parse(source)
        batch = FactBatch()
        module_id = f"c:{module}" if module else f"c:{rel}"
        batch.add_node(Node(module_id, NodeKind.MODULE, module or rel, "c", Provenance(rel, 1)))

        top_level = list(_iter_top_level(tree.root_node))
        # Pass 1: declarations → nodes + CONTAINS; record this file's function ids so a
        # call to a local `static` function resolves to its file-scoped id (not global).
        local_funcs: dict[str, str] = {}
        for node in top_level:
            self._top_level(node, module_id, path, source, rel, batch, local_funcs)
        # Pass 2: CALLS — walk each definition's body now that local function ids exist.
        for node in top_level:
            if node.type == "function_definition":
                self._calls(node, source, rel, batch, local_funcs)
        return batch

    # --- top-level dispatch --------------------------------------------------

    def _top_level(
        self,
        node: TSNode,
        module_id: str,
        path: Path,
        source: bytes,
        rel: str,
        batch: FactBatch,
        local_funcs: dict[str, str],
    ) -> None:
        if node.type == "preproc_include":
            self._include(node, module_id, path, source, rel, batch)
        elif node.type == "type_definition":
            self._typedef(node, module_id, source, rel, batch)
        elif node.type in _TYPE_SPECIFIERS:
            name = _field_text(node, "name", source)
            if name and _has_body(node):
                self._emit_type(node, name, module_id, source, rel, batch)
        elif node.type == "function_definition":
            self._function(node, module_id, source, rel, batch, local_funcs, is_definition=True)
        elif node.type == "declaration":
            self._declaration(node, module_id, source, rel, batch, local_funcs)

    # --- includes ------------------------------------------------------------

    def _include(
        self, node: TSNode, module_id: str, path: Path, source: bytes, rel: str, batch: FactBatch
    ) -> None:
        target = node.child_by_field_name("path")
        if target is None:
            return
        line = node.start_point[0] + 1
        if target.type == "system_lib_string":
            name = _text(target, source).strip("<>")
            tid = f"c:{name}"
            batch.add_node(Node(tid, NodeKind.MODULE, name, "c", external=True))
            batch.add_edge(Edge(module_id, tid, EdgeKind.IMPORTS, Provenance(rel, line)))
            return
        # A "quoted" include: resolve in-repo (relative to this file, then repo root).
        raw = _string_content(target, source)
        if not raw:
            return
        resolved = _resolve_include(path, rel, raw)
        if resolved is not None:
            tid = f"c:{resolved}"
            batch.add_node(Node(tid, NodeKind.MODULE, resolved, "c", Provenance(resolved, 1)))
        else:
            tid = f"c:{raw}"
            batch.add_node(Node(tid, NodeKind.MODULE, raw, "c", external=True))
        batch.add_edge(Edge(module_id, tid, EdgeKind.IMPORTS, Provenance(rel, line)))

    # --- types ---------------------------------------------------------------

    def _typedef(self, node: TSNode, module_id: str, source: bytes, rel: str, batch: FactBatch) -> None:
        spec = next((c for c in node.named_children if c.type in _TYPE_SPECIFIERS), None)
        typedef_name = _last_type_identifier(node, source)
        if spec is not None:
            name = typedef_name or _field_text(spec, "name", source)
            if name:
                self._emit_type(spec, name, module_id, source, rel, batch)

    def _emit_type(
        self, spec: TSNode, name: str, module_id: str, source: bytes, rel: str, batch: FactBatch
    ) -> None:
        type_id = f"c:{name}"
        line = spec.start_point[0] + 1
        batch.add_node(Node(type_id, NodeKind.TYPE, name, "c", Provenance(rel, line, spec.end_point[0] + 1)))
        batch.add_edge(Edge(module_id, type_id, EdgeKind.CONTAINS, Provenance(rel, line)))

        body = spec.child_by_field_name("body")
        if body is None:
            return
        if spec.type == "enum_specifier":
            for enumerator in body.named_children:
                if enumerator.type == "enumerator":
                    ename = _field_text(enumerator, "name", source)
                    if ename:
                        self._add_member(type_id, ename, enumerator.start_point[0] + 1, rel, batch)
            return
        for field in body.named_children:
            if field.type != "field_declaration":
                continue
            fline = field.start_point[0] + 1
            member_type = _member_type_name(field, source)
            for mname in _declared_names(field, source):
                self._add_member(type_id, mname, fline, rel, batch)
            # REFERENCES: a member whose type is another struct/union (the C data edge).
            if member_type is not None and member_type != name:
                batch.add_edge(Edge(type_id, f"c:{member_type}", EdgeKind.REFERENCES, Provenance(rel, fline)))

    @staticmethod
    def _add_member(type_id: str, name: str, line: int, rel: str, batch: FactBatch) -> None:
        mid = f"{type_id}.{name}"
        batch.add_node(Node(mid, NodeKind.FIELD, name, "c", Provenance(rel, line)))
        batch.add_edge(Edge(type_id, mid, EdgeKind.CONTAINS, Provenance(rel, line)))

    # --- functions + globals -------------------------------------------------

    def _function(
        self,
        node: TSNode,
        module_id: str,
        source: bytes,
        rel: str,
        batch: FactBatch,
        local_funcs: dict[str, str],
        *,
        is_definition: bool,
    ) -> str | None:
        fdeclr = _function_declarator(node.child_by_field_name("declarator"))
        if fdeclr is None:
            return None
        name = _declarator_name(fdeclr, source)
        if not name:
            return None
        static = _is_static(node, source)
        fid = f"c:{rel}::{name}" if static else f"c:{name}"
        line = node.start_point[0] + 1
        # A prototype (declaration) is a placeholder; the definition is grounded, so the
        # definition's provenance wins on merge no matter which file is parsed first.
        batch.add_node(
            Node(fid, NodeKind.FUNCTION, name, "c", Provenance(rel, line), external=not is_definition)
        )
        batch.add_edge(Edge(module_id, fid, EdgeKind.CONTAINS, Provenance(rel, line)))
        local_funcs[name] = fid
        return fid

    def _declaration(
        self,
        node: TSNode,
        module_id: str,
        source: bytes,
        rel: str,
        batch: FactBatch,
        local_funcs: dict[str, str],
    ) -> None:
        declarators = [c for c in node.named_children if c.type in ("identifier", *_DECLARATOR_WRAPPERS)]
        type_node = node.child_by_field_name("type")
        # A struct/union/enum definition written as a declaration (`struct S { … };`).
        if not declarators and type_node is not None and type_node.type in _TYPE_SPECIFIERS:
            name = _field_text(type_node, "name", source)
            if name and _has_body(type_node):
                self._emit_type(type_node, name, module_id, source, rel, batch)
            return
        static = _is_static(node, source)
        extern = _has_storage(node, source, "extern")
        for d in declarators:
            if _function_declarator(d) is not None:  # a function prototype
                self._function(node, module_id, source, rel, batch, local_funcs, is_definition=False)
                continue
            vname = _declarator_name(d, source)
            if not vname:
                continue
            vid = f"c:{rel}::{vname}" if static else f"c:{vname}"
            line = d.start_point[0] + 1
            batch.add_node(Node(vid, NodeKind.FIELD, vname, "c", Provenance(rel, line), external=extern))
            batch.add_edge(Edge(module_id, vid, EdgeKind.CONTAINS, Provenance(rel, line)))

    # --- calls (2.3) ---------------------------------------------------------

    def _calls(
        self, fdef: TSNode, source: bytes, rel: str, batch: FactBatch, local_funcs: dict[str, str]
    ) -> None:
        fdeclr = _function_declarator(fdef.child_by_field_name("declarator"))
        if fdeclr is None:
            return
        name = _declarator_name(fdeclr, source)
        caller = local_funcs.get(name)
        if caller is None:
            return
        body = fdef.child_by_field_name("body")
        if body is None:
            return
        for callee, line in _calls_in(body, source):
            # A local static callee keeps its file-scoped id; everything else is global.
            target = local_funcs.get(callee, f"c:{callee}")
            batch.add_edge(Edge(caller, target, EdgeKind.CALLS, Provenance(rel, line)))


# --- helpers ---------------------------------------------------------------


def _iter_top_level(root: TSNode) -> list[TSNode]:
    """Top-level declarations, descending through preprocessor conditionals so the
    contents of an ``#ifndef`` include guard are treated as top-level."""
    out: list[TSNode] = []
    for child in root.named_children:
        if child.type in _PREPROC_CONTAINERS:
            out.extend(_iter_top_level(child))
        else:
            out.append(child)
    return out


def _resolve_include(path: Path, rel: str, include: str) -> str | None:
    """Resolve a ``"quoted"`` include to a repo-relative path of an EXISTING in-repo
    file, or ``None`` (→ external). Tries, in order: relative to the including file,
    relative to the repo root, then — for a bare ``"foo.h"`` reached via an ``-I``
    include directory — a uniquely-named header anywhere in the repo. The root is
    derived by stripping ``rel`` from the file's absolute path."""
    abs_path = path.resolve()
    root = abs_path
    for _ in Path(rel).parts:
        root = root.parent
    for base in (abs_path.parent, root):
        cand = (base / include).resolve()
        try:
            within = cand.relative_to(root)
        except ValueError:
            continue
        if cand.is_file():
            return within.as_posix()
    # Include-dir convention: a header named like the include, unique in the repo.
    return _header_index(root).get(Path(include).name)


_HEADER_INDEX_CACHE: dict[str, dict[str, str]] = {}


def _header_index(root: Path) -> dict[str, str]:
    """``{basename: repo-relative-path}`` for every ``.h`` whose basename is UNIQUE in
    the repo (built once per root, cached). Ambiguous basenames are dropped so a bare
    include never resolves to the wrong file (precision-first)."""
    key = str(root)
    cached = _HEADER_INDEX_CACHE.get(key)
    if cached is not None:
        return cached
    seen: dict[str, str | None] = {}
    for p in root.rglob("*.h"):
        try:
            rel = p.relative_to(root).as_posix()
        except ValueError:
            continue
        seen[p.name] = rel if p.name not in seen else None  # None marks ambiguous
    idx = {b: r for b, r in seen.items() if r is not None}
    _HEADER_INDEX_CACHE[key] = idx
    return idx


def _calls_in(node: TSNode, source: bytes) -> list[tuple[str, int]]:
    """``(callee_name, line)`` for each direct call (``foo(...)``) under ``node``."""
    out: list[tuple[str, int]] = []
    stack = list(node.named_children)
    while stack:
        n = stack.pop()
        if n.type == "call_expression":
            fn = n.child_by_field_name("function")
            if fn is not None and fn.type == "identifier":
                out.append((_text(fn, source), n.start_point[0] + 1))
        stack.extend(n.named_children)
    return out


def _function_declarator(node: TSNode | None) -> TSNode | None:
    """Descend pointer/parenthesized declarators to the ``function_declarator``."""
    while node is not None:
        if node.type == "function_declarator":
            return node
        if node.type in ("pointer_declarator", "parenthesized_declarator"):
            node = node.child_by_field_name("declarator")
        else:
            return None
    return None


def _declarator_name(node: TSNode | None, source: bytes) -> str:
    """The identifier name inside a (possibly wrapped) declarator."""
    while node is not None:
        if node.type in ("identifier", "field_identifier", "type_identifier"):
            return _text(node, source)
        nxt = node.child_by_field_name("declarator")
        if nxt is None:
            # function_declarator's name is its `declarator` child; fall back to first id.
            ids = [c for c in node.named_children if c.type in ("identifier", "field_identifier")]
            return _text(ids[0], source) if ids else ""
        node = nxt
    return ""


def _declared_names(field: TSNode, source: bytes) -> list[str]:
    """Member names declared in a ``field_declaration`` (handles ``int x, y;``)."""
    names: list[str] = []
    for child in field.named_children:
        if child.type in ("field_identifier", "identifier"):
            names.append(_text(child, source))
        elif child.type in _DECLARATOR_WRAPPERS:
            nm = _declarator_name(child, source)
            if nm:
                names.append(nm)
    return [n for n in names if n]


def _member_type_name(field: TSNode, source: bytes) -> str | None:
    """The struct/union type a member refers to (for REFERENCES), or ``None`` for a
    primitive / non-aggregate type."""
    ty = field.child_by_field_name("type")
    if ty is None:
        return None
    if ty.type == "type_identifier":
        return _text(ty, source)
    if ty.type in ("struct_specifier", "union_specifier"):
        nm = _field_text(ty, "name", source)
        return nm or None
    return None


def _last_type_identifier(node: TSNode, source: bytes) -> str:
    """The typedef alias — the last top-level ``type_identifier`` child."""
    names = [c for c in node.named_children if c.type == "type_identifier"]
    return _text(names[-1], source) if names else ""


def _has_body(spec: TSNode) -> bool:
    return spec.child_by_field_name("body") is not None


def _is_static(node: TSNode, source: bytes) -> bool:
    return _has_storage(node, source, "static")


def _has_storage(node: TSNode, source: bytes, keyword: str) -> bool:
    return any(
        c.type == "storage_class_specifier" and _text(c, source) == keyword for c in node.named_children
    )


def _string_content(node: TSNode, source: bytes) -> str:
    for child in node.named_children:
        if child.type == "string_content":
            return _text(child, source)
    return _text(node, source).strip('"')


def _field_text(node: TSNode, field: str, source: bytes) -> str:
    child = node.child_by_field_name(field)
    return _text(child, source) if child is not None else ""


def _text(node: TSNode | None, source: bytes) -> str:
    if node is None:
        return ""
    return source[node.start_byte : node.end_byte].decode("utf-8", "replace").strip()


def _c_parser() -> Any:
    try:
        import tree_sitter_c
        from tree_sitter import Language, Parser
    except ImportError as exc:  # pragma: no cover - exercised only without the extra
        raise RuntimeError(
            "C extraction needs tree-sitter; install the extra: "
            "uv pip install 'tree-sitter>=0.21' 'tree-sitter-c>=0.21'"
        ) from exc
    language = Language(tree_sitter_c.language())
    try:
        return Parser(language)
    except TypeError:  # older tree-sitter API
        parser = Parser()
        parser.language = language
        return parser


__all__ = ["CExtractor"]
