"""Hybrid retrieval over the PKG: four ranked legs fused by reciprocal rank (hybrid KG search, D6/D7/D23).

Spine ranked a ticket's words against symbol names and path words only (``retrieval.GroundedRetriever``).
That leg
stays exactly as it is and becomes one of four:

* **name** — today's ``scored_symbols`` order, the title-first rule (SSPN-192) included;
* **content** — BM25 over each symbol's own source text (``symbol_text`` + ``bm25``);
* **meaning** — cosine similarity from a local, pinned embedding model, only when one is given (the
``semantic``
  extra, PR2); without it the core runs three legs and says so (``legs_used``);
* **graph** — personalized PageRank over the graph's structural edges, seeded by the fused top of the other
legs,
  so a symbol none of the words name is still found when it sits next to ones they do.

The legs are fused by reciprocal rank fusion (``Σ wᵢ / (k + rankᵢ)``, k = 60): ranks only, so no leg's
score scale
leaks into another's. Everything here is deterministic — sorted iteration, rounded scores, ties broken by
node id —
and nothing calls a model unless an ``Embedder`` is passed in. ``understand`` and ``state`` never import
this module
(invariant 2).

What is returned is **code**: ``Doc`` and ``Intent`` nodes may carry a walk, but are never a landing site
(B158), and
test code (``Node.test``) is walked through but never returned (D6) unless asked for. Each hit says which
legs found
it and, for the graph, the edge it was reached by — a ranked hit with no stated reason cannot be checked
(D20).

The numbers (k, seeds, restart, iterations, the edge set) are the pre-registered ones
(``~/plans/hybrid-kg-search-p0/PREREGISTRATION.md`` §3); changing one changes what was measured.
"""

from __future__ import annotations

import math
import os
import weakref
from array import array
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from orchestrator.pkg.bm25 import BM25Index, index_symbol_texts
from orchestrator.pkg.facts import EdgeKind, Node
from orchestrator.pkg.retrieval import GroundedRetriever, ScoredSymbol, word_tokens
from orchestrator.pkg.store import FactStore
from orchestrator.pkg.symbol_text import CODE_KINDS, Recipe, symbol_texts
from orchestrator.pkg.vector_cache import VectorCache

#: The edges the graph leg walks, in both directions. SERVES (symbol → ticket) and PROVIDES are left out:
#: one ties
#: code to tickets, the other to DI containers, and neither says two pieces of code work together (D6).
TRAVERSAL_EDGES = frozenset(
    {
        EdgeKind.CALLS,
        EdgeKind.CONTAINS,
        EdgeKind.IMPORTS,
        EdgeKind.IMPLEMENTS,
        EdgeKind.EXPOSES,
        EdgeKind.CONSUMES,
        EdgeKind.REFERENCES,
        EdgeKind.MENTIONS,
        EdgeKind.READS,
        EdgeKind.WRITES,
    }
)

LEG_NAME, LEG_CONTENT, LEG_MEANING, LEG_GRAPH = "name", "content", "meaning", "graph"

#: The stages that can take hybrid search, each with its own off-switch ``SPINE_HYBRID_<STAGE>=0`` (D11, D14).
STAGES = ("investigate", "rca", "design", "plan", "autorun")


def hybrid_enabled(stage: str | None) -> bool:
    """Whether ``stage`` ranks with hybrid search.

    Off unless ``SPINE_HYBRID_SEARCH=1`` (the default is off until each stage has passed its pre-registered
    bar,
    PR3), and then on for every named stage except one switched off with ``SPINE_HYBRID_<STAGE>=0``. A
    caller that
    names no stage never gets it, so a new caller of ``build_investigation`` keeps the lexical ranking.
    """
    if stage is None or stage not in STAGES:
        return False
    if os.environ.get("SPINE_HYBRID_SEARCH", "0").strip() != "1":
        return False
    return os.environ.get(f"SPINE_HYBRID_{stage.upper()}", "1").strip() != "0"


_SCORE_PLACES = 6
_COSINE_PLACES = 4  # D17: rounded before ranking, so a last-bit float difference across CPUs never reorders
_PPR_PLACES = 9


class Embedder(Protocol):
    """A text embedding model (``pkg.embed``; the real ones need the ``semantic`` extra).

    ``model_id`` is what a brief prints; ``sha256`` is the model file's digest, which keys the vector cache.
    """

    @property
    def model_id(self) -> str: ...

    @property
    def sha256(self) -> str: ...

    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


@dataclass(frozen=True)
class HybridConfig:
    """The pre-registered knobs (§3, §4). A grid cell is a recipe, a name weight and a model."""

    recipe: Recipe = Recipe.WITH_BODY
    name_weight: float = 1.0
    rrf_k: int = 60
    seeds: int = 20
    restart: float = 0.15
    iterations: int = 30
    pool: int = 200  # how deep each leg is read before fusion
    include_tests: bool = False


@dataclass(frozen=True)
class HybridHit:
    """A fused hit and the evidence it rests on."""

    node: Node
    score: float
    #: Leg name → this node's 1-based rank in that leg. Only legs that found it.
    legs: dict[str, int] = field(default_factory=dict)
    #: For a graph-leg hit that was not a seed: ``(seed id, edge kind)`` or, two hops out,
    #: ``(seed id, edge kind, middle id, edge kind)``. Empty otherwise.
    via: tuple[str, ...] = ()
    #: The name leg's shared words, when the name leg found it.
    matched: tuple[str, ...] = ()
    #: The name leg calls it weak and no other leg supports it independently (D25).
    weak: bool = False
    found_by: str = ""


def reciprocal_rank_fusion(
    ranked: Mapping[str, Sequence[str]], *, k: int = 60, weights: Mapping[str, float] | None = None
) -> list[tuple[str, float]]:
    """``Σ w_leg / (k + rank)`` over the legs each id appears in, best first, ties by id."""
    scores: dict[str, float] = {}
    for leg in sorted(ranked):
        w = (weights or {}).get(leg, 1.0)
        for rank, item in enumerate(ranked[leg], start=1):
            scores[item] = scores.get(item, 0.0) + w / (k + rank)
    return sorted(((i, round(s, _SCORE_PLACES)) for i, s in scores.items()), key=lambda r: (-r[1], r[0]))


@dataclass
class _Adjacency:
    ids: list[str]
    index: dict[str, int]
    #: Per node: sorted unique (neighbour index, edge kind) pairs, both directions.
    nbrs: list[list[tuple[int, str]]]


_ADJ_CACHE: weakref.WeakKeyDictionary[FactStore, _Adjacency] = weakref.WeakKeyDictionary()


def _adjacency(store: FactStore) -> _Adjacency:
    cached = _ADJ_CACHE.get(store)
    if cached is not None:
        return cached
    pairs: set[tuple[str, str, str]] = set()
    ids: set[str] = {n.id for n in store.nodes}
    for kind in TRAVERSAL_EDGES:
        for e in store.edges_of_kind(kind):
            if e.src == e.dst:
                continue
            pairs.add((e.src, e.dst, kind.value))
            pairs.add((e.dst, e.src, kind.value))
            ids.update((e.src, e.dst))
    order = sorted(ids)
    index = {nid: i for i, nid in enumerate(order)}
    nbrs: list[set[tuple[int, str]]] = [set() for _ in order]
    for src, dst, edge in pairs:
        nbrs[index[src]].add((index[dst], edge))
    adj = _Adjacency(order, index, [sorted(s) for s in nbrs])
    _ADJ_CACHE[store] = adj
    return adj


def personalized_pagerank(
    store: FactStore, seeds: Mapping[str, float], *, restart: float = 0.15, iterations: int = 30
) -> dict[str, float]:
    """Personalized PageRank by power iteration over :data:`TRAVERSAL_EDGES`, both directions.

    ``p ← restart·s + (1 − restart)·Wᵀp``, ``W`` row-normalised by degree; a node with no edge returns its
    mass to
    the seeds. A fixed number of iterations, nodes in id order: the same graph and seeds give the same
    numbers.
    Only nodes with a positive score are returned.
    """
    adj = _adjacency(store)
    total = sum(w for nid, w in seeds.items() if nid in adj.index and w > 0)
    if total <= 0:
        return {}
    s = {adj.index[nid]: w / total for nid, w in sorted(seeds.items()) if nid in adj.index and w > 0}
    p = dict(s)
    keep = 1.0 - restart
    for _ in range(iterations):
        new: dict[int, float] = {i: restart * w for i, w in s.items()}
        dangling = 0.0
        for u in sorted(p):
            mass = p[u]
            nb = adj.nbrs[u]
            if not nb:
                dangling += mass
                continue
            share = keep * mass / len(nb)
            for v, _kind in nb:
                new[v] = new.get(v, 0.0) + share
        if dangling:
            for i, w in s.items():
                new[i] = new.get(i, 0.0) + keep * dangling * w
        p = new
    return {adj.ids[i]: round(v, _PPR_PLACES) for i, v in p.items() if v > 0}


_TEXT_CACHE: weakref.WeakKeyDictionary[
    FactStore, dict[tuple[str, str, bool], tuple[dict[str, str], BM25Index]]
] = weakref.WeakKeyDictionary()


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return round(dot / (na * nb), _COSINE_PLACES) if na and nb else 0.0


def _f32(vec: Sequence[float]) -> list[float]:
    """``vec`` as float32 values. Fresh and cached vectors both pass through here, so a cold and a warm run
    rank on the same numbers."""
    return array("f", vec).tolist()


class _MeaningIndex:
    """The symbol vectors of one graph, ranked against a query by cosine rounded to 4 places, ties by id
    (D17).

    With numpy (the ``semantic`` extra) the vectors are one float32 matrix and a query is one product; without
    it, plain Python. Both round each similarity with Python's ``round`` before ranking, so they rank
    alike."""

    #: Tests switch this off to compare the two paths.
    use_numpy = True

    def __init__(self, ids: list[str], rows: list[list[float]] | None, matrix: object | None) -> None:
        self.ids = ids
        self._rows = rows
        self._matrix = matrix

    @classmethod
    def build(cls, ids: Sequence[str], vectors: Sequence[Sequence[float]]) -> _MeaningIndex:
        if cls.use_numpy:
            try:
                import numpy as np
            except ImportError:
                pass
            else:
                m = np.asarray(vectors, dtype=np.float32).reshape(len(ids), -1)
                norms = np.linalg.norm(m, axis=1, keepdims=True)
                m = np.where(norms > 0, m / np.where(norms > 0, norms, 1.0), 0.0).astype(np.float32)
                return cls(list(ids), None, m)
        return cls(list(ids), [list(v) for v in vectors], None)

    def rank(self, query: Sequence[float], pool: int) -> list[str]:
        if self._matrix is not None:
            import numpy as np

            q = np.asarray(query, dtype=np.float32)
            nq = float(np.linalg.norm(q))
            if not nq or not self.ids:
                return []
            sims = (self._matrix @ (q / nq)).tolist()  # type: ignore[operator]
            scored = [(nid, round(s, _COSINE_PLACES)) for nid, s in zip(self.ids, sims, strict=True)]
        else:
            scored = [(nid, _cosine(query, vec)) for nid, vec in zip(self.ids, self._rows or [], strict=True)]
        scored = [r for r in scored if r[1] > 0]
        scored.sort(key=lambda r: (-r[1], r[0]))
        return [nid for nid, _ in scored[:pool]]


_MEANING_CACHE: weakref.WeakKeyDictionary[FactStore, dict[tuple[str, str, bool, str], _MeaningIndex]] = (
    weakref.WeakKeyDictionary()
)


class HybridRetriever:
    """The four legs over one graph. Build once per graph and root; ``search`` many times."""

    def __init__(
        self,
        store: FactStore,
        root: Path | str | None,
        *,
        config: HybridConfig | None = None,
        embedder: Embedder | None = None,
        roots: Mapping[str, Path | str] | None = None,
        vector_cache: VectorCache | None = None,
    ) -> None:
        self._store = store
        self._root = Path(root) if root is not None else None
        self._roots = roots
        self._cfg = config or HybridConfig()
        self._embedder = embedder
        self._vector_cache = vector_cache
        self._index: _MeaningIndex | None = None

    @property
    def legs_used(self) -> tuple[str, ...]:
        legs = [LEG_NAME]
        if self._root is not None or self._roots:
            legs.append(LEG_CONTENT)
        if self._embedder is not None and (self._root is not None or self._roots):
            legs.append(LEG_MEANING)
        legs.append(LEG_GRAPH)
        return tuple(legs)

    @property
    def model(self) -> str | None:
        """The embedding model the meaning leg ranks with, or ``None`` when that leg does not run (D17)."""
        return (
            self._embedder.model_id if self._embedder is not None and LEG_MEANING in self.legs_used else None
        )

    def _texts(self) -> tuple[dict[str, str], BM25Index]:
        root = self._root if self._root is not None else Path(".")
        key = (str(root.resolve()), self._cfg.recipe.value, self._cfg.include_tests)
        per_store = _TEXT_CACHE.setdefault(self._store, {})
        if key not in per_store:
            texts = symbol_texts(
                self._store, root, self._cfg.recipe, include_tests=self._cfg.include_tests, roots=self._roots
            )
            per_store[key] = (texts, index_symbol_texts(texts))
        return per_store[key]

    def _returnable(self, node: Node | None) -> bool:
        return (
            node is not None
            and node.grounded
            and node.kind in CODE_KINDS
            and (self._cfg.include_tests or not node.test)
        )

    def _meaning(self, text: str, texts: Mapping[str, str]) -> list[str]:
        if self._embedder is None:
            return []
        if self._index is None:
            self._index = self._meaning_index(self._embedder, texts)
        [query] = self._embedder.embed([text])
        return self._index.rank(_f32(query), self._cfg.pool)

    def _meaning_index(self, embedder: Embedder, texts: Mapping[str, str]) -> _MeaningIndex:
        """Every symbol's vector: from the content-keyed cache (D9), embedding only the texts it lacks."""
        root = self._root if self._root is not None else Path(".")
        memo_key = (str(root.resolve()), self._cfg.recipe.value, self._cfg.include_tests, embedder.sha256)
        per_store = _MEANING_CACHE.setdefault(self._store, {})
        if memo_key in per_store:
            return per_store[memo_key]
        cache = self._vector_cache if self._vector_cache is not None else VectorCache()
        ids = list(texts)
        keys = {nid: VectorCache.key(embedder.sha256, self._cfg.recipe.value, texts[nid]) for nid in ids}
        text_of = {keys[nid]: texts[nid] for nid in ids}
        found = cache.get_many(sorted(text_of))
        missing = sorted(k for k in text_of if k not in found)
        if missing:
            fresh = {
                k: _f32(v)
                for k, v in zip(missing, embedder.embed([text_of[k] for k in missing]), strict=True)
            }
            cache.put_many(fresh)
            found.update(fresh)
        index = _MeaningIndex.build(ids, [found[keys[nid]] for nid in ids])
        per_store[memo_key] = index
        return index

    def search(
        self, text: str, *, title: str | None = None, limit: int = 50, extra_seeds: Sequence[str] = ()
    ) -> list[HybridHit]:
        """The fused ranking for ``text`` (the ticket's title and body; ``title`` keeps SSPN-192's rule
        inside the
        name leg). ``extra_seeds`` are node ids the caller already trusts — RCA's stated files and exception
        classes — added to the graph leg's seeds at the top seed's weight."""
        cfg = self._cfg
        store = self._store
        legs: dict[str, list[str]] = {}
        named: dict[str, ScoredSymbol] = {}
        for hit in GroundedRetriever(store).scored_symbols(text, limit=cfg.pool, title=title):
            if self._returnable(hit.node):
                named[hit.node.id] = hit
        legs[LEG_NAME] = list(named)
        if LEG_CONTENT in self.legs_used:
            texts, index = self._texts()
            legs[LEG_CONTENT] = [nid for nid, _ in index.ranked(word_tokens(text), limit=cfg.pool)]
            if LEG_MEANING in self.legs_used:
                legs[LEG_MEANING] = self._meaning(text, texts)
        weights = {LEG_NAME: cfg.name_weight}

        seeded = reciprocal_rank_fusion(legs, k=cfg.rrf_k, weights=weights)[: cfg.seeds]
        seeds = {nid: 1.0 / (cfg.rrf_k + rank) for rank, (nid, _) in enumerate(seeded, start=1)}
        top = max(seeds.values(), default=1.0 / (cfg.rrf_k + 1))
        for nid in extra_seeds:
            if store.node(nid) is not None:
                seeds[nid] = max(seeds.get(nid, 0.0), top)
        ppr = personalized_pagerank(store, seeds, restart=cfg.restart, iterations=cfg.iterations)
        walked = sorted(ppr.items(), key=lambda r: (-r[1], r[0]))
        legs[LEG_GRAPH] = [nid for nid, _ in walked if self._returnable(store.node(nid))][: cfg.pool]

        fused = reciprocal_rank_fusion(legs, k=cfg.rrf_k, weights=weights)[:limit]
        rank_in = {leg: {nid: i for i, nid in enumerate(ids, start=1)} for leg, ids in legs.items()}
        out: list[HybridHit] = []
        for nid, score in fused:
            node = store.node(nid)
            if node is None:
                continue
            found = {leg: ranks[nid] for leg, ranks in rank_in.items() if nid in ranks}
            via = self._via(nid, seeds) if LEG_GRAPH in found and nid not in seeds else ()
            name_hit = named.get(nid)
            weak = (
                name_hit is not None
                and name_hit.weak
                and not self._independently_supported(nid, found, seeds)
            )
            out.append(
                HybridHit(
                    node=node,
                    score=score,
                    legs=found,
                    via=via,
                    matched=name_hit.matched if name_hit else (),
                    weak=weak,
                    found_by=self._found_by(found, via),
                )
            )
        return out

    def _independently_supported(
        self, nid: str, found: Mapping[str, int], seeds: Mapping[str, float]
    ) -> bool:
        """Evidence beyond the name leg (D25): the content or meaning leg ranks it in its top ``seeds``, or
        the
        graph reached it without it being a seed. Every name hit is also a content hit (its text holds its
        name)
        and every seed comes back from the walk, so "found by another leg" alone would make nothing weak."""
        if any(found.get(leg, self._cfg.seeds + 1) <= self._cfg.seeds for leg in (LEG_CONTENT, LEG_MEANING)):
            return True
        return LEG_GRAPH in found and nid not in seeds

    def _via(self, nid: str, seeds: Mapping[str, float]) -> tuple[str, ...]:
        """The strongest seed one edge away, else two; ties by id."""
        adj = _adjacency(self._store)
        i = adj.index.get(nid)
        if i is None:
            return ()
        best: tuple[float, str, tuple[str, ...]] | None = None
        for j, kind in adj.nbrs[i]:
            sid = adj.ids[j]
            if sid in seeds:
                one: tuple[float, str, tuple[str, ...]] = (-seeds[sid], sid, (sid, kind))
                best = one if best is None or one < best else best
        if best is not None:
            return best[2]
        for j, kind_mid in adj.nbrs[i]:
            mid = adj.ids[j]
            for m, kind_seed in adj.nbrs[j]:
                sid = adj.ids[m]
                if sid in seeds and sid != nid:
                    two: tuple[float, str, tuple[str, ...]] = (
                        -seeds[sid],
                        sid + "\0" + mid,
                        (sid, kind_seed, mid, kind_mid),
                    )
                    best = two if best is None or two < best else best
        return best[2] if best is not None else ()

    def _found_by(self, found: Mapping[str, int], via: tuple[str, ...]) -> str:
        parts = [f"{leg} (#{found[leg]})" for leg in (LEG_NAME, LEG_CONTENT, LEG_MEANING) if leg in found]
        if LEG_GRAPH in found:
            if via:
                steps = [self._short(via[0]), via[1]]
                if len(via) == 4:
                    steps += [self._short(via[2]), via[3]]
                parts.append("graph via " + " → ".join(steps))
            else:
                parts.append(f"graph (#{found[LEG_GRAPH]})")
        return ", ".join(parts)

    def _short(self, nid: str) -> str:
        node = self._store.node(nid)
        return node.name if node is not None else nid


__all__ = [
    "LEG_CONTENT",
    "LEG_GRAPH",
    "LEG_MEANING",
    "LEG_NAME",
    "STAGES",
    "TRAVERSAL_EDGES",
    "hybrid_enabled",
    "Embedder",
    "HybridConfig",
    "HybridHit",
    "HybridRetriever",
    "personalized_pagerank",
    "reciprocal_rank_fusion",
]
