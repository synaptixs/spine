#!/usr/bin/env python
"""Parse every file of a language with its grammar; report the recall ceiling.

Independent of a front-end's extraction logic: this asks only "how much of this real
codebase does the grammar itself parse cleanly?" — files with an ``ERROR`` node, lines
inside ``ERROR`` spans, and a frequency count of every named CST node kind, so a
declaration-shaped kind (``sub`` vs. ``Function``, say) can be eyeballed against a
independent count like ``grep -c '^sub '``. This is what a D1 grammar-choice decision is
made from, and — for a grammar that is error-tolerant on hard/legacy source (tree-sitter
generally is) — the number a "P1 exit criteria" recall claim should be measured against
rather than asserted from labels.

Not tied to any one language: the grammar is passed by importable module name, so every
future language-support track reuses this for its own D1 row (perl-support-roadmap.md §8.3).

Usage::

    uv run --frozen python scripts/parse-census.py tree_sitter_perl /path/to/repo \\
        --suffix .pl .pm .t

Stdlib + ``tree_sitter`` only (no dependency on the rest of ``orchestrator``), so it can run
standalone against a scratch clone the same way the D1 grammar probe did.
"""

from __future__ import annotations

import argparse
import ctypes
import importlib
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

_IGNORE_DIRS = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        "node_modules",
        "vendor",
        ".venv",
        "venv",
        "dist",
        "build",
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
    }
)


def _load_language(module_name: str, language_attr: str) -> Any:
    from tree_sitter import Language

    mod = importlib.import_module(module_name)
    raw = getattr(mod, language_attr)()
    try:
        return Language(raw)
    except OverflowError:
        # Windows-only: some bindings return a bare pointer-sized int
        # (`PyLong_FromVoidPtr`) rather than a PyCapsule, and tree-sitter's Windows
        # binding parses a bare int via a 32-bit `unsigned long` format code, which
        # overflows a real 64-bit pointer. Linux/macOS (8-byte `unsigned long`) never
        # hit this. Wrap it in a capsule ourselves rather than fail a census run over a
        # platform quirk in a third-party binding.
        pythonapi = ctypes.pythonapi
        pythonapi.PyCapsule_New.restype = ctypes.py_object
        pythonapi.PyCapsule_New.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_void_p]
        capsule = pythonapi.PyCapsule_New(ctypes.c_void_p(raw), b"tree_sitter.Language", None)
        return Language(capsule)


def _make_parser(language: Any) -> Any:
    from tree_sitter import Parser

    try:
        return Parser(language)
    except TypeError:  # older tree-sitter API
        parser = Parser()
        parser.language = language
        return parser


def _iter_files(root: Path, suffixes: tuple[str, ...]) -> list[Path]:
    out: list[Path] = []
    stack = [root]
    while stack:
        d = stack.pop()
        for entry in sorted(d.iterdir()):
            if entry.is_dir():
                if entry.name not in _IGNORE_DIRS and not entry.name.startswith("."):
                    stack.append(entry)
            elif entry.suffix in suffixes:
                out.append(entry)
    return sorted(out)


def _error_lines(root_node: Any) -> set[int]:
    lines: set[int] = set()
    stack = [root_node]
    while stack:
        n = stack.pop()
        if n.type == "ERROR":
            lines.update(range(n.start_point[0] + 1, n.end_point[0] + 2))
        stack.extend(n.children)
    return lines


def _node_kind_counts(root_node: Any, counts: Counter[str]) -> None:
    stack = [root_node]
    while stack:
        n = stack.pop()
        if n.is_named:
            counts[n.type] += 1
        stack.extend(n.children)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("grammar_module", help="importable tree-sitter grammar module, e.g. tree_sitter_perl")
    ap.add_argument("dir", type=Path, help="directory to scan (a shallow clone, ideally .git-less)")
    ap.add_argument("--suffix", nargs="+", required=True, help="file suffixes to parse, e.g. .pl .pm .t")
    ap.add_argument(
        "--language-attr", default="language", help="the module's language-getter (default: language)"
    )
    ap.add_argument("--json", action="store_true", help="emit machine-readable JSON instead of a report")
    args = ap.parse_args()

    language = _load_language(args.grammar_module, args.language_attr)
    parser = _make_parser(language)

    files = _iter_files(args.dir, tuple(args.suffix))
    files_with_error: list[str] = []
    total_error_lines = 0
    total_lines = 0
    kind_counts: Counter[str] = Counter()

    for f in files:
        source = f.read_bytes()
        tree = parser.parse(source)
        rel = f.relative_to(args.dir).as_posix()
        total_lines += source.count(b"\n") + 1
        if tree.root_node.has_error:
            files_with_error.append(rel)
            total_error_lines += len(_error_lines(tree.root_node))
        _node_kind_counts(tree.root_node, kind_counts)

    result = {
        "grammar_module": args.grammar_module,
        "dir": str(args.dir),
        "files_scanned": len(files),
        "files_with_error": len(files_with_error),
        "files_with_error_list": files_with_error,
        "total_lines": total_lines,
        "lines_inside_error_spans": total_error_lines,
        "node_kind_counts": dict(kind_counts.most_common()),
    }

    if args.json:
        print(json.dumps(result, indent=2))
        return 0

    print(f"grammar:            {args.grammar_module}")
    print(f"scanned:            {args.dir}")
    print(f"files matched:      {len(files)}")
    print(f"files with ERROR:   {len(files_with_error)}")
    print(f"lines in ERROR span:{total_error_lines} / {total_lines}")
    if files_with_error:
        print("files with ERROR:")
        for rel in files_with_error:
            print(f"  - {rel}")
    print("declaration counts by CST kind (top 20):")
    for kind, n in kind_counts.most_common(20):
        print(f"  {n:6d}  {kind}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
