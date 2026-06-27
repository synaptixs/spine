"""Block C: test-run seam.

The feature pipeline runs the generated tests through a ``TestRunner``. Unlike
most Block-C stages this has a *real* default — ``SubprocessTestRunner`` shells
out to ``pytest`` in the worktree — because the generated module + test are
real, runnable files and running them for real is what makes the refinement
loop meaningful. ``StubTestRunner`` is kept for offline tests that want to
script pass/fail outcomes without spawning a subprocess.

Like the workspace git calls, the subprocess goes through
``asyncio.create_subprocess_exec`` with an explicit argv list (no shell), so a
worktree path can't smuggle in shell metacharacters. It runs inside a Temporal
activity, never in workflow code.
"""

from __future__ import annotations

import asyncio
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

# Cap captured output so a chatty test run can't bloat the activity result /
# the audit row; the tail is the most useful part for the refinement prompt.
_MAX_OUTPUT_CHARS = 4000

# Env prefixes stripped before running the worktree's tests. Two reasons:
# (1) SECURITY — generated code must never see the orchestrator's live
# credentials; (2) CORRECTNESS — repo tests assert "unconfigured adapter"
# behavior, and inherited CONFLUENCE_/JIRA_ vars make adapters look
# configured (run #7's failure mode).
_SECRET_ENV_PREFIXES = (
    "ANTHROPIC_",
    "OPENAI_",
    "CONFLUENCE_",
    "JIRA_",
    "GITHUB_",
    "AWS_",
    "ORCHESTRATOR_",
    "SDLC_",
    "MINIO_",
    "TEMPORAL_",
)


async def pytest_available(python: str | None = None) -> bool:
    """True if ``pytest`` is importable by ``python`` (defaults to the current
    interpreter). The feature runner preflights this before the test/refine loop
    so a missing pytest fails fast with an actionable message instead of letting
    the refine model flail at ``pyproject.toml`` — pytest is a dev-only
    dependency, absent from a plain ``pip install`` (the installed-wheel lesson)."""
    proc = await asyncio.create_subprocess_exec(
        python or sys.executable,
        "-c",
        "import pytest",
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    await proc.wait()
    return proc.returncode == 0


@dataclass(frozen=True)
class TestRunResult:
    """Outcome of running a worktree's tests."""

    __test__ = False  # not a pytest test class despite the Test* name

    passed: bool
    returncode: int
    output: str = ""


@runtime_checkable
class TestRunner(Protocol):
    """Runs the tests in a worktree, returning pass/fail + captured output."""

    async def run(self, *, path: str) -> TestRunResult: ...


class SubprocessTestRunner:
    """Runs ``python -m pytest`` in the worktree via exec (no shell).

    ``passed`` is ``returncode == 0``. pytest's exit code 5 ("no tests
    collected") is treated as a failure so an empty worktree doesn't look green.
    """

    def __init__(self, python: str | None = None, *, timeout: float = 120.0) -> None:
        self._python = python or sys.executable
        self._timeout = timeout

    async def run(self, *, path: str) -> TestRunResult:
        # ``-B`` (don't write ``__pycache__``) is essential for the refinement
        # loop: it rewrites a module and reruns, and a same-size edit within
        # mtime granularity would otherwise serve a stale ``.pyc`` and report a
        # false result. ``-p no:cacheprovider`` likewise drops pytest's cache.
        #
        # When the worktree is a real repo with a src/ layout, the *worktree's*
        # src must win over any installed copy of the same package — otherwise
        # the generated change is invisible to its own tests and the refinement
        # loop chases a phantom failure.
        env = {k: v for k, v in os.environ.items() if not k.startswith(_SECRET_ENV_PREFIXES)}
        src = Path(path) / "src"
        if src.is_dir():
            existing = env.get("PYTHONPATH", "")
            env["PYTHONPATH"] = f"{src}{os.pathsep}{existing}" if existing else str(src)
        proc = await asyncio.create_subprocess_exec(
            self._python,
            "-B",
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            cwd=path,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            stdout_bytes, _ = await asyncio.wait_for(proc.communicate(), timeout=self._timeout)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            return TestRunResult(passed=False, returncode=-1, output="test run timed out")

        output = stdout_bytes.decode("utf-8", "replace")
        if len(output) > _MAX_OUTPUT_CHARS:
            output = output[-_MAX_OUTPUT_CHARS:]
        rc = proc.returncode if proc.returncode is not None else -1
        return TestRunResult(passed=rc == 0, returncode=rc, output=output)


class MavenTestRunner:
    """Runs ``mvn -B -q test`` in a Java worktree via exec (no shell).

    Maven exits non-zero on a compile or test failure → ``passed`` is
    ``returncode == 0``. ``-q`` keeps the output to errors (compiler + surefire
    failures), which is exactly what the refine prompt needs."""

    def __init__(self, mvn: str = "mvn", *, timeout: float = 600.0) -> None:
        self._mvn = mvn
        self._timeout = timeout

    async def run(self, *, path: str) -> TestRunResult:
        env = {k: v for k, v in os.environ.items() if not k.startswith(_SECRET_ENV_PREFIXES)}
        proc = await asyncio.create_subprocess_exec(
            self._mvn,
            "-B",
            "-q",
            "test",
            cwd=path,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            stdout_bytes, _ = await asyncio.wait_for(proc.communicate(), timeout=self._timeout)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            return TestRunResult(passed=False, returncode=-1, output="maven test run timed out")
        output = stdout_bytes.decode("utf-8", "replace")
        if len(output) > _MAX_OUTPUT_CHARS:
            output = output[-_MAX_OUTPUT_CHARS:]
        rc = proc.returncode if proc.returncode is not None else -1
        return TestRunResult(passed=rc == 0, returncode=rc, output=output)


class DotnetTestRunner:
    """Runs ``dotnet test`` in a C# worktree via exec (no shell).

    With no project/solution argument the .NET CLI resolves the single ``.sln`` or
    ``.csproj`` in the worktree (the greenfield scaffold writes a solution tying the
    source + xUnit test projects). ``dotnet test`` restores packages, builds, and
    runs the tests, exiting non-zero on any compile or test failure → ``passed`` is
    ``returncode == 0``. ``--nologo`` trims the banner so the captured tail is the
    build/test errors the refine prompt needs."""

    def __init__(self, dotnet: str = "dotnet", *, timeout: float = 600.0) -> None:
        self._dotnet = dotnet
        self._timeout = timeout

    async def run(self, *, path: str) -> TestRunResult:
        env = {k: v for k, v in os.environ.items() if not k.startswith(_SECRET_ENV_PREFIXES)}
        # `dotnet test` with no argument needs exactly one project/solution in cwd.
        # In a monorepo the solution is often NESTED (e.g. backend/App/App.sln), so
        # point it at the single .sln when there is one; otherwise let dotnet resolve
        # at the root (greenfield scaffold writes the .sln at the root).
        target = _discover_dotnet_target(Path(path))
        args = ["test", "--nologo"]
        if target:
            args.insert(1, target)  # `dotnet test <solution> --nologo`
        proc = await asyncio.create_subprocess_exec(
            self._dotnet,
            *args,
            cwd=path,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            stdout_bytes, _ = await asyncio.wait_for(proc.communicate(), timeout=self._timeout)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            return TestRunResult(passed=False, returncode=-1, output="dotnet test run timed out")
        output = stdout_bytes.decode("utf-8", "replace")
        if len(output) > _MAX_OUTPUT_CHARS:
            output = output[-_MAX_OUTPUT_CHARS:]
        rc = proc.returncode if proc.returncode is not None else -1
        return TestRunResult(passed=rc == 0, returncode=rc, output=output)


class NodeTestRunner:
    """Runs the project's ``test`` script via its package manager (``<pm> test``).

    The Vitest scaffold sets ``"test": "vitest run"`` in ``package.json``; npm /
    yarn / pnpm all run that script with ``<pm> test``. Vitest exits non-zero on a
    failing test or a TS compile error → ``passed`` is ``returncode == 0``."""

    def __init__(self, package_manager: str = "npm", *, timeout: float = 600.0) -> None:
        self._pm = package_manager or "npm"
        self._timeout = timeout

    async def run(self, *, path: str) -> TestRunResult:
        env = {k: v for k, v in os.environ.items() if not k.startswith(_SECRET_ENV_PREFIXES)}
        proc = await asyncio.create_subprocess_exec(
            self._pm,
            "test",
            cwd=path,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            stdout_bytes, _ = await asyncio.wait_for(proc.communicate(), timeout=self._timeout)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            return TestRunResult(passed=False, returncode=-1, output="node test run timed out")
        output = stdout_bytes.decode("utf-8", "replace")
        if len(output) > _MAX_OUTPUT_CHARS:
            output = output[-_MAX_OUTPUT_CHARS:]
        rc = proc.returncode if proc.returncode is not None else -1
        return TestRunResult(passed=rc == 0, returncode=rc, output=output)


_DOTNET_SKIP_DIRS = {"bin", "obj", "node_modules", ".git", ".vs"}


def _discover_dotnet_target(root: Path) -> str | None:
    """The single ``.sln`` in the worktree (so a nested solution is found), else the
    single ``.csproj``, else ``None`` (run ``dotnet test`` at the root). Returns a
    path only when it's unambiguous — multiple solutions stay at the root so this
    never guesses which one to test."""
    for ext in ("*.sln", "*.csproj"):
        hits: list[Path] = []
        for p in root.rglob(ext):
            if _DOTNET_SKIP_DIRS.isdisjoint(part.lower() for part in p.relative_to(root).parts):
                hits.append(p)
        if len(hits) == 1:
            return str(hits[0])
        if hits:  # several solutions/projects → ambiguous; defer to the root
            return None
    return None


class StubTestRunner:
    """Scriptable in-memory runner for offline tests.

    ``outcomes`` is consumed one entry per ``run`` call (so a test can make the
    first run fail and the second pass to exercise the refinement loop); once
    exhausted it repeats the last value. Defaults to always-pass.
    """

    def __init__(self, outcomes: list[bool] | None = None) -> None:
        self._outcomes = list(outcomes) if outcomes else [True]
        self._calls = 0

    async def run(self, *, path: str) -> TestRunResult:
        _ = path
        idx = min(self._calls, len(self._outcomes) - 1)
        self._calls += 1
        passed = self._outcomes[idx]
        return TestRunResult(passed=passed, returncode=0 if passed else 1, output="stub")


__all__ = [
    "DotnetTestRunner",
    "MavenTestRunner",
    "NodeTestRunner",
    "StubTestRunner",
    "SubprocessTestRunner",
    "TestRunResult",
    "TestRunner",
    "pytest_available",
]
