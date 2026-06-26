"""PKG persistence: JSON round-trip + the commit-keyed extraction cache."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from orchestrator.pkg import (
    Edge,
    EdgeKind,
    FactBatch,
    FactCacheError,
    Node,
    NodeKind,
    Provenance,
    load_facts,
    load_or_extract,
    repo_state,
    save_facts,
)


def _batch() -> FactBatch:
    batch = FactBatch()
    batch.add_node(Node("py:a.Klass", NodeKind.TYPE, "Klass", "python", Provenance("a.py", 3, 9)))
    batch.add_node(Node("py:ext.dep", NodeKind.MODULE, "dep", "python", external=True))
    batch.add_edge(Edge("py:a.Klass", "py:ext.dep", EdgeKind.IMPORTS, Provenance("a.py", 1)))
    return batch


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], capture_output=True, check=True)


def _git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "mod.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    _git(repo, "init", "-q")
    _git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A")
    _git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init")
    return repo


# ---- round-trip -------------------------------------------------------------


def test_round_trip_preserves_nodes_edges_provenance(tmp_path: Path) -> None:
    path = tmp_path / "facts.json"
    save_facts(_batch(), path)
    loaded = load_facts(path)

    by_id = {n.id: n for n in loaded.nodes}
    klass = by_id["py:a.Klass"]
    assert klass.kind is NodeKind.TYPE and klass.grounded
    assert (klass.provenance.file, klass.provenance.line, klass.provenance.end_line) == ("a.py", 3, 9)  # type: ignore[union-attr]
    assert by_id["py:ext.dep"].external
    (edge,) = loaded.edges
    assert edge.kind is EdgeKind.IMPORTS and str(edge.provenance) == "a.py:1"


def test_load_rejects_corrupt_and_wrong_version(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("not json", encoding="utf-8")
    with pytest.raises(FactCacheError, match="unreadable"):
        load_facts(bad)
    versioned = tmp_path / "v99.json"
    versioned.write_text(json.dumps({"version": 99, "nodes": [], "edges": []}), encoding="utf-8")
    with pytest.raises(FactCacheError, match="version"):
        load_facts(versioned)


# ---- repo_state -------------------------------------------------------------


def test_repo_state_clean_dirty_and_non_git(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path)
    sha, dirty = repo_state(repo)
    assert sha and not dirty
    (repo / "mod.py").write_text("def f():\n    return 2\n", encoding="utf-8")
    _, dirty = repo_state(repo)
    assert dirty
    assert repo_state(tmp_path / "nowhere") == (None, True)


# ---- load_or_extract --------------------------------------------------------


def test_cache_hit_skips_extraction(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path)
    cache = tmp_path / "cache"

    first = load_or_extract(repo, cache_dir=cache)
    assert any(n.id == "py:mod.f" for n in first.nodes)
    (cache_file,) = list(cache.glob("*.json"))

    # Poison the cache to prove the second call reads it instead of re-walking.
    payload = json.loads(cache_file.read_text(encoding="utf-8"))
    payload["nodes"][0]["id"] = "py:CACHED.marker"
    cache_file.write_text(json.dumps(payload), encoding="utf-8")

    second = load_or_extract(repo, cache_dir=cache)
    assert any(n.id == "py:CACHED.marker" for n in second.nodes)


def test_dirty_tree_bypasses_cache(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path)
    cache = tmp_path / "cache"
    load_or_extract(repo, cache_dir=cache)  # populate at clean HEAD

    (repo / "mod.py").write_text("def g():\n    return 2\n", encoding="utf-8")  # dirty
    fresh = load_or_extract(repo, cache_dir=cache)
    assert any(n.id == "py:mod.g" for n in fresh.nodes)  # re-extracted, not cached


def test_non_git_dir_always_extracts(tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    (plain / "x.py").write_text("def h():\n    return 3\n", encoding="utf-8")
    cache = tmp_path / "cache"
    batch = load_or_extract(plain, cache_dir=cache)
    assert any(n.id == "py:x.h" for n in batch.nodes)
    assert not list(cache.glob("*.json"))  # nothing cached without a SHA


def test_corrupt_cache_is_rebuilt(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path)
    cache = tmp_path / "cache"
    load_or_extract(repo, cache_dir=cache)
    (cache_file,) = list(cache.glob("*.json"))
    cache_file.write_text("garbage", encoding="utf-8")

    batch = load_or_extract(repo, cache_dir=cache)
    assert any(n.id == "py:mod.f" for n in batch.nodes)  # extracted fresh
    assert json.loads(cache_file.read_text(encoding="utf-8"))["version"] == 1  # re-saved
