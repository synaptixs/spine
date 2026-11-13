"""The symbol-vector cache (hybrid KG search, D9): content-keyed, size-capped, least-recently-used out first.

A vector is keyed by the model's file sha256, the symbol-text recipe and its version, and the text itself — so
it stays valid on a dirty tree and across commits, and a new model, recipe or text never meets an old vector.
The cache is an optimisation: a broken or unwritable one must cost time, never change an answer.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from orchestrator.pkg import symbol_text
from orchestrator.pkg.vector_cache import DEFAULT_MAX_BYTES, VectorCache, default_vector_cache_dir


def test_the_key_changes_with_the_model_the_recipe_its_version_and_the_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    k = VectorCache.key("m" * 64, "B", "def f(): pass")
    assert len(k) == 64
    assert k == VectorCache.key("m" * 64, "B", "def f(): pass")
    others = {
        VectorCache.key("n" * 64, "B", "def f(): pass"),
        VectorCache.key("m" * 64, "A", "def f(): pass"),
        VectorCache.key("m" * 64, "B", "def g(): pass"),
    }
    assert k not in others and len(others) == 3
    monkeypatch.setattr(symbol_text, "RECIPE_VERSION", symbol_text.RECIPE_VERSION + 1)
    assert VectorCache.key("m" * 64, "B", "def f(): pass") != k


def test_vectors_round_trip_as_float32_and_a_miss_is_absent(tmp_path: Path) -> None:
    cache = VectorCache(tmp_path)
    cache.put_many({"a": [0.5, -0.25, 1.0], "b": [0.1, 0.2, 0.3]})
    got = cache.get_many(["a", "b", "zz"])
    assert got["a"] == [0.5, -0.25, 1.0]
    assert got["b"] == pytest.approx([0.1, 0.2, 0.3], abs=1e-7)
    assert "zz" not in got
    # Another instance (another process) sees what this one wrote.
    assert VectorCache(tmp_path).get_many(["a"]) == {"a": [0.5, -0.25, 1.0]}


def test_over_the_cap_the_least_recently_used_vectors_go_first(tmp_path: Path) -> None:
    vec = [0.0] * 64  # 256 bytes of payload, plus the 64-byte key
    cache = VectorCache(tmp_path, max_bytes=3 * 320)
    cache.put_many({"k1": vec})
    cache.put_many({"k2": vec})
    cache.put_many({"k3": vec})
    assert cache.get_many(["k1"])  # k1 is now the most recently used; k2 the least
    cache.put_many({"k4": vec})
    left = set(cache.get_many(["k1", "k2", "k3", "k4"]))
    assert left == {"k1", "k3", "k4"}
    assert cache.size_bytes() <= 3 * 320


def test_a_batch_larger_than_the_cap_keeps_the_newest_that_fit(tmp_path: Path) -> None:
    cache = VectorCache(tmp_path, max_bytes=2 * 320)
    cache.put_many({f"k{i}": [0.0] * 64 for i in range(5)})
    assert len(cache.get_many([f"k{i}" for i in range(5)])) == 2


def test_the_directory_is_overridable_and_otherwise_beside_the_pkg_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ORCHESTRATOR_VECTOR_CACHE_DIR", str(tmp_path / "v"))
    assert default_vector_cache_dir() == tmp_path / "v"
    monkeypatch.delenv("ORCHESTRATOR_VECTOR_CACHE_DIR")
    monkeypatch.setenv("ORCHESTRATOR_PKG_CACHE_DIR", str(tmp_path / "pkg"))
    assert default_vector_cache_dir() == tmp_path / "vectors"
    monkeypatch.setenv("ORCHESTRATOR_VECTOR_CACHE_MAX_MB", "7")
    assert VectorCache(tmp_path / "c").max_bytes == 7 * 1024 * 1024
    monkeypatch.delenv("ORCHESTRATOR_VECTOR_CACHE_MAX_MB")
    assert VectorCache(tmp_path / "d").max_bytes == DEFAULT_MAX_BYTES == 1024 * 1024 * 1024


def test_a_corrupt_cache_file_is_rebuilt_not_trusted(tmp_path: Path) -> None:
    VectorCache(tmp_path).put_many({"a": [1.0]})
    path = VectorCache(tmp_path).path
    path.write_bytes(b"this is not a database" * 100)
    cache = VectorCache(tmp_path)
    assert cache.get_many(["a"]) == {}
    cache.put_many({"a": [2.0]})
    assert cache.get_many(["a"]) == {"a": [2.0]}


def test_an_unwritable_cache_costs_time_never_an_answer(tmp_path: Path) -> None:
    blocker = tmp_path / "file"
    blocker.write_text("x")
    cache = VectorCache(blocker / "sub")  # its directory can never be created
    cache.put_many({"a": [1.0]})
    assert cache.get_many(["a"]) == {}
