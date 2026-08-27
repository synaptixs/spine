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
import importlib.util
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Sequence

    from orchestrator.pkg.join_link import JoinReport
    from orchestrator.pkg.repos import RepoSet

from orchestrator.pkg.extractor import RepoCodeExtractor
from orchestrator.pkg.facts import Edge, EdgeKind, FactBatch, Node, NodeKind, Provenance

# v2: batches carry post-``link_imports`` resolved IMPORTS edges — v1 caches
# predate the join and would silently reintroduce the dangling-import bug.
# v3: Java endpoints are import-resolved — v2 caches predate the check and would
# silently keep the Retrofit-as-JAX-RS endpoints it removes. The cache is keyed on
# the analyzed repo's HEAD, not ours, so an unchanged repo never re-extracts on
# upgrade; a false endpoint looks exactly like a real one, so bump on any change
# that makes previously-emitted facts wrong.
# v4: the cache key carries an *extractor fingerprint* (below), so this constant is
# no longer the only thing standing between an upgrade and a stale graph. Bumping
# it by hand stays correct but is now a belt to the fingerprint's braces.
_FORMAT_VERSION = 4


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


# Grammar packages whose presence changes what the extractor set can emit. A repo
# scanned with ``tree_sitter_java`` installed yields Java facts; the same repo at the
# same commit without it yields none — so the *available* front-ends are part of the
# cache key, not just the code. Probed with ``find_spec`` (no import), because this
# runs on the cache-*hit* path where loading a grammar would be pure cost.
_GRAMMAR_MODULES = (
    "tree_sitter",
    "tree_sitter_java",
    "tree_sitter_typescript",
    "tree_sitter_c_sharp",
    "tree_sitter_c",
    "tree_sitter_cpp",
    "tree_sitter_go",
    "sqlglot",
)

_FINGERPRINT: str | None = None


def extractor_fingerprint(*, package_dir: Path | None = None) -> str:
    """A stable digest of *what would be extracted*, not of the repo being scanned.

    The cache is keyed on the analyzed repo's HEAD, which says nothing about the
    extractor — so upgrading Spine over a repo that hasn't moved served the old graph
    forever, silently. The endpoint work is the case that made this load-bearing: it
    added 71 ``Endpoint`` nodes to this repo, and every warm cache kept answering
    "no endpoints" while ``pkg verify`` called the cached graph healthy.

    Two things go into it:

    - the **source** of every module in ``pkg/`` (sorted, name + bytes), so any change
      to a front-end, the vocabulary, or a post-pass invalidates by construction, with
      nobody remembering to bump a constant;
    - the **available grammars**, because the same code with a different set of
      installed extras extracts a different graph.

    Stable across processes and machines: it hashes file contents in sorted order and
    depends on no dict/set iteration order, no path, and no timestamp. Computed once
    per process — the source cannot change under a running interpreter.
    """
    global _FINGERPRINT
    if package_dir is None and _FINGERPRINT is not None:
        return _FINGERPRINT

    root = package_dir or Path(__file__).resolve().parent
    digest = hashlib.sha256()
    for path in sorted(root.glob("*.py"), key=lambda p: p.name):
        digest.update(path.name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    available = [name for name in _GRAMMAR_MODULES if importlib.util.find_spec(name) is not None]
    digest.update(",".join(available).encode("utf-8"))

    fingerprint = digest.hexdigest()[:16]
    if package_dir is None:
        _FINGERPRINT = fingerprint
    return fingerprint


def _cache_path(cache_dir: Path, root: Path, sha: str) -> Path:
    repo_key = hashlib.sha256(str(root.resolve()).encode("utf-8")).hexdigest()[:16]
    # The fingerprint sits in the *filename*, so a stale entry is never read and never
    # needs parsing to be rejected — a mismatch is simply a different file.
    return cache_dir / f"{repo_key}-{sha}-{extractor_fingerprint()}.json"


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


# ---- multi-repo -------------------------------------------------------------


@dataclass(frozen=True)
class RepoState:
    """What was loaded for one repository, and whether it can be trusted."""

    key: str
    root: Path
    sha: str | None
    dirty: bool
    cached: bool  # served from the commit-keyed cache rather than re-extracted

    @property
    def trusted(self) -> bool:
        """A clean tree at a known commit. Anything else describes uncommitted or unknown work."""
        return self.sha is not None and not self.dirty


@dataclass(frozen=True)
class MergedFacts:
    """A merged multi-repo graph, with the standing of every input attached.

    The standing is not decoration. A merged graph assembled from four repositories where one
    has a dirty tree describes work that is not committed anywhere, so it cannot back a currency
    gate, cannot be reproduced at a commit, and must not be quoted as a measurement. Carrying
    ``repos`` is what lets a caller tell that from a clean one — the alternative is a graph that
    looks identical either way, which is how "0 findings" comes to mean "nothing ran".
    """

    batch: FactBatch
    repos: tuple[RepoState, ...]
    #: What the declared joins placed, and what they could not. ``None`` when no joins are
    #: declared — which is not the same as "nothing to join", and the CLI says so.
    joins: JoinReport | None = None

    @property
    def trusted(self) -> bool:
        """**Any** dirty or non-git repository makes the whole merged graph untrusted.

        Not a per-repo verdict, because the graph is one artifact: a cache that is right about
        three of four inputs is not a cache, and a join that crosses into the dirty one is
        wrong wherever it lands.
        """
        return all(r.trusted for r in self.repos)

    @property
    def untrusted_keys(self) -> tuple[str, ...]:
        return tuple(r.key for r in self.repos if not r.trusted)

    def cache_key(self) -> tuple[tuple[str, str], ...]:
        """``((repo, sha), …)`` in key order — the multi-repo analogue of one HEAD SHA.

        Empty when any repo is untrusted, so it cannot accidentally name a graph that includes
        uncommitted work. Identity for a merged graph, not a filename: there is no merged cache
        entry, by design (see :func:`load_or_extract_repos`).
        """
        if not self.trusted:
            return ()
        return tuple((r.key, r.sha or "") for r in self.repos)


def _calls_sidecar(cache_file: Path) -> Path:
    """Where a repo's unresolved calls live, beside its cached facts.

    **A sidecar, not a key in the fact cache.** The facts file is the graph; these are not facts
    and must not travel inside it — the whole reason `python_client.emit` keeps them out of the
    batch. A separate file also means an absent or unreadable sidecar degrades to "extract
    again", never to a corrupt graph.
    """
    return cache_file.with_suffix(".calls.json")


def _save_calls(calls: Sequence[Any], path: Path) -> None:
    payload = [
        {
            "verb": c.verb,
            "path": c.path,
            "caller_id": c.caller_id,
            "file": c.provenance.file,
            "line": c.provenance.line,
        }
        for c in calls
    ]
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    except OSError:
        pass  # a cache that cannot be written is a slow path, never a failure


def _load_calls(path: Path) -> list[Any] | None:
    from orchestrator.pkg.python_client import PendingCall

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    try:
        return [
            PendingCall(
                verb=str(e["verb"]),
                path=str(e["path"]),
                caller_id=str(e["caller_id"]),
                provenance=Provenance(str(e["file"]), int(e["line"])),
            )
            for e in raw
        ]
    except (KeyError, TypeError, ValueError):
        return None


def load_or_extract_repos(
    repo_set: RepoSet,
    *,
    cache_dir: Path | None = None,
    extractor: RepoCodeExtractor | None = None,
) -> MergedFacts:
    """Extract every declared repository and merge into one scoped graph.

    **Per-repo caches, merged on read — there is no merged cache entry.** Change one of four
    repositories and one re-extracts while three are served from cache; a single entry keyed on
    the whole tuple would invalidate everything on any change. Merging is cheap and extraction
    is not, so the composition is done every time and the expensive half is what gets reused.

    Repos are processed in key order, and :func:`~orchestrator.pkg.scoping.merge_repos` sorts
    again, so the same declarations always produce the same graph regardless of how the YAML was
    written.
    """
    from orchestrator.pkg.join_link import link_joins
    from orchestrator.pkg.scoping import merge_repos

    batches: dict[str, FactBatch] = {}
    unresolved: dict[str, list[Any]] = {}
    states: list[RepoState] = []
    for key, root in repo_set:
        sha, dirty = repo_state(root)
        cache_file = _cache_path(cache_dir or default_cache_dir(), root, sha) if sha and not dirty else None
        was_cached = cache_file is not None and cache_file.exists()
        # A fresh extractor per repo: `unresolved_calls` accumulates on the instance, and one
        # shared extractor would attribute web's calls to billing.
        per_repo = extractor or RepoCodeExtractor()
        batches[key] = load_or_extract(root, cache_dir=cache_dir, extractor=per_repo)

        # The side-channel has to survive the cache. On a warm hit `load_or_extract` never runs
        # the extractor, so `unresolved_calls` is empty — and the joiner would silently see no
        # candidates and place nothing, which looks exactly like two uncoupled services.
        sidecar = _calls_sidecar(cache_file) if cache_file is not None else None
        cached_calls = _load_calls(sidecar) if sidecar is not None and was_cached else None
        if cached_calls is None:
            if was_cached:
                # Facts cached, calls missing: re-extract for the side-channel only, and write
                # it so this happens once rather than every load.
                per_repo.unresolved_calls.clear()
                per_repo.extract(root)
            cached_calls = list(per_repo.unresolved_calls)
            if sidecar is not None:
                _save_calls(cached_calls, sidecar)
        unresolved[key] = cached_calls
        per_repo.unresolved_calls.clear()
        states.append(RepoState(key, root, sha, dirty, cached=was_cached))

    merged = merge_repos(batches)
    # The joiner runs here and **only** here — never from `RepoCodeExtractor.extract` and never
    # beside `import_link`, which run on every extraction. A single-repo user declared no
    # topology, and must not inherit edges from a matcher that assumes one.
    report = None
    if repo_set.joins:
        merged, report = link_joins(merged, repo_set.joins, unresolved)
    return MergedFacts(merged, tuple(states), report)


__all__ = [
    "MergedFacts",
    "RepoState",
    "load_or_extract_repos",
    "FactCacheError",
    "default_cache_dir",
    "extractor_fingerprint",
    "facts_from_dict",
    "facts_to_dict",
    "load_facts",
    "load_or_extract",
    "repo_state",
    "save_facts",
]
