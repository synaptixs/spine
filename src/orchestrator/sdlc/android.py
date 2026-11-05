"""Android specifics for Kotlin codegen — P9, D13 of ``docs/specs/kotlin-support-roadmap.md``.

An Android repository *is* a Kotlin repository, and P8 already reads it correctly:
``detect_kotlin_layout`` searches ``src/main/java`` precisely because Android keeps Kotlin
in the Java source root. Two things are still different, and each decides whether generated
code compiles at all.

**Sources live in Gradle submodules, never at the repository root.** The validation app is
28 modules and has no ``src/`` at its root, so the single-module placement P8 ships resolves
to a directory that belongs to no Gradle project: the file would be written, compiled by
nothing, and verified by a task that does not exist. Placement therefore chooses the module
*first*, keyed on the target package — the one piece of information that actually says where
a change belongs.

**The unit-test task is per-variant.** The Android Gradle Plugin compiles a library once per
build variant, so its JVM unit tests run as ``testDebugUnitTest``. A plain Kotlin/JVM module
in the same build — ``:core:model`` is one — still uses ``test``, so the task is a property
of the *module*, not of the repository, and a build that mixes both needs both spellings.

Instrumented tests (``connectedAndroidTest``, ``src/androidTest/``) are out of scope by
decision rather than omission: they need a device or an emulator (§10). A generated screen
gets a JVM-testable slice and the UI test is stated as an explicit TODO — said, not hidden.
"""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

from orchestrator.pkg.walk_rules import repo_files, walk_repo

BUILD_SCRIPTS = ("build.gradle.kts", "build.gradle")
SETTINGS_SCRIPTS = ("settings.gradle.kts", "settings.gradle")
# Both Gradle spellings of a JVM source root, in the order Android actually uses them.
SOURCE_ROOTS = ("java", "kotlin")
# `testDebugUnitTest` and not `test`: `test` depends on *every* variant's unit tests, so on
# a two-flavour module it runs the same assertions four times for no extra signal.
ANDROID_UNIT_TEST_TASK = "testDebugUnitTest"
JVM_TEST_TASK = "test"

# An `android { … }` block is the durable marker. The plugin *id* is not: Android projects
# of any size apply a convention plugin (`id("<product>.android.library")`), and the
# string `com.android.library` then appears only inside `build-logic/`, never in the module
# that is actually an Android library.
_ANDROID_BLOCK = re.compile(r"^\s*android\s*(\{|=)", re.MULTILINE)


def build_script(module: Path) -> Path | None:
    """The module's Gradle build script, Kotlin DSL preferred, or ``None``."""
    for name in BUILD_SCRIPTS:
        candidate = module / name
        if candidate.is_file():
            return candidate
    return None


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def is_android_module(module: Path) -> bool:
    """True when this Gradle module is built by the Android Gradle Plugin.

    Two independent signals, because either alone has a real blind spot: an
    ``AndroidManifest.xml`` under ``src/main/`` (absent in AGP 8 modules that declare
    ``namespace`` in the build script instead), and an ``android { }`` block in the build
    script (absent when a convention plugin configures everything and the module body is
    only ``dependencies { }``).
    """
    if (module / "src" / "main" / "AndroidManifest.xml").is_file():
        return True
    script = build_script(module)
    return script is not None and _ANDROID_BLOCK.search(_read(script)) is not None


def unit_test_task(module: Path) -> str:
    """The Gradle task running this module's JVM unit tests.

    Never an instrumented-test task: those need a device, and Spine does not run them.
    """
    return ANDROID_UNIT_TEST_TASK if is_android_module(module) else JVM_TEST_TASK


def gradle_modules(root: Path) -> list[Path]:
    """Every Gradle module in this build that could hold JVM sources, root included.

    Included builds are skipped whole. ``build-logic/`` is a build of its own — it holds
    the convention plugins that configure the real modules — and placing a feature there
    would compile into the build's own classpath instead of the app.
    """
    found: list[Path] = []
    # The graph's walk: `build/`, dot-directories (`.gradle`, `.idea`), `node_modules/`, as the
    # private set had it, plus gitignored output and nested checkouts.
    for here, dirnames, _files in walk_repo(root):
        if here != root and any((here / name).is_file() for name in SETTINGS_SCRIPTS):
            dirnames[:] = []  # an included build: not part of this project's module graph
            continue
        if build_script(here) is not None and (here / "src").is_dir():
            found.append(here)
    return found


def package_dir(module: Path, package: str) -> tuple[str, str] | None:
    """``(source_dir, tests_dir)`` relative to ``module`` when it already holds ``package``.

    The main tree decides; the test tree is spelled to match even when it does not exist
    yet, because a module with sources but no tests is exactly where a first test goes.
    """
    path = package.replace(".", "/")
    for source_root in SOURCE_ROOTS:
        if (module / "src" / "main" / source_root / path).is_dir():
            return (f"src/main/{source_root}/{path}", f"src/test/{source_root}/{path}")
    return None


def base_packages(module: Path) -> tuple[str, ...]:
    """Every shallowest package under this module's main source root that holds Kotlin.

    Empty when the module has no Kotlin at all — a resources-only or pure-Java module.

    **Plural on purpose.** A module is not required to root all its sources at one package,
    and several real ones do not: a benchmark module holding both its own package and a
    helper under a third-party namespace is the case that turned up in the validation app.
    Answering with only the first meant a target under any *other* of the module's packages
    matched nothing, and `module_for_package` then refused a placement it could have made —
    a refusal that reads exactly like the honest "no module holds that package" one, which
    is what makes it worth fixing rather than tolerating.
    """
    found: list[str] = []
    for source_root in SOURCE_ROOTS:
        main = module / "src" / "main" / source_root
        if not main.is_dir():
            continue
        for here, dirnames, files in walk_repo(main):
            if any(f.endswith(".kt") for f in files):
                found.append(here.relative_to(main).as_posix().replace("/", "."))
                dirnames[:] = []  # its sub-packages are under this one, not beside it
    return tuple(dict.fromkeys(found))


def base_package(module: Path) -> str:
    """The module's primary package — the first of :func:`base_packages`, or ``""``.

    Kept for the callers that need *a* package to spell a new directory with; anything
    *matching* a package must use the plural form.
    """
    packages = base_packages(module)
    return packages[0] if packages else ""


def module_for_package(root: Path, package: str) -> Path | None:
    """The module a new file in ``package`` belongs to, or ``None`` when nothing matches.

    Two passes, strongest evidence first. A module that *already holds* the package is the
    answer — that is placement into an existing package, which is what brownfield means.
    Failing that, the module whose own base package is the longest prefix of the target:
    ``…core.data.repository`` lands in ``core/data`` because that module is rooted at
    ``…core.data``, and a longer prefix wins over a shorter one so ``core/data`` beats a
    module rooted at ``…core``.

    Returning ``None`` rather than a best guess is deliberate. Writing a repository into the
    wrong module of a 28-module build produces a file that compiles, a test that passes, and
    a change in a place no one would look for it.
    """
    modules = sorted(gradle_modules(root))
    exact = [m for m in modules if package_dir(m, package) is not None]
    if exact:
        return exact[0]
    best: Path | None = None
    longest = -1
    for module in modules:
        for base in base_packages(module):
            if not base or not (package == base or package.startswith(f"{base}.")):
                continue
            if len(base) > longest:
                best, longest = module, len(base)
    return best


def is_android_project(root: Path) -> bool:
    """True when any module in this build is an Android module."""
    return any(is_android_module(m) for m in gradle_modules(root))


def android_sdk_root() -> Path | None:
    """The Android SDK, or ``None``.

    ``ANDROID_HOME`` first because that is what the Android Gradle Plugin itself reads, then
    the deprecated-but-still-set ``ANDROID_SDK_ROOT``, then the per-platform default install
    location, and last an ``sdkmanager`` on PATH — whose grandparent *is* the SDK root, since
    it ships at ``<sdk>/cmdline-tools/latest/bin/sdkmanager``.
    """
    for var in ("ANDROID_HOME", "ANDROID_SDK_ROOT"):
        value = os.environ.get(var)
        if value and Path(value).is_dir():
            return Path(value)
    for default in (Path.home() / "Library" / "Android" / "sdk", Path.home() / "Android" / "Sdk"):
        if default.is_dir():
            return default
    tool = shutil.which("sdkmanager")
    if tool is not None:
        for parent in Path(tool).resolve().parents:
            if (parent / "platforms").is_dir() or (parent / "platform-tools").is_dir():
                return parent
    return None


# Which library a test file is written against, strongest-claim first. `kotlin.test` wins when
# both are present because `kotlin.test.Test` maps onto whichever engine the build runs, so it
# compiles in a JUnit 4 module as readily as in a JUnit 5 one.
_IMPORT_MARKERS = (
    ("kotlin.test", ("import kotlin.test.",)),
    ("junit5", ("import org.junit.jupiter.",)),
    ("junit4", ("import org.junit.Test", "import org.junit.Assert")),
)
_DECLARATION_MARKERS = (
    ("kotlin.test", ('kotlin("test")', "kotlin-test")),
    ("junit5", ("junit-jupiter", "junit.jupiter")),
    ("junit4", ("libs.junit4", "junit:junit")),
)
_PLUGIN_ID = re.compile(r'id\s*=\s*"([^"]+)"\s*\n\s*implementationClass\s*=\s*"([^"]+)"')
_APPLIED_ID = re.compile(r'id\("([^"]+)"\)')


def _scan(text: str, markers: tuple[tuple[str, tuple[str, ...]], ...]) -> str:
    for name, needles in markers:
        if any(needle in text for needle in needles):
            return name
    return ""


def _convention_sources(root: Path, module: Path) -> str:
    """The text of every convention plugin this module applies, concatenated.

    An Android module's build script is frequently nothing but a list of plugin ids, and the
    dependencies those ids bring are declared in a separate included build. The registration
    block maps each id to its implementing class, and the class name is the file name, so the
    hop is exact rather than a search for something that looks related.
    """
    script = build_script(module)
    if script is None:
        return ""
    applied = set(_APPLIED_ID.findall(_read(script)))
    if not applied:
        return ""
    texts: list[str] = []
    for conventions in (root / "build-logic", root / "buildSrc"):
        if not conventions.is_dir():
            continue
        registrations = {}
        for registry in repo_files(conventions, "build.gradle.kts"):
            registrations.update(dict(_PLUGIN_ID.findall(_read(registry))))
        wanted = {cls for plugin_id, cls in registrations.items() if plugin_id in applied}
        for source in repo_files(conventions, "*.kt"):
            if source.stem in wanted:
                texts.append(_read(source))
    return "\n".join(texts)


def module_test_library(root: Path, module: Path) -> str:
    """Which assertion library *this module* can compile a test against, or ``""`` if none.

    P9. P8 read this per repository, which is already wrong one level down: in the validation
    app `core/data` gets `kotlin("test")` from a convention plugin while `core/model` — a plain
    `id("kotlin")` library in the same build — declares no test dependency at all. A repo-wide
    answer of `kotlin.test` produces `Unresolved reference: test` there, which is exactly the
    class of failure P8 fixed at the repository level.

    Evidence in order of strength: what the module's existing tests *already import*, which is
    proof rather than inference; then what its own build script declares; then what the
    convention plugins it applies declare on its behalf. ``""`` means the module genuinely has
    no test dependency, and the caller has to say so rather than pick one and hope.
    """
    for source_root in SOURCE_ROOTS:
        tests = module / "src" / "test" / source_root
        if not tests.is_dir():
            continue
        found = _scan("\n".join(_read(f) for f in sorted(repo_files(tests, "*.kt"))), _IMPORT_MARKERS)
        if found:
            return found
    script = build_script(module)
    declared = _scan(_read(script), _DECLARATION_MARKERS) if script is not None else ""
    return declared or _scan(_convention_sources(root, module), _DECLARATION_MARKERS)
