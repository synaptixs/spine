"""Persist extracted PKG facts — the graph becomes a cacheable artifact.

``save_facts``/``load_facts`` round-trip a ``FactBatch`` through JSON.
``load_or_extract`` is the consumer-facing entry: it keys the cache on the
repo's **HEAD commit SHA** and only trusts it for a *clean* tree — a dirty
worktree or a non-git directory always re-extracts, because cached facts
could silently disagree with the source (the one sin the PKG must never
commit). This is the groundwork for Track 1.4's merge-hook freshness: the
graph is a build artifact keyed to a commit, never a one-time crawl.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

from orchestrator.pkg.extractor import RepoCodeExtractor
from orchestrator.pkg.facts import Edge, EdgeKind, FactBatch, Node, NodeKind, Provenance

_FORMAT_VERSION = 1


class FactCacheError(RuntimeError):
    """A cache file exists but cannot be understood."""


# ---- JSON round-trip --------------------------------------------------------


def _prov_to_dict(prov: Provenance | None) -> dict[str, Any] | None:
    if prov is None:
        return None
    return {"file": prov.file, "line": prov.line, "end_line": prov.end_line}


def _prov_from_dict(raw: dict[str, Any] | None) -> Provenance | None:
    if raw is None:
        return None
    return Provenance(file=str(raw["file"]), line=int(raw["line"]), end_line=raw.get("end_line"))


def facts_to_dict(batch: FactBatch) -> dict[str, Any]:
    return {
        "version": _FORMAT_VERSION,
        "nodes": [
            {
                "id": n.id,
                "kind": n.kind.value,
                "name": n.name,
                "language": n.language,
                "provenance": _prov_to_dict(n.provenance),
                "external": n.external,
            }
            for n in batch.nodes
        ],
        "edges": [
            {
                "src": e.src,
                "dst": e.dst,
                "kind": e.kind.value,
                "provenance": _prov_to_dict(e.provenance),
            }
            for e in batch.edges
        ],
    }


def facts_from_dict(payload: dict[str, Any]) -> FactBatch:
    if payload.get("version") != _FORMAT_VERSION:
        raise FactCacheError(f"unsupported fact-cache version: {payload.get('version')!r}")
    batch = FactBatch()
    for raw in payload.get("nodes") or []:
        batch.add_node(
            Node(
                id=str(raw["id"]),
                kind=NodeKind(raw["kind"]),
                name=str(raw["name"]),
                language=str(raw.get("language") or ""),
                provenance=_prov_from_dict(raw.get("provenance")),
                external=bool(raw.get("external", False)),
            )
        )
    for raw in payload.get("edges") or []:
        batch.add_edge(
            Edge(
                src=str(raw["src"]),
                dst=str(raw["dst"]),
                kind=EdgeKind(raw["kind"]),
                provenance=_prov_from_dict(raw.get("provenance")),
            )
        )
    return batch


def save_facts(batch: FactBatch, path: Path | str) -> None:
    """Write the batch as JSON (parents created)."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(facts_to_dict(batch)), encoding="utf-8")


def load_facts(path: Path | str) -> FactBatch:
    """Load a batch saved by ``save_facts``."""
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FactCacheError(f"unreadable fact cache {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise FactCacheError(f"fact cache {path} is not a JSON object")
    return facts_from_dict(payload)


# ---- commit-keyed cache -----------------------------------------------------


def _git(root: Path, *args: str) -> str | None:
    proc = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, timeout=30, check=False
    )
    return proc.stdout.strip() if proc.returncode == 0 else None


def repo_state(root: Path | str) -> tuple[str | None, bool]:
    """``(head_sha, dirty)`` for the repo at ``root``; ``(None, True)`` outside git."""
    root_path = Path(root)
    sha = _git(root_path, "rev-parse", "HEAD")
    if sha is None:
        return None, True
    status = _git(root_path, "status", "--porcelain")
    return sha, bool(status)


def default_cache_dir() -> Path:
    return Path.home() / ".cache" / "orchestrator" / "pkg"


def _cache_path(cache_dir: Path, root: Path, sha: str) -> Path:
    repo_key = hashlib.sha256(str(root.resolve()).encode("utf-8")).hexdigest()[:16]
    return cache_dir / f"{repo_key}-{sha}.json"


def load_or_extract(
    root: Path | str,
    *,
    cache_dir: Path | None = None,
    extractor: RepoCodeExtractor | None = None,
) -> FactBatch:
    """Extract ``root``'s facts, reusing a commit-keyed cache when safe.

    Cache hit requires: a git repo, a *clean* tree, and a cache file for the
    exact HEAD SHA. Anything else (dirty tree, non-git dir, stale/corrupt
    cache) falls back to a fresh extraction; clean trees re-populate the cache.
    """
    root_path = Path(root)
    cache = cache_dir or default_cache_dir()
    sha, dirty = repo_state(root_path)

    cache_file = _cache_path(cache, root_path, sha) if sha and not dirty else None
    if cache_file is not None and cache_file.exists():
        try:
            return load_facts(cache_file)
        except FactCacheError:
            cache_file.unlink(missing_ok=True)  # corrupt — rebuild below

    batch = (extractor or RepoCodeExtractor()).extract(root_path)
    if cache_file is not None:
        save_facts(batch, cache_file)
    return batch


__all__ = [
    "FactCacheError",
    "default_cache_dir",
    "facts_from_dict",
    "facts_to_dict",
    "load_facts",
    "load_or_extract",
    "repo_state",
    "save_facts",
]
