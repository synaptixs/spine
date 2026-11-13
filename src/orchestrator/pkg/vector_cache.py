"""The symbol-vector cache for hybrid search's meaning leg (hybrid KG search, D9).

Embedding every symbol of a large repository is the expensive step (minutes on a CPU); embedding the same text
with the same model twice is waste. So each vector is stored under a key made of what decides it — the model
file's sha256, the symbol-text recipe and its :data:`symbol_text.RECIPE_VERSION`, and the text itself — and
nothing else: no path, no commit. That is why it is valid on a dirty tree and incremental across commits (only
changed symbols are embedded again), and why a new model, recipe or text can never be served an old vector.

One SQLite file (stdlib ``sqlite3``) per cache directory, not one file per symbol: the PKG cache's
file-per-entry layout is what grew to 79,012 files here (B157). The directory is
``$ORCHESTRATOR_VECTOR_CACHE_DIR``, else
``vectors/`` beside the PKG cache. It is **capped** (``$ORCHESTRATOR_VECTOR_CACHE_MAX_MB``, default 1 GiB —
room for about five django-sized repositories with the largest shortlisted model) and evicts the least
recently used vectors first. Size is counted as each vector's bytes plus a fixed 64 per row (its key), so the
cap is the same number on every platform whatever SQLite's page overhead.

A cache is an optimisation, so it must only ever cost time: a file that is not a database is replaced, and a
cache that cannot be opened or written is skipped — the vectors are then computed, never guessed.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import sqlite3
from array import array
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path

DEFAULT_MAX_BYTES = 1024 * 1024 * 1024
#: Bytes counted per row on top of the vector itself: the 64 hex characters of its key.
ROW_OVERHEAD = 64
_FILE = "vectors.sqlite3"
#: SQLite's default limit on bound parameters is 999 on older builds; stay under it.
_CHUNK = 900


def default_vector_cache_dir() -> Path:
    """``$ORCHESTRATOR_VECTOR_CACHE_DIR``, else ``vectors/`` beside the PKG cache."""
    override = os.getenv("ORCHESTRATOR_VECTOR_CACHE_DIR", "").strip()
    if override:
        return Path(override).expanduser()
    from orchestrator.pkg.persistence import default_cache_dir

    return default_cache_dir().parent / "vectors"


def _max_bytes_from_env() -> int:
    raw = os.getenv("ORCHESTRATOR_VECTOR_CACHE_MAX_MB", "").strip()
    try:
        return int(float(raw) * 1024 * 1024) if raw else DEFAULT_MAX_BYTES
    except ValueError:
        return DEFAULT_MAX_BYTES


class VectorCache:
    """Content-keyed float32 vectors with an LRU size cap. Safe to share between processes (SQLite locks)."""

    def __init__(self, directory: Path | str | None = None, *, max_bytes: int | None = None) -> None:
        self.directory = Path(directory) if directory is not None else default_vector_cache_dir()
        self.path = self.directory / _FILE
        self.max_bytes = max_bytes if max_bytes is not None else _max_bytes_from_env()

    @staticmethod
    def key(model_sha256: str, recipe: str, text: str) -> str:
        """sha256 over the model file's digest, the recipe and its version, and the text."""
        from orchestrator.pkg import symbol_text

        h = hashlib.sha256()
        for part in (model_sha256, f"{recipe}:{symbol_text.RECIPE_VERSION}", text):
            h.update(part.encode("utf-8"))
            h.update(b"\0")
        return h.hexdigest()

    def _open(self) -> sqlite3.Connection | None:
        for attempt in (0, 1):
            try:
                self.directory.mkdir(parents=True, exist_ok=True)
                con = sqlite3.connect(self.path, timeout=30)
                # Both only take effect on a new file. A vector is 1.5–3 KB: on the default 4 KB page each row
                # takes a page of its own and the file is twice its contents (measured on django-17058); on
                # 64 KB pages the waste is a few percent.
                con.execute("PRAGMA page_size = 65536")
                con.execute("PRAGMA auto_vacuum = INCREMENTAL")
                con.execute(
                    "CREATE TABLE IF NOT EXISTS vectors "
                    "(key TEXT PRIMARY KEY, data BLOB NOT NULL, used INTEGER)"
                )
                con.execute("CREATE INDEX IF NOT EXISTS vectors_used ON vectors (used)")
                con.execute("CREATE TABLE IF NOT EXISTS meta (name TEXT PRIMARY KEY, value INTEGER)")
                return con
            except sqlite3.DatabaseError:
                # Not a database (or a damaged one): it holds nothing we can trust, so start it again, once.
                if attempt == 0:
                    with contextlib.suppress(OSError):
                        self.path.unlink()
                    continue
                return None
            except OSError:
                return None
        return None

    @contextlib.contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection | None]:
        con = self._open()
        try:
            yield con
        finally:
            if con is not None:
                con.close()

    @staticmethod
    def _tick(con: sqlite3.Connection) -> int:
        """A counter that only goes up: the LRU clock. Not wall time, so no clock skew reorders it."""
        row = con.execute("SELECT value FROM meta WHERE name = 'clock'").fetchone()
        now = (row[0] if row else 0) + 1
        con.execute("INSERT OR REPLACE INTO meta (name, value) VALUES ('clock', ?)", (now,))
        return now

    def get_many(self, keys: Sequence[str]) -> dict[str, list[float]]:
        """The cached vectors among ``keys`` (a miss is absent), each marked as just used."""
        found: dict[str, list[float]] = {}
        with self._connection() as con:
            if con is None:
                return found
            try:
                with con:
                    for start in range(0, len(keys), _CHUNK):
                        chunk = list(keys[start : start + _CHUNK])
                        marks = ",".join("?" * len(chunk))
                        # Only "?" placeholders are interpolated; every key is a bound parameter.
                        query = f"SELECT key, data FROM vectors WHERE key IN ({marks})"  # noqa: S608
                        for key, data in con.execute(query, chunk):
                            found[key] = array("f", data).tolist()
                    if found:
                        now = self._tick(con)
                        con.executemany(
                            "UPDATE vectors SET used = ? WHERE key = ?", ((now, k) for k in found)
                        )
            except sqlite3.Error:
                return found
        return found

    def put_many(self, items: Mapping[str, Sequence[float]]) -> None:
        """Store ``items`` as float32, then evict least-recently-used rows until under the cap."""
        if not items:
            return
        with self._connection() as con:
            if con is None:
                return
            try:
                with con:
                    now = self._tick(con)
                    con.executemany(
                        "INSERT OR REPLACE INTO vectors (key, data, used) VALUES (?, ?, ?)",
                        ((k, array("f", v).tobytes(), now) for k, v in items.items()),
                    )
                    self._evict(con)
                con.execute("PRAGMA incremental_vacuum")
            except sqlite3.Error:
                return

    def _evict(self, con: sqlite3.Connection) -> None:
        total = self._size(con)
        if total <= self.max_bytes:
            return
        # Oldest first; within one batch (same tick) the later key goes first, so a batch over the cap keeps a
        # deterministic subset.
        doomed: list[str] = []
        for key, size in con.execute(
            "SELECT key, length(data) + ? FROM vectors ORDER BY used ASC, key DESC", (ROW_OVERHEAD,)
        ):
            if total <= self.max_bytes:
                break
            doomed.append(key)
            total -= size
        con.executemany("DELETE FROM vectors WHERE key = ?", ((k,) for k in doomed))

    @staticmethod
    def _size(con: sqlite3.Connection) -> int:
        query = "SELECT COALESCE(SUM(length(data) + ?), 0) FROM vectors"
        row = con.execute(query, (ROW_OVERHEAD,)).fetchone()
        return int(row[0])

    def size_bytes(self) -> int:
        """Bytes counted against the cap (vectors plus :data:`ROW_OVERHEAD` per row)."""
        with self._connection() as con:
            if con is None:
                return 0
            try:
                return self._size(con)
            except sqlite3.Error:
                return 0


__all__ = ["DEFAULT_MAX_BYTES", "ROW_OVERHEAD", "VectorCache", "default_vector_cache_dir"]
