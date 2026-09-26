"""Compiler diagnostics, read from a run's *whole* output before anything is cut.

**Why this exists (NSS-1243).** ``DotnetTestRunner`` kept the last 4,000 characters of
``dotnet test``. On a Blazor project with 138 warnings that tail is warnings and the MSBuild
summary: the one error that mattered — a ``using`` line the model had just written — was printed
first and cut first. Refine read what was left, a cascade of errors naming other files, and spent
five attempts editing a correct ``_Imports.razor``. The errors are now lifted out of the full
output, de-duplicated (MSBuild prints each twice) and put **first**, with paths relative to the
worktree; the tail follows for the test results.

The same module answers refine's other question: which files does a failure actually name?
Refine may edit a pre-existing file only when the failure, the spec or the design names it
(``codegen.refine``), so the answer has to come from the text the model was shown.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath

#: How many distinct errors lead the digest. The first few carry the cause; past twenty the
#: rest are the cascade, and they would crowd the tail out of the cap.
_MAX_ERRORS = 20
#: A single diagnostic line is never allowed more than this — a Razor error can quote a page.
_MAX_LINE_CHARS = 300

# `/abs/WebApp/X.razor.cs(3,11): error CS1001: Identifier expected [/abs/WebApp/App.csproj]`
# Codes: CS (compiler), RZ (Razor), MSB (MSBuild), NU (NuGet), NETSDK, ….
_MSBUILD_ERROR = re.compile(
    r"^\s*(?P<path>[^\s(][^()\r\n]*?)\((?P<line>\d+),(?P<col>\d+)(?:,\d+,\d+)?\):\s*error\s+"
    r"(?P<code>[A-Z]+\d+):\s*(?P<message>.*?)(?:\s+\[[^\]\r\n]+\])?\s*$",
    re.MULTILINE,
)

#: A path-looking token with a source or project suffix, in any failure text (tracebacks,
#: compiler lines, test output). Deliberately broad: a token that resolves to no file under the
#: worktree is discarded, so over-matching costs nothing.
_PATH_TOKEN = re.compile(
    r"(?P<path>(?:[A-Za-z]:)?[\w@.\\/-]*[\w-]\.(?:py|pyi|cs|razor|cshtml|csproj|ts|tsx|js|jsx|mjs|"
    r"java|kt|kts|go|rb|php|pl|pm|t|c|h|cc|cpp|hpp|rs|swift|scala|sql|json|toml|ya?ml|xml))\b"
)


@dataclass(frozen=True)
class Diagnostic:
    """One compiler error, its path relative to the worktree when it lies inside it."""

    path: str
    line: int
    column: int
    code: str
    message: str

    def render(self) -> str:
        text = f"{self.path}({self.line},{self.column}): error {self.code}: {self.message}"
        return text if len(text) <= _MAX_LINE_CHARS else text[: _MAX_LINE_CHARS - 1] + "…"


def _relative(path: str, root: Path | None) -> str:
    """``path`` relative to ``root`` with ``/`` separators, or as given when it lies elsewhere."""
    if root is None:
        return path.replace("\\", "/")
    windows = re.match(r"^[A-Za-z]:[\\/]", path) is not None
    pure = PureWindowsPath(path) if windows else PurePosixPath(path.replace("\\", "/"))
    if not pure.is_absolute():
        return pure.as_posix()
    # Both spellings: macOS resolves /tmp to /private/tmp, so a worktree under one is reported
    # under the other by tools that canonicalize.
    for base in (root, root.resolve()):
        try:
            return PurePosixPath(pure.as_posix()).relative_to(base.as_posix()).as_posix()
        except ValueError:
            continue
    return pure.as_posix()


def dotnet_errors(output: str, root: Path | None = None) -> list[Diagnostic]:
    """Every distinct MSBuild/compiler **error** in ``output``, in the order first printed."""
    seen: set[tuple[str, int, str, str]] = set()
    found: list[Diagnostic] = []
    for match in _MSBUILD_ERROR.finditer(output):
        diag = Diagnostic(
            path=_relative(match["path"].strip(), root),
            line=int(match["line"]),
            column=int(match["col"]),
            code=match["code"],
            message=match["message"].strip(),
        )
        key = (diag.path, diag.line, diag.code, diag.message)
        if key not in seen:
            seen.add(key)
            found.append(diag)
    return found


def digest_dotnet_output(output: str, root: Path | None, *, cap: int) -> str:
    """The errors first, then as much of the tail as the cap leaves (at least a quarter).

    Unchanged — the plain tail — when the output holds no error lines, so a run that compiled
    and failed an assertion reads exactly as it did.
    """
    errors = dotnet_errors(output, root)
    if not errors:
        return output[-cap:] if len(output) > cap else output
    shown = errors[:_MAX_ERRORS]
    more = f" (first {len(shown)} of {len(errors)})" if len(errors) > len(shown) else ""
    head = (
        f"COMPILER ERRORS{more}, in the order the build reported them — the first is usually "
        "the cause and the rest its cascade; warnings omitted:\n"
        + "\n".join(f"  {d.render()}" for d in shown)
        + "\n\n--- end of the build output ---\n"
    )
    head = head[: (cap * 3) // 4]
    room = cap - len(head)
    return head + (output[-room:] if len(output) > room else output)


def paths_named(text: str, root: Path) -> list[str]:
    """Worktree-relative paths of the existing files ``text`` names, in first-mention order."""
    found: list[str] = []
    resolved_root = root.resolve()
    for match in _PATH_TOKEN.finditer(text):
        rel = _relative(match["path"], root)
        target = (root / rel).resolve()
        if not target.is_relative_to(resolved_root) or not target.is_file():
            continue
        rel = target.relative_to(resolved_root).as_posix()
        if rel not in found:
            found.append(rel)
    return found


# --- pytest ---------------------------------------------------------------------------------

# pytest's short summary (`-rfE`): `FAILED tests/x.py::test_a - assert …` for a failing test,
# `ERROR tests/y.py - ModuleNotFoundError: …` for a file that did not collect. A parametrized id
# may hold spaces (`test_p[x y]`), so the id runs to the ` - ` separator, not to whitespace.
_PYTEST_SUMMARY = re.compile(r"^(?:FAILED|ERROR) (?P<id>.+?)(?: - .*)?$", re.MULTILINE)
_MISSING_MODULE = re.compile(r"No module named '([\w.]+)'")


def pytest_problems(output: str) -> tuple[str, ...]:
    """Node ids (and uncollectable files) pytest's short summary names, in order, once each."""
    return tuple(dict.fromkeys(m["id"].strip() for m in _PYTEST_SUMMARY.finditer(output)))


def is_collection_error(problem: str) -> bool:
    """A problem naming a file and no test is a file that failed to collect."""
    return "::" not in problem


def missing_modules(output: str) -> list[str]:
    """Top-level modules an import could not find, in first-mention order."""
    return list(dict.fromkeys(m.split(".")[0] for m in _MISSING_MODULE.findall(output)))


__all__ = [
    "Diagnostic",
    "digest_dotnet_output",
    "dotnet_errors",
    "is_collection_error",
    "missing_modules",
    "paths_named",
    "pytest_problems",
]
