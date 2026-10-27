"""One loaded graph per repository, kept for the life of the MCP server process (B110).

Before this, every tool call loaded the graph again: on a clean tree it re-read and re-parsed the
commit-keyed cache file (~2.9 s per call on a ~1,000-file repository), and on a dirty tree — while
someone is editing — it re-extracted the whole repository on every call (10–13 s). An editor asking
about each open file, or an agent asking ten questions, paid that every time.

**What a kept graph is keyed on, and why that is the existing trust rule.** The key is the tree
state the graph was extracted from: ``HEAD`` and the extractor fingerprint, plus — on a dirty tree —
every path ``git status`` reports and the bytes now in it (untracked files included; Spine's own
``.spine/plans/`` excluded, as in :func:`~orchestrator.pkg.persistence.worktree_dirty`). The same
tree gives the same facts (the extraction is deterministic), so a hit returns exactly what a fresh
load would. This is what the commit-keyed disk cache already trusts on a clean tree, extended to a
dirty one by hashing what makes it dirty; like that cache it does not see changes to files git
ignores. A directory outside git has no tree state to key on and is never kept.

**What a caller gets.** A shared :class:`~orchestrator.pkg.FactStore` (it copies the batch's nodes
and edges when built, and only adds lazy indexes after) and a *copy* of the batch: doc linking adds
``Doc`` nodes and ``MENTIONS`` edges to the batch it is given, so the kept batch is never handed out.

**The doc-linked store is kept with it** (in ``docs``, filled by the first call that links docs):
a repository's own docs are files in the same tree the key covers. One with external sources
(Confluence, RAG) binds them at read time and is never kept — those pages are not in the tree.

**When nothing is kept.** A load whose tree changed while it ran (the key is taken again after it),
or whose untraced-call list could not be loaded (the next call tries again).
"""

from __future__ import annotations

import hashlib
import subprocess
import threading
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from orchestrator.pkg import FactBatch, FactStore
    from orchestrator.pkg.unbound import UnboundIndex

#: Repositories kept at once; the least recently used is dropped first.
MAX_REPOS = 4

#: ``(batch, untraced index or None, why the index is missing or None)`` — what a load returns.
Loaded = tuple["FactBatch", "UnboundIndex | None", "str | None"]


@dataclass(frozen=True)
class _Kept:
    key: str
    batch: FactBatch
    store: FactStore
    index: UnboundIndex | None
    docs: list[FactStore] = field(default_factory=list)  # the doc-linked store, once linked


_kept: OrderedDict[Path, _Kept] = OrderedDict()
_lock = threading.Lock()


def clear() -> None:
    """Forget every kept graph."""
    with _lock:
        _kept.clear()


def kept_batch(root: Path) -> FactBatch | None:
    """The graph kept for ``root`` (resolved), or ``None`` — for tests and diagnostics."""
    with _lock:
        kept = _kept.get(root)
    return kept.batch if kept is not None else None


def tree_key(root: Path) -> str | None:
    """A digest of the tree state at ``root``, or ``None`` when there is none to trust."""
    from orchestrator.pkg.persistence import extractor_fingerprint, repo_state

    sha, dirty = repo_state(root)
    if sha is None:
        return None
    digest = hashlib.sha256(f"{sha}\0{extractor_fingerprint()}".encode())
    if dirty:
        changes = _dirty_digest(root)
        if changes is None:
            return None
        digest.update(changes.encode())
    return digest.hexdigest()


def _dirty_digest(root: Path) -> str | None:
    """Every path git reports as changed, with the bytes now in it."""
    from orchestrator.pkg.persistence import _SPINE_GENERATED

    def git(*args: str) -> bytes | None:
        proc = subprocess.run(["git", "-C", str(root), *args], capture_output=True, timeout=30, check=False)
        return proc.stdout if proc.returncode == 0 else None

    top = git("rev-parse", "--show-toplevel")
    status = git("status", "--porcelain=v1", "-z", "-uall", "--", _SPINE_GENERATED)
    if top is None or status is None:
        return None
    base = Path(top.decode().strip())
    digest = hashlib.sha256(status)
    # -z entries are "XY path"; a rename or copy entry is followed by its source path as a field
    # of its own, which names no content now and is already in the status bytes above
    fields = iter(status.split(b"\0"))
    for entry in fields:
        if not entry:
            continue
        code, rel = entry[:2], entry[3:]
        if b"R" in code or b"C" in code:
            next(fields, None)
        path = base / rel.decode("utf-8", "surrogateescape")
        digest.update(b"\0" + rel + b"\0")
        digest.update(path.read_bytes() if path.is_file() else b"-")
    return digest.hexdigest()


def load(
    root: Path, *, with_unbound: bool, loader: Callable[[], Loaded]
) -> tuple[FactBatch, FactStore, Loaded, list[FactStore] | None]:
    """``(batch, store, loaded, docs)`` for ``root``: the kept graph when the tree has not changed
    since, else ``loader()`` — kept for the next call when its tree held still. ``batch`` is the
    caller's own copy; ``store`` may be shared; ``docs`` is the kept graph's slot for its doc-linked
    store (``None`` when nothing is kept for this tree)."""
    from orchestrator.pkg import FactStore

    key = tree_key(root)
    if key is not None:
        with _lock:
            kept = _kept.get(root)
            if kept is not None and kept.key == key and (kept.index is not None or not with_unbound):
                _kept.move_to_end(root)
                return _copy(kept.batch), kept.store, (kept.batch, kept.index, None), kept.docs

    loaded = loader()
    batch, index, why = loaded
    store = FactStore(batch)
    if key is None or why is not None or tree_key(root) != key:
        return batch, store, loaded, None
    kept = _Kept(key, _copy(batch), store, index)
    with _lock:
        _kept[root] = kept
        _kept.move_to_end(root)
        while len(_kept) > MAX_REPOS:
            _kept.popitem(last=False)
    return batch, store, loaded, kept.docs


def _copy(batch: FactBatch) -> FactBatch:
    from orchestrator.pkg import FactBatch

    out = FactBatch()
    out._nodes = dict(batch._nodes)
    out._edges = dict(batch._edges)
    return out
