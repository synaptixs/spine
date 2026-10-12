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

**Why it also reads what has no position (B73).** ``path(line,col): error`` was the only shape
read. A restore failure (``NU1301``), an MSBuild task failure (``MSB4018``) and a bare
``error CS…`` line carry no position, matched nothing, and left the digest to hand refine a
4,000-character tail that — on a project with 138 warnings — is warnings. Warnings never fail a
build, so the tail drops them, and a failure that still explains nothing says so rather than
inviting an edit to silence them. Failing tests and the environment failures no edit can fix
(``dotnet_blocker``) are named the same way, from the whole output.
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

# `/abs/WebApp/App.csproj : error NU1301: Failed to retrieve … [/abs/s.sln]`, `MSBUILD : error
# MSB1009: …`, or a bare `error CS0103: …`. The path, when there is one, is followed by a SPACE,
# a colon and a space; a line that merely contains the words (an assertion message, `Failure:
# error CS0001: …`) has neither that nor `error` at its start, so it is not a build error.
_BARE_ERROR = re.compile(
    r"^\s*(?:(?P<path>\S.*?)\s:\s)?error\s+(?P<code>[A-Z]+\d+):\s*(?P<message>.*?)(?:\s+\[[^\]\r\n]+\])?\s*$",
    re.MULTILINE,
)

#: An MSBuild warning line. A warning never fails a build, so none is worth tail space.
_WARNING_LINE = re.compile(r"\bwarning\s+[A-Z]+\d+:")

# vstest's console logger, whatever the test framework: `  Failed Ns.Class.Method [3 ms]`. Two
# spaces at most, so indented message text ("   Failed to parse …") is not a test; `Failed!`
# and `Failed:` (the summary) have no space after the word; `Failed to restore …` is NuGet's.
_FAILED_TEST = re.compile(
    r"^ {0,2}Failed[ \t]+(?!to\s)(?P<name>\S.*?)(?:[ \t]+\[[^\]\r\n]*\])?[ \t]*$", re.MULTILINE
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
        if self.line > 0:
            text = f"{self.path}({self.line},{self.column}): error {self.code}: {self.message}"
        else:  # no position to print (B73): `path: error CODE: …`, or just `error CODE: …`
            text = (
                f"{self.path}: error {self.code}: {self.message}"
                if self.path
                else f"error {self.code}: {self.message}"
            )
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
    """Every distinct MSBuild/compiler **error** in ``output``, in the order first printed.

    With a position (``path(line,col)``) or without one (``line`` is 0): a restore or task failure
    names a project at most, and a bare ``error CS…`` line names nothing.
    """
    hits = sorted([*_MSBUILD_ERROR.finditer(output), *_BARE_ERROR.finditer(output)], key=lambda m: m.start())
    seen: set[tuple[str, int, str, str]] = set()
    found: list[Diagnostic] = []
    for match in hits:
        positioned = "line" in match.re.groupindex
        raw_path = (match["path"] or "").strip()
        diag = Diagnostic(
            path=_relative(raw_path, root) if raw_path else "",
            line=int(match["line"]) if positioned else 0,
            column=int(match["col"]) if positioned else 0,
            code=match["code"],
            message=match["message"].strip(),
        )
        key = (diag.path, diag.line, diag.code, diag.message)
        if key not in seen:
            seen.add(key)
            found.append(diag)
    return found


def _without_warnings(output: str) -> str:
    return "".join(line for line in output.splitlines(keepends=True) if not _WARNING_LINE.search(line))


def digest_dotnet_output(output: str, root: Path | None, *, cap: int, returncode: int | None = None) -> str:
    """What explains the failure first, then as much of the tail as the cap leaves (at least a quarter).

    The tail never holds a warning line: a warning does not fail a build, and on a project with
    138 of them the tail used to be nothing else (B73). In order of what the output yields:

    * errors, positioned or not — ``COMPILER ERRORS``;
    * else the failing tests — ``FAILED TESTS``;
    * else, when ``returncode`` says the run failed — ``UNPARSED FAILURE``, which tells refine
      that nothing here names a cause and that warnings are not one;
    * else the tail alone, so a run that compiled and passed reads exactly as it did.
    """
    errors = dotnet_errors(output, root)
    failing = () if errors else dotnet_failing_tests(output)
    body = _without_warnings(output)
    if errors:
        shown = errors[:_MAX_ERRORS]
        more = f" (first {len(shown)} of {len(errors)})" if len(errors) > len(shown) else ""
        head = (
            f"COMPILER ERRORS{more}, in the order the build reported them — the first is usually "
            "the cause and the rest its cascade; warnings omitted:\n"
            + "\n".join(f"  {d.render()}" for d in shown)
            + "\n\n--- end of the build output ---\n"
        )
    elif failing:
        shown_tests = failing[:_MAX_ERRORS]
        more = f" (first {len(shown_tests)} of {len(failing)})" if len(failing) > len(shown_tests) else ""
        head = (
            f"FAILED TESTS ({len(failing)}){more}, in the order the run reported them:\n"
            + "\n".join(f"  {name}" for name in shown_tests)
            + "\n\n--- end of the test list ---\n"
        )
    elif returncode not in (None, 0):
        head = (
            f"UNPARSED FAILURE (exit code {returncode}) — this output names no error and no failing "
            "test; warnings are not the cause, so edit nothing to silence them. The rest of the "
            "output follows without its warnings:\n"
        )
    else:
        return body[-cap:] if len(body) > cap else body
    head = head[: (cap * 3) // 4]
    room = cap - len(head)
    return head + (body[-room:] if len(body) > room else body)


@dataclass(frozen=True)
class DotnetBlocker:
    """A `dotnet` failure no edit to the code can fix: the machine or the feed, not the change."""

    kind: str
    detail: str


def dotnet_failing_tests(output: str) -> tuple[str, ...]:
    """Fully qualified names of the tests the run reports as failed, once each, in order.

    Read from vstest's console logger, which prints the same ``Failed <name> [duration]`` line
    for xunit, NUnit and MSTest; the duration is not part of the name. A build that failed has no
    tests to name, so callers ask this only when ``dotnet_errors`` found nothing.
    """
    return tuple(dict.fromkeys(m["name"].strip() for m in _FAILED_TEST.finditer(output)))


_RESTORE_ERROR = re.compile(r"error\s+(?P<code>NU1\d{3}|NETSDK1004)\b")
_SDK_ERROR = re.compile(
    r"error\s+(?P<code>NETSDK1045|NETSDK1141)\b"
    r"|A compatible \.NET SDK was not found"
    r"|No \.NET SDKs were found"
    r"|You must install or update \.NET to run this application"
    r"|The framework '[^']+', version '[^']+'.* was not found"
)


def dotnet_blocker(output: str, *, timed_out: bool = False) -> DotnetBlocker | None:
    """A failure no edit to the code can fix, or ``None``.

    ``restore`` (NuGet could not fetch or resolve: an unreachable or unauthorised feed, a package
    that does not exist), ``sdk`` (no .NET SDK or runtime that the project needs) and ``timeout``.
    Warnings never qualify (``warning NU1603`` is a version note), and neither does a compile
    error: that one is the change's. The same family of failure as a Python module the
    environment lacks (``baseline.environment_blocker``).
    """
    if timed_out:
        return DotnetBlocker("timeout", "dotnet test did not finish within its time limit")
    restore = _RESTORE_ERROR.search(output)
    if restore:
        first = next((d for d in dotnet_errors(output) if d.code == restore["code"]), None)
        detail = first.render() if first else f"NuGet restore failed ({restore['code']})"
        return DotnetBlocker("restore", detail)
    sdk = _SDK_ERROR.search(output)
    if sdk:
        return DotnetBlocker("sdk", sdk.group(0).strip())
    return None


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
    "DotnetBlocker",
    "dotnet_blocker",
    "dotnet_failing_tests",
    "digest_dotnet_output",
    "dotnet_errors",
    "is_collection_error",
    "missing_modules",
    "paths_named",
    "pytest_problems",
]
