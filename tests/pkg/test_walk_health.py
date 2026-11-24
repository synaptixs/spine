"""The walk says what it could not read (KG P1, D9/D14; B107, B108, B125).

An empty or partial graph used to look exactly like a repository with no code: an unreadable folder
(B108), a language whose grammar is not installed (B107) and a gitignore rule that switched itself off
(B125) all produced fewer facts and no word about it. These tests pin the report, ``WalkHealth``, and
the two rules around it: a partial walk is never cached, and the cache's own git calls read the
repository they are pointed at, not the one a git hook names.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import os
import stat
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from orchestrator.pkg.extractor import PythonExtractor, RepoCodeExtractor
from orchestrator.pkg.persistence import load_or_extract, repo_state

_REPO_ENV = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR", "GIT_OBJECT_DIRECTORY")

_root_cannot_be_locked_out = pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0, reason="root reads a chmod-000 directory anyway"
)


@pytest.fixture(autouse=True)
def _isolated_git(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.parent))
    for name in _REPO_ENV:
        monkeypatch.delenv(name, raising=False)


def _write(path: Path, text: str = "def f():\n    return 1\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _git(root: Path, *args: str) -> str:
    out = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True)
    return out.stdout.strip()


def _commit(root: Path) -> str:
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    _git(root, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "c")
    return _git(root, "rev-parse", "HEAD")


@pytest.fixture
def locked(tmp_path: Path) -> Iterator[Path]:
    """A repository whose ``secret/`` folder cannot be listed, unlocked again after the test."""
    root = tmp_path / "repo"
    _write(root / "a.py")
    _write(root / "secret" / "b.py", "def g():\n    return 2\n")
    secret = root / "secret"
    secret.chmod(0)
    try:
        yield root
    finally:
        secret.chmod(stat.S_IRWXU)


# ---- B108: an unreadable folder is reported, never silently empty -------------------------------


@_root_cannot_be_locked_out
def test_an_unreadable_folder_is_reported_by_the_walk(locked: Path) -> None:
    from orchestrator.pkg.walk_rules import WalkHealth, walk_repo

    health = WalkHealth()
    files = [
        Path(d, f).relative_to(locked).as_posix()
        for d, _, names in walk_repo(locked, health=health)
        for f in names
    ]
    assert files == ["a.py"]
    assert health.unreadable == ("secret",)
    assert health.partial


@_root_cannot_be_locked_out
def test_the_extractor_reports_an_unreadable_folder_and_keeps_the_rest(locked: Path) -> None:
    extractor = RepoCodeExtractor(extractors=[PythonExtractor()])
    batch = extractor.extract(locked)
    assert any(n.name == "f" for n in batch.nodes)
    assert extractor.walk_health.unreadable == ("secret",)
    assert batch.walk_health is not None and batch.walk_health.partial


@_root_cannot_be_locked_out
def test_a_root_that_cannot_be_listed_raises_instead_of_mapping_to_nothing(tmp_path: Path) -> None:
    from orchestrator.pkg.walk_rules import UnreadableRootError

    root = tmp_path / "repo"
    _write(root / "a.py")
    root.chmod(0)
    try:
        with pytest.raises(UnreadableRootError):
            RepoCodeExtractor(extractors=[PythonExtractor()]).extract(root)
    finally:
        root.chmod(stat.S_IRWXU)


@_root_cannot_be_locked_out
def test_a_partial_walk_is_never_cached(tmp_path: Path) -> None:
    """D14. A *tracked* folder that cannot be read leaves the tree clean to git (``git status`` warns on
    stderr and exits 0), so the cache is written — and the partial graph would be this commit's graph
    after the folder became readable again."""
    root = tmp_path / "repo"
    _write(root / "a.py")
    _write(root / "secret" / "b.py", "def g():\n    return 2\n")
    _commit(root)
    (root / "secret").chmod(0)
    cache = tmp_path / "cache"
    try:
        batch = load_or_extract(
            root, cache_dir=cache, extractor=RepoCodeExtractor(extractors=[PythonExtractor()])
        )
        assert batch.walk_health is not None and batch.walk_health.unreadable == ("secret",)
        assert not list(cache.rglob("*.json")), "a partial walk was cached"
    finally:
        (root / "secret").chmod(stat.S_IRWXU)


def test_a_full_walk_is_cached_with_its_health(tmp_path: Path) -> None:
    """The cache keeps what the walk reported, so a cache hit says the same as the run that wrote it."""
    root = tmp_path / "repo"
    _write(root / "a.py")
    _write(root / "m.ts", "export function a() { return 1 }\n")
    _commit(root)
    cache = tmp_path / "cache"
    first = load_or_extract(
        root, cache_dir=cache, extractor=RepoCodeExtractor(extractors=[PythonExtractor()])
    )
    assert list(cache.rglob("*.json"))
    again = load_or_extract(
        root, cache_dir=cache, extractor=RepoCodeExtractor(extractors=[PythonExtractor()])
    )
    assert first.walk_health is not None and again.walk_health is not None
    assert again.walk_health.missing_grammars == first.walk_health.missing_grammars == {"typescript": 1}


# ---- B107: files no front-end can read are counted, by the extra that would read them ------------


def test_files_whose_front_end_is_not_installed_are_counted_by_extra(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _write(root / "a.py")
    _write(root / "m.ts", "export function a() { return b() }\nexport function b() { return 1 }\n")
    _write(root / "web" / "x.ts", "export const x = 1\n")
    extractor = RepoCodeExtractor(extractors=[PythonExtractor()])
    extractor.extract(root)
    assert extractor.walk_health.missing_grammars == {"typescript": 2}
    assert not extractor.walk_health.partial  # unread by choice of install, not unreadable


def test_a_clean_walk_reports_nothing(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _write(root / "a.py")
    extractor = RepoCodeExtractor(extractors=[PythonExtractor()])
    extractor.extract(root)
    assert extractor.walk_health.clean
    assert extractor.walk_health.as_dict() == {}


# ---- B125: the cache's git calls read the repository they are given ------------------------------


def test_repo_state_ignores_a_hooks_git_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Run from a pre-commit hook in another repository, ``repo_state`` answered with *that*
    repository's HEAD, so a graph was saved under the wrong commit (invariant 8)."""
    other = tmp_path / "other"
    _write(other / "x.py")
    other_sha = _commit(other)
    root = tmp_path / "repo"
    _write(root / "a.py")
    sha = _commit(root)
    assert sha != other_sha
    monkeypatch.setenv("GIT_DIR", str(other / ".git"))
    monkeypatch.setenv("GIT_INDEX_FILE", str(other / ".git" / "index"))
    assert repo_state(root) == (sha, False)


# ---- D9: the report reaches the surfaces, not only `pkg extract`'s text ---------------------------


def test_pkg_extract_json_carries_the_walk_health(tmp_path: Path) -> None:
    from orchestrator.cli import app

    root = tmp_path / "repo"
    _write(root / "a.py")
    result = CliRunner().invoke(app, ["pkg", "extract", str(root), "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert "walk_health" in payload
    assert isinstance(payload["walk_health"], dict)


class _Registry:
    """Stands in for the MCP server: keeps each tool exactly as ``_register_tools`` hands it over."""

    def __init__(self) -> None:
        self.tools: dict[str, Any] = {}

    def tool(self, **_: Any) -> Any:
        def keep(fn: Any) -> Any:
            self.tools[fn.__name__] = fn
            return fn

        return keep


@_root_cannot_be_locked_out
def test_an_mcp_answer_says_the_graph_is_partial(locked: Path) -> None:
    """B108's own scenario, through the path a host reaches: ``blast_radius`` on a repository it can
    only partly read must say so, not answer as if the unread code did not exist."""
    from orchestrator.plugin import server

    registry = _Registry()
    server._register_tools(registry, "pkg")

    def call(**kwargs: Any) -> Any:
        out = registry.tools["blast_radius"](**kwargs)
        return asyncio.run(out) if inspect.iscoroutine(out) else out  # the plugin suite's guard is async

    answer = call(repo_path=str(locked), symbol="f")
    assert answer.get("walk_health", {}).get("unreadable") == ["secret"]
    clean = call(repo_path=str(locked.parent / "missing"), symbol="f")
    assert "walk_health" not in clean


def test_state_says_what_the_graph_could_not_read(tmp_path: Path) -> None:
    from orchestrator.knowledge.current_state import render_walk_health
    from orchestrator.pkg.facts import FactBatch
    from orchestrator.pkg.walk_rules import WalkHealth

    clean = FactBatch()
    clean.walk_health = WalkHealth()
    assert render_walk_health(clean) == ""
    thin = FactBatch()
    thin.walk_health = WalkHealth(unreadable=("secret",), missing_grammars={"typescript": 2})
    text = render_walk_health(thin)
    assert "Partial graph" in text and "`secret`" in text
    assert "2 file(s) need the `typescript` extra" in text


def test_every_front_end_suffix_names_the_extra_that_installs_it() -> None:
    """The B107 table must cover every suffix an optional front-end reads, or a missing grammar for
    that suffix goes unreported again. Python is built in and needs no extra."""
    from orchestrator.pkg.extractor import FRONT_END_EXTRA_BY_SUFFIX, default_extractors

    claimed = {s for e in default_extractors() if type(e).__name__ != "PythonExtractor" for s in e.suffixes}
    assert claimed <= set(FRONT_END_EXTRA_BY_SUFFIX), sorted(claimed - set(FRONT_END_EXTRA_BY_SUFFIX))


# ---- B153: an unreadable *file* is skipped and reported, never an aborted extraction ---------------


@pytest.fixture
def locked_file(tmp_path: Path) -> Iterator[Path]:
    """A repository whose ``b.py`` cannot be read, made readable again after the test."""
    root = tmp_path / "repo"
    _write(root / "a.py")
    _write(root / "pkg" / "b.py", "def g():\n    return 2\n")
    locked = root / "pkg" / "b.py"
    locked.chmod(0)
    try:
        yield root
    finally:
        locked.chmod(stat.S_IRUSR | stat.S_IWUSR)


@_root_cannot_be_locked_out
def test_an_unreadable_file_is_skipped_and_reported(locked_file: Path) -> None:
    """One file the process cannot read raised ``PermissionError`` out of the per-file loop and the
    whole graph was lost (B153). Now the rest is extracted and the file is named."""
    extractor = RepoCodeExtractor(extractors=[PythonExtractor()])
    batch = extractor.extract(locked_file)
    assert any(n.name == "f" for n in batch.nodes)
    assert not any(n.name == "g" for n in batch.nodes)
    health = extractor.walk_health
    assert health.unreadable_files == ("pkg/b.py",)
    assert health.unreadable == ()  # the folder was listed; only the file could not be read
    assert health.partial and not health.clean
    assert health.as_dict() == {
        "unreadable_files": ["pkg/b.py"],
        "unreadable_files_count": 1,
        "partial": True,
    }


@_root_cannot_be_locked_out
def test_a_graph_with_an_unreadable_file_is_never_cached(tmp_path: Path) -> None:
    """D14 holds for files too: cached, the partial graph would outlive the file becoming readable.

    ``git status`` reports a chmod-000 tracked file as modified, which alone keeps the cache off; the
    file is marked ``assume-unchanged`` so the tree reads clean and only the partial rule can refuse.
    """
    root = tmp_path / "repo"
    _write(root / "a.py")
    _write(root / "b.py", "def g():\n    return 2\n")
    _commit(root)
    _git(root, "update-index", "--assume-unchanged", "b.py")
    (root / "b.py").chmod(0)
    assert repo_state(root)[1] is False, "the tree must read clean, or this test proves nothing"
    cache = tmp_path / "cache"
    try:
        batch = load_or_extract(
            root, cache_dir=cache, extractor=RepoCodeExtractor(extractors=[PythonExtractor()])
        )
        assert batch.walk_health is not None and batch.walk_health.unreadable_files == ("b.py",)
        assert not list(cache.rglob("*.json")), "a graph missing an unreadable file was cached"
    finally:
        (root / "b.py").chmod(stat.S_IRUSR | stat.S_IWUSR)


def test_unreadable_files_are_capped_and_survive_the_cache_payload() -> None:
    from orchestrator.pkg.walk_rules import MAX_UNREADABLE_SHOWN, WalkHealth

    many = tuple(f"f{i:02d}.py" for i in range(MAX_UNREADABLE_SHOWN + 5))
    health = WalkHealth(unreadable_files=many)
    shown = health.as_dict()
    assert shown["unreadable_files"] == list(many[:MAX_UNREADABLE_SHOWN])
    assert shown["unreadable_files_count"] == len(many)
    assert WalkHealth.from_payload(health.to_payload()) == health


def test_the_state_report_names_unreadable_files() -> None:
    from orchestrator.knowledge.current_state import render_walk_health
    from orchestrator.pkg.facts import FactBatch
    from orchestrator.pkg.walk_rules import WalkHealth

    batch = FactBatch()
    batch.walk_health = WalkHealth(unreadable_files=("pkg/b.py",))
    text = render_walk_health(batch)
    assert "1 file(s) could not be read" in text and "`pkg/b.py`" in text


# ---- B152 #3 (D9): a virtualenv is recognised by what it holds, not by its name ------------------


def test_an_unignored_virtualenv_is_never_walked(tmp_path: Path) -> None:
    """``env/`` is not a skipped name, so a virtualenv git does not ignore put site-packages in the graph
    and in ``test_location``'s walk (B152 #3). Recognised by ``pyvenv.cfg`` (venv/virtualenv) or
    ``conda-meta/`` (conda), whatever the folder is called; a real package named ``env`` is kept."""
    from orchestrator.pkg.walk_rules import WalkHealth, repo_files

    root = tmp_path / "repo"
    _write(root / "app" / "main.py")
    _write(root / "app" / "env" / "settings.py")  # a package that happens to be called env
    _write(root / "env" / "pyvenv.cfg", "home = /usr/bin\n")
    _write(root / "env" / "lib" / "python3.12" / "site-packages" / "requests" / "api.py")
    _write(root / "py311" / "pyvenv.cfg", "home = /usr/bin\n")
    _write(root / "py311" / "lib" / "x.py")
    _write(root / "conda_env" / "conda-meta" / "history", "\n")
    _write(root / "conda_env" / "lib" / "y.py")
    health = WalkHealth()
    files = sorted(p.relative_to(root).as_posix() for p in repo_files(root, "*.py", health=health))
    assert files == ["app/env/settings.py", "app/main.py"]
    assert health.clean  # skipped on purpose, like node_modules: context, not a problem
