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
import re
import shutil
import sys
from pathlib import Path

from orchestrator.sdlc import android, process
from orchestrator.sdlc.contracts import TestRunner as TestRunner
from orchestrator.sdlc.contracts import TestRunResult as TestRunResult
from orchestrator.sdlc.diagnostics import digest_dotnet_output
from orchestrator.sdlc.process import _SECRET_ENV_PREFIXES as _SECRET_ENV_PREFIXES

# Cap captured output so a chatty test run can't bloat the activity result /
# the audit row; the tail is the most useful part for the refinement prompt.
_MAX_OUTPUT_CHARS = 4000
# Seconds a language's suite may run. 600 is what the Maven / dotnet / npm / CMake /
# Meson runners below already allow; ``SDLC_TEST_TIMEOUT`` raises it for a slow suite.
DEFAULT_TEST_TIMEOUT = 600.0


def _timeout_from_env() -> float:
    raw = os.getenv("SDLC_TEST_TIMEOUT")
    try:
        return float(raw) if raw else DEFAULT_TEST_TIMEOUT
    except ValueError:
        return DEFAULT_TEST_TIMEOUT


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


class SubprocessTestRunner:
    """Runs ``python -m pytest`` in the worktree via exec (no shell).

    ``passed`` is ``returncode == 0``. pytest's exit code 5 ("no tests
    collected") is treated as a failure so an empty worktree doesn't look green.
    """

    def __init__(self, python: str | None = None, *, timeout: float | None = None) -> None:
        self._python = python or sys.executable
        # Python ran on a 120s budget while every other language runner here gets 600 —
        # an oversight that reads as a code failure, not a clock one: a repo whose own
        # suite takes ~100s locally is slower still in a cold venv, so the run reports
        # FAILED for work that was fine. Aligned with the rest, and overridable for a
        # genuinely long suite.
        self._timeout = timeout if timeout is not None else _timeout_from_env()

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
        # Errors first, lifted from the WHOLE output, then the tail. A plain tail on a project
        # with 138 warnings (NSS-1243) kept warnings and dropped the one error that mattered.
        output = digest_dotnet_output(
            stdout_bytes.decode("utf-8", "replace"), Path(path), cap=_MAX_OUTPUT_CHARS
        )
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


class CTestRunner:
    """Configures, builds, and tests a CMake project via ``cmake`` + ``ctest``.

    Runs, in order: ``cmake -S <root> -B <root>/build``, ``cmake --build <root>/build``,
    then ``ctest --test-dir <root>/build --output-on-failure``. The first non-zero step
    fails the run (``passed`` is rc == 0) and its output — a configure error, a
    compiler error, or a failing assertion — is what the refine prompt needs. The
    greenfield scaffold's ``CMakeLists.txt`` globs ``src/*.c`` into a library and each
    ``tests/*.c`` into a ctest executable, so no build argument is needed."""

    def __init__(self, cmake: str = "cmake", ctest: str = "ctest", *, timeout: float = 600.0) -> None:
        self._cmake = cmake
        self._ctest = ctest
        self._timeout = timeout

    async def run(self, *, path: str) -> TestRunResult:
        build = str(Path(path) / "build")
        steps = (
            (self._cmake, "-S", path, "-B", build),
            (self._cmake, "--build", build),
            (self._ctest, "--test-dir", build, "--output-on-failure"),
        )
        captured: list[str] = []
        for argv in steps:
            rc, out = await _exec_capture(argv, cwd=path, timeout=self._timeout)
            captured.append(out)
            if rc != 0:
                return TestRunResult(passed=False, returncode=rc, output=_clip("\n".join(captured)))
        return TestRunResult(passed=True, returncode=0, output=_clip("\n".join(captured)))


class MesonTestRunner:
    """Configures, builds, and tests a Meson project via ``meson`` + ``ninja``.

    Runs ``meson setup build`` once (configure), then ``meson test -C build
    --print-errorlogs`` — which rebuilds changed targets and re-configures on a
    ``meson.build`` edit before running the tests. The first non-zero step fails the
    run (``passed`` is rc == 0); its output is the configure/compiler/test-log error
    the refine prompt needs. Used for brownfield C repos whose build system is
    Meson; greenfield still scaffolds CMake."""

    def __init__(self, meson: str = "meson", *, timeout: float = 600.0) -> None:
        self._meson = meson
        self._timeout = timeout

    async def run(self, *, path: str) -> TestRunResult:
        captured: list[str] = []
        if not (Path(path) / "build").exists():
            rc, out = await _exec_capture((self._meson, "setup", "build"), cwd=path, timeout=self._timeout)
            captured.append(out)
            if rc != 0:
                return TestRunResult(passed=False, returncode=rc, output=_clip("\n".join(captured)))
        rc, out = await _exec_capture(
            (self._meson, "test", "-C", "build", "--print-errorlogs"), cwd=path, timeout=self._timeout
        )
        captured.append(out)
        return TestRunResult(passed=rc == 0, returncode=rc, output=_clip("\n".join(captured)))


class PhpUnitTestRunner:
    """Lint every changed PHP file, then run only this change's PHPUnit test files."""

    def __init__(self, php: str = "php", *, phpunit: str | None = None, timeout: float = 600) -> None:
        self._php = php
        self._phpunit = phpunit
        self._timeout = timeout

    async def run(self, *, path: str) -> TestRunResult:
        from orchestrator.sdlc.php import changed_php_files, read_phpunit_config
        from orchestrator.sdlc.preflight import make_preflight_runner

        try:
            root = Path(path).resolve()
            config = read_phpunit_config(root)
            changed = await changed_php_files(root, capture=_exec_capture)
            lint = await make_preflight_runner("php", executable=self._php, capture=_exec_capture).run(
                path=path
            )
            if not lint.passed:
                return TestRunResult(False, 1, lint.output)
            tests = [name for name in changed if name.endswith(config.suffix)]
            if not tests:
                return TestRunResult(
                    False, 5, f"No changed PHP tests matching {config.suffix}; refusing an empty test run."
                )
            executable = root / "vendor/bin/phpunit"
            if not executable.is_file():
                if self._phpunit is None:
                    return TestRunResult(
                        False, 2, "PHPUnit is unavailable; run PhpToolEnvironment.ensure() first."
                    )
                executable = Path(self._phpunit)
            argv: tuple[str, ...] = (
                self._php,
                str(executable),
                "--do-not-cache-result",
                "--fail-on-risky",
                "--fail-on-skipped",
                "--fail-on-incomplete",
            )
            argv += ("--configuration", config.path) if config.path else ("--no-configuration",)
            captured = [lint.output]
            for test in tests:
                # One explicit file per invocation also works on PHPUnit 9, which only
                # accepts one positional argument. Never load the target's entire suite.
                rc, output = await _exec_capture(
                    (*argv, str(root / test)), cwd=str(root), timeout=self._timeout
                )
                captured.append(f"# {test}\n{output}")
                if rc or "No tests executed" in output or "No tests found" in output:
                    return TestRunResult(False, rc or 5, _clip("\n".join(captured)))
            return TestRunResult(True, 0, _clip("\n".join(captured)))
        except (OSError, ValueError, RuntimeError) as exc:
            return TestRunResult(False, 2, str(exc))


class ProveTestRunner:
    """Compile changed Perl sources, test their owners, then every distribution's suite."""

    def __init__(self, perl: str = "perl", prove: str = "prove", *, timeout: float = 600) -> None:
        self._perl = perl
        self._prove = prove
        self._timeout = timeout

    async def run(self, *, path: str) -> TestRunResult:
        from orchestrator.sdlc.perl import (
            changed_perl_files,
            distribution_for,
            distributions,
            include_args,
            owning_tests,
            perl_files,
        )
        from orchestrator.sdlc.preflight import make_preflight_runner

        captured: list[str] = []
        try:
            root = Path(path).resolve()
            if next(perl_files(root, (".xs",)), None) is not None:
                return TestRunResult(False, 2, "Perl XS builds are unsupported; .xs requires compilation.")
            preflight = await make_preflight_runner("perl", executable=self._perl, capture=_exec_capture).run(
                path=path
            )
            captured.append(preflight.output)
            if not preflight.passed:
                return TestRunResult(False, 1, _clip("\n".join(captured)))
            changed = await changed_perl_files(root, capture=_exec_capture)
            # A clean independent checkout still compiles and tests the real distribution.
            sources = changed or list(perl_files(root))
            owners = {distribution_for(file, root) for file in sources}
            suites = sorted(set(distributions(root)) | owners) or [root]
            # Changed sources were compiled by preflight; clean checkouts still compile all.
            for file in () if changed else sources:
                if file.suffix not in {".pm", ".pl"}:
                    continue
                dist = distribution_for(file, root)
                argv = (self._perl, *include_args(dist), "-c", str(file))
                rc, output = await _exec_capture(argv, cwd=str(dist), timeout=self._timeout)
                captured.append(f"# {' '.join(argv)}\n{output}")
                if rc:
                    return TestRunResult(False, rc, _clip("\n".join(captured)))
            targets: list[tuple[Path, Path]] = []
            for file in changed:
                dist = distribution_for(file, root)
                target = owning_tests(file, dist)
                if (dist, target) not in targets:
                    targets.append((dist, target))
            # Whole suites always follow owning targets, including other distributions.
            targets.extend((dist, dist / "t") for dist in suites)
            for dist, target in targets:
                tests = (
                    [target]
                    if target.is_file() and target.suffix == ".t"
                    else list(perl_files(target, (".t",)))
                )
                if not tests:
                    return TestRunResult(
                        False,
                        5,
                        _clip(
                            "\n".join(captured)
                            + f"\nNo Perl tests found in {target}; refusing an empty green."
                        ),
                    )
                # prove does not recurse by default; nested suites need -r to be whole.
                recurse = ("-r",) if target.is_dir() and any(f.parent != target for f in tests) else ()
                argv = (
                    self._prove,
                    "-l",
                    *include_args(dist)[2:],
                    *recurse,
                    target.relative_to(dist).as_posix(),
                )
                rc, output = await _exec_capture(argv, cwd=str(dist), timeout=self._timeout)
                captured.append(f"# ({dist.relative_to(root)}) {' '.join(argv)}\n{output}")
                if rc or "NOTESTS" in output:
                    return TestRunResult(False, rc or 5, _clip("\n".join(captured)))
            return TestRunResult(True, 0, _clip("\n".join(captured)))
        except (OSError, ValueError, RuntimeError) as exc:
            return TestRunResult(False, 2, _clip("\n".join(captured) + f"\n{exc}"))


class GoTestRunner:
    """Builds and tests the Go module(s) a change actually touched, via ``go build ./...``
    then ``go test ./... -v`` **run from each changed module's directory**.

    A Go repo is often multi-module (its own ``go.mod`` per subtree); running ``go test
    ./...`` only from the repo root silently skips code generated into a sub-module — which
    reports a false green (a passing root module while the changed sub-module never compiled).
    So this discovers the modules containing the changed ``.go`` files (via ``git status`` +
    the nearest ``go.mod``) and builds/tests each. The first non-zero step fails the run
    (``passed`` is rc == 0); its output is what the refine prompt needs. Falls back to the
    repo root when nothing is detected (fresh greenfield scaffold, or no git)."""

    def __init__(self, go: str = "go", *, timeout: float = 600.0) -> None:
        self._go = go
        self._timeout = timeout

    async def run(self, *, path: str) -> TestRunResult:
        modules = await self._changed_modules(path) or ["."]
        captured: list[str] = []
        for mod in modules:
            cwd = path if mod == "." else str(Path(path) / mod)
            for argv in ((self._go, "build", "./..."), (self._go, "test", "./...", "-v")):
                rc, out = await _exec_capture(argv, cwd=cwd, timeout=self._timeout)
                captured.append(f"# ({mod}) {' '.join(argv)}\n{out}")
                if rc != 0:
                    return TestRunResult(passed=False, returncode=rc, output=_clip("\n".join(captured)))
        return TestRunResult(passed=True, returncode=0, output=_clip("\n".join(captured)))

    async def _changed_modules(self, path: str) -> list[str]:
        """Repo-relative dirs of the go.mod modules that own the worktree's changed ``.go``
        files (uncommitted at test time). ``.`` is the root module."""
        rc, out = await _exec_capture(("git", "status", "--porcelain"), cwd=path, timeout=60.0)
        if rc != 0:
            return []
        root = Path(path)
        mods: set[str] = set()
        for line in out.splitlines():
            name = line[3:].strip() if len(line) > 3 else ""
            if "->" in name:  # a rename: "old -> new"
                name = name.split("->", 1)[1].strip()
            name = name.strip('"')
            if not name.endswith(".go"):
                continue
            mod_dir = _nearest_go_mod(root / name, root)
            if mod_dir is not None:
                rel = mod_dir.relative_to(root).as_posix()
                mods.add("." if rel == "" else rel)
        return sorted(mods)


class GradleTestRunner:
    """Runs ``./gradlew test`` (or ``gradle test``) in a JVM worktree via exec, no shell.

    P8 of docs/specs/kotlin-support-roadmap.md, §9.3. ``layout.py`` has detected Gradle
    since the Java track shipped, but ``MavenTestRunner`` was the only JVM runner — so
    codegen on *any* Gradle project, Java included, could not run its tests. This closes
    that for both languages at once; Kotlin simply has no Maven era to be backwards
    compatible with.

    **The wrapper is preferred, and that is not a style choice.** ``./gradlew`` pins the
    Gradle version the project was written against and downloads it on first use, so it
    builds the same way on a machine that has never seen Gradle. A bare ``gradle`` on PATH
    is whatever version happens to be installed, which for a real project is a coin flip.
    Wrapper first, ``gradle`` second, and when there is neither the run **fails with a
    hint** rather than reporting a green suite that never compiled anything.

    **It runs the module that owns the changed files** — the Go 4.5 lesson. A Gradle build
    is routinely multi-project, and ``./gradlew test`` from the root does run every module's
    tests, but a repo whose root aggregates dozens of Android modules spends minutes on code
    the change never touched. Naming the owning modules (``:core:data:test``) keeps the
    refine loop's feedback tied to what it just wrote. Falling back to the whole build when
    nothing is detected is the safe direction: it tests more, never less.

    ``--console=plain`` because the refine prompt reads this output, and Gradle's rich
    console is ANSI cursor movement that renders as line noise in a transcript.
    """

    def __init__(self, gradle: str = "gradle", *, timeout: float = 900.0) -> None:
        # Gradle's first run downloads a distribution and resolves a dependency graph, so
        # the default is longer than Maven's: a cold wrapper bootstrap alone can take
        # minutes, and a timeout there would look exactly like a failing test.
        self._gradle = gradle
        self._timeout = timeout
        # Variant task names Gradle has already told us about, keyed by the name we asked
        # for. The refine loop reuses one runner across iterations, so the build that
        # discovers `testDemoDebugUnitTest` is the only one that pays for the discovery.
        self._resolved: dict[str, str] = {}

    async def run(self, *, path: str) -> TestRunResult:
        argv_base = self._invocation(Path(path))
        if argv_base is None:
            return TestRunResult(
                passed=False,
                returncode=-1,
                output=(
                    "gradle test could not run: this project has no ./gradlew wrapper and no "
                    "`gradle` on PATH. Add the Gradle wrapper (`gradle wrapper`) or install "
                    "Gradle, then retry."
                ),
            )
        tasks = [self._resolved.get(t, t) for t in (await self._changed_module_tasks(path) or ["test"])]
        argv = (*argv_base, *tasks, "--console=plain")
        try:
            rc, out = await _exec_capture(argv, cwd=path, timeout=self._timeout)
        except (PermissionError, OSError) as exc:
            # A `gradlew` committed without its executable bit is common enough on
            # Windows-authored repositories to be worth naming. The class docstring
            # promises a hinted failure rather than a silent green; an exception escaping
            # `run()` is neither, and it aborts the whole feature run instead of the test.
            return TestRunResult(
                passed=False,
                returncode=-1,
                output=(
                    f"gradle test could not run: {argv[0]} is present but not executable "
                    f"({exc}). Run `chmod +x gradlew`, or remove the wrapper to fall back to "
                    "`gradle` on PATH, then retry."
                ),
            )
        retry = _variant_tasks(out, tasks) if rc != 0 else None
        if retry is None:
            return TestRunResult(passed=rc == 0, returncode=rc, output=_clip(f"# {' '.join(argv)}\n{out}"))
        self._resolved.update(dict(zip(tasks, retry, strict=True)))
        argv = (*argv_base, *retry, "--console=plain")
        rc, out = await _exec_capture(argv, cwd=path, timeout=self._timeout)
        # The note is prepended *after* clipping, not folded into the text being clipped:
        # `_clip` keeps the tail, and an Android build log is long enough to push anything
        # written before it off the front — which would leave the reader of this transcript
        # wondering why the task named here is not the task that ran.
        note = (
            f"# {' '.join(tasks)} does not exist in this build (Gradle listed the build-variant\n"
            f"# tasks that replace it), so it was re-run as:\n# {' '.join(argv)}\n"
        )
        return TestRunResult(passed=rc == 0, returncode=rc, output=note + _clip(out))

    def _invocation(self, root: Path) -> tuple[str, ...] | None:
        """The wrapper when the project ships one, else ``gradle``, else ``None``."""
        wrapper = root / "gradlew"
        if wrapper.is_file():
            return (str(wrapper),)
        return (self._gradle,) if shutil.which(self._gradle) else None

    async def _changed_module_tasks(self, path: str) -> list[str]:
        """``:core:data:test`` for each Gradle module owning a changed JVM source file.

        Empty when nothing is detected — a fresh scaffold, a non-git worktree, or a change
        outside any module — and the caller then runs the whole build's ``test``.
        """
        # `-uall` rather than the default: git collapses a wholly-new untracked directory to
        # the directory itself ("core/model/src/test/"), and a generated test is very often
        # the first file in a directory that did not exist. Without this the change looks
        # like it touches no source file at all and the whole build is tested instead.
        rc, out = await _exec_capture(("git", "status", "--porcelain", "-uall"), cwd=path, timeout=60.0)
        if rc != 0:
            return []
        root = Path(path)
        modules: set[str] = set()
        for line in out.splitlines():
            name = line[3:].strip() if len(line) > 3 else ""
            if "->" in name:  # a rename reports "old -> new"
                name = name.split("->", 1)[1].strip()
            name = name.strip('"')
            if not name.endswith((".kt", ".kts", ".java")):
                continue
            module_dir = _nearest_gradle_module(root / name, root)
            if module_dir is None:
                continue
            rel = module_dir.relative_to(root).as_posix()
            # The root project's own tests are the bare `test` task; a subproject is
            # addressed by its path with `/` as `:`, which is Gradle's own spelling.
            task = android.unit_test_task(module_dir)
            modules.add(task if rel == "" else f":{rel.replace('/', ':')}:{task}")
        return sorted(modules)


# Gradle spells the same complaint two ways, and both name the tasks that do exist:
#   Cannot locate tasks that match ':core:data:testDebugUnitTest' as task
#   'testDebugUnitTest' is ambiguous in project ':core:data'. Candidates are:
#   'testDemoDebugUnitTest', 'testProdDebugUnitTest'.
#   Task 'testDebugUnitTest' not found in project ':app'. Some candidates are: '…'.
_ASKED_TASK = re.compile(
    r"Cannot locate tasks that match '(?P<path>[^']+)'|Task '(?P<task>[^']+)' not found in project"
)
_CANDIDATES = re.compile(r"[Cc]andidates are: (?P<list>'[^']+'(?:,\s*'[^']+')*)")
_QUOTED = re.compile(r"'([^']+)'")

#: A Gradle task path, and nothing else. This text comes out of the *repository's* build
#: — an error message a build script can write whatever it likes into — and goes straight
#: back in as an argv element, so a "candidate" spelled `--init-script` would be a flag to
#: Gradle rather than a task. Anchored and leading-dash-free by construction.
_TASK_NAME = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.:-]*")


def _variant_tasks(output: str, tasks: list[str]) -> list[str] | None:
    """Rewrite ``tasks`` using the variant task names Gradle just named, or ``None``.

    ``testDebugUnitTest`` is the documented Android unit-test task and it does not
    universally exist: the moment a module declares product flavours, the Android Gradle
    Plugin replaces it with one task per flavour — the validation app applies flavours to
    every *library* module through a convention plugin, so ``:core:data:testDebugUnitTest``
    is ambiguous there and ``:core:data:testDemoDebugUnitTest`` is what runs.

    Flavour names cannot be read off a build script (they are computed in Kotlin, inside a
    separate included build), so this does not try to predict them. Gradle's own failure
    lists the candidates; using that list is the difference between a derived answer and a
    guess. A ``Debug`` variant is preferred because unit tests run against the debug build
    type by convention, and the first of Gradle's alphabetical candidates settles ties so
    that two runs of the same build pick the same task.
    """
    asked = _ASKED_TASK.search(output)
    listed = _CANDIDATES.search(output)
    if asked is None or listed is None:
        return None
    path = asked.group("path") or asked.group("task") or ""
    name = path.rsplit(":", 1)[-1]
    candidates = [c for c in _QUOTED.findall(listed.group("list")) if _TASK_NAME.fullmatch(c)]
    if not name or not candidates:
        return None
    # §10: never an emulator, and never by luck. `connectedDebugAndroidTest` contains
    # "Debug" and would win the preference below, so instrumented tasks are removed by
    # name first — leaving the guarantee on an explicit rule rather than on an
    # undocumented property of whatever Gradle happens to print in its error.
    candidates = [c for c in candidates if not _INSTRUMENTED.search(c)]
    if not candidates:
        return None
    debug = [c for c in candidates if "Debug" in c]
    chosen = (debug or candidates)[0]
    if chosen == name:
        return None
    rewritten = [t if t.rsplit(":", 1)[-1] != name else t[: len(t) - len(name)] + chosen for t in tasks]
    return rewritten if rewritten != tasks else None


#: Task names that run on a device or emulator. AGP spells them several ways —
#: `connectedDebugAndroidTest`, `demoDebugAndroidTest`, `pixel2api30DebugAndroidTest`
#: for a managed device — and none of them may ever be selected (§10).
_INSTRUMENTED = re.compile(r"(?i)connected|androidTest|managedDevice|deviceTest")


def _nearest_gradle_module(start: Path, root: Path) -> Path | None:
    """Nearest ancestor of ``start`` holding a Gradle build script, within ``root``."""
    d = start.parent
    while True:
        if (d / "build.gradle.kts").is_file() or (d / "build.gradle").is_file():
            return d
        if d == root:
            return None
        d = d.parent
        if d != root and root not in d.parents:
            return None


def _nearest_go_mod(start: Path, root: Path) -> Path | None:
    """Nearest ancestor of ``start`` (a file) within ``root`` that holds a ``go.mod``."""
    d = start.parent
    while True:
        if (d / "go.mod").is_file():
            return d
        if d == root:
            return None
        d = d.parent
        if d != root and root not in d.parents:
            return None


def _clip(output: str) -> str:
    return output[-_MAX_OUTPUT_CHARS:] if len(output) > _MAX_OUTPUT_CHARS else output


async def _exec_capture(argv: tuple[str, ...], *, cwd: str, timeout: float) -> tuple[int, str]:
    """Compatibility seam; adapters receive this callable explicitly."""
    return await process.exec_capture(argv, cwd=cwd, timeout=timeout)


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


class SqlTestRunner:
    """Validates generated SQL by applying it to an ephemeral SQLite database.

    SQL has no unit-test process to exec (SQL Track B). Instead the migration
    ``.sql`` files in the worktree are transpiled to SQLite and applied in order
    (see ``sql_build.apply_sql``); ``passed`` means *the DDL applied cleanly* — a
    real DDL error (bad reference, duplicate table) is the refine signal. No
    external toolchain: ``sqlite3`` is in the standard library, so this always
    runs. Migration order is the ``migrations/`` fold order when present, else
    a stable path sort."""

    def __init__(self, dialect: str = "postgres") -> None:
        self._dialect = dialect

    async def run(self, *, path: str) -> TestRunResult:
        from orchestrator.pkg.migrations import find_migration_files
        from orchestrator.sdlc.sql_build import apply_sql

        root = Path(path)
        files = find_migration_files(root) or sorted(root.rglob("*.sql"))
        if not files:
            return TestRunResult(passed=False, returncode=-1, output="no .sql files to apply")
        texts = [f.read_text(encoding="utf-8") for f in files]
        result = await asyncio.to_thread(apply_sql, texts, dialect=self._dialect)
        if result.ok:
            n_tables = len(result.schema.tables) if result.schema else 0
            return TestRunResult(
                passed=True,
                returncode=0,
                output=f"applied {result.applied} statement(s) → {n_tables} table(s)",
            )
        return TestRunResult(passed=False, returncode=1, output=result.error)


class PostgresSqlTestRunner:
    """Validates generated SQL against a throwaway **Postgres** (SQL Track B, B4).

    Higher fidelity than the SQLite default — dialect-specific Postgres features
    apply as written. Spins an ephemeral Postgres via ``testcontainers`` (needs
    Docker + the ``sql-postgres`` extra) and applies the worktree's migrations
    with ``psycopg``. Opt-in via ``SDLC_SQL_ENGINE=postgres``; falls back to a
    clear failure (not a crash) when the toolchain is absent."""

    def __init__(self, dialect: str = "postgres", *, timeout: float = 300.0) -> None:
        self._dialect = dialect
        self._timeout = timeout

    async def run(self, *, path: str) -> TestRunResult:
        from orchestrator.pkg.migrations import find_migration_files
        from orchestrator.sdlc.sql_build import apply_sql_postgres

        root = Path(path)
        files = find_migration_files(root) or sorted(root.rglob("*.sql"))
        if not files:
            return TestRunResult(passed=False, returncode=-1, output="no .sql files to apply")
        texts = [f.read_text(encoding="utf-8") for f in files]

        def _run() -> TestRunResult:
            try:
                # `unused-ignore` because whether this ignore is needed depends on the
                # environment: with the `sql-postgres` extra installed the import resolves and
                # the ignore is redundant, without it the import is unfindable and the ignore is
                # required. Naming both codes keeps `mypy src tests` green either way — CI
                # installs one set, a contributor may have the other, and a gate that fails on a
                # file you did not touch is a gate people learn to distrust.
                from testcontainers.postgres import (  # type: ignore[import-not-found, unused-ignore]
                    PostgresContainer,
                )
            except ImportError:
                return TestRunResult(
                    passed=False,
                    returncode=-1,
                    output="postgres engine needs the 'sql-postgres' extra "
                    "(testcontainers + psycopg) and Docker",
                )
            with PostgresContainer("postgres:16-alpine", driver="psycopg") as pg:
                dsn = pg.get_connection_url().replace("postgresql+psycopg://", "postgresql://")
                result = apply_sql_postgres(texts, dsn, dialect=self._dialect)
            if result.ok:
                return TestRunResult(
                    passed=True, returncode=0, output=f"applied {result.applied} statement(s)"
                )
            return TestRunResult(passed=False, returncode=1, output=result.error)

        return await asyncio.to_thread(_run)


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
    "PhpUnitTestRunner",
    "DEFAULT_TEST_TIMEOUT",
    "CTestRunner",
    "DotnetTestRunner",
    "GoTestRunner",
    "MavenTestRunner",
    "MesonTestRunner",
    "NodeTestRunner",
    "PostgresSqlTestRunner",
    "SqlTestRunner",
    "StubTestRunner",
    "SubprocessTestRunner",
    "TestRunResult",
    "TestRunner",
    "pytest_available",
]
