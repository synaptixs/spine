"""What the pkg walkers skip lives in a leaf module, so `clang_includes` need not import the extractor.

CodeQL's `py/cyclic-import` flagged `clang_includes` importing `pkg.extractor` (alert 319, a re-fingerprint
of 244 after B63 edited the line), and a Code-scanning comment at Note severity blocks a release promotion
until its thread is resolved. The three names `clang_includes` took from the extractor — the ignore set and
two directory predicates — have no reason to live beside the language front-ends, so they live in
`pkg/walk_rules.py`, which imports nothing from the package; the extractor re-exports them, so every other
importer (`catalog`, `sdlc`, `knowledge`, the docs walker) is untouched. No behaviour change.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from orchestrator.pkg import extractor

_PKG = Path(extractor.__file__).parent
_NAMES = ["DEFAULT_IGNORE_DIRS", "PKG_IGNORE_DIRS", "is_dotnet_output_dir", "is_nested_repo"]


def _imported_modules(path: Path) -> set[str]:
    """Every module a file imports, at any depth (a function-local import still counts to CodeQL)."""
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module if not node.level else f"relative.{node.module}")
    return found


@pytest.mark.parametrize("name", _NAMES)
def test_the_extractor_still_exports_what_every_other_module_imports_from_it(name: str) -> None:
    from orchestrator.pkg import walk_rules

    assert getattr(extractor, name) is getattr(walk_rules, name)
    assert name in extractor.__all__


def test_the_leaf_imports_nothing_from_the_package() -> None:
    leaf = _imported_modules(_PKG / "walk_rules.py")
    assert not {m for m in leaf if m.startswith("orchestrator")}, leaf


def test_include_admission_no_longer_imports_the_extractor() -> None:
    """The cycle, pinned: `clang_includes` -> `extractor` -> ... -> `clang_includes`."""
    assert "orchestrator.pkg.extractor" not in _imported_modules(_PKG / "clang_includes.py")


def test_the_rules_are_unchanged() -> None:
    from orchestrator.pkg.walk_rules import DEFAULT_IGNORE_DIRS, PKG_IGNORE_DIRS

    assert "bin" in DEFAULT_IGNORE_DIRS and "bin" not in PKG_IGNORE_DIRS
    assert DEFAULT_IGNORE_DIRS - {"bin"} == PKG_IGNORE_DIRS
    assert {
        "node_modules",
        "vendor",
        "Pods",
        "episteme",
        "memory-bank",
        "target",
        "obj",
    } <= DEFAULT_IGNORE_DIRS
