"""BM25 over symbol text — the content leg of hybrid KG search (D4).

The name ranker (``retrieval.GroundedRetriever``) scores a ticket's words against symbol *names*; a symbol
whose body does the thing the ticket describes, under a name that says something else, never surfaces. Okapi
BM25 over the text :mod:`orchestrator.pkg.symbol_text` reads for each symbol is that missing signal, and it
needs no model: it is the same scoring the plain-text baseline uses on whole files, applied to the graph's
own symbols.

Standard form, Lucene's non-negative idf: ``idf(t) = ln(1 + (N - df + 0.5) / (df + 0.5))`` and ``score(d) =
Σ idf(t) · tf·(k1 + 1) / (tf + k1·(1 - b + b·|d| / avgdl))`` over the query's distinct words, with k1 = 1.2
and b = 0.75 (pre-registration §3). Tokens come from ``retrieval.word_tokens`` — one tokenizer, so both legs
stem alike. Deterministic: query words are summed in sorted order, scores rounded to six places, ties broken
by id.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from orchestrator.pkg.retrieval import word_tokens

#: Scores are rounded to this many places before ranking, so a last-bit float difference never reorders a tie.
_SCORE_PLACES = 6


@dataclass(frozen=True)
class BM25Params:
    k1: float = 1.2
    b: float = 0.75


class BM25Index:
    """An inverted index over ``docs`` (id → tokens). Build once per graph, query many times."""

    def __init__(self, docs: Mapping[str, Sequence[str]], params: BM25Params | None = None) -> None:
        self._p = params or BM25Params()
        self._n = len(docs)
        self._len: dict[str, int] = {}
        self._postings: dict[str, list[tuple[str, int]]] = {}
        for doc_id in sorted(docs):
            counts = Counter(docs[doc_id])
            self._len[doc_id] = sum(counts.values())
            for term, tf in counts.items():
                self._postings.setdefault(term, []).append((doc_id, tf))
        self._avgdl = (sum(self._len.values()) / self._n) if self._n else 0.0

    def ranked(self, query: Iterable[str], *, limit: int | None = None) -> list[tuple[str, float]]:
        """``(doc id, score)`` for every doc sharing a word with ``query``, best first, ties by id."""
        if not self._n:
            return []
        k1, b = self._p.k1, self._p.b
        scores: dict[str, float] = {}
        for term in sorted(set(query)):
            postings = self._postings.get(term)
            if not postings:
                continue
            df = len(postings)
            idf = math.log(1 + (self._n - df + 0.5) / (df + 0.5))
            for doc_id, tf in postings:
                norm = self._len[doc_id] / self._avgdl if self._avgdl else 1.0
                scores[doc_id] = scores.get(doc_id, 0.0) + idf * tf * (k1 + 1) / (
                    tf + k1 * (1 - b + b * norm)
                )
        ranked = sorted(
            ((d, round(s, _SCORE_PLACES)) for d, s in scores.items()), key=lambda r: (-r[1], r[0])
        )
        return ranked[:limit] if limit is not None else ranked


def index_symbol_texts(texts: Mapping[str, str], params: BM25Params | None = None) -> BM25Index:
    """A BM25 index over ``symbol_text.symbol_texts`` output, tokenized as the name ranker tokenizes."""
    return BM25Index({node_id: word_tokens(text) for node_id, text in texts.items()}, params)


def rank_symbol_texts(
    texts: Mapping[str, str], query: str, *, limit: int | None = None, params: BM25Params | None = None
) -> list[tuple[str, float]]:
    """One-shot: index ``texts`` and rank them against ``query``'s words."""
    return index_symbol_texts(texts, params).ranked(word_tokens(query), limit=limit)


__all__ = ["BM25Index", "BM25Params", "index_symbol_texts", "rank_symbol_texts"]
