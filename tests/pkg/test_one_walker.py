"""P2 of the KG track (SSPN-333): every repository walker lists files the way the graph does.

The graph's walker (`walk_rules.walk_repo`) skips dot-directories, the PKG ignore names (``bin`` only
beside a .NET project), what git ignores and does not track, nested checkouts and symlinks that lead
somewhere the walk would never go, and it matches every name against the **root-relative** path.
About forty other walkers had their own copies of some of those rules. Two defects came of it:

* **B126** (SSPN-293): the sdlc walkers pruned by name only, so after ``tsc`` a ticket naming
  ``calls.js`` resolved to the compiled ``out/src/calls.js``, and the spec writer was told the
  extension was mostly JavaScript.
* **B141** (SSPN-338): three walkers matched names against the *absolute* path, so where the
  checkout lives changed the answer -- a dot-parent (this very worktree sits under ``.claude/``)
  emptied ``design_validator._known_dirs``, a parent named ``migrations`` made every ``.sql`` a
  migration, and a parent named ``build`` hid every Kubernetes manifest.

The fixtures make no commits, and every test runs with the developer's git configuration and any
inherited repository variables shut out, as in ``test_gitignored_trees.py``.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from orchestrator.knowledge.infrastructure import _has_k8s, detect_infrastructure
from orchestrator.pkg.c_extractor import _HEADER_INDEX_CACHE, _header_index
from orchestrator.pkg.facts import FactBatch
from orchestrator.pkg.migrations import find_migration_files
from orchestrator.pkg.store import FactStore
from orchestrator.sdlc.conventions import _sample
from orchestrator.sdlc.design_validator import _known_dirs
from orchestrator.sdlc.perl import perl_files
from orchestrator.sdlc.preflight import GradlePreflightRunner
from orchestrator.sdlc.source_paths import basename_index, find_by_basename, resolve
from orchestrator.sdlc.spec_context import repository_languages
from orchestrator.sdlc.testrunner import _discover_dotnet_target

_REPO_ENV = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_COMMON_DIR",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_PREFIX",
    "GIT_CONFIG",
    "GIT_CONFIG_PARAMETERS",
    "GIT_CONFIG_COUNT",
)


@pytest.fixture(autouse=True)
def _isolated_git(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.parent))
    for name in _REPO_ENV:
        monkeypatch.delenv(name, raising=False)


def _git_init(root: Path) -> None:
    subprocess.run(["git", "init", "-q", str(root)], capture_output=True, check=True)


def _put(root: Path, rel: str, text: str = "x\n") -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _compiled_extension(tmp_path: Path) -> Path:
    """A TypeScript extension after ``tsc``: sources in ``src/``, the compiled copy in gitignored ``out/``."""
    root = tmp_path / "ext"
    _git_init(root)
    _put(root, ".gitignore", "out/\n")
    for stem in ("calls", "extension", "panel"):
        _put(root, f"src/{stem}.ts", "export const a = 1;\n")
        _put(root, f"out/src/{stem}.js", "exports.a = 1;\n")
    _put(root, "media/main.js", "console.log(1);\n")  # tracked JavaScript stays
    return root


# --- B126: the sdlc walkers honour the gitignore rule -------------------------------------------


def test_b126_a_ticket_naming_a_compiled_file_does_not_resolve_into_out(tmp_path: Path) -> None:
    root = _compiled_extension(tmp_path)
    assert find_by_basename(root, "calls.js") == []
    assert resolve("calls.js", root) is None
    index = basename_index(root)
    assert "calls.js" not in index
    assert resolve("calls.js", root, index=index) is None
    assert resolve("calls.ts", root, index=index) == "src/calls.ts"


def test_b126_the_spec_writer_is_not_told_the_compiled_copy_is_the_language(tmp_path: Path) -> None:
    root = _compiled_extension(tmp_path)
    assert dict(repository_languages(root)) == {"TypeScript": 3, "JavaScript": 1}


# --- B141: names are matched against the root-relative path, never the absolute one --------------


def test_b141_a_dot_parent_does_not_empty_the_known_directories(tmp_path: Path) -> None:
    root = tmp_path / ".claude" / "worktrees" / "repo"
    _put(root, "docs/guide.md")
    _put(root, "scripts/run.sh")
    _put(root, ".github/workflows/ci.yml")
    dirs = _known_dirs(FactStore(FactBatch()), root)
    assert {"docs", "scripts"} <= dirs
    assert ".github" not in dirs and ".github/workflows" not in dirs


def test_b141_a_parent_named_migrations_does_not_make_every_sql_file_a_migration(tmp_path: Path) -> None:
    root = tmp_path / "migrations" / "repo"
    _put(root, "schema/tables.sql", "CREATE TABLE a (id int);\n")
    _put(root, "db/migrations/001_init.sql", "CREATE TABLE b (id int);\n")
    found = [p.relative_to(root).as_posix() for p in find_migration_files(root)]
    assert found == ["db/migrations/001_init.sql"]


_DEPLOYMENT = "apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: web\n"


def test_b141_a_parent_named_build_does_not_hide_the_kubernetes_manifests(tmp_path: Path) -> None:
    root = tmp_path / "build" / "repo"
    _put(root, "deploy/web.yaml", _DEPLOYMENT)
    _put(root, "charts/web/Chart.yaml", "name: web\n")
    assert _has_k8s(root)
    found = detect_infrastructure(root).categories.get("Containers & deploy", [])
    assert "Kubernetes" in found and "Helm" in found


def test_b141_inside_the_repository_an_ignored_directory_still_hides_its_manifests(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _put(root, "node_modules/some-chart/deploy.yaml", _DEPLOYMENT)
    _put(root, "build/generated/deploy.yaml", _DEPLOYMENT)
    _put(root, "node_modules/some-chart/Chart.yaml", "name: dep\n")
    assert not _has_k8s(root)
    assert "Containers & deploy" not in detect_infrastructure(root).categories


# --- Found in P2: the same absolute-path mistake in the conventions sampler ----------------------


def test_a_parent_named_tests_does_not_make_every_module_a_test(tmp_path: Path) -> None:
    root = tmp_path / "tests" / "repo"
    _put(root, "src/app/core.py", "def f() -> None: ...\n")
    _put(root, "tests/test_core.py", "def test_f() -> None: ...\n")
    modules = [p.relative_to(root).as_posix() for p in _sample(root, ".", 10, want_tests=False)]
    tests = [p.relative_to(root).as_posix() for p in _sample(root, ".", 10, want_tests=True)]
    assert modules == ["src/app/core.py"]
    assert tests == ["tests/test_core.py"]


def test_the_conventions_sample_is_this_repositorys_code(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _git_init(root)
    _put(root, ".gitignore", "env/\n")
    _put(root, "tests/test_core.py", "def test_f() -> None: ...\n")
    _put(root, ".venv/lib/site-packages/test_venv.py")
    _put(root, "env/lib/site-packages/test_env.py")
    _put(root, "node_modules/pkg/test_nm.py")
    tests = [p.relative_to(root).as_posix() for p in _sample(root, ".", 10, want_tests=True)]
    assert tests == ["tests/test_core.py"]


# --- What the graph never walks is not an include target, a build target or a linter ------------


def test_a_header_copy_in_an_ignored_tree_does_not_make_the_real_one_ambiguous(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _put(root, "include/util.h", "int u;\n")
    _put(root, "vendor/x/util.h", "int u;\n")
    _put(root, "build/generated/util.h", "int u;\n")
    _HEADER_INDEX_CACHE.pop(str(root), None)
    assert _header_index(root) == {"util.h": "include/util.h"}


def test_a_nuke_build_project_does_not_make_the_dotnet_target_ambiguous(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _put(root, "src/App/App.csproj", "<Project/>\n")
    _put(root, "src/App/bin/Debug/App.csproj", "<Project/>\n")
    _put(root, "build/_build.csproj", "<Project/>\n")
    assert _discover_dotnet_target(root) == str(root / "src" / "App" / "App.csproj")


def test_a_linter_named_only_by_a_dependency_is_not_configured(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _put(root, "build.gradle.kts", 'plugins { kotlin("jvm") }\n')
    _put(root, "node_modules/rn/android/build.gradle", "apply plugin: 'io.gitlab.arturbosch.detekt'\n")
    runner = GradlePreflightRunner.__new__(GradlePreflightRunner)
    assert runner._configured_linter(root) is None
    _put(root, "app/build.gradle.kts", 'plugins { id("io.gitlab.arturbosch.detekt") }\n')
    assert runner._configured_linter(root) == "detekt"


# --- The two helpers every walker now goes through ------------------------------------------------


def test_repo_files_matches_names_case_sensitively_and_honours_git_below_the_top(tmp_path: Path) -> None:
    from orchestrator.pkg.walk_rules import repo_files

    root = tmp_path / "repo"
    _git_init(root)
    _put(root, "pkg/.gitignore", "out/\n")
    _put(root, "pkg/src/a.py")
    _put(root, "pkg/src/B.PY")
    _put(root, "pkg/out/c.py")
    _put(root, "pkg/build/d.py")
    sub = root / "pkg"
    assert [p.relative_to(sub).as_posix() for p in repo_files(sub, "*.py")] == ["src/a.py"]
    assert [p.relative_to(sub).as_posix() for p in repo_files(sub, "*.py", "*.PY")] == [
        "src/B.PY",
        "src/a.py",
    ]
    everything = {p.relative_to(sub).as_posix() for p in repo_files(sub)}
    assert everything == {".gitignore", "src/a.py", "src/B.PY"}


@pytest.mark.parametrize(
    ("rel", "skipped"),
    [
        ("tests/test_a.py", False),
        ("bin/test_tool.py", False),  # a bin/ with no .NET project beside it is source (B63)
        ("dotnet/bin/test_gen.py", True),  # .NET build output
        (".spine/plans/test_p.py", True),  # a dot-directory
        ("build/test_b.py", True),
        ("vendor/pkg/tests/test_v.py", True),
        ("node_modules/x/test_n.py", True),
        ("nested/tests/test_i.py", True),  # another checkout
        ("test_root.py", False),
        ("a\\b\\test_win.py", False),
    ],
)
def test_in_skipped_dir_is_walk_repos_name_rule_for_one_path(tmp_path: Path, rel: str, skipped: bool) -> None:
    from orchestrator.pkg.walk_rules import in_skipped_dir

    root = tmp_path / "repo"
    _put(root, "dotnet/App.csproj", "<Project/>\n")
    (root / "dotnet" / "bin").mkdir(parents=True)
    (root / "bin").mkdir()
    (root / "nested" / ".git").mkdir(parents=True)
    assert in_skipped_dir(root, rel) is skipped


def test_a_directory_that_is_not_there_holds_nothing_and_is_not_an_unreadable_root(tmp_path: Path) -> None:
    """``root/lib`` may not exist. ``os.walk`` answered "nothing"; only a root that exists and cannot be
    listed is the B108 refusal."""
    from orchestrator.pkg.walk_rules import walk_repo

    _put(tmp_path, "a_file.txt")
    assert list(walk_repo(tmp_path / "absent")) == []
    assert list(walk_repo(tmp_path / "a_file.txt")) == []
    assert list(perl_files(tmp_path / "absent" / "lib")) == []
    assert find_migration_files(tmp_path / "absent") == []


def test_a_changed_test_in_a_directory_the_graph_never_walks_is_not_selected(tmp_path: Path) -> None:
    """``testpaths._SKIPPED_DIRS`` knew ``.git``, ``__pycache__``, ``node_modules`` and ``.spine``; the
    walk's name rules also drop vendored, built and nested-checkout tests."""
    import asyncio

    from orchestrator.sdlc.testpaths import changed_test_files

    root = tmp_path / "repo"
    _git_init(root)
    for key, value in (("user.name", "t"), ("user.email", "t@example.invalid"), ("commit.gpgsign", "false")):
        subprocess.run(["git", "-C", str(root), "config", key, value], capture_output=True, check=True)
    _put(root, "README.md")
    subprocess.run(["git", "-C", str(root), "add", "."], capture_output=True, check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-q", "-m", "init"], capture_output=True, check=True)
    for rel in ("tests/test_a.py", "vendor/pkg/tests/test_v.py", "build/tests/test_b.py", ".spine/test_p.py"):
        _put(root, rel, "def test_x() -> None: ...\n")
    assert asyncio.run(changed_test_files(root)) == ["tests/test_a.py"]
