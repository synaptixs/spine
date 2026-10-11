"""Convention learning (G8): derive a repo's house style from its own code.

Codegen that passes CI still has to *look* like the team wrote it. Rather than
hand-author a style guide (which rots), this derives a compact convention
digest by sampling the repository's existing source and tests — the project's
"derived, not authored" principle applied to style. The digest is rendered
into the codegen prompt so new code matches observed practice: future-imports,
import style, typing, docstrings, line length, and test shape.

Everything here is best-effort and read-only: a malformed file is skipped, and
a repo with nothing to observe and no declared Python yields an empty digest that
renders to no prompt block (codegen then behaves exactly as before).
"""

from __future__ import annotations

import ast
import configparser
import io
import re
import tokenize
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar

from orchestrator.sdlc.layout import TargetLayout

# Bound the scan so a large repo can't blow up extraction time/cost.
_MAX_SOURCE_SAMPLE = 40
_MAX_TEST_SAMPLE = 30
# A convention is "observed" only when this fraction of sampled files show it —
# avoids cementing a one-off into a rule.
_PREVALENCE = 0.6
_DEFAULT_LINE_LENGTH = 88  # ruff's own default when pyproject doesn't say

# What each Python release added that a model trained on current code reaches for by habit,
# as (first release, text). Keyed to the DOCUMENTED release, not to the oldest interpreter that
# happens to accept the construct (3.9's parser accepts a parenthesised multi-item `with`,
# documented as 3.10, so it is listed under 3.10). Each entry was run on real 3.6, 3.8, 3.9
# and 3.10 interpreters when this table was written (B65); the 3.7 and 3.11 boundaries are
# bracketed by their neighbours, not measured. `dataclasses` is left out on purpose: the PyPI
# backport makes it importable on 3.6. The 3.6 rows are from the language's documentation, not
# measured: 3.6 is the oldest interpreter that was available. Python 3 only: a declared 2.x gets
# every entry, and says nothing of `print`, `unicode` or `yield from` idioms.
_NEWER_PYTHON_FEATURES: tuple[tuple[tuple[int, int], str], ...] = (
    ((3, 6), "f-strings"),
    ((3, 6), "variable annotations such as `x: int = 1`"),
    ((3, 7), "`from __future__ import annotations`"),
    ((3, 8), "the walrus `:=`"),
    ((3, 8), "positional-only `/` parameters"),
    ((3, 8), "the f-string `=` specifier"),
    ((3, 8), "`typing.Literal`, `Protocol`, `TypedDict`, `Final` and `functools.cached_property`"),
    # Only when evaluated at runtime: under `from __future__ import annotations` (3.7+) a
    # `list[str]` annotation is fine, so the wording must not forbid what the import allows.
    ((3, 9), "builtin generics in runtime-evaluated annotations or expressions, such as `list[str]`"),
    ((3, 9), "`dict | dict` merging, `str.removeprefix`/`removesuffix` and `zoneinfo`"),
    # Runtime-evaluated only, for the same reason as the builtin generics above.
    ((3, 10), "`X | Y` unions in runtime-evaluated annotations or expressions"),
    ((3, 10), "`match` statements"),
    ((3, 10), "parenthesised multi-item `with`"),
    ((3, 10), "`zip(strict=...)`"),
    ((3, 11), "`tomllib`, `except*`, `typing.Self` and `enum.StrEnum`"),
    ((3, 12), "`type X = ...` aliases and `def f[T]` generics"),
)
_DECLARATION_FILES = ("pyproject.toml", "setup.cfg", "setup.py")
# The lower-bound clauses of a PEP 440 specifier; `<`, `<=` and `!=` say nothing about the floor.
# Version numbers are capped at three digits so a hostile file cannot reach int()'s length limit.
_LOWER_BOUND = re.compile(r"^(?:>=|>|~=|===|==)\s*(\d{1,3})\.(\d{1,3})(?!\d)")
# `Python :: 3` and `Python :: 3 :: Only` carry no minor version and do not match.
_PYTHON_CLASSIFIER = re.compile(r"Programming Language :: Python :: (\d{1,3})\.(\d{1,3})(?!\d)")
_R = TypeVar("_R")
_STATEMENT_END = {tokenize.NEWLINE, tokenize.ENDMARKER}


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
    # The oldest Python the repository declares it supports (B65), or None if it declares none.
    min_python: tuple[int, int] | None = None

    @property
    def is_empty(self) -> bool:
        return self.sampled == 0

    def prompt_block(self, *, target_python: bool = True) -> str:
        """Render the digest as a codegen instruction block, or '' if empty.

        The TARGET PYTHON paragraph does not depend on any source file having been sampled:
        a repository can declare its Python while holding no code yet. It is an instruction to
        a model about Python, so ``target_python=False`` leaves it out for the readers that are
        not that: the ``understand`` page, which is for people, and the toolchains of other
        languages.
        """
        target = self._target_python_paragraph() if target_python else ""
        if self.is_empty:
            return target
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
            f"reads as team-written):\n{body}\n{target}"
        )

    def _target_python_paragraph(self) -> str:
        """One deterministic paragraph naming what the declared minimum Python lacks, or ''."""
        if self.min_python is None:
            return ""
        newer = [text for first, text in _NEWER_PYTHON_FEATURES if first > self.min_python]
        if not newer:
            return ""
        major, minor = self.min_python
        return (
            f"TARGET PYTHON (declared by this repository): {major}.{minor} is the oldest version "
            "it supports. Tests and code must run on it. Do not use, because they need a newer "
            f"Python: {'; '.join(newer)}. Spell them the older way (`typing.List`, "
            "`typing.Optional`, nested `with`, `if`/`elif`).\n"
        )


def extract_conventions(root: Path) -> RepoConventions:
    """Derive a ``RepoConventions`` digest by sampling ``root``'s Python files."""
    root = Path(root)
    min_python = declared_min_python(root)
    src_files = _sample(root, "src", _MAX_SOURCE_SAMPLE, want_tests=False)
    if not src_files:
        # Repos without a src/ layout: sample any non-test modules.
        src_files = _sample(root, ".", _MAX_SOURCE_SAMPLE, want_tests=False)
    if not src_files:
        return RepoConventions(min_python=min_python)

    bodies = [_read(f) for f in src_files]
    bodies = [b for b in bodies if b]
    n = len(bodies)
    if n == 0:
        return RepoConventions(min_python=min_python)

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
        min_python=min_python,
    )


def declared_min_python(root: Path) -> tuple[int, int] | None:
    """The oldest Python ``root`` declares it supports, or ``None`` if it declares none.

    Read from the packaging contract, in order: ``pyproject.toml`` ``project.requires-python``,
    ``setup.cfg`` ``[options] python_requires``, a *literal* ``python_requires="..."`` in
    ``setup.py``, then the lowest ``Programming Language :: Python :: X.Y`` classifier in those
    three files. Classifiers rank last because they go stale. ``setup.py`` is tokenized, never
    run or imported: a value built at run time (``'>={}.{}'.format(...)``, a concatenation, a
    name) is not a literal and falls through, and text in a comment or a docstring is not code.
    Whatever a file holds, this never raises — it is guidance for a prompt and must not stop a
    run — an unreadable or malformed file is simply skipped.
    """
    root = Path(root)
    toml = _safely(_toml, _read_text(root / "pyproject.toml"))
    cfg = _read_text(root / "setup.cfg")
    setup_py = _read_text(root / "setup.py", lenient=True)  # PEP 263 files exist in the wild
    for floor in (
        _safely(_pyproject_floor, toml),
        _safely(_setup_cfg_floor, cfg),
        _safely(_setup_py_floor, setup_py),
    ):
        if floor is not None:
            return floor
    classified = [
        *(_safely(_pyproject_classifiers, toml) or []),
        *(_safely(_setup_cfg_classifiers, cfg) or []),
        *(_safely(_setup_py_classifiers, setup_py) or []),
    ]
    return min(classified) if classified else None


def _safely(read: Callable[[Any], _R], arg: Any) -> _R | None:
    try:
        return read(arg)
    except Exception:  # noqa: BLE001 — a declaration we cannot read must never stop a run
        return None


def _read_text(path: Path, *, lenient: bool = False) -> str:
    """The file's text without a UTF-8 BOM, or '' if it is missing or not UTF-8.

    ``lenient`` falls back to Latin-1 (which cannot fail) instead of skipping the file.
    """
    try:
        data = path.read_bytes()
    except OSError:
        return ""
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("latin-1") if lenient else ""


def _specifier_floor(specifier: str) -> tuple[int, int] | None:
    """The lowest Python a PEP 440 specifier admits: its highest lower-bound clause."""
    floors = [
        (int(m[1]), int(m[2])) for clause in specifier.split(",") if (m := _LOWER_BOUND.match(clause.strip()))
    ]
    return max(floors) if floors else None


def _classifier_versions(texts: list[str]) -> list[tuple[int, int]]:
    return [(int(major), int(minor)) for text in texts for major, minor in _PYTHON_CLASSIFIER.findall(text)]


def _toml(text: str) -> dict[str, Any] | None:
    return tomllib.loads(text) if text else None


def _pyproject_floor(data: dict[str, Any] | None) -> tuple[int, int] | None:
    value = data.get("project", {}).get("requires-python") if data else None
    return _specifier_floor(value) if isinstance(value, str) else None


def _pyproject_classifiers(data: dict[str, Any] | None) -> list[tuple[int, int]]:
    if not data:
        return []
    found = [
        data.get("project", {}).get("classifiers"),
        data.get("tool", {}).get("poetry", {}).get("classifiers"),
    ]
    return _classifier_versions(
        [c for lst in found if isinstance(lst, list) for c in lst if isinstance(c, str)]
    )


def _setup_cfg(text: str) -> configparser.ConfigParser:
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    parser.read_string(text)  # comment lines, including inside a multi-line value, are dropped
    return parser


def _setup_cfg_floor(text: str) -> tuple[int, int] | None:
    return _specifier_floor(_setup_cfg(text).get("options", "python_requires", fallback=""))


def _setup_cfg_classifiers(text: str) -> list[tuple[int, int]]:
    return _classifier_versions([_setup_cfg(text).get("metadata", "classifiers", fallback="")])


def _setup_py_tokens(text: str) -> list[tokenize.TokenInfo]:
    """The code tokens of ``text``: no comments, no blank lines. Tokenizing, unlike parsing,
    also reads a Python 2 ``setup.py``, and a docstring is one STRING token, not code."""
    skip = {tokenize.COMMENT, tokenize.NL}
    return [t for t in tokenize.generate_tokens(io.StringIO(text).readline) if t.type not in skip]


def _setup_py_floor(text: str) -> tuple[int, int] | None:
    """The floor of the first ``python_requires = "<plain string literal>"`` that has digits."""
    tokens = _setup_py_tokens(text)
    ends = {",", ")", "]", "}"}
    for i in range(len(tokens) - 3):
        name, eq, value, after = tokens[i : i + 4]
        if name.string != "python_requires" or name.type != tokenize.NAME or eq.string != "=":
            continue
        # A literal is a lone string followed by the end of its argument or statement. Anything
        # glued on (`+ X`, `.format(...)`, `% V`, `if c else ...`, a second string) is code.
        if value.type != tokenize.STRING or (after.string not in ends and after.type not in _STATEMENT_END):
            continue
        try:
            literal = ast.literal_eval(value.string)
        except (ValueError, SyntaxError):
            continue
        floor = _specifier_floor(literal) if isinstance(literal, str) else None
        if floor is not None:
            return floor
    return None


def _setup_py_classifiers(text: str) -> list[tuple[int, int]]:
    return _classifier_versions([t.string for t in _setup_py_tokens(text) if t.type == tokenize.STRING])


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


__all__ = ["RepoConventions", "declared_min_python", "extract_conventions"]


def perl_convention_block(root: Path, layout: TargetLayout | None) -> str:
    """Observe the owning distribution's object and test styles, with bounded examples."""
    from orchestrator.core.prompt_safety import fence_untrusted
    from orchestrator.sdlc.perl import perl_files

    source = root / layout.source_dir if layout is not None else root / "lib"
    tests = root / layout.tests_dir if layout is not None else root / "t"
    files = list(perl_files(source, (".pm",)))[:3] + list(perl_files(tests, (".t",)))[:3]
    bodies = [(file, file.read_text(encoding="utf-8", errors="replace")[:1800]) for file in files]
    text = "\n".join(body for _, body in bodies)
    objects = (
        "Moose"
        if re.search(r"\buse\s+Moose\b", text)
        else "Moo"
        if re.search(r"\buse\s+Moo\b", text)
        else "classic bless"
    )
    testing = "Test2::V0" if re.search(r"\buse\s+Test2::V0\b", text) else "Test::More"
    examples = "\n".join(fence_untrusted(file.relative_to(root).as_posix(), body) for file, body in bodies)
    return (
        f"PERL CONVENTIONS: use strict; use warnings; object style: {objects}; tests: {testing}. "
        "Follow neighboring package clauses; underscore private subs; POD for public subs.\n" + examples
    )


def php_convention_block(root: Path, layout: TargetLayout) -> str:
    """Bounded source and test examples, retaining legacy naming/import evidence."""
    from orchestrator.core.prompt_safety import fence_untrusted
    from orchestrator.sdlc.php import php_files

    groups: list[str] = []
    for directory, want_tests in ((layout.source_dir, False), (layout.tests_dir, True)):
        count = 0
        for file in php_files(root / directory):
            is_test = file.name.endswith(layout.test_suffix) or (
                layout.tests_dir != "." and file.is_relative_to(root / layout.tests_dir)
            )
            if is_test != want_tests:
                continue
            text = file.read_text(encoding="utf-8", errors="replace")[:1800]
            groups.append(fence_untrusted(file.relative_to(root).as_posix(), text))
            count += 1
            if count == 3:
                break
    return (
        "PHP CONVENTION EXAMPLES (legacy tests show layout only; generate modern PHPUnit tests):\n"
        + "\n".join(groups)
        if groups
        else ""
    )
