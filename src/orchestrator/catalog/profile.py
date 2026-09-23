"""Deterministic project profiling (Phase 1).

``ProjectProfile.from_repo`` inspects a checkout with cheap, bounded heuristics —
file extensions for languages, a handful of marker files for framework / DB /
test runner — and folds in the task type derived from the intent. It is
deliberately small (the v1 signal set); grow it only when a real project shows a
gap. Literal C++ include routing uses the optional CST grammar; no network, same input
and installed grammars → same profile.
"""

from __future__ import annotations

import importlib.util
import os
from dataclasses import dataclass
from pathlib import Path

from orchestrator.pkg.extractor import DEFAULT_IGNORE_DIRS, is_nested_repo

_LANG_BY_SUFFIX = {
    ".py": "python",
    ".java": "java",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".cs": "csharp",
    ".razor": "csharp",  # a Blazor component: C# plus markup, read by the C# front-end
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".cxx": "cpp",
    ".hpp": "cpp",
    ".hh": "cpp",
    ".hxx": "cpp",
    ".go": "go",
    ".rb": "ruby",
    ".sql": "sql",
    ".php": "php",
    ".pl": "perl",
    ".pm": "perl",
    ".t": "perl",
    # `.kts` is deliberately absent: a Gradle build script is a DSL, not Kotlin
    # source, and it gets its own reader (kotlin-support-roadmap.md D11/D12).
    # It is detected as a *marker* below instead, which is what makes a Gradle
    # project's build system visible without inventing a module per script.
    ".kt": "kotlin",
}


def task_type_from_intent(title: str | None = None, summary: str | None = None) -> str:
    """Classify the work: ``migration`` | ``bugfix`` | ``feature`` (the default)."""
    text = f"{title or ''} {summary or ''}".lower()
    if any(kw in text for kw in ("migrat", "backfill", "rename across", "upgrade dependency")):
        return "migration"
    if any(kw in text for kw in ("fix", "bug", "defect", "regression", "hotfix")):
        return "bugfix"
    return "feature"


@dataclass(frozen=True)
class ProjectProfile:
    """A compact, deterministic fingerprint of a project."""

    languages: frozenset[str]
    framework: str | None
    has_db: bool
    has_migrations: bool
    test_runner: str | None
    task_type: str

    @classmethod
    def from_repo(
        cls,
        root: Path | str,
        *,
        intent_title: str | None = None,
        intent_summary: str | None = None,
    ) -> ProjectProfile:
        root_path = Path(root)
        languages = _detect_languages(root_path)
        markers = _read_markers(root_path)
        framework = _detect_framework(markers, languages)
        has_migrations = _detect_migrations(root_path, markers)
        has_db = has_migrations or _detect_db(markers)
        test_runner = _detect_test_runner(root_path, markers, languages)
        return cls(
            languages=languages,
            framework=framework,
            has_db=has_db,
            has_migrations=has_migrations,
            test_runner=test_runner,
            task_type=task_type_from_intent(intent_title, intent_summary),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "languages": sorted(self.languages),
            "framework": self.framework,
            "has_db": self.has_db,
            "has_migrations": self.has_migrations,
            "test_runner": self.test_runner,
            "task_type": self.task_type,
        }


def _detect_languages(root: Path) -> frozenset[str]:
    return frozenset(language_file_counts(root))


def language_file_counts(root: Path) -> dict[str, int]:
    """Source files per language under ``root``, walked the way the extractor walks.

    The *counts*, not only the set: `--language auto` used to scaffold Python whenever a single
    `.py` existed anywhere the walk reached — a build script under `ios/Pods` turned a React
    Native app into a Python package (CB-686). What a repository *is* is what most of its
    source is; one stray file is not a vote.
    """
    found: dict[str, int] = {}
    paths: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        here = Path(dirpath)
        dirnames[:] = sorted(
            d
            for d in dirnames
            if d not in DEFAULT_IGNORE_DIRS and not d.startswith(".") and not is_nested_repo(here, d)
        )
        paths.extend(here / name for name in sorted(filenames))
    cpp_headers: frozenset[str] = frozenset()
    if importlib.util.find_spec("tree_sitter") and importlib.util.find_spec("tree_sitter_cpp"):
        from orchestrator.pkg.c_extractor import cpp_header_paths

        cpp_headers = cpp_header_paths(root, paths)
    for path in paths:
        rel = path.relative_to(root).as_posix()
        lang = "cpp" if rel in cpp_headers else _LANG_BY_SUFFIX.get(path.suffix)
        if lang:
            found[lang] = found.get(lang, 0) + 1
    return found


def _read_markers(root: Path) -> str:
    """Concatenate a few small dependency/manifest files (lowercased) to scan."""
    blobs: list[str] = []
    for rel in (
        "pyproject.toml",
        "package.json",
        "pom.xml",
        "build.gradle",
        # The Kotlin Gradle DSL, which is the Android and modern-JVM default and
        # which nothing read before: a Gradle-Kotlin repo reported `languages: []`
        # and no framework at all because only the Groovy `build.gradle` was read
        # (kotlin-support-roadmap.md D12). The version catalog carries the
        # dependency coordinates a multi-module build keeps out of the scripts.
        "build.gradle.kts",
        "settings.gradle.kts",
        "gradle/libs.versions.toml",
        "go.mod",
        "composer.json",
        "cpanfile",
        "Makefile.PL",
        "Build.PL",
        "dist.ini",
    ):
        path = root / rel
        if path.is_file():
            blobs.append(_safe_read(path))
    for req in root.glob("requirements*.txt"):
        blobs.append(_safe_read(req))
    blobs.extend(_read_dotnet_markers(root))  # .csproj/.sln can live in subdirs
    return "\n".join(blobs).lower()


def _read_dotnet_markers(root: Path, *, limit: int = 50) -> list[str]:
    """A bounded set of .NET project files (SDK + PackageReference live here)."""
    blobs: list[str] = []
    for dirpath, dirnames, files in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in DEFAULT_IGNORE_DIRS and not d.startswith(".")]
        for fn in files:
            if fn.endswith((".csproj", ".sln")):
                blobs.append(_safe_read(Path(dirpath) / fn))
                if len(blobs) >= limit:
                    return blobs
    return blobs


def _safe_read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


def _detect_framework(markers: str, languages: frozenset[str]) -> str | None:
    for needle, name in (
        ("django", "django"),
        ("fastapi", "fastapi"),
        ("flask", "flask"),
        ("springframework", "spring"),
        ('"react"', "react"),
        # Blazor before ASP.NET: every Blazor project also references Microsoft.AspNetCore, and
        # the first needle wins. The Components package or the WebAssembly SDK is the tell.
        ("microsoft.aspnetcore.components", "blazor"),
        ("microsoft.net.sdk.blazorwebassembly", "blazor"),
        ("microsoft.aspnetcore", "aspnet"),
        ("microsoft.net.sdk.web", "aspnet"),
        ("io.ktor", "ktor"),
        # Android is named by the Gradle plugin id or by any AndroidX/Compose
        # coordinate. Checked after the server frameworks on purpose: a Spring or
        # Ktor service is a provider of routes, an Android app is a consumer of
        # them (D10), and the framework name is what tells the two apart.
        ("com.android.application", "android"),
        ("com.android.library", "android"),
        ("androidx", "android"),
        ('"laravel/framework"', "laravel"),
        ('"symfony/', "symfony"),
        ("mojolicious", "mojolicious"),
        ("catalyst::runtime", "catalyst"),
        ("dancer2", "dancer2"),
    ):
        if needle in markers:
            return name
    return None


def _detect_migrations(root: Path, markers: str) -> bool:
    if (root / "alembic.ini").is_file() or "alembic" in markers:
        return True
    for _dirpath, dirnames, _files in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in DEFAULT_IGNORE_DIRS and not d.startswith(".")]
        if "migrations" in {d.lower() for d in dirnames}:
            return True
    return False


def _detect_db(markers: str) -> bool:
    return any(lib in markers for lib in ("sqlalchemy", "psycopg", "asyncpg", "django.db", "sqlmodel"))


def _detect_test_runner(root: Path, markers: str, languages: frozenset[str]) -> str | None:
    if (root / "pytest.ini").is_file() or "[tool.pytest" in markers or "pytest" in markers:
        return "pytest"
    if '"jest"' in markers or "jest" in markers:
        return "jest"
    if "junit" in markers:
        return "junit"
    if "xunit" in markers:
        return "xunit"
    if "nunit" in markers:
        return "nunit"
    if "mstest" in markers or "microsoft.net.test.sdk" in markers:
        return "mstest"
    if '"pestphp/pest"' in markers:
        return "pest"
    if '"phpunit/phpunit"' in markers:
        return "phpunit"
    if "perl" in languages and (root / "t").is_dir():
        return "prove"
    if "python" in languages:
        return "pytest"
    return None


__all__ = ["ProjectProfile", "task_type_from_intent"]
