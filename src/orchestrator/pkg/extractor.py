"""Code → grounded facts. PKG Layer 1, the *grounded extractor*.

``RepoCodeExtractor`` walks a repository and dispatches each file to a
``LanguageExtractor`` chosen by suffix. The extractors map a language's syntax
tree onto the universal ``facts`` vocabulary — so adding a language is a new
front-end against an unchanged core, never a reshape of the knowledge graph.

``PythonExtractor`` (the only front-end in v0) uses the stdlib ``ast`` and emits:
``Module``/``Type``/``Function`` nodes; ``IMPORTS`` (module→imported symbol),
``CONTAINS`` (module→type, type→method), and best-effort ``CALLS`` edges. Call
resolution is precision-first: it resolves bare-name calls to imports or
module-level defs and ``self.method`` calls to sibling methods, and *skips*
ambiguous attribute chains rather than emit noise.
"""

from __future__ import annotations

import ast
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Protocol

from orchestrator.pkg.facts import Edge, EdgeKind, FactBatch, Node, NodeKind, Provenance

_PY_BUILTINS = frozenset(
    [
        "print",
        "len",
        "sum",
        "range",
        "list",
        "dict",
        "set",
        "tuple",
        "str",
        "int",
        "float",
        "bool",
        "enumerate",
        "zip",
        "map",
        "filter",
        "open",
        "isinstance",
        "issubclass",
        "getattr",
        "setattr",
        "hasattr",
        "delattr",
        "super",
        "min",
        "max",
        "sorted",
        "reversed",
        "any",
        "all",
        "repr",
        "type",
        "vars",
        "id",
        "abs",
        "round",
        "next",
        "iter",
        "format",
        "bytes",
        "frozenset",
        "property",
        "staticmethod",
        "classmethod",
        "callable",
        "hash",
        "ord",
        "chr",
    ]
)

DEFAULT_IGNORE_DIRS = frozenset(
    {
        ".git",
        ".venv",
        "venv",
        "__pycache__",
        "node_modules",
        ".mypy_cache",
        ".ruff_cache",
        ".pytest_cache",
        "build",
        "dist",
        ".tox",
        "obj",  # .NET build output (generated *.g.cs, *.AssemblyInfo.cs)
        "bin",  # .NET build output
    }
)


class LanguageExtractor(Protocol):
    """A per-language front-end mapping source → universal facts."""

    language: str
    suffixes: tuple[str, ...]

    def module_name(self, path: Path, root: Path) -> str:
        """The language's notion of this file's module/namespace name.

        Default is the repo-relative POSIX path; Python collapses it to a
        dotted qualname. Each front-end owns this so the dispatcher stays
        language-agnostic (no Python-specific path logic in the core).
        """
        ...

    def extract(self, *, path: Path, module: str, rel: str) -> FactBatch: ...


def module_qualname(path: Path, root: Path) -> str:
    """Dotted module path relative to ``root`` (``src/`` stripped, ``__init__`` collapsed)."""
    parts = list(path.resolve().relative_to(root.resolve()).parts)
    if parts and parts[0] == "src":
        parts = parts[1:]
    if not parts:
        return ""
    if parts[-1] == "__init__.py":
        parts = parts[:-1]
    elif parts[-1].endswith(".py"):
        parts[-1] = parts[-1][:-3]
    return ".".join(parts)


def rel_module_name(path: Path, root: Path) -> str:
    """Repo-relative POSIX path — the language-agnostic default module name."""
    return path.resolve().relative_to(root.resolve()).as_posix()


class PythonExtractor:
    """Python front-end (stdlib ``ast``)."""

    language: str = "python"
    suffixes: tuple[str, ...] = (".py",)

    def module_name(self, path: Path, root: Path) -> str:
        return module_qualname(path, root)

    def extract(self, *, path: Path, module: str, rel: str) -> FactBatch:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
        batch = FactBatch()
        module_id = f"py:{module}" if module else "py:<root>"
        batch.add_node(Node(module_id, NodeKind.MODULE, module or rel, "python", Provenance(rel, 1)))

        imports = self._collect_imports(tree)
        module_names = self._collect_defs(tree.body, module_id)
        self._emit_body(tree.body, module_id, module_id, imports, module_names, {}, rel, batch)
        return batch

    # ---- pass 1: names available for call resolution --------------------

    def _collect_imports(self, tree: ast.Module) -> dict[str, str]:
        binds: dict[str, str] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    binds[a.asname or a.name.split(".")[0]] = f"py:{a.name}"
            elif isinstance(node, ast.ImportFrom) and node.module:
                for a in node.names:
                    binds[a.asname or a.name] = f"py:{node.module}.{a.name}"
        return binds

    def _collect_defs(self, body: list[ast.stmt], parent_id: str) -> dict[str, str]:
        names: dict[str, str] = {}
        for stmt in body:
            if isinstance(stmt, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
                names[stmt.name] = f"{parent_id}.{stmt.name}"
        return names

    # ---- pass 2: emit nodes + edges -------------------------------------

    def _emit_body(
        self,
        body: list[ast.stmt],
        parent_id: str,
        module_id: str,
        imports: dict[str, str],
        names: dict[str, str],
        class_methods: dict[str, str],
        rel: str,
        batch: FactBatch,
    ) -> None:
        for stmt in body:
            self._emit_stmt(stmt, parent_id, module_id, None, imports, names, class_methods, rel, batch)

    def _emit_stmt(
        self,
        stmt: ast.stmt,
        parent_id: str,
        module_id: str,
        current_func: str | None,
        imports: dict[str, str],
        names: dict[str, str],
        class_methods: dict[str, str],
        rel: str,
        batch: FactBatch,
    ) -> None:
        prov = Provenance(rel, getattr(stmt, "lineno", 1))

        if isinstance(stmt, ast.Import | ast.ImportFrom):
            self._emit_import(stmt, module_id, rel, batch)

        elif isinstance(stmt, ast.ClassDef):
            type_id = f"{parent_id}.{stmt.name}"
            span = Provenance(rel, stmt.lineno, getattr(stmt, "end_lineno", None))
            batch.add_node(Node(type_id, NodeKind.TYPE, stmt.name, "python", span))
            batch.add_edge(Edge(parent_id, type_id, EdgeKind.CONTAINS, prov))
            methods = self._collect_defs(stmt.body, type_id)
            self._emit_fields(stmt, type_id, methods, imports, names, rel, batch)
            self._emit_bases(stmt, type_id, imports, names, rel, batch)
            self._emit_body(stmt.body, type_id, module_id, imports, names, methods, rel, batch)

        elif isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef):
            func_id = f"{parent_id}.{stmt.name}"
            span = Provenance(rel, stmt.lineno, getattr(stmt, "end_lineno", None))
            batch.add_node(Node(func_id, NodeKind.FUNCTION, stmt.name, "python", span))
            batch.add_edge(Edge(parent_id, func_id, EdgeKind.CONTAINS, prov))
            for inner in stmt.body:
                self._emit_stmt(inner, func_id, module_id, func_id, imports, names, class_methods, rel, batch)

        elif isinstance(
            stmt, ast.If | ast.For | ast.AsyncFor | ast.While | ast.With | ast.AsyncWith | ast.Try
        ):
            # Compound: recurse into sub-bodies; scan the header expressions for calls.
            self._scan_calls(stmt, current_func, imports, names, class_methods, rel, batch, headers_only=True)
            for inner in self._sub_bodies(stmt):
                self._emit_stmt(
                    inner, parent_id, module_id, current_func, imports, names, class_methods, rel, batch
                )

        elif current_func is not None:
            # Simple statement inside a function: attribute its calls to that function.
            self._scan_calls(
                stmt, current_func, imports, names, class_methods, rel, batch, headers_only=False
            )

    def _emit_import(
        self, stmt: ast.Import | ast.ImportFrom, module_id: str, rel: str, batch: FactBatch
    ) -> None:
        prov = Provenance(rel, stmt.lineno)
        if isinstance(stmt, ast.Import):
            targets = [(a.name, f"py:{a.name}") for a in stmt.names]
        else:
            mod = stmt.module or ""
            targets = [(f"{mod}.{a.name}", f"py:{mod}.{a.name}") for a in stmt.names]
        for name, tid in targets:
            batch.add_node(Node(tid, NodeKind.MODULE, name, "python", external=True))
            batch.add_edge(Edge(module_id, tid, EdgeKind.IMPORTS, prov))

    def _emit_fields(
        self,
        cls: ast.ClassDef,
        type_id: str,
        methods: dict[str, str],
        imports: dict[str, str],
        names: dict[str, str],
        rel: str,
        batch: FactBatch,
    ) -> None:
        """Emit a FIELD node per data attribute of the class (CONTAINS-linked).

        Two sources, precision-first: class-body annotated/simple assignments
        (dataclass fields, Pydantic models, class constants, enum members), and
        ``self.<attr> = ...`` assignments anywhere in the class's methods
        (instance attributes set in __init__ etc.). A name that is also a
        method is skipped — methods win, and a field/method id clash would
        otherwise merge in the graph.
        """
        seen: set[str] = set()

        def emit(name: str, lineno: int) -> None:
            if not name or name in seen or name in methods:
                return
            seen.add(name)
            field_id = f"{type_id}.{name}"
            batch.add_node(Node(field_id, NodeKind.FIELD, name, "python", Provenance(rel, lineno)))
            batch.add_edge(Edge(type_id, field_id, EdgeKind.CONTAINS, Provenance(rel, lineno)))

        # 1. Class-body assignments (direct children only — nested scopes aren't fields).
        for node in cls.body:
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                emit(node.target.id, node.lineno)
            elif isinstance(node, ast.Assign):
                for tgt in node.targets:
                    if isinstance(tgt, ast.Name):
                        emit(tgt.id, node.lineno)

        # 2. ``self.<attr> = ...`` in the class's OWN methods. Walk each method
        # but don't descend into nested ClassDefs — their self-attrs belong to
        # the inner class, not this one.
        for node in self._iter_self_assign_nodes(cls.body):
            target_list: list[ast.expr] = (
                [node.target] if isinstance(node, ast.AnnAssign) else list(node.targets)
            )
            for tgt in target_list:
                if (
                    isinstance(tgt, ast.Attribute)
                    and isinstance(tgt.value, ast.Name)
                    and tgt.value.id == "self"
                ):
                    emit(tgt.attr, getattr(node, "lineno", cls.lineno))

    def _iter_self_assign_nodes(self, body: list[ast.stmt]) -> Iterator[ast.Assign | ast.AnnAssign]:
        """Yield Assign/AnnAssign in this class's methods, not nested classes.

        Manual recursion that prunes at ``ClassDef`` boundaries — a nested
        class's ``self`` is a different instance, so its assignments are not
        this class's fields.
        """
        for stmt in body:
            if isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef):
                yield from self._walk_no_classes(stmt.body)

    def _walk_no_classes(self, body: list[ast.stmt]) -> Iterator[ast.Assign | ast.AnnAssign]:
        for stmt in body:
            if isinstance(stmt, ast.ClassDef):
                continue  # prune: don't enter a nested class
            if isinstance(stmt, ast.Assign | ast.AnnAssign):
                yield stmt
            yield from self._walk_no_classes(self._sub_bodies(stmt))

    def _emit_bases(
        self,
        cls: ast.ClassDef,
        type_id: str,
        imports: dict[str, str],
        names: dict[str, str],
        rel: str,
        batch: FactBatch,
    ) -> None:
        """Emit an IMPLEMENTS edge to each base class that resolves.

        Resolution is precision-first (same discipline as call resolution):
        a bare-name base resolves to an import or a module-level def; anything
        ambiguous (attribute-chain bases, unresolved names) is skipped rather
        than guessed.
        """
        prov = Provenance(rel, cls.lineno)
        for base in cls.bases:
            if not isinstance(base, ast.Name):
                continue  # e.g. typing.Generic[...] / pkg.Base — skip, don't guess
            target = imports.get(base.id) or names.get(base.id)
            if target is not None:
                batch.add_edge(Edge(type_id, target, EdgeKind.IMPLEMENTS, prov))

    def _sub_bodies(self, stmt: ast.stmt) -> list[ast.stmt]:
        out: list[ast.stmt] = []
        for attr in ("body", "orelse", "finalbody"):
            out.extend(getattr(stmt, attr, []) or [])
        for handler in getattr(stmt, "handlers", []) or []:
            out.extend(handler.body)
        return out

    def _scan_calls(
        self,
        node: ast.AST,
        current_func: str | None,
        imports: dict[str, str],
        names: dict[str, str],
        class_methods: dict[str, str],
        rel: str,
        batch: FactBatch,
        *,
        headers_only: bool,
    ) -> None:
        if current_func is None:
            return
        # For compound statements, scan only the header expressions (test/iter/items),
        # not the nested bodies (those recurse separately).
        roots: list[ast.AST] = [e for e in self._header_exprs(node)] if headers_only else [node]
        for root in roots:
            for child in ast.walk(root):
                if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                    continue
                if isinstance(child, ast.Call):
                    target = self._resolve_call(child.func, imports, names, class_methods)
                    if target is not None:
                        prov = Provenance(rel, getattr(child, "lineno", 1))
                        batch.add_node(
                            Node(
                                target,
                                NodeKind.FUNCTION,
                                target.rsplit(".", 1)[-1],
                                "python",
                                external=target not in names.values()
                                and target not in class_methods.values(),
                            )
                        )
                        batch.add_edge(Edge(current_func, target, EdgeKind.CALLS, prov))

    def _header_exprs(self, stmt: ast.AST) -> list[ast.expr]:
        out: list[ast.expr] = []
        for attr in ("test", "iter"):
            value = getattr(stmt, attr, None)
            if isinstance(value, ast.expr):
                out.append(value)
        for item in getattr(stmt, "items", []) or []:
            if isinstance(item.context_expr, ast.expr):
                out.append(item.context_expr)
        return out

    def _resolve_call(
        self,
        func: ast.expr,
        imports: dict[str, str],
        names: dict[str, str],
        class_methods: dict[str, str],
    ) -> str | None:
        if isinstance(func, ast.Name):
            if func.id in _PY_BUILTINS:
                return None
            if func.id in imports:
                return imports[func.id]
            if func.id in names:
                return names[func.id]
            return f"py:{func.id}"
        if isinstance(func, ast.Attribute):
            value = func.value
            if isinstance(value, ast.Name):
                if value.id == "self" and func.attr in class_methods:
                    return class_methods[func.attr]
                if value.id in imports:
                    return f"{imports[value.id]}.{func.attr}"
        return None  # ambiguous attribute chain — skip rather than guess


def default_extractors() -> list[LanguageExtractor]:
    """The language front-ends used when none are passed explicitly.

    Always Python (stdlib ``ast``). Java, TypeScript, C#, and C are added **only when
    their tree-sitter grammar is importable** (the ``java`` / ``typescript`` /
    ``csharp`` / ``c`` extras) so the base install stays stdlib-only — this is what makes
    ``understand`` / grounding / ``pkg extract`` multi-language without forcing
    the parser dependency on everyone.
    """
    import importlib.util

    has_tree_sitter = importlib.util.find_spec("tree_sitter") is not None
    extractors: list[LanguageExtractor] = [PythonExtractor()]
    if has_tree_sitter and importlib.util.find_spec("tree_sitter_java"):
        from orchestrator.pkg.java_extractor import JavaExtractor

        extractors.append(JavaExtractor())
    if has_tree_sitter and importlib.util.find_spec("tree_sitter_typescript"):
        from orchestrator.pkg.typescript_extractor import TypeScriptExtractor

        extractors.append(TypeScriptExtractor())
    if has_tree_sitter and importlib.util.find_spec("tree_sitter_c_sharp"):
        from orchestrator.pkg.csharp_extractor import CSharpExtractor

        extractors.append(CSharpExtractor())
    if has_tree_sitter and importlib.util.find_spec("tree_sitter_c"):
        from orchestrator.pkg.c_extractor import CExtractor

        extractors.append(CExtractor())
    if has_tree_sitter and importlib.util.find_spec("tree_sitter_cpp"):
        from orchestrator.pkg.cpp_extractor import CppExtractor

        extractors.append(CppExtractor())
    # SQL uses sqlglot (pure-Python, no tree-sitter) behind the ``sql`` extra.
    if importlib.util.find_spec("sqlglot"):
        from orchestrator.pkg.sql_extractor import SqlExtractor

        extractors.append(SqlExtractor())
    return extractors


class RepoCodeExtractor:
    """Walk a repository → one merged ``FactBatch`` of grounded facts."""

    def __init__(
        self,
        extractors: list[LanguageExtractor] | None = None,
        *,
        ignore_dirs: frozenset[str] = DEFAULT_IGNORE_DIRS,
    ) -> None:
        self._by_suffix: dict[str, LanguageExtractor] = {}
        for ex in extractors if extractors is not None else default_extractors():
            for suffix in ex.suffixes:
                self._by_suffix[suffix] = ex
        self._ignore_dirs = ignore_dirs
        self.skipped: list[str] = []

    def extract(self, root: Path | str) -> FactBatch:
        root_path = Path(root)
        batch = FactBatch()
        for path in self._iter_files(root_path):
            extractor = self._by_suffix.get(path.suffix)
            if extractor is None:
                continue
            rel = path.resolve().relative_to(root_path.resolve()).as_posix()
            try:
                module = extractor.module_name(path, root_path)
                batch.merge(extractor.extract(path=path, module=module, rel=rel))
            except (SyntaxError, UnicodeDecodeError, ValueError):
                self.skipped.append(rel)
        return batch

    def _iter_files(self, root: Path) -> Iterator[Path]:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in self._ignore_dirs and not d.startswith(".")]
            for name in filenames:
                yield Path(dirpath) / name


__all__ = [
    "DEFAULT_IGNORE_DIRS",
    "LanguageExtractor",
    "PythonExtractor",
    "RepoCodeExtractor",
    "module_qualname",
    "rel_module_name",
]
