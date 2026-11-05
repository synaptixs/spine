"""The walk says what it could not read (KG P1, D9/D14; B107, B108, B125).

An empty or partial graph used to look exactly like a repository with no code: an unreadable folder
(B108), a language whose grammar is not installed (B107) and a gitignore rule that switched itself off
(B125) all produced fewer facts and no word about it. These tests pin the report, ``WalkHealth``, and
the two rules around it: a partial walk is never cached, and the cache's own git calls read the
repository they are pointed at, not the one a git hook names.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
from collections.abc import Iterator
from pathlib import Path

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


@_root_cannot_be_locked_out
def test_an_mcp_answer_says_the_graph_is_partial(locked: Path) -> None:
    """B108's own scenario: ``blast_radius`` on a repository it can only partly read must say so,
    not answer as if the unread code did not exist."""
    from orchestrator.plugin.server import blast_radius

    answer = blast_radius(repo_path=str(locked), symbol="f")
    assert answer.get("walk_health", {}).get("unreadable") == ["secret"]


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
