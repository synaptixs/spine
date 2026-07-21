"""Target-layout resolution for SDLC codegen.

The codegen adapter writes files into a worktree. Without a declared layout it
invents paths in greenfield repos (e.g. leaking ``src/orchestrator/pkg/...`` into
an unrelated project) and drifts run-to-run. ``TargetLayout`` is a small,
deterministic contract — package name + source/tests dirs — computed once per run
and threaded into the codegen prompts so placement is project-appropriate.

Modes (``--layout``):
- ``auto``     — existing recognizable package → ``existing``; else → ``new``.
- ``new``      — scaffold a fresh ``src/<package>/`` structure, then generate into it.
- ``existing`` — never scaffold; follow the repo's current package layout.

Deterministic and read-only (filesystem reads + string munging, no LLM).
"""

from __future__ import annotations

import keyword
import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from orchestrator.pkg.extractor import DEFAULT_IGNORE_DIRS

# Top-level dirs that are never a project's source package.
_NON_PACKAGE_DIRS = {"tests", "test", "docs", "doc", "examples", "scripts", "build", "dist"}

_FALLBACK_PACKAGE = "app"
_JAVA_GROUP = "org.example"  # default reverse-DNS group for greenfield Java

# Source-file extension per language (Python is the default).
_SOURCE_EXT = {
    "java": "java",
    "typescript": "ts",
    "csharp": "cs",
    "c": "c",
    "cpp": "cpp",
    "sql": "sql",
    "go": "go",
}

# Go package names can't be a reserved keyword (or `init`); guard the derived slug.
_GO_KEYWORDS = frozenset(
    {
        "break",
        "case",
        "chan",
        "const",
        "continue",
        "default",
        "defer",
        "else",
        "fallthrough",
        "for",
        "func",
        "go",
        "goto",
        "if",
        "import",
        "interface",
        "map",
        "package",
        "range",
        "return",
        "select",
        "struct",
        "switch",
        "type",
        "var",
        "init",
    }
)


@dataclass(frozen=True)
class TargetLayout:
    """Where generated code goes, and how it's imported.

    ``mode`` is ``"new"`` (scaffold a structure) or ``"existing"`` (follow the
    repo). ``scaffolded`` is set by the runner once the skeleton is actually
    written (so an idempotent no-op scaffold still reads ``False``).
    """

    package_name: str
    source_dir: str
    tests_dir: str
    src_layout: bool
    mode: str
    scaffolded: bool = False
    language: str = "python"
    build_tool: str = ""  # "maven"|"gradle" (Java) | "npm"|"yarn"|"pnpm" (TypeScript) | ""
    # Greenfield C# target-framework moniker (e.g. "net8.0"/"net10.0"). Empty → the
    # scaffold's default; the runner sets it from the installed SDK so the generated
    # project both builds AND runs (a TFM with no matching runtime fails at test host).
    target_framework: str = ""

    def module_rel_path(self, module: str) -> str:
        """Worktree-relative path for a new source module/class (no leading dir)."""
        ext = _SOURCE_EXT.get(self.language, "py")
        return f"{self.source_dir}/{module}.{ext}"


def derive_package_name(name: str) -> str:
    """Sanitize a repo URL / directory name into a valid Python package name.

    ``Example-Service.`` → ``example_service``. Lowercase,
    drop a trailing ``.git``, map every run of non-alphanumerics to ``_``, strip
    leading/trailing ``_``, and guard against empty / digit-leading / keyword
    results.
    """
    base = name.rstrip("/").rsplit("/", 1)[-1]
    if base.endswith(".git"):
        base = base[: -len(".git")]
    slug = re.sub(r"[^0-9a-zA-Z]+", "_", base.lower()).strip("_")
    if not slug:
        return _FALLBACK_PACKAGE
    if slug[0].isdigit():
        slug = f"pkg_{slug}"
    if keyword.iskeyword(slug):
        slug = f"{slug}_pkg"
    return slug


def detect_existing_package(root: Path) -> tuple[str, str] | None:
    """Find the repo's source package, if it has a recognizable one.

    Returns ``(package_name, source_dir)`` or ``None``. Prefers a ``src/<pkg>/``
    layout, then a top-level ``<pkg>/__init__.py`` (excluding tests/docs/etc.).
    """
    src = root / "src"
    if src.is_dir():
        for child in sorted(src.iterdir()):
            if child.is_dir() and (child / "__init__.py").is_file():
                return child.name, f"src/{child.name}"
    for child in sorted(root.iterdir()):
        if (
            child.is_dir()
            and child.name not in _NON_PACKAGE_DIRS
            and not child.name.startswith(".")
            and child.name not in DEFAULT_IGNORE_DIRS
            and (child / "__init__.py").is_file()
        ):
            return child.name, child.name
    return None


def derive_java_package(name: str) -> str:
    """Repo name → reverse-DNS Java package (``org.example.<slug>``)."""
    base = name.rstrip("/").rsplit("/", 1)[-1]
    if base.endswith(".git"):
        base = base[: -len(".git")]
    slug = re.sub(r"[^0-9a-zA-Z]+", "", base.lower())
    if not slug:
        slug = _FALLBACK_PACKAGE
    if slug[0].isdigit():
        slug = f"p{slug}"
    return f"{_JAVA_GROUP}.{slug}"


def _java_dirs(package: str) -> tuple[str, str]:
    path = package.replace(".", "/")
    return f"src/main/java/{path}", f"src/test/java/{path}"


def _detect_build_tool(root: Path) -> str:
    if (root / "pom.xml").is_file():
        return "maven"
    if (root / "build.gradle").is_file() or (root / "build.gradle.kts").is_file():
        return "gradle"
    return ""


def detect_java_layout(root: Path) -> tuple[str, str, str] | None:
    """If ``src/main/java`` holds a package, return ``(package, source_dir, tests_dir)``.

    The package is the first dir under ``src/main/java`` that directly contains
    ``.java`` files (path → dotted)."""
    main = root / "src" / "main" / "java"
    if not main.is_dir():
        return None
    for dirpath, _dirs, files in os.walk(main):
        if any(f.endswith(".java") for f in files):
            rel = Path(dirpath).relative_to(main)
            package = str(rel).replace(os.sep, ".")
            return package, f"src/main/java/{rel.as_posix()}", f"src/test/java/{rel.as_posix()}"
    return None


def _resolve_java_layout(
    root: Path, *, mode: str, package_name: str | None, repo: str | None
) -> TargetLayout:
    existing = detect_java_layout(root)
    derived = package_name or derive_java_package(repo or str(root))
    build_tool = _detect_build_tool(root) or "maven"
    if mode == "existing" or (mode == "auto" and existing is not None):
        if existing is not None:
            pkg, source_dir, tests_dir = existing
            return TargetLayout(
                package_name=package_name or pkg,
                source_dir=source_dir,
                tests_dir=tests_dir,
                src_layout=True,
                mode="existing",
                language="java",
                build_tool=build_tool,
            )
        src, tst = _java_dirs(derived)
        return TargetLayout(derived, src, tst, True, "existing", language="java", build_tool=build_tool)
    src, tst = _java_dirs(derived)
    return TargetLayout(derived, src, tst, True, "new", language="java", build_tool=build_tool)


def derive_npm_package(name: str) -> str:
    """Repo name → a valid npm package name (lowercase, hyphen-separated).

    ``Example-Service.`` → ``example-service``. npm names
    are url-safe lowercase and may contain hyphens (unlike Python's underscores)."""
    base = name.rstrip("/").rsplit("/", 1)[-1]
    if base.endswith(".git"):
        base = base[: -len(".git")]
    slug = re.sub(r"[^0-9a-z]+", "-", base.lower()).strip("-")
    return slug or _FALLBACK_PACKAGE


def _detect_node_pm(root: Path) -> str:
    """Package manager from the lockfile (pnpm > yarn > npm); default ``npm``."""
    if (root / "pnpm-lock.yaml").is_file():
        return "pnpm"
    if (root / "yarn.lock").is_file():
        return "yarn"
    return "npm"


def _read_package_json_name(root: Path) -> str | None:
    pkg = root / "package.json"
    if not pkg.is_file():
        return None
    try:
        import json

        name = json.loads(pkg.read_text(encoding="utf-8")).get("name")
    except (OSError, ValueError):
        return None
    return name if isinstance(name, str) and name else None


def detect_typescript_layout(root: Path) -> tuple[str, str, str] | None:
    """If the repo is a recognizable Node/TS project (has ``package.json``), return
    ``(package_name, source_dir, tests_dir)``. Tests are co-located with source
    (``*.test.ts`` beside the code), so ``tests_dir == source_dir``."""
    if not (root / "package.json").is_file():
        return None
    name = _read_package_json_name(root) or derive_npm_package(str(root))
    source_dir = "src" if (root / "src").is_dir() else "."
    return name, source_dir, source_dir


def _resolve_typescript_layout(
    root: Path, *, mode: str, package_name: str | None, repo: str | None
) -> TargetLayout:
    existing = detect_typescript_layout(root)
    pm = _detect_node_pm(root)
    derived = package_name or derive_npm_package(repo or str(root))
    if mode == "existing" or (mode == "auto" and existing is not None):
        if existing is not None:
            pkg, source_dir, tests_dir = existing
            return TargetLayout(
                package_name=package_name or pkg,
                source_dir=source_dir,
                tests_dir=tests_dir,
                src_layout=source_dir.startswith("src"),
                mode="existing",
                language="typescript",
                build_tool=pm,
            )
        return TargetLayout(derived, "src", "src", True, "existing", language="typescript", build_tool=pm)
    return TargetLayout(derived, "src", "src", True, "new", language="typescript", build_tool=pm)


def derive_csharp_namespace(name: str) -> str:
    """Repo name → a PascalCase .NET root namespace / project name.

    ``example-service.`` → ``ExampleService``. .NET names are PascalCase
    identifiers; map every run of non-alphanumerics to a word boundary, capitalize
    each word, and guard against empty / digit-leading results."""
    base = name.rstrip("/").rsplit("/", 1)[-1]
    if base.endswith(".git"):
        base = base[: -len(".git")]
    words = [w for w in re.split(r"[^0-9a-zA-Z]+", base) if w]
    slug = "".join(w[:1].upper() + w[1:] for w in words)
    if not slug:
        return "App"
    if slug[0].isdigit():
        slug = f"App{slug}"
    return slug


def _csharp_dirs(project: str) -> tuple[str, str]:
    """Source + test dirs for a greenfield C# project (src + xUnit test project)."""
    return f"src/{project}", f"tests/{project}.Tests"


def detect_csharp_layout(root: Path) -> tuple[str, str, str] | None:
    """If the repo is a recognizable .NET project, return ``(project, source_dir,
    tests_dir)``. The project is the first ``*.csproj`` whose name doesn't look
    like a test project; ``source_dir`` is that project's directory. Tests go to a
    sibling ``<Project>.Tests`` project when one exists, else a derived one."""
    csprojs = sorted(root.rglob("*.csproj"))
    if not csprojs:
        return None
    # Prefer the first non-test project as the source project.
    src_proj = next(
        (p for p in csprojs if not p.stem.lower().endswith(("test", "tests"))),
        csprojs[0],
    )
    project = src_proj.stem
    source_dir = src_proj.parent.relative_to(root).as_posix()
    test_proj = next(
        (p for p in csprojs if p.stem.lower().endswith(("test", "tests"))),
        None,
    )
    tests_dir = (
        test_proj.parent.relative_to(root).as_posix() if test_proj is not None else _csharp_dirs(project)[1]
    )
    return project, source_dir, tests_dir


def _resolve_csharp_layout(
    root: Path, *, mode: str, package_name: str | None, repo: str | None
) -> TargetLayout:
    existing = detect_csharp_layout(root)
    derived = package_name or derive_csharp_namespace(repo or str(root))
    if mode == "existing" or (mode == "auto" and existing is not None):
        if existing is not None:
            pkg, source_dir, tests_dir = existing
            return TargetLayout(
                package_name=package_name or pkg,
                source_dir=source_dir,
                tests_dir=tests_dir,
                src_layout=source_dir.startswith("src"),
                mode="existing",
                language="csharp",
                build_tool="dotnet",
            )
        src, tst = _csharp_dirs(derived)
        return TargetLayout(derived, src, tst, True, "existing", language="csharp", build_tool="dotnet")
    src, tst = _csharp_dirs(derived)
    return TargetLayout(derived, src, tst, True, "new", language="csharp", build_tool="dotnet")


def _detect_c_build_tool(root: Path) -> str:
    if (root / "CMakeLists.txt").is_file():
        return "cmake"
    if (root / "meson.build").is_file():
        return "meson"
    if (root / "Makefile").is_file() or (root / "makefile").is_file():
        return "make"
    return ""


# Source globs that mark a directory as a native (C / C++) source dir.
_C_SRC_GLOBS = ("*.c",)
_CPP_SRC_GLOBS = ("*.cpp", "*.cc", "*.cxx")


def _detect_native_layout(root: Path, src_globs: tuple[str, ...]) -> tuple[str, str, str] | None:
    """Shared C/C++ layout detection: a recognizable CMake/Meson/Make project →
    ``(package, source_dir, tests_dir)``. ``source_dir`` is ``src`` when it holds
    matching source files, else the repo root."""
    if _detect_c_build_tool(root) == "":
        return None
    package = _read_cmake_project_name(root) or derive_package_name(str(root))
    src = root / "src"
    source_dir = "src" if src.is_dir() and any(src.glob(g) for g in src_globs) else "."
    tests_dir = "tests" if (root / "tests").is_dir() else source_dir
    return package, source_dir, tests_dir


def detect_c_layout(root: Path) -> tuple[str, str, str] | None:
    """``(package, source_dir, tests_dir)`` for a recognizable C (CMake/Meson/Make) repo."""
    return _detect_native_layout(root, _C_SRC_GLOBS)


def detect_cpp_layout(root: Path) -> tuple[str, str, str] | None:
    """``(package, source_dir, tests_dir)`` for a recognizable C++ (CMake/Meson) repo."""
    return _detect_native_layout(root, _CPP_SRC_GLOBS)


def _read_cmake_project_name(root: Path) -> str | None:
    cmake = root / "CMakeLists.txt"
    if not cmake.is_file():
        return None
    m = re.search(r"project\s*\(\s*([A-Za-z_][\w-]*)", cmake.read_text(encoding="utf-8", errors="replace"))
    return m.group(1) if m else None


def _resolve_native_layout(
    root: Path,
    *,
    mode: str,
    package_name: str | None,
    repo: str | None,
    language: str,
    detect: Callable[[Path], tuple[str, str, str] | None],
) -> TargetLayout:
    existing = detect(root)
    derived = package_name or derive_package_name(repo or str(root))
    build_tool = _detect_c_build_tool(root) or "cmake"
    if mode == "existing" or (mode == "auto" and existing is not None):
        if existing is not None:
            pkg, source_dir, tests_dir = existing
            return TargetLayout(
                package_name=package_name or pkg,
                source_dir=source_dir,
                tests_dir=tests_dir,
                src_layout=source_dir.startswith("src"),
                mode="existing",
                language=language,
                build_tool=build_tool,
            )
        return TargetLayout(
            derived, "src", "tests", True, "existing", language=language, build_tool=build_tool
        )
    return TargetLayout(derived, "src", "tests", True, "new", language=language, build_tool="cmake")


def _resolve_c_layout(root: Path, *, mode: str, package_name: str | None, repo: str | None) -> TargetLayout:
    return _resolve_native_layout(
        root, mode=mode, package_name=package_name, repo=repo, language="c", detect=detect_c_layout
    )


def detect_sql_layout(root: Path) -> bool:
    """True if the repo already has a migrations directory holding ``.sql`` files."""
    for name in ("migrations", "migration", "db/migrate", "db/migrations"):
        d = root / name
        if d.is_dir() and any(d.glob("*.sql")):
            return True
    return False


def _resolve_sql_layout(root: Path, *, mode: str, package_name: str | None, repo: str | None) -> TargetLayout:
    """SQL greenfield/brownfield layout: ordered DDL under ``migrations/``.

    There is no source/test split — generated migrations *are* the artifact, and
    "tests" run by applying them to an ephemeral database. ``build_tool`` carries
    the SQL **dialect** (default ``postgres``), which threads into the SQL test
    environment/runner (transpiled to SQLite on apply)."""
    derived = package_name or derive_package_name(repo or str(root))
    dialect = "postgres"
    existing = detect_sql_layout(root)
    mode_out = "existing" if (mode == "existing" or (mode == "auto" and existing)) else "new"
    return TargetLayout(
        package_name=derived,
        source_dir="migrations",
        tests_dir="migrations",
        src_layout=True,
        mode=mode_out,
        language="sql",
        build_tool=dialect,
    )


def _resolve_cpp_layout(root: Path, *, mode: str, package_name: str | None, repo: str | None) -> TargetLayout:
    return _resolve_native_layout(
        root, mode=mode, package_name=package_name, repo=repo, language="cpp", detect=detect_cpp_layout
    )


def derive_go_module(name: str) -> str:
    """Repo name → a valid Go module path that is also a valid package identifier.

    ``Example-Service.`` → ``exampleservice``. A single lowercase word doubles as the
    greenfield ``go.mod`` module path and the ``package`` clause; guarded against empty,
    digit-leading, and reserved-keyword results."""
    base = name.rstrip("/").rsplit("/", 1)[-1]
    if base.endswith(".git"):
        base = base[: -len(".git")]
    slug = re.sub(r"[^0-9a-z]+", "", base.lower())
    if not slug:
        slug = _FALLBACK_PACKAGE
    if slug[0].isdigit():
        slug = f"pkg{slug}"
    if slug in _GO_KEYWORDS:
        slug = f"{slug}pkg"
    return slug


def _read_go_module(root: Path) -> str | None:
    """The module path from ``go.mod``'s ``module`` directive, if present."""
    gomod = root / "go.mod"
    if not gomod.is_file():
        return None
    m = re.search(r"^\s*module\s+(\S+)", gomod.read_text(encoding="utf-8", errors="replace"), re.M)
    return m.group(1) if m else None


# Path segments whose packages are never a good brownfield placement target (example /
# demo apps, fixtures). ``main`` packages are filtered separately (by package clause).
_GO_SKIP_DIR_SEGMENTS = frozenset({"demo", "example", "examples", "testdata", "vendor"})


def _go_package_of_dir(d: Path) -> str:
    """The ``package`` clause of a directory's first non-test ``.go`` file (``""`` if none)."""
    for f in sorted(d.glob("*.go")):
        if f.name.endswith("_test.go"):
            continue
        m = re.search(r"^\s*package\s+(\w+)", f.read_text(encoding="utf-8", errors="replace"), re.M)
        if m:
            return m.group(1)
    return ""


def _nearest_go_module_dir(start: Path, root: Path) -> Path | None:
    """The nearest ancestor of ``start`` (inclusive, within ``root``) holding a ``go.mod``."""
    d = start
    while True:
        if (d / "go.mod").is_file():
            return d
        if d == root:
            return None
        d = d.parent
        if d != root and root not in d.parents:
            return None


def _pick_go_source_dir(root: Path) -> tuple[str, str] | None:
    """Pick a brownfield placement: ``(source_dir, package_clause)`` for a **library**
    package (not ``package main``), preferring one in the repo-**root** module so the
    generated code lands where ``go build``/``go test`` on that module actually reaches it
    (a ``main`` / ``demo`` / nested-module dir is what produced 4.4's false green). Skips
    demo/example/testdata trees. Deterministic: root-module first, then shallowest, then path."""
    root_mod = root if (root / "go.mod").is_file() else None
    best_key: tuple[int, int, str] | None = None
    best: tuple[str, str] | None = None
    for dirpath, dirnames, files in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in DEFAULT_IGNORE_DIRS and not d.startswith(".")]
        rel = Path(dirpath).relative_to(root)
        if any(seg in _GO_SKIP_DIR_SEGMENTS for seg in rel.parts):
            continue
        if not any(f.endswith(".go") and not f.endswith("_test.go") for f in files):
            continue
        pkg = _go_package_of_dir(Path(dirpath))
        if not pkg or pkg == "main":
            continue
        in_root = _nearest_go_module_dir(Path(dirpath), root) == root_mod
        key = (0 if in_root else 1, len(rel.parts), rel.as_posix())
        if best_key is None or key < best_key:
            best_key = key
            best = (rel.as_posix() if rel.parts else ".", pkg)
    return best


def detect_go_layout(root: Path) -> tuple[str, str, str] | None:
    """``(package_clause, source_dir, tests_dir)`` for a recognizable Go module (has
    ``go.mod``). ``package_clause`` is the **existing** ``package`` name of the chosen
    placement dir (so brownfield codegen matches it, not the module's last path element —
    the 4.4 package-conflict bug). Go tests are co-located, so ``tests_dir == source_dir``."""
    if not (root / "go.mod").is_file():
        return None
    picked = _pick_go_source_dir(root)
    if picked is not None:
        source_dir, pkg = picked
        return pkg, source_dir, source_dir
    # go.mod but only main packages / no lib package: default to the module root.
    root_pkg = (
        _go_package_of_dir(root) or (_read_go_module(root) or derive_go_module(str(root))).rsplit("/", 1)[-1]
    )
    return root_pkg, ".", "."


def _resolve_go_layout(root: Path, *, mode: str, package_name: str | None, repo: str | None) -> TargetLayout:
    """Go layout. Greenfield = a single package at the module root (``go.mod`` + `.go`
    files beside it), the simplest module ``go build ./...`` / ``go test ./...`` accept.
    Brownfield = a library package in the root module, using that dir's existing ``package``
    clause. Co-located tests → ``tests_dir == source_dir``. ``build_tool`` is ``go``."""
    existing = detect_go_layout(root)
    derived = package_name or derive_go_module(repo or str(root))
    if mode == "existing" or (mode == "auto" and existing is not None):
        if existing is not None:
            pkg_clause, source_dir, tests_dir = existing
            return TargetLayout(
                package_name=package_name or pkg_clause,
                source_dir=source_dir,
                tests_dir=tests_dir,
                src_layout=False,
                mode="existing",
                language="go",
                build_tool="go",
            )
        return TargetLayout(derived, ".", ".", False, "existing", language="go", build_tool="go")
    return TargetLayout(derived, ".", ".", False, "new", language="go", build_tool="go")


def is_effectively_empty(root: Path) -> bool:
    """True when the worktree holds no source the model could extend (a fresh
    clone of an empty repo is just ``.git`` + maybe a README/LICENSE). Used to
    warn when ``new`` scaffolds into a repo that already has loose files."""
    for child in root.iterdir():
        if child.name.startswith(".") or child.name in DEFAULT_IGNORE_DIRS:
            continue
        if child.is_file() and child.suffix.lower() in {".md", ".rst", ".txt"}:
            continue  # README / LICENSE / docs text don't count as source
        return False
    return True


def resolve_layout(
    root: Path | str,
    *,
    mode: str = "auto",
    package_name: str | None = None,
    repo: str | None = None,
    src_layout: bool = True,
    language: str = "python",
) -> TargetLayout:
    """Resolve the target layout for a worktree.

    ``mode`` ∈ {``auto``, ``new``, ``existing``}. ``language`` ∈ {``python``,
    ``java``, ``typescript``, ``csharp``} selects the layout convention (Python
    ``src/<pkg>``; Java ``src/main/java/<pkg path>`` + Maven/Gradle; TypeScript
    ``src/`` with co-located ``*.test.ts`` + npm/yarn/pnpm; C# ``src/<Project>`` +
    ``tests/<Project>.Tests`` xUnit project, built with ``dotnet``).
    ``package_name`` overrides the
    derived name; ``repo`` (clone URL) seeds derivation. Deterministic; the caller
    scaffolds when ``mode == "new"``.
    """
    root_path = Path(root)
    if language == "java":
        return _resolve_java_layout(root_path, mode=mode, package_name=package_name, repo=repo)
    if language == "typescript":
        return _resolve_typescript_layout(root_path, mode=mode, package_name=package_name, repo=repo)
    if language == "csharp":
        return _resolve_csharp_layout(root_path, mode=mode, package_name=package_name, repo=repo)
    if language == "c":
        return _resolve_c_layout(root_path, mode=mode, package_name=package_name, repo=repo)
    if language == "cpp":
        return _resolve_cpp_layout(root_path, mode=mode, package_name=package_name, repo=repo)
    if language == "go":
        return _resolve_go_layout(root_path, mode=mode, package_name=package_name, repo=repo)
    if language == "sql":
        return _resolve_sql_layout(root_path, mode=mode, package_name=package_name, repo=repo)
    existing = detect_existing_package(root_path)
    derived = package_name or derive_package_name(repo or str(root_path))

    if mode == "existing" or (mode == "auto" and existing is not None):
        if existing is not None:
            pkg, source_dir = existing
            return TargetLayout(
                package_name=package_name or pkg,
                source_dir=source_dir,
                tests_dir="tests",
                src_layout=source_dir.startswith("src/"),
                mode="existing",
            )
        # mode=existing but nothing recognizable: best-effort defaults, no scaffold.
        source_dir = f"src/{derived}" if src_layout else derived
        return TargetLayout(derived, source_dir, "tests", src_layout, "existing")

    # mode == "new", or auto with no existing package → scaffold a fresh structure.
    source_dir = f"src/{derived}" if src_layout else derived
    return TargetLayout(derived, source_dir, "tests", src_layout, "new")


__all__ = [
    "TargetLayout",
    "derive_csharp_namespace",
    "derive_go_module",
    "derive_java_package",
    "derive_npm_package",
    "derive_package_name",
    "detect_c_layout",
    "detect_cpp_layout",
    "detect_csharp_layout",
    "detect_existing_package",
    "detect_go_layout",
    "detect_java_layout",
    "detect_typescript_layout",
    "is_effectively_empty",
    "resolve_layout",
]
