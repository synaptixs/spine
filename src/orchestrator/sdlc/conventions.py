"""Convention learning (G8): derive a repo's house style from its own code.

Codegen that passes CI still has to *look* like the team wrote it. Rather than
hand-author a style guide (which rots), this derives a compact convention
digest by sampling the repository's existing source and tests — the project's
"derived, not authored" principle applied to style. The digest is rendered
into the codegen prompt so new code matches observed practice: future-imports,
import style, typing, docstrings, line length, and test shape.

Everything here is best-effort and read-only: a malformed file is skipped, and
an empty or unreadable repo yields an empty digest that renders to no prompt
block (codegen then behaves exactly as before).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

# Bound the scan so a large repo can't blow up extraction time/cost.
_MAX_SOURCE_SAMPLE = 40
_MAX_TEST_SAMPLE = 30
# A convention is "observed" only when this fraction of sampled files show it —
# avoids cementing a one-off into a rule.
_PREVALENCE = 0.6
_DEFAULT_LINE_LENGTH = 88  # ruff's own default when pyproject doesn't say


@dataclass(frozen=True)
class RepoConventions:
    """Observed house-style conventions, derived from the repo's own files."""

    future_annotations: bool = False
    absolute_imports: bool = False
    module_docstrings: bool = False
    typed_defs: bool = False
    line_length: int = _DEFAULT_LINE_LENGTH
    test_style: str = ""  # "functions" | "classes" | ""
    top_package: str = ""  # e.g. "orchestrator"
    sampled: int = 0

    @property
    def is_empty(self) -> bool:
        return self.sampled == 0

    def prompt_block(self) -> str:
        """Render the digest as a codegen instruction block, or '' if empty."""
        if self.is_empty:
            return ""
        rules: list[str] = []
        if self.future_annotations:
            rules.append("start every module with `from __future__ import annotations`")
        if self.absolute_imports and self.top_package:
            rules.append(
                f"import first-party code with absolute paths under `{self.top_package}.` "
                "(not relative imports), kept sorted"
            )
        if self.module_docstrings:
            rules.append("give every module a one-line-plus triple-quoted docstring")
        if self.typed_defs:
            rules.append("fully type-annotate every function and method, including `-> None`")
        rules.append(f"keep lines within {self.line_length} characters")
        if self.test_style == "functions":
            rules.append("write tests as module-level `def test_*` functions (not test classes)")
        elif self.test_style == "classes":
            rules.append("group tests in `class Test*` classes, matching the existing suite")
        if not rules:
            return ""
        body = "\n".join(f"- {r}" for r in rules)
        return (
            "REPO CONVENTIONS (observed in this codebase — match them so the change "
            f"reads as team-written):\n{body}\n"
        )


def extract_conventions(root: Path) -> RepoConventions:
    """Derive a ``RepoConventions`` digest by sampling ``root``'s Python files."""
    root = Path(root)
    src_files = _sample(root, "src", _MAX_SOURCE_SAMPLE, want_tests=False)
    if not src_files:
        # Repos without a src/ layout: sample any non-test modules.
        src_files = _sample(root, ".", _MAX_SOURCE_SAMPLE, want_tests=False)
    if not src_files:
        return RepoConventions()

    bodies = [_read(f) for f in src_files]
    bodies = [b for b in bodies if b]
    n = len(bodies)
    if n == 0:
        return RepoConventions()

    def frac(pred: Callable[[str], bool]) -> bool:
        hits = sum(1 for b in bodies if pred(b))
        return hits / n >= _PREVALENCE

    return RepoConventions(
        future_annotations=frac(lambda b: "from __future__ import annotations" in b),
        absolute_imports=frac(_has_absolute_import),
        module_docstrings=frac(_starts_with_docstring),
        typed_defs=frac(_defs_are_typed),
        line_length=_line_length(root),
        test_style=_test_style(root),
        top_package=_top_package(root),
        sampled=n,
    )


def _sample(root: Path, subdir: str, limit: int, *, want_tests: bool) -> list[Path]:
    base = root / subdir if subdir != "." else root
    if not base.is_dir():
        return []
    out: list[Path] = []
    for f in sorted(base.rglob("*.py")):
        if ".git" in f.parts or "__pycache__" in f.parts:
            continue
        is_test = f.name.startswith("test_") or "tests" in f.parts
        if is_test != want_tests:
            continue
        out.append(f)
        if len(out) >= limit:
            break
    return out


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _has_absolute_import(body: str) -> bool:
    return bool(re.search(r"^\s*(?:from|import)\s+[a-z_][\w.]*\b", body, re.M)) and "from ." not in body


def _starts_with_docstring(body: str) -> bool:
    stripped = body.lstrip()
    return stripped.startswith('"""') or stripped.startswith("'''")


def _defs_are_typed(body: str) -> bool:
    defs = re.findall(r"^\s*(?:async\s+)?def\s+\w+\([^)]*\)(\s*->\s*[^:]+)?:", body, re.M)
    if not defs:
        return False
    typed = sum(1 for ann in defs if ann.strip())
    return typed / len(defs) >= _PREVALENCE


def _line_length(root: Path) -> int:
    text = _read(root / "pyproject.toml")
    m = re.search(r"^\s*line-length\s*=\s*(\d+)", text, re.M)
    return int(m.group(1)) if m else _DEFAULT_LINE_LENGTH


def _test_style(root: Path) -> str:
    tests = _sample(root, "tests", _MAX_TEST_SAMPLE, want_tests=True)
    funcs = classes = 0
    for f in tests:
        body = _read(f)
        funcs += len(re.findall(r"^def test_\w+", body, re.M))
        classes += len(re.findall(r"^class Test\w+", body, re.M))
    if funcs == 0 and classes == 0:
        return ""
    return "functions" if funcs >= classes else "classes"


def _top_package(root: Path) -> str:
    src = root / "src"
    base = src if src.is_dir() else root
    if not base.is_dir():
        return ""
    children = [c for c in sorted(base.iterdir()) if c.is_dir() and not c.name.startswith(".")]
    # Prefer a real package (has __init__.py); fall back to any dir holding .py
    # files (namespace packages, or a freshly-scaffolded src/<pkg>/).
    for child in children:
        if (child / "__init__.py").is_file():
            return child.name
    for child in children:
        if any(child.rglob("*.py")):
            return child.name
    return ""


__all__ = ["RepoConventions", "extract_conventions"]
