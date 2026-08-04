"""PKG persistence: JSON round-trip + the commit-keyed extraction cache."""

from __future__ import annotations

import json
import os
import subprocess
import sys
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
from orchestrator.pkg.persistence import _FORMAT_VERSION, extractor_fingerprint


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
    assert json.loads(cache_file.read_text(encoding="utf-8"))["version"] == _FORMAT_VERSION  # re-saved


def test_outdated_format_cache_is_rebuilt(tmp_path: Path) -> None:
    """An extractor fix must reach a repo that hasn't moved since it was cached.

    The cache is keyed on the *analyzed* repo's HEAD, not on our version, so this
    is the one path where a corrected fact silently survives an upgrade: bumping
    ``_FORMAT_VERSION`` is what forces the re-extraction.
    """
    repo = _git_repo(tmp_path)
    cache = tmp_path / "cache"
    load_or_extract(repo, cache_dir=cache)
    (cache_file,) = list(cache.glob("*.json"))

    stale = json.loads(cache_file.read_text(encoding="utf-8"))
    stale["version"] = _FORMAT_VERSION - 1
    stale["nodes"] = []  # a fact the older extractor got wrong
    cache_file.write_text(json.dumps(stale), encoding="utf-8")

    batch = load_or_extract(repo, cache_dir=cache)
    assert any(n.id == "py:mod.f" for n in batch.nodes)  # re-extracted, not served stale
    assert json.loads(cache_file.read_text(encoding="utf-8"))["version"] == _FORMAT_VERSION


# ---- extractor fingerprint --------------------------------------------------


def test_the_fingerprint_is_stable_within_a_process() -> None:
    assert extractor_fingerprint() == extractor_fingerprint()


def test_the_fingerprint_is_stable_across_processes() -> None:
    """The acceptance criterion says *across processes and machines*, so this pays for a
    subprocess: a digest built from set/dict iteration would pass in-process and drift
    between runs under a different PYTHONHASHSEED."""
    code = "from orchestrator.pkg.persistence import extractor_fingerprint; print(extractor_fingerprint())"
    seen = set()
    for seed in ("0", "12345"):
        proc = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            check=True,
            env={**os.environ, "PYTHONHASHSEED": seed},
        )
        seen.add(proc.stdout.strip())
    assert len(seen) == 1
    assert seen == {extractor_fingerprint()}


def test_changing_an_extractor_changes_the_fingerprint(tmp_path: Path) -> None:
    """The whole point: a front-end that learns a new fact kind must invalidate caches
    without anyone remembering to bump a constant."""
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "extractor.py").write_text("EMITS = ('Module',)\n", encoding="utf-8")
    (pkg / "facts.py").write_text("KINDS = 7\n", encoding="utf-8")
    before = extractor_fingerprint(package_dir=pkg)

    (pkg / "extractor.py").write_text("EMITS = ('Module', 'Endpoint')\n", encoding="utf-8")
    after = extractor_fingerprint(package_dir=pkg)

    assert before != after
    # ...and adding a front-end counts too, not only editing one.
    (pkg / "rust_extractor.py").write_text("EMITS = ()\n", encoding="utf-8")
    assert extractor_fingerprint(package_dir=pkg) != after


def test_a_cache_from_a_different_extractor_is_not_read(tmp_path: Path) -> None:
    """The regression that motivated this: same repo, same commit, upgraded Spine.

    The old entry is written under the old fingerprint; the new key cannot name it, so
    the graph is rebuilt rather than silently served without the new facts.
    """
    repo = _git_repo(tmp_path)
    cache = tmp_path / "cache"
    load_or_extract(repo, cache_dir=cache)
    (cache_file,) = list(cache.glob("*.json"))

    # Rename the entry as if it had been written by an earlier extractor.
    stale = cache_file.with_name(cache_file.name.replace(extractor_fingerprint(), "0" * 16))
    payload = json.loads(cache_file.read_text(encoding="utf-8"))
    payload["nodes"][0]["id"] = "py:STALE.marker"
    stale.write_text(json.dumps(payload), encoding="utf-8")
    cache_file.unlink()

    rebuilt = load_or_extract(repo, cache_dir=cache)

    assert not any(n.id == "py:STALE.marker" for n in rebuilt.nodes)
    assert any(n.id == "py:mod.f" for n in rebuilt.nodes)
    assert stale.exists()  # left alone, not silently deleted


def test_the_fingerprint_is_in_the_cache_filename(tmp_path: Path) -> None:
    """In the name rather than the payload, so a mismatch is never parsed to be rejected."""
    repo = _git_repo(tmp_path)
    cache = tmp_path / "cache"
    load_or_extract(repo, cache_dir=cache)
    (cache_file,) = list(cache.glob("*.json"))
    assert extractor_fingerprint() in cache_file.name
