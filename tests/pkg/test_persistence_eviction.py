"""The PKG cache has a size cap and evicts the least recently used graphs (B157, G6 D5).

``~/.cache/orchestrator/pkg`` was never evicted: 46 GB in 90,053 files on one machine (G6 P0), one
entry per commit, extractor fingerprint and repository, plus three sidecars each. Most of the file
count was this test suite's own temporary repositories, because nothing pointed the suite elsewhere.

The cap is ``ORCHESTRATOR_PKG_CACHE_MAX_MB`` (default 2048; ``0`` turns eviction off). An entry is the
graph file and its ``.calls``, ``.exports`` and ``.unbound`` sidecars, evicted together; its recency
is the newest modification time among them, and a cache hit touches the graph file. Only names the
cache itself writes are ever deleted, and never a file written in the last minute (another process
may be mid-save). The commit key and the clean-tree rule are untouched: eviction only turns a hit
into a miss.
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import pytest

from orchestrator.pkg import persistence
from orchestrator.pkg.persistence import cache_max_bytes, default_cache_dir, evict_cache, load_or_extract

_KEY = "0123456789abcdef"
_FP = "fedcba9876543210"


def _entry(cache: Path, sha: str, size: int, age_s: float, *, sidecars: bool = True) -> list[Path]:
    """One cache entry ``size`` bytes big, last touched ``age_s`` seconds ago."""
    stem = f"{_KEY}-{sha}-{_FP}"
    files = [cache / f"{stem}.json"]
    if sidecars:
        files += [
            cache / f"{stem}.calls.json",
            cache / f"{stem}.exports.json",
            cache / f"{stem}.unbound.json",
        ]
    cache.mkdir(parents=True, exist_ok=True)
    when = time.time() - age_s
    for i, f in enumerate(files):
        f.write_bytes(b"x" * (size if i == 0 else 10))
        os.utime(f, (when, when))
    return files


def _sha(n: int) -> str:
    return f"{n:040x}"


def test_the_oldest_entries_go_first_with_their_sidecars(tmp_path: Path) -> None:
    cache = tmp_path / "pkg"
    oldest = _entry(cache, _sha(1), 600_000, 3000)
    middle = _entry(cache, _sha(2), 600_000, 2000)
    newest = _entry(cache, _sha(3), 600_000, 1000)
    removed = evict_cache(cache, max_bytes=1_000_000)
    assert removed == [f"{_KEY}-{_sha(1)}-{_FP}", f"{_KEY}-{_sha(2)}-{_FP}"]
    assert not any(f.exists() for f in oldest + middle)
    assert all(f.exists() for f in newest)


def test_nothing_goes_while_the_cache_is_under_its_cap(tmp_path: Path) -> None:
    cache = tmp_path / "pkg"
    files = _entry(cache, _sha(1), 1000, 3000) + _entry(cache, _sha(2), 1000, 2000)
    assert evict_cache(cache, max_bytes=1_000_000) == []
    assert all(f.exists() for f in files)


def test_files_the_cache_did_not_write_are_never_touched(tmp_path: Path) -> None:
    """The directory may hold anything a user or a volume mount put there."""
    cache = tmp_path / "pkg"
    _entry(cache, _sha(1), 600_000, 3000)
    strangers = [cache / "notes.txt", cache / "graph.json", cache / f"{_KEY}-short-{_FP}.json"]
    for f in strangers:
        f.write_bytes(b"y" * 600_000)
        os.utime(f, (0, 0))
    (cache / "vectors").mkdir()
    (cache / "vectors" / "v.sqlite").write_bytes(b"z" * 600_000)
    evict_cache(cache, max_bytes=1)
    assert all(f.exists() for f in strangers) and (cache / "vectors" / "v.sqlite").exists()


def test_an_entry_written_in_the_last_minute_is_never_evicted(tmp_path: Path) -> None:
    """Another process may be writing its sidecars right now; deleting under it loses its graph."""
    cache = tmp_path / "pkg"
    fresh = _entry(cache, _sha(1), 600_000, 5)
    assert evict_cache(cache, max_bytes=1) == []
    assert all(f.exists() for f in fresh)


def test_a_protected_entry_survives_even_when_old(tmp_path: Path) -> None:
    cache = tmp_path / "pkg"
    just_saved = _entry(cache, _sha(1), 600_000, 3000)
    assert evict_cache(cache, max_bytes=1, protect=just_saved[0]) == []


def test_the_cap_comes_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ORCHESTRATOR_PKG_CACHE_MAX_MB", raising=False)
    assert cache_max_bytes() == 2048 * 1024 * 1024
    monkeypatch.setenv("ORCHESTRATOR_PKG_CACHE_MAX_MB", "1.5")
    assert cache_max_bytes() == int(1.5 * 1024 * 1024)
    monkeypatch.setenv("ORCHESTRATOR_PKG_CACHE_MAX_MB", "lots")
    assert cache_max_bytes() == 2048 * 1024 * 1024
    monkeypatch.setenv("ORCHESTRATOR_PKG_CACHE_MAX_MB", "0")
    assert cache_max_bytes() == 0


def test_zero_turns_eviction_off(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cache = tmp_path / "pkg"
    files = _entry(cache, _sha(1), 600_000, 3000)
    monkeypatch.setenv("ORCHESTRATOR_PKG_CACHE_MAX_MB", "0")
    assert evict_cache(cache) == []
    assert all(f.exists() for f in files)


# ---- through load_or_extract: a save sweeps, a hit refreshes ---------------------------------------


def _repo(root: Path, body: str) -> Path:
    root.mkdir(parents=True)
    (root / "m.py").write_text(body, encoding="utf-8")
    env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
    for args in (
        ("init", "-q"),
        ("add", "-A"),
        ("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "c"),
    ):
        subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, env=env)
    return root


def _graph_files(cache: Path) -> dict[str, Path]:
    return {p.name: p for p in cache.glob("*.json") if p.name.count(".") == 1}


def test_a_save_sweeps_and_a_hit_keeps_an_entry_recent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(persistence, "_MIN_AGE_S", 0)
    cache = tmp_path / "pkg"
    a = _repo(tmp_path / "a", "def a():\n    return 1\n")
    b = _repo(tmp_path / "b", "def b():\n    return 2\n")
    c = _repo(tmp_path / "c", "def c():\n    return 3\n")
    load_or_extract(a, cache_dir=cache)
    load_or_extract(b, cache_dir=cache)
    files = _graph_files(cache)
    assert len(files) == 2
    size = max(p.stat().st_size for p in files.values())
    a_file = next(p for p in files.values() if b'"py:m.a"' in p.read_bytes())
    b_file = next(p for p in files.values() if p != a_file)
    os.utime(a_file, (time.time() - 300, time.time() - 300))
    os.utime(b_file, (time.time() - 200, time.time() - 200))
    load_or_extract(a, cache_dir=cache)  # a hit: `a` becomes the most recent
    assert a_file.stat().st_mtime > time.time() - 60
    monkeypatch.setenv("ORCHESTRATOR_PKG_CACHE_MAX_MB", str((2 * size + size // 2) / (1024 * 1024)))
    load_or_extract(c, cache_dir=cache)  # saves `c`, then sweeps: `b` is the least recent
    assert a_file.exists() and not b_file.exists()
    assert len(_graph_files(cache)) == 2


# ---- the suite itself never writes to the user's cache ---------------------------------------------


def test_the_suite_never_writes_to_the_users_cache() -> None:
    """194 files (24.7 MB) per full run went to whatever cache the suite was pointed at (G6 P0), and
    nothing pointed it away from ``~/.cache``: 88,397 of the 90,053 files there were the suite's."""
    assert not default_cache_dir().resolve().is_relative_to((Path.home() / ".cache").resolve())
