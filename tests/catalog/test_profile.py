"""ProjectProfile.from_repo — deterministic, marker-driven detection."""

from __future__ import annotations

from pathlib import Path

from orchestrator.catalog import ProjectProfile, task_type_from_intent


def _write(root: Path, rel: str, body: str = "x") -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")


def test_python_django_with_db(tmp_path: Path) -> None:
    _write(tmp_path, "manage.py")
    _write(tmp_path, "app/models.py", "class M: pass")
    _write(tmp_path, "pyproject.toml", "[project]\ndependencies=['django','psycopg']\n")
    _write(tmp_path, "app/migrations/0001_initial.py")
    prof = ProjectProfile.from_repo(tmp_path)
    assert "python" in prof.languages
    assert prof.framework == "django"
    assert prof.has_db is True and prof.has_migrations is True
    assert prof.test_runner == "pytest"  # python default
    assert prof.task_type == "feature"


def test_java_repo(tmp_path: Path) -> None:
    _write(tmp_path, "src/Main.java", "class Main {}")
    _write(
        tmp_path,
        "pom.xml",
        "<project><dependency>org.springframework</dependency><artifactId>junit</artifactId></project>",
    )
    prof = ProjectProfile.from_repo(tmp_path)
    assert prof.languages == frozenset({"java"})
    assert prof.framework == "spring"
    assert prof.test_runner == "junit"


def test_greenfield_empty_repo(tmp_path: Path) -> None:
    prof = ProjectProfile.from_repo(tmp_path)
    assert prof.languages == frozenset()
    assert prof.framework is None
    assert prof.has_db is False and prof.has_migrations is False
    assert prof.test_runner is None


def test_ignores_vendored_dirs(tmp_path: Path) -> None:
    _write(tmp_path, "main.py", "print(1)")
    _write(tmp_path, "node_modules/pkg/index.js", "module.exports={}")
    _write(tmp_path, ".venv/lib/thing.py", "x=1")
    prof = ProjectProfile.from_repo(tmp_path)
    # node_modules + .venv are ignored, so only the real python file counts.
    assert prof.languages == frozenset({"python"})


def test_task_type_classification() -> None:
    assert task_type_from_intent("Migrate users to new schema") == "migration"
    assert task_type_from_intent("Fix the off-by-one bug") == "bugfix"
    assert task_type_from_intent("Add CSV export") == "feature"
    assert task_type_from_intent(None, None) == "feature"


def test_profile_is_deterministic(tmp_path: Path) -> None:
    _write(tmp_path, "a.py")
    _write(tmp_path, "b.ts")
    assert ProjectProfile.from_repo(tmp_path) == ProjectProfile.from_repo(tmp_path)
