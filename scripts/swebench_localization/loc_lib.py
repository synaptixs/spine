"""Pure helpers for the SWE-bench localization harness (standard library only).

Importable from both environments the harness uses: the bench environment (driver, analysis) and
Spine's own (worker). Nothing here reads a model, the network or the dataset.
"""

from __future__ import annotations

import math
import re
from collections import Counter

_HUMP1 = re.compile(r"([a-z0-9])([A-Z])")
_HUMP2 = re.compile(r"([A-Z]+)([A-Z][a-z])")
_RUN = re.compile(r"[A-Za-z0-9]+")


def tokenize(text: str) -> list[str]:
    """Lowercase runs of letters/digits; split at underscores (non-alnum) and camelCase humps.

    No stemming, no stop list.
    """
    text = _HUMP2.sub(r"\1 \2", _HUMP1.sub(r"\1 \2", text))
    return [t.lower() for t in _RUN.findall(text)]


def bm25_rank(
    docs: dict[str, str], query: str, *, limit: int = 50, k1: float = 1.5, b: float = 0.75
) -> list[str]:
    """Rank files by BM25. Each distinct query term counts once; idf = ln(1 + (N - n + 0.5) / (n + 0.5)).

    Only files scoring above zero are returned; ties break on path, ascending.
    """
    qterms = set(tokenize(query))
    if not qterms or not docs:
        return []
    tfs: dict[str, Counter[str]] = {}
    lens: dict[str, int] = {}
    for path, text in docs.items():
        toks = tokenize(path + "\n" + text)
        lens[path] = len(toks)
        tfs[path] = Counter(t for t in toks if t in qterms)
    n_docs = len(docs)
    avg = (sum(lens.values()) / n_docs) or 1.0
    df: Counter[str] = Counter()
    for c in tfs.values():
        df.update(c.keys())
    scores: dict[str, float] = {}
    for path, c in tfs.items():
        s = 0.0
        for term, f in c.items():
            idf = math.log(1 + (n_docs - df[term] + 0.5) / (df[term] + 0.5))
            s += idf * f * (k1 + 1) / (f + k1 * (1 - b + b * lens[path] / avg))
        if s > 0:
            scores[path] = s
    return [p for p, _ in sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))[:limit]]


def chance_p(n_files: int, n_gold: int, k: int) -> float:
    """P(at least one gold file among k distinct files drawn at random from n_files)."""
    k = min(k, n_files)
    if n_files <= 0 or n_gold <= 0:
        return 0.0
    if n_files - n_gold < k:
        return 1.0
    return 1.0 - math.comb(n_files - n_gold, k) / math.comb(n_files, k)


def _distinct(files: list[str]) -> list[str]:
    seen: list[str] = []
    for f in files:
        if f not in seen:
            seen.append(f)
    return seen


def rank_of(files: list[str], gold: set[str] | list[str]) -> int | None:
    """1-based position of the first gold file among distinct files; None = not found."""
    wanted = set(gold)
    return next((i for i, f in enumerate(_distinct(files), start=1) if f in wanted), None)


def all_gold_within(files: list[str], gold: set[str] | list[str], k: int) -> bool:
    return set(gold) <= set(_distinct(files)[:k])


def gold_paths(patch: str) -> list[str]:
    """Pre-fix paths named by the patch: the ``a/`` side of every ``diff --git``."""
    return re.findall(r"^diff --git a/(\S+) b/", patch, re.M)


def quotes_gold(text: str, gold: list[str]) -> bool:
    """True when the issue text contains a gold file's path or basename literally (case-sensitive)."""
    return any(g in text or g.rsplit("/", 1)[-1] in text for g in gold)


def accounting(rows: list[dict], total: int) -> tuple[int, int]:
    """(scored, skipped); raises unless every instance is accounted for exactly once."""
    ids = [r["instance_id"] for r in rows if "instance_id" in r]
    scored = sum(1 for r in rows if r.get("status") == "scored")
    skipped = sum(1 for r in rows if r.get("status") == "skipped")
    if len(ids) != len(set(ids)) or scored + skipped != total or len(ids) != total:
        raise AssertionError(
            f"accounting broken: scored={scored} skipped={skipped} rows={len(ids)} "
            f"unique={len(set(ids))} expected={total}"
        )
    return scored, skipped
