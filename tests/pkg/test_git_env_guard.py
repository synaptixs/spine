"""Every git call on the read side ignores a git hook's repository variables (B151, G6 D7).

A git hook exports ``GIT_DIR`` and ``GIT_INDEX_FILE``, and git obeys them over ``-C``. The walker and
the cache's commit key already drop them (B119, B125). Four other read-side calls did not: run from a
hook in another repository, ``state``'s churn areas, the intent layer's ``git log``/``blame``, the
graph memo's dirty digest and the RCA/enhancement churn list all read *that* repository.

Two halves. The behavioural tests point ``GIT_DIR`` at another repository and check each call still
reads the one it was given. The guard reads the source: any ``subprocess`` call whose argv starts with
``"git"`` in ``pkg/``, ``knowledge/``, ``plugin/`` or ``sdlc/churn.py`` must go through
``walk_rules.run_git``, the one place the scrub lives, so the next call cannot regress.
"""

from __future__ import annotations

import ast
import os
import subprocess
from pathlib import Path

import pytest

from orchestrator.pkg import walk_rules

_SRC = Path(walk_rules.__file__).resolve().parents[1]
_GUARDED = [_SRC / "pkg", _SRC / "knowledge", _SRC / "plugin", _SRC / "sdlc" / "churn.py"]
_SUBPROCESS_CALLS = frozenset({"run", "Popen", "check_output", "call", "check_call"})
_REPO_ENV = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR", "GIT_OBJECT_DIRECTORY")


@pytest.fixture(autouse=True)
def _isolated_git(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.parent))
    for name in _REPO_ENV:
        monkeypatch.delenv(name, raising=False)


def _git(root: Path, *args: str) -> str:
    out = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True)
    return out.stdout.strip()


def _repo(root: Path, files: dict[str, str], message: str = "c") -> str:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    if not (root / ".git").exists():
        _git(root, "init", "-q")
    _git(root, "add", "-A")
    _git(root, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", message)
    return _git(root, "rev-parse", "HEAD")


@pytest.fixture
def two_repos(tmp_path: Path) -> tuple[Path, Path]:
    """``root`` (the one asked about) and ``other`` (the one a hook's ``GIT_DIR`` names)."""
    root = tmp_path / "repo"
    _repo(root, {"src/billing/invoice.py": "def f():\n    return 1\n"}, "SSPN-1 invoices")
    other = tmp_path / "other"
    _repo(other, {"lib/elsewhere/thing.py": "def g():\n    return 2\n"}, "OTHER-9 elsewhere")
    return root, other


def _hook_env(monkeypatch: pytest.MonkeyPatch, other: Path) -> None:
    monkeypatch.setenv("GIT_DIR", str(other / ".git"))
    monkeypatch.setenv("GIT_INDEX_FILE", str(other / ".git" / "index"))


# ---- behaviour: each read-side call reads the repository it is given -----------------------------


def test_churn_reads_the_given_repo_not_the_hooks(
    two_repos: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    from orchestrator.sdlc.churn import recently_changed_files

    root, other = two_repos
    _hook_env(monkeypatch, other)
    assert recently_changed_files(root) == {"src/billing/invoice.py"}


def test_state_churn_areas_read_the_given_repo(
    two_repos: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    from orchestrator.knowledge.current_state import _recent_areas

    root, other = two_repos
    _hook_env(monkeypatch, other)
    assert _recent_areas(root) == [("src/billing", 1)]


def test_intent_layer_reads_the_given_repo(
    two_repos: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    from orchestrator.pkg.intent_link import _git as intent_git

    root, other = two_repos
    expected = _git(root, "rev-parse", "HEAD")
    _hook_env(monkeypatch, other)
    out = intent_git(root, "log", "--format=%H %s")
    assert out is not None
    assert out.split()[0] == expected
    assert "OTHER-9" not in out


def test_graph_memo_digest_reads_the_given_repo(
    two_repos: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    from orchestrator.plugin.graph_memo import _dirty_digest

    root, other = two_repos
    (root / "src/billing/invoice.py").write_text("def f():\n    return 3\n", encoding="utf-8")
    clean_env = _dirty_digest(root)
    assert clean_env is not None
    _hook_env(monkeypatch, other)
    assert _dirty_digest(root) == clean_env


def test_run_git_drops_the_repository_variables_and_keeps_config(
    two_repos: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    root, other = two_repos
    expected = _git(root, "rev-parse", "HEAD")
    _hook_env(monkeypatch, other)
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "spine.probe")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "kept")
    head = walk_rules.run_git(root, "rev-parse", "HEAD")
    assert head.stdout.decode().strip() == expected
    probe = walk_rules.run_git(root, "config", "--get", "spine.probe")
    assert probe.stdout.decode().strip() == "kept"


# ---- the guard: no read-side git call bypasses run_git -------------------------------------------


def _git_calls_outside_run_git(path: Path, base: Path = _SRC) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    allowed: set[int] = set()
    if path.name == "walk_rules.py":
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "run_git":
                allowed.update(id(n) for n in ast.walk(node))
    hits: list[str] = []
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in _SUBPROCESS_CALLS
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "subprocess"
            and node.args
        ):
            continue
        argv = node.args[0]
        if (
            isinstance(argv, (ast.List, ast.Tuple))
            and argv.elts
            and isinstance(argv.elts[0], ast.Constant)
            and argv.elts[0].value == "git"
            and id(node) not in allowed
        ):
            hits.append(f"{path.relative_to(base).as_posix()}:{node.lineno}")
    return hits


def test_no_read_side_git_call_bypasses_run_git() -> None:
    files = [p for g in _GUARDED for p in ([g] if g.is_file() else sorted(g.rglob("*.py")))]
    assert len(files) > 50, "the guard scanned almost nothing: the paths above moved"
    hits = [hit for p in files for hit in _git_calls_outside_run_git(p)]
    assert hits == [], (
        "these git calls run outside walk_rules.run_git, so a git hook's GIT_DIR / GIT_INDEX_FILE "
        f"redirects them to another repository (B151): {hits}"
    )


def test_the_guard_sees_a_bare_git_call(tmp_path: Path) -> None:
    """The guard is only worth having if it fires: a module with a bare call is reported."""
    bad = tmp_path / "pkg" / "probe.py"
    bad.parent.mkdir()
    bad.write_text('import subprocess\nsubprocess.run(["git", "status"])\n', encoding="utf-8")
    assert _git_calls_outside_run_git(bad, base=tmp_path) == ["pkg/probe.py:2"]
