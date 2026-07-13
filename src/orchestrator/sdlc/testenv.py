"""Isolated test environments for the codegen loop.

``SubprocessTestRunner`` runs the generated tests with some interpreter. Running
them with the *orchestrator's* interpreter (``sys.executable``) means the
generated project's third-party deps must already be installed in our env — so a
missing one fails at collection (``rc=2``) and the refine loop can't fix it, and
generated code runs in our process. This module gives each worktree its own
environment instead:

- ``VenvTestEnvironment`` (default) — a per-worktree venv (``uv`` if available,
  else stdlib ``venv``) with the *project's* deps installed. Created once and
  reused across refine iterations.
- ``LocalTestEnvironment`` — the legacy in-process interpreter; back-compat and
  the default for the orchestrator's own fast unit tests
  (``SDLC_TEST_ISOLATION=local``).

``run_with_autoheal`` wraps a runner so an *undeclared* import self-heals: on
``ModuleNotFoundError`` it installs the mapped package into the env and retries —
bounded, and gated so it won't ``pip install`` an arbitrary model-named package
(supply-chain safety) unless it's declared / well-known / explicitly opted in.
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Protocol, runtime_checkable

from orchestrator.sdlc.testrunner import TestRunner, TestRunResult

# Import module name (as seen in "No module named 'X'") → PyPI package name when
# they differ. Unknown modules default to the module name (usually correct).
MODULE_TO_PACKAGE = {
    "pytest_mock": "pytest-mock",
    "bs4": "beautifulsoup4",
    "cv2": "opencv-python",
    "PIL": "Pillow",
    "yaml": "pyyaml",
    "sklearn": "scikit-learn",
    "dotenv": "python-dotenv",
    "dateutil": "python-dateutil",
    "jwt": "pyjwt",
    "OpenSSL": "pyopenssl",
}

# Well-known packages auto-heal may install without the opt-in flag. Generous
# enough that the common demo cases (requests, pytest-mock, …) just work, while
# arbitrary/unknown names still require SDLC_AUTOHEAL_UNLISTED=1.
_SAFE_PACKAGES = {
    "requests",
    "httpx",
    "aiohttp",
    "pydantic",
    "pytest-mock",
    "pytest-asyncio",
    "beautifulsoup4",
    "lxml",
    "pyyaml",
    "python-dotenv",
    "numpy",
    "pandas",
    "click",
    "rich",
    "jinja2",
    "sqlalchemy",
    "fastapi",
    "flask",
    "starlette",
    "tenacity",
    "python-dateutil",
}

# Test-harness deps installed into every venv (trusted; they ARE the runner).
_FRAMEWORK_DEPS = ["pytest>=8", "pytest-asyncio>=0.24"]

_MAX_AUTO_INSTALLS = 3


@runtime_checkable
class TestEnvironment(Protocol):
    """An interpreter (and its installed deps) to run a worktree's tests with."""

    @property
    def python(self) -> str: ...
    async def ensure(self, worktree: Path | str) -> None: ...
    async def install(self, packages: list[str]) -> bool: ...
    def describe(self) -> str: ...


class LocalTestEnvironment:
    """The orchestrator's own interpreter — no isolation. Back-compat + unit tests."""

    declared: set[str] = set()

    @property
    def python(self) -> str:
        return sys.executable

    async def ensure(self, worktree: Path | str) -> None:
        return None

    async def install(self, packages: list[str]) -> bool:
        # Never mutate the shared orchestrator env; auto-heal is a no-op here.
        return False

    def describe(self) -> str:
        return "local (orchestrator interpreter — no isolation)"


class VenvTestEnvironment:
    """A per-worktree venv with the project's deps. uv if present, else stdlib venv."""

    def __init__(self) -> None:
        self._python: str | None = None
        self._uv = shutil.which("uv")
        self.declared: set[str] = set()

    @property
    def python(self) -> str:
        if self._python is None:
            raise RuntimeError("VenvTestEnvironment.ensure() must run before .python")
        return self._python

    async def ensure(self, worktree: Path | str) -> None:
        root = Path(worktree)
        # Create the venv as a SIBLING of the worktree, never inside it — a venv
        # in the worktree would be picked up by `git add -A` and pushed in the PR.
        venv = root.parent / f"{root.name}.sdlc-venv"
        bindir = "Scripts" if os.name == "nt" else "bin"
        exe = "python.exe" if os.name == "nt" else "python"
        py = venv / bindir / exe
        if not py.exists():
            if self._uv:
                await _run(self._uv, "venv", str(venv))
            else:
                await _run(sys.executable, "-m", "venv", str(venv))
        self._python = str(py)
        # Framework + the project's declared deps (best-effort).
        deps = list(_FRAMEWORK_DEPS)
        if _tests_use_mocker(root):
            deps.append("pytest-mock>=3")
        project = _project_dependencies(root)
        self.declared = {_dist_name(d) for d in project} | {_dist_name(d) for d in deps}
        await self.install(deps + project)

    async def install(self, packages: list[str]) -> bool:
        if not packages or self._python is None:
            return False
        if self._uv:
            rc = await _run(self._uv, "pip", "install", "--python", self._python, *packages)
        else:
            rc = await _run(self._python, "-m", "pip", "install", *packages)
        return rc == 0

    def describe(self) -> str:
        return f"venv via {'uv' if self._uv else 'stdlib venv'} ({len(self.declared)} deps declared)"


class JavaToolEnvironment:
    """Java build toolchain (Maven). Dependencies come from ``pom.xml``, not pip —
    so ``install`` (auto-heal) is a no-op and ``ensure`` does nothing (Maven
    resolves on ``mvn test``). ``python`` is unavailable by design."""

    declared: set[str] = set()

    @property
    def python(self) -> str:
        raise RuntimeError("JavaToolEnvironment has no Python interpreter")

    async def ensure(self, worktree: Path | str) -> None:
        return None

    async def install(self, packages: list[str]) -> bool:
        return False  # Java deps are declared in pom.xml, not pip-installed

    def describe(self) -> str:
        return "java toolchain (Maven; deps resolved from pom.xml)"


class NodeToolEnvironment:
    """Node.js toolchain (npm/yarn/pnpm). Dependencies come from ``package.json``,
    so ``ensure`` runs ``<pm> install`` (resolving the scaffolded devDeps —
    TypeScript + Vitest) and ``install`` (auto-heal) is a no-op: codegen declares
    new deps in ``package.json``, not pip-style. ``python`` is unavailable by
    design."""

    declared: set[str] = set()

    def __init__(self, package_manager: str = "npm") -> None:
        self.package_manager = package_manager or "npm"

    @property
    def python(self) -> str:
        raise RuntimeError("NodeToolEnvironment has no Python interpreter")

    async def ensure(self, worktree: Path | str) -> None:
        # Install declared deps so Vitest + tsc are present before the test loop.
        # Best-effort: a failure here surfaces as a test failure the refine loop sees.
        await _run_in(Path(worktree), self.package_manager, "install")

    async def install(self, packages: list[str]) -> bool:
        return False  # Node deps are declared in package.json, not auto-installed

    def describe(self) -> str:
        return f"node toolchain ({self.package_manager}; deps resolved from package.json)"


class DotnetToolEnvironment:
    """.NET build toolchain (``dotnet``). Dependencies come from ``.csproj``
    ``<PackageReference>`` entries restored on build, not pip — so ``install``
    (auto-heal) is a no-op and ``ensure`` does nothing (``dotnet test`` restores).
    ``python`` is unavailable by design."""

    declared: set[str] = set()

    @property
    def python(self) -> str:
        raise RuntimeError("DotnetToolEnvironment has no Python interpreter")

    async def ensure(self, worktree: Path | str) -> None:
        return None

    async def install(self, packages: list[str]) -> bool:
        return False  # .NET deps are declared in .csproj, not pip-installed

    def describe(self) -> str:
        return "dotnet toolchain (deps resolved from .csproj PackageReferences)"


def java_toolchain_available() -> bool:
    """True if both ``mvn`` and ``java`` are on PATH (Java codegen prerequisite)."""
    return shutil.which("mvn") is not None and shutil.which("java") is not None


class CToolEnvironment:
    """C build toolchain (CMake or Meson + a C compiler). Dependencies come from the
    system / build files, not pip — so ``install`` (auto-heal) is a no-op and
    ``ensure`` does nothing (the build configures on the test run). ``build_tool``
    (``cmake``/``meson``) selects the runner. ``python`` is unavailable by design."""

    declared: set[str] = set()

    def __init__(self, build_tool: str = "cmake") -> None:
        self.build_tool = build_tool or "cmake"

    @property
    def python(self) -> str:
        raise RuntimeError("CToolEnvironment has no Python interpreter")

    async def ensure(self, worktree: Path | str) -> None:
        return None

    async def install(self, packages: list[str]) -> bool:
        return False  # C deps are system / CMake-resolved, not pip-installed

    def describe(self) -> str:
        return f"c toolchain ({self.build_tool} + system compiler)"


class SqlToolEnvironment:
    """SQL 'toolchain' for greenfield DDL validation (SQL Track B).

    There is no external toolchain: generated migrations are validated by
    applying them to an in-memory SQLite database (``sqlite3`` is stdlib), so
    this environment is **always available**. ``install``/``ensure`` are no-ops
    and ``python`` is unavailable by design (validation runs in-process via the
    ``SqlTestRunner``, not by exec-ing an interpreter). ``dialect`` is the source
    dialect the generated DDL is written in (transpiled to SQLite on apply)."""

    declared: set[str] = set()

    def __init__(self, dialect: str = "postgres") -> None:
        self.dialect = dialect or "postgres"

    @property
    def python(self) -> str:
        raise RuntimeError("SqlToolEnvironment has no Python interpreter")

    async def ensure(self, worktree: Path | str) -> None:
        return None

    async def install(self, packages: list[str]) -> bool:
        return False  # nothing to install — SQLite validation is stdlib

    def describe(self) -> str:
        return f"sql toolchain (in-memory SQLite; dialect={self.dialect})"


def sql_toolchain_available() -> bool:
    """Always True — SQL validation uses stdlib ``sqlite3`` (no external tool)."""
    return True


def dotnet_toolchain_available() -> bool:
    """True if the ``dotnet`` CLI is on PATH (C# codegen prerequisite)."""
    return shutil.which("dotnet") is not None


def _c_compiler_available() -> bool:
    return any(shutil.which(cc) is not None for cc in ("cc", "gcc", "clang"))


def _cpp_compiler_available() -> bool:
    return any(shutil.which(cc) is not None for cc in ("c++", "g++", "clang++"))


def c_toolchain_available() -> bool:
    """True if CMake and a C compiler are on PATH (CMake C codegen prerequisite)."""
    return shutil.which("cmake") is not None and _c_compiler_available()


def cpp_toolchain_available() -> bool:
    """True if CMake and a C++ compiler are on PATH (CMake C++ codegen prerequisite)."""
    return shutil.which("cmake") is not None and _cpp_compiler_available()


def meson_toolchain_available() -> bool:
    """True if Meson, Ninja and a C compiler are on PATH (Meson C codegen prereq)."""
    return shutil.which("meson") is not None and shutil.which("ninja") is not None and _c_compiler_available()


def detect_dotnet_tfm(default: str = "net8.0") -> str:
    """The installed SDK's target-framework moniker (``net{major}.0``), or ``default``.

    A greenfield project must target a framework whose RUNTIME is installed — a
    ``net8.0`` project on a box with only the .NET 10 runtime builds but can't launch
    the test host (roll-forward doesn't cross majors). So the scaffold targets the
    SDK actually present. Best-effort: falls back to ``default`` if ``dotnet`` is
    missing or its version can't be parsed."""
    import subprocess

    exe = shutil.which("dotnet")
    if exe is None:
        return default
    try:
        proc = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=30)  # noqa: S603
    except (OSError, subprocess.SubprocessError):
        return default
    m = re.match(r"\s*(\d+)\.", proc.stdout)
    return f"net{m.group(1)}.0" if m else default


def node_toolchain_available(package_manager: str = "npm") -> bool:
    """True if both ``node`` and the package manager are on PATH (TS prerequisite)."""
    return shutil.which("node") is not None and shutil.which(package_manager or "npm") is not None


def make_test_environment(language: str = "python", *, build_tool: str = "") -> TestEnvironment:
    """The test environment for ``language``: Java toolchain, Node toolchain
    (``build_tool`` selects the package manager), or a Python venv
    (``VenvTestEnvironment`` unless ``SDLC_TEST_ISOLATION=local``)."""
    if language == "java":
        return JavaToolEnvironment()
    if language == "typescript":
        return NodeToolEnvironment(build_tool or "npm")
    if language == "csharp":
        return DotnetToolEnvironment()
    if language in ("c", "cpp"):
        return CToolEnvironment(build_tool or "cmake")
    if language == "sql":
        return SqlToolEnvironment(build_tool or "postgres")
    if os.getenv("SDLC_TEST_ISOLATION", "venv").lower() == "local":
        return LocalTestEnvironment()
    return VenvTestEnvironment()


def make_test_runner(language: str, env: TestEnvironment) -> TestRunner:
    """The runner for ``language``: Maven for Java, the package manager's ``test``
    script for TypeScript, pytest (on the env's interpreter) for Python."""
    from orchestrator.sdlc.testrunner import (
        CTestRunner,
        DotnetTestRunner,
        MavenTestRunner,
        MesonTestRunner,
        NodeTestRunner,
        SubprocessTestRunner,
    )

    if language == "java":
        return MavenTestRunner()
    if language == "typescript":
        return NodeTestRunner(package_manager=getattr(env, "package_manager", "npm"))
    if language == "csharp":
        return DotnetTestRunner()
    if language in ("c", "cpp"):
        # The build tool (cmake/meson) is carried on the C/C++ tool environment.
        return MesonTestRunner() if getattr(env, "build_tool", "cmake") == "meson" else CTestRunner()
    if language == "sql":
        dialect = getattr(env, "dialect", "postgres")
        # Default: fast, zero-dependency SQLite. Opt into real Postgres (Docker +
        # the sql-postgres extra) for dialect fidelity via SDLC_SQL_ENGINE=postgres.
        if os.getenv("SDLC_SQL_ENGINE", "sqlite").lower() == "postgres":
            from orchestrator.sdlc.testrunner import PostgresSqlTestRunner

            return PostgresSqlTestRunner(dialect=dialect)
        from orchestrator.sdlc.testrunner import SqlTestRunner

        return SqlTestRunner(dialect=dialect)
    return SubprocessTestRunner(python=env.python)


_MISSING_RE = re.compile(r"No module named ['\"]([\w.]+)['\"]")


def parse_missing_module(output: str) -> str | None:
    """First ``ModuleNotFoundError`` module (top-level package), or None."""
    m = _MISSING_RE.search(output)
    return m.group(1).split(".")[0] if m else None


def _autoheal_allowed(package: str, module: str, env: TestEnvironment) -> bool:
    if os.getenv("SDLC_AUTOHEAL_UNLISTED") == "1":
        return True
    declared: set[str] = getattr(env, "declared", set())
    return package in declared or package in _SAFE_PACKAGES or module in _SAFE_PACKAGES


async def run_with_autoheal(
    runner: TestRunner,
    env: TestEnvironment,
    path: str,
    *,
    emit: Callable[[str], None] | None = None,
) -> TestRunResult:
    """Run tests; on a missing-module collection error, install the dep and retry.

    Bounded by ``_MAX_AUTO_INSTALLS`` and gated by ``_autoheal_allowed`` so a
    real assertion failure (no missing module) goes straight to the refine loop,
    and an unlisted/arbitrary package is not installed without opt-in."""
    say = emit or (lambda _m: None)
    tried: set[str] = set()
    while True:
        result = await runner.run(path=path)
        if result.passed:
            return result
        module = parse_missing_module(result.output)
        if module is None:
            return result  # genuine test failure → refine handles it
        package = MODULE_TO_PACKAGE.get(module, module)
        if package in tried or len(tried) >= _MAX_AUTO_INSTALLS:
            return result
        if not _autoheal_allowed(package, module, env):
            say(
                f"[testenv] missing module '{module}' → not auto-installing unlisted '{package}' "
                "(declare it in the project or set SDLC_AUTOHEAL_UNLISTED=1)"
            )
            return result
        say(f"[testenv] missing module '{module}' — installing '{package}' into the project env…")
        if not await env.install([package]):
            say(f"[testenv] install of '{package}' failed; leaving the verdict to refine")
            return result
        tried.add(package)


# --- helpers ---------------------------------------------------------------


async def _run(*argv: str) -> int:
    proc = await asyncio.create_subprocess_exec(
        *argv, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
    )
    await proc.wait()
    return proc.returncode if proc.returncode is not None else -1


async def _run_in(cwd: Path, *argv: str) -> int:
    proc = await asyncio.create_subprocess_exec(
        *argv, cwd=str(cwd), stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
    )
    await proc.wait()
    return proc.returncode if proc.returncode is not None else -1


def _dist_name(requirement: str) -> str:
    """Bare distribution name from a requirement string (``requests>=2`` → ``requests``)."""
    return re.split(r"[<>=!~ \[]", requirement.strip(), maxsplit=1)[0].lower()


def _tests_use_mocker(root: Path) -> bool:
    tests = root / "tests"
    if not tests.is_dir():
        return False
    for f in tests.rglob("test_*.py"):
        try:
            if "mocker" in f.read_text(encoding="utf-8") or "pytest_mock" in f.read_text(encoding="utf-8"):
                return True
        except OSError:
            continue
    return False


def _project_dependencies(root: Path) -> list[str]:
    """The project's declared runtime deps from pyproject / requirements (best-effort)."""
    deps: list[str] = []
    pyproject = root / "pyproject.toml"
    if pyproject.is_file():
        try:
            import tomllib

            data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
            deps += [str(d) for d in data.get("project", {}).get("dependencies", []) or []]
        except (OSError, ValueError, ModuleNotFoundError):
            pass
    for req in (*root.glob("requirements*.txt"), root / "requirements" / "base.txt"):
        if req.is_file():
            try:
                for line in req.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if line and not line.startswith(("#", "-")):
                        deps.append(line)
            except OSError:
                continue
    return deps


__all__ = [
    "CToolEnvironment",
    "DotnetToolEnvironment",
    "JavaToolEnvironment",
    "LocalTestEnvironment",
    "MODULE_TO_PACKAGE",
    "NodeToolEnvironment",
    "SqlToolEnvironment",
    "TestEnvironment",
    "VenvTestEnvironment",
    "c_toolchain_available",
    "cpp_toolchain_available",
    "detect_dotnet_tfm",
    "dotnet_toolchain_available",
    "java_toolchain_available",
    "meson_toolchain_available",
    "make_test_environment",
    "make_test_runner",
    "node_toolchain_available",
    "parse_missing_module",
    "run_with_autoheal",
    "sql_toolchain_available",
]
