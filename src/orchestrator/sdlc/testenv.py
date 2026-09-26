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

from orchestrator.sdlc.contracts import TestEnvironment as TestEnvironment
from orchestrator.sdlc.contracts import ToolchainLayout
from orchestrator.sdlc.sam import RUNTIME_PROVIDED, function_requirements, sam_template
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
    """Java build toolchain. Dependencies come from ``pom.xml`` or ``build.gradle``, not
    pip — so ``install`` (auto-heal) is a no-op and ``ensure`` does nothing (the build
    resolves on ``mvn test`` / ``gradle test``). ``python`` is unavailable by design.

    ``build_tool`` is what the layout detected. It is carried here so the *runner* can be
    chosen from it: `kotlin-support-roadmap.md` §9.3 is the whole reason `GradleTestRunner`
    exists — "Java codegen on a Gradle project cannot run its tests today" — and until this
    field existed the Java row still hardwired Maven, so the gap §9.3 was written to close
    stayed open while the roadmap counted it delivered.
    """

    declared: set[str] = set()

    def __init__(self, build_tool: str = "maven") -> None:
        self.build_tool = build_tool or "maven"

    @property
    def python(self) -> str:
        raise RuntimeError("JavaToolEnvironment has no Python interpreter")

    async def ensure(self, worktree: Path | str) -> None:
        return None

    async def install(self, packages: list[str]) -> bool:
        return False  # Java deps are declared in the build file, not pip-installed

    def describe(self) -> str:
        source = "build.gradle" if self.build_tool == "gradle" else "pom.xml"
        return f"java toolchain ({self.build_tool.capitalize()}; deps resolved from {source})"


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


class KotlinToolEnvironment:
    """Kotlin/JVM build toolchain (Gradle). Dependencies are declared in
    ``build.gradle.kts`` and resolved by the build, not pip-installed — so ``install``
    (auto-heal) is a no-op and ``ensure`` does nothing. ``python`` is unavailable by design.

    P8, D13. Kotlin has no compiler to find: ``kotlin("jvm")`` in the build script brings
    its own, versioned with the project, which is why the probe looks for Gradle and a JDK
    and never for ``kotlinc``.
    """

    declared: set[str] = set()

    @property
    def python(self) -> str:
        raise RuntimeError("KotlinToolEnvironment has no Python interpreter")

    async def ensure(self, worktree: Path | str) -> None:
        return None

    async def install(self, packages: list[str]) -> bool:
        return False  # Kotlin deps are declared in build.gradle.kts, not pip-installed

    def describe(self) -> str:
        return "kotlin toolchain (Gradle; deps resolved from build.gradle.kts)"


def gradle_available(root: Path | str = ".") -> bool:
    """True when this project can run Gradle at all — a wrapper here, or ``gradle`` on PATH.

    The wrapper counts even though Gradle itself is absent: ``./gradlew`` downloads the
    version the project pins, which is the whole point of committing it.
    """
    return (Path(root) / "gradlew").is_file() or shutil.which("gradle") is not None


def android_toolchain_available() -> bool:
    """True when an Android SDK is installed (P9, D13).

    The Android Gradle Plugin reads ``ANDROID_HOME`` itself and fails the *configuration*
    phase without it — minutes into a build, with a message about a missing SDK directory
    rather than about the machine being unprepared. Probing first turns that into one
    sentence before anything is generated.

    Deliberately no check for an emulator or a connected device: Spine runs JVM unit tests
    and never instrumented ones (§10), so a headless machine with only the SDK is supported.
    """
    from orchestrator.sdlc.android import android_sdk_root

    return android_sdk_root() is not None


def kotlin_project_error(root: Path | str, layout: ToolchainLayout) -> str | None:
    """Why Kotlin codegen cannot run against *this* checkout, or ``None`` if it can.

    Three failures, three sentences. ``kotlin_toolchain_available`` has already answered the
    machine-wide question (is there a JDK); every case here is a property of the repository,
    and collapsing them into one hint would tell someone with a perfectly good JDK and Gradle
    installed that they need a JDK and Gradle.
    """
    from orchestrator.sdlc import android

    root = Path(root)
    if not gradle_available(root):
        return (
            "Kotlin codegen needs Gradle: this project has no ./gradlew wrapper and there is "
            "no `gradle` on PATH. Add the wrapper (`gradle wrapper`) or install Gradle, then "
            "retry."
        )
    if android.is_android_project(root) and android.android_sdk_root() is None:
        return (
            "This is an Android project and no Android SDK was found. Install one (Android "
            "Studio, or `sdkmanager`) and set ANDROID_HOME, then retry. Only JVM unit tests "
            "are run, so no emulator or device is needed."
        )
    if layout.mode == "existing" and not layout.source_dir:
        # A multi-module build where nothing claimed the package: `_module_layout` produced
        # an empty placement on purpose rather than picking a module on a hunch.
        modules = [m.relative_to(root).as_posix() for m in android.gradle_modules(root) if m != root]
        listed = ", ".join(modules[:8]) + (", …" if len(modules) > 8 else "")
        return (
            f"This Gradle build has {len(modules)} modules and none of them holds the target "
            "package, so there is no way to tell which one this feature belongs in. Re-run "
            f"with --package-name <the module's package> (modules here: {listed}), or --layout new "
            "to scaffold a standalone project instead."
        )
    return None


def kotlin_toolchain_available() -> bool:
    """True if a JDK is on PATH — the half of the prerequisite that is machine-wide.

    Deliberately **not** a check for ``kotlinc``: the Kotlin compiler arrives as a Gradle
    plugin pinned in the build script, so a machine with a JDK and Gradle can build Kotlin
    without a Kotlin install anywhere on it. Gradle itself is checked per-repository by
    :func:`gradle_available`, because a committed ``./gradlew`` makes a project buildable
    on a machine that has no Gradle — and a PATH-only probe would reject most real ones.
    """
    return shutil.which("java") is not None


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


class PhpToolEnvironment:
    """Composer when declared; otherwise a checksum-pinned, workspace-local PHPUnit."""

    declared: set[str] = set()

    def __init__(self) -> None:
        self.php = shutil.which("php") or "php"
        self.version = "unknown"
        self.phpunit: str | None = None
        self.setup_note = ""

    @property
    def python(self) -> str:
        raise RuntimeError("PhpToolEnvironment has no Python interpreter")

    async def ensure(self, worktree: Path | str) -> None:
        from orchestrator.sdlc.php import read_composer
        from orchestrator.sdlc.testrunner import _exec_capture

        root = Path(worktree).resolve()
        rc, output = await _exec_capture((self.php, "--version"), cwd=str(root), timeout=30)
        match = re.search(r"PHP (\d+)\.(\d+)", output)
        if rc or match is None:
            raise RuntimeError(f"Cannot determine PHP version: {output}")
        version = (int(match[1]), int(match[2]))
        self.version = f"{version[0]}.{version[1]}"
        pin_file = root / ".php-version"
        if pin_file.is_file():
            pin = pin_file.read_text().strip()
            requested = re.fullmatch(r"(\d+)\.(\d+)(?:\.\d+)?", pin)
            if requested and version != (int(requested[1]), int(requested[2])):
                raise RuntimeError(
                    f"Repository requests PHP {pin}; found {self.version}. Select the requested PHP on PATH."
                )
        manifest = read_composer(root)
        if manifest is not None:
            composer = shutil.which("composer")
            if composer is None:
                raise RuntimeError("PHP repository has composer.json: install Composer on PATH, then retry.")
            # An interrupted/extraction-failed install can leave a bin proxy without an
            # autoloader. Retry once before codegen; the model cannot repair tool setup.
            for _attempt in range(2):
                rc, output = await _exec_capture(
                    (composer, "install", "--no-interaction", "--prefer-dist"),
                    cwd=str(root),
                    timeout=600,
                )
                if rc == 0:
                    break
            self.setup_note = (
                f"Composer install exited {rc}: {output[-1000:]}" if rc else "Composer dependencies installed"
            )
            self.phpunit = str(root / "vendor/bin/phpunit")
            if not Path(self.phpunit).is_file() or not (root / "vendor/autoload.php").is_file():
                raise RuntimeError(
                    "Composer did not provide vendor/bin/phpunit and vendor/autoload.php. "
                    f"Declare phpunit/phpunit in require-dev. {self.setup_note}"
                )
            return
        if version < (7, 3):
            raise RuntimeError(
                f"PHP {self.version} is too old for the modern PHPUnit runner (requires PHP >=7.3)."
            )
        release, checksum = _PHPUNIT_PHARS[11 if version >= (8, 2) else 9]
        destination = root.parent / f".sdlc-phpunit-{release}.phar"
        await asyncio.to_thread(_ensure_phpunit_phar, destination, release, checksum)
        self.phpunit = str(destination)
        self.setup_note = f"PHPUnit {release} (verified PHAR outside worktree)"

    async def install(self, packages: list[str]) -> bool:
        return False

    def describe(self) -> str:
        return f"PHP {self.version}; {self.setup_note}"


# SHA-256 published at https://phar.phpunit.de/; use immutable release URLs.
_PHPUNIT_PHARS = {
    11: ("11.5.56", "915fa161f496dc04a45cd6032855879bca0bab644048cd0516982dffe678e9f1"),
    9: ("9.6.36", "d9552a130747f02f9d7fc2427b143189c638e273c502c8faa88ab6b04c5f2662"),
}


def _ensure_phpunit_phar(destination: Path, release: str, checksum: str) -> None:
    import hashlib
    import tempfile

    import httpx

    if (
        destination.is_file()
        and not destination.is_symlink()
        and hashlib.sha256(destination.read_bytes()).hexdigest() == checksum
    ):
        return
    try:
        response = httpx.get(
            f"https://phar.phpunit.de/phpunit-{release}.phar", timeout=60, follow_redirects=True
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise RuntimeError(f"Cannot download PHPUnit {release}: {exc}") from exc
    data = response.content
    if hashlib.sha256(data).hexdigest() != checksum:
        raise RuntimeError(f"PHPUnit {release} checksum mismatch; refusing to execute download")
    # Unique temporary file and atomic replace avoid partial downloads and concurrent writers.
    with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as staged:
        temporary = Path(staged.name)
        staged.write(data)
    try:
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)


def php_toolchain_available() -> bool:
    return shutil.which("php") is not None


class PerlToolEnvironment:
    """Core Perl/prove are required; installing declared CPAN dependencies is best effort."""

    declared: set[str] = set()

    def __init__(self) -> None:
        self.setup_note = "dependencies not checked"

    @property
    def python(self) -> str:
        raise RuntimeError("PerlToolEnvironment has no Python interpreter")

    async def ensure(self, worktree: Path | str) -> None:
        import logging

        from orchestrator.sdlc.perl import distributions, perl_files
        from orchestrator.sdlc.testrunner import _exec_capture

        root = Path(worktree).resolve()
        if not perl_toolchain_available():
            raise RuntimeError("Perl codegen needs `perl` and `prove` on PATH (install both, then retry).")
        if next(perl_files(root, (".xs",)), None) is not None:
            raise RuntimeError("Perl XS builds are unsupported: this runner does not compile .xs sources.")
        notes: list[str] = []
        cpanm = shutil.which("cpanm")
        if cpanm is None:
            warning = "WARNING: cpanm is unavailable; using installed dependencies"
            logging.getLogger(__name__).warning(warning)
            notes.append(warning)
        for dist in distributions(root) or [root]:
            if not (dist / "cpanfile").is_file():
                note = f"{dist.relative_to(root)}: no cpanfile; using installed dependencies"
            elif cpanm is None:
                note = "cpanfile present; dependency installation skipped because cpanm is unavailable"
            else:
                try:
                    rc, output = await _exec_capture(
                        (cpanm, "--installdeps", ".", "--notest"), cwd=str(dist), timeout=600
                    )
                    note = (
                        f"{dist.relative_to(root)}: CPAN dependencies installed"
                        if rc == 0
                        else f"WARNING: cpanm exited {rc}; using installed dependencies: {output[-1000:]}"
                    )
                except OSError as exc:
                    note = f"WARNING: cpanm could not run; using installed dependencies: {exc}"
            if note.startswith("WARNING"):
                logging.getLogger(__name__).warning(note)
            notes.append(note)
        self.setup_note = "; ".join(notes)

    async def install(self, packages: list[str]) -> bool:
        return False

    def describe(self) -> str:
        return f"Perl/prove; {self.setup_note}"


def perl_toolchain_available() -> bool:
    return shutil.which("perl") is not None and shutil.which("prove") is not None


class GoToolEnvironment:
    """Go build toolchain. Dependencies are resolved from ``go.mod`` (not pip), so
    ``install`` (auto-heal) is a no-op; ``ensure`` runs ``go mod download`` best-effort
    (a no-op for a stdlib-only greenfield module, and offline-safe). ``python`` is
    unavailable by design."""

    declared: set[str] = set()

    @property
    def python(self) -> str:
        raise RuntimeError("GoToolEnvironment has no Python interpreter")

    async def ensure(self, worktree: Path | str) -> None:
        exe = shutil.which("go")
        if exe is None:
            return None
        try:
            proc = await asyncio.create_subprocess_exec(
                exe,
                "mod",
                "download",
                cwd=str(worktree),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            await proc.communicate()
        except OSError:
            return None  # best-effort: a build/test run surfaces any real dependency error
        return None

    async def install(self, packages: list[str]) -> bool:
        return False  # Go deps are declared in go.mod / fetched by `go get`, not pip

    def describe(self) -> str:
        return "go toolchain (deps via go mod download)"


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


def go_toolchain_available() -> bool:
    """True if the ``go`` toolchain is on PATH (Go codegen prerequisite)."""
    return shutil.which("go") is not None


def make_test_environment(language: str = "python", *, build_tool: str = "") -> TestEnvironment:
    """The test environment for ``language``: Java toolchain, Node toolchain
    (``build_tool`` selects the package manager), or a Python venv
    (``VenvTestEnvironment`` unless ``SDLC_TEST_ISOLATION=local``)."""
    from orchestrator.sdlc.toolchains import get_toolchain

    return get_toolchain(language).environment(build_tool)


def make_test_runner(language: str, env: TestEnvironment) -> TestRunner:
    """The runner for ``language``: Maven for Java, the package manager's ``test``
    script for TypeScript, pytest (on the env's interpreter) for Python."""
    from orchestrator.sdlc.toolchains import get_toolchain

    return get_toolchain(language).runner(env)


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


# Test dependencies rarely live in ``project.dependencies``: pytest plugins, ASGI
# harnesses and fixture libraries sit in a dev/test extra or a PEP 735 group. An env
# built from runtime deps alone cannot import the project's own suite — pytest dies at
# *collection*, so every refine iteration sees the same error and the verdict is FAILED
# no matter how good the generated code is. Only test-ish groups are pulled in: an extra
# like ``cpp`` or ``sql`` is a heavyweight parser install no test run needs.
_TEST_DEP_GROUPS = frozenset({"dev", "test", "tests", "testing", "dev-dependencies"})


def _test_group_deps(table: object) -> list[str]:
    """Requirement strings from the dev/test entries of an extras / group table."""
    if not isinstance(table, dict):
        return []
    out: list[str] = []
    for name, entries in table.items():
        if str(name).lower() not in _TEST_DEP_GROUPS or not isinstance(entries, list):
            continue
        # PEP 735 groups may hold ``{include-group = "…"}`` tables; keep requirements only.
        out += [str(entry) for entry in entries if isinstance(entry, str)]
    return out


def _project_dependencies(root: Path) -> list[str]:
    """The project's declared deps — runtime plus its dev/test group (best-effort)."""
    deps: list[str] = []
    pyproject = root / "pyproject.toml"
    if pyproject.is_file():
        try:
            import tomllib

            data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
            project = data.get("project") or {}
            deps += [str(d) for d in project.get("dependencies") or []]
            deps += _test_group_deps(project.get("optional-dependencies"))
            deps += _test_group_deps(data.get("dependency-groups"))
        except (OSError, ValueError, ModuleNotFoundError):
            pass
    # An AWS SAM repository declares its dependencies per function, beside each handler, and
    # leaves out what the Lambda runtime provides (CB-764: `boto3` was never installed, so every
    # test importing a handler failed to collect). Read both.
    functions = function_requirements(root)
    if sam_template(root) is not None:
        deps += list(RUNTIME_PROVIDED)
    for req in (*root.glob("requirements*.txt"), root / "requirements" / "base.txt", *functions):
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
    "PhpToolEnvironment",
    "php_toolchain_available",
    "CToolEnvironment",
    "DotnetToolEnvironment",
    "GoToolEnvironment",
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
    "go_toolchain_available",
    "java_toolchain_available",
    "meson_toolchain_available",
    "make_test_environment",
    "make_test_runner",
    "node_toolchain_available",
    "parse_missing_module",
    "run_with_autoheal",
    "sql_toolchain_available",
]
