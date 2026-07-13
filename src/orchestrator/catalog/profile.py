"""Deterministic project profiling (Phase 1).

``ProjectProfile.from_repo`` inspects a checkout with cheap, bounded heuristics —
file extensions for languages, a handful of marker files for framework / DB /
test runner — and folds in the task type derived from the intent. It is
deliberately small (the v1 signal set); grow it only when a real project shows a
gap. No AST parsing, no network, same input → same profile.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from orchestrator.pkg.extractor import DEFAULT_IGNORE_DIRS

_LANG_BY_SUFFIX = {
    ".py": "python",
    ".java": "java",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "javascript",
    ".jsx": "javascript",
    ".cs": "csharp",
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
    found: set[str] = set()
    for _dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in DEFAULT_IGNORE_DIRS and not d.startswith(".")]
        for name in filenames:
            lang = _LANG_BY_SUFFIX.get(Path(name).suffix)
            if lang:
                found.add(lang)
    return frozenset(found)


def _read_markers(root: Path) -> str:
    """Concatenate a few small dependency/manifest files (lowercased) to scan."""
    blobs: list[str] = []
    for rel in ("pyproject.toml", "package.json", "pom.xml", "build.gradle", "go.mod"):
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
        ("microsoft.aspnetcore", "aspnet"),
        ("microsoft.net.sdk.web", "aspnet"),
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
    if "python" in languages:
        return "pytest"
    return None


__all__ = ["ProjectProfile", "task_type_from_intent"]
