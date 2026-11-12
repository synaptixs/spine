"""Two signals a ticket gives search before search runs: its quoted text, and one call from what it names.

Deterministic and free (no model). RCA reads both off its one search and reports them beside the
ranking (SSPN-390); it does not feed them back as ``extra_seeds``. Measured on the 8-ticket dev set, a
second pass seeded with them left 15 of 22 fix files in the top 10 under every setting tried, and the
quoted-text signal matched on 0 of 8 tickets (it fires on SSPN-376, the ticket it was built from). One
hop reached 3 fix files the tickets did not name, among 22 files returned.

Why each exists, measured on SSPN-376 (``~/plans/sdlc-search-use-roadmap.md`` §1):

* **Quoted text.** A bug report quotes what the tool printed: "Nothing in the product depends on what
  changes." That sentence was a string literal 44 lines into ``builddoc._blast_prose`` — past the 20
  body lines the hybrid indexes (``symbol_text.BODY_LINES``), so no search leg could see it. An exact
  lookup can. A quote found in more than :data:`MAX_QUOTE_FILES` files says nothing about where the
  fault is and is dropped; test code is never a hit.
* **One hop.** The cause, ``store.importers_of``, was one CALLS edge from ``impact.blast_radius`` in a
  file the ticket named. Seeding search with every node of every named file (440 on that ticket)
  drowned the edge. Following CALLS both ways from the first *k* search hits in named files keeps it.
  A neighbour in a file the ticket already named is not new information and is not returned.

Neither signal is a verdict. Each carries its evidence (the quote and ``file:line``, or the edge), for
use only as a hypothesis.

**Completion** (sdlc-search-use P3, decisions D23–D26) answers a different question: given the files a
design already proposes, which file next to them does the fix also need? It ranks the proposed files'
one-hop graph neighbours by where the hybrid search first hit them and keeps the first
:data:`COMPLETION_K`. On the pre-registered probe (SWE-bench dev, the 67 multi-file issues) that put a
missed fix file in the top 3 on 20 of 41 eligible issues, against 4 of 41 for path order (sign test
p = 0.00015). Only 12 % of the files it adds are fix files, against 33 % of the design's own, so a
candidate is a file to *read*, never one to edit. On Spine's own 8 dev tickets it reached 0 of the 3
eligible. The function runs no search: the caller hands it the search's file order, so it cannot
drift from the search the design already ran.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from orchestrator.pkg import FactStore
from orchestrator.pkg.facts import EdgeKind, Node, NodeKind
from orchestrator.pkg.test_rule import is_test_path

MIN_QUOTE_WORDS = 4
MAX_QUOTE_FILES = 3
#: How many of search's first hits in named files the one-hop step starts from. Chosen on the dev set
#: (D6) and frozen before the held-out grading.
DEFAULT_HOP_ORIGINS = 3

#: How many completion candidates a design is shown (D20: the probe scored the top 3).
COMPLETION_K = 3
#: The links between two files that make one a neighbour of the other, in the order that picks which
#: link a candidate shows (D25, D26). ``CONTAINS`` is nesting, not a link. These are the probe's kinds.
LINK_KINDS = (
    EdgeKind.CALLS,
    EdgeKind.IMPORTS,
    EdgeKind.IMPLEMENTS,
    EdgeKind.READS,
    EdgeKind.WRITES,
    EdgeKind.REFERENCES,
)

_QUOTED = re.compile(r"[\"“]([^\"”\n]{4,300})[\"”]")
_ELLIPSIS = re.compile(r"…|\.\.\.")
_CALLABLE = (NodeKind.FUNCTION, NodeKind.TYPE)


@dataclass(frozen=True)
class QuoteHit:
    """A ticket's quoted text found verbatim in source: where, and the innermost symbol holding it."""

    quote: str
    file: str
    line: int
    node_id: str | None


@dataclass(frozen=True)
class HopHit:
    """A symbol one CALLS edge from a top search hit in a named file. ``direction`` is from the origin."""

    origin: str
    node_id: str
    file: str
    direction: str  # "calls" | "called by"


def ticket_quotes(text: str) -> list[str]:
    """Double-quoted runs (straight or curly) of at least :data:`MIN_QUOTE_WORDS` words, split at an
    ellipsis — a reporter's "…" marks text left out, so each side is matched on its own."""
    out: list[str] = []
    for raw in _QUOTED.findall(text):
        for part in _ELLIPSIS.split(raw):
            frag = part.strip().strip(".,;:").strip()
            if len(frag.split()) >= MIN_QUOTE_WORDS and frag not in out:
                out.append(frag)
    return out


def _source_files(store: FactStore) -> list[str]:
    files = {
        n.provenance.file
        for n in store.nodes
        if n.grounded and n.provenance is not None and not n.test and not is_test_path(n.provenance.file)
    }
    return sorted(files)


def _read(root: Path, rel: str) -> str | None:
    base = root.resolve()
    path = (base / rel).resolve()
    if not path.is_relative_to(base) or not path.is_file():
        return None
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _innermost(nodes: Iterable[Node], line: int) -> str | None:
    best: tuple[int, str] | None = None
    for n in nodes:
        p = n.provenance
        if p is None or p.line > line:
            continue
        end = p.end_line if p.end_line is not None else p.line
        if line > end:
            continue
        span = (end - p.line, n.id)
        if best is None or span < best:
            best = span
    return best[1] if best else None


def quoted_text_hits(text: str, store: FactStore, root: Path | str) -> list[QuoteHit]:
    """Every place the ticket's quoted text appears verbatim in non-test source, quote by quote.

    A quote matching more than :data:`MAX_QUOTE_FILES` files is dropped whole. Files are read only
    inside ``root``.
    """
    quotes = ticket_quotes(text)
    if not quotes:
        return []
    base = Path(root)
    by_file: dict[str, list[Node]] = {}
    for n in store.nodes:
        if n.grounded and n.provenance is not None:
            by_file.setdefault(n.provenance.file, []).append(n)
    sources = {rel: body for rel in _source_files(store) if (body := _read(base, rel)) is not None}
    hits: list[QuoteHit] = []
    for q in quotes:
        found = [(rel, body.find(q)) for rel, body in sources.items() if q in body]
        if not found or len(found) > MAX_QUOTE_FILES:
            continue
        for rel, at in found:
            line = sources[rel].count("\n", 0, at) + 1
            hits.append(
                QuoteHit(quote=q, file=rel, line=line, node_id=_innermost(by_file.get(rel, []), line))
            )
    return hits


def one_hop(
    store: FactStore, ranked_ids: Sequence[str], named_files: set[str], *, k: int = DEFAULT_HOP_ORIGINS
) -> list[HopHit]:
    """CALLS neighbours (both ways, test code excluded) of the first ``k`` ranked symbols in named files.

    Origins are functions, methods and types; a module node calls nothing. Neighbours in a named file
    are dropped — the ticket already pointed there.
    """
    if k <= 0:
        return []
    by_id = {n.id: n for n in store.nodes}
    origins: list[Node] = []
    for nid in ranked_ids:
        n = by_id.get(nid)
        if n and n.kind in _CALLABLE and n.provenance and n.provenance.file in named_files:
            origins.append(n)
            if len(origins) == k:
                break
    out: list[HopHit] = []
    seen: set[str] = set()
    for o in origins:
        callees = [("calls", c) for c in sorted(store.callees_of(o.id), key=lambda n: n.id)]
        callers = [("called by", s.caller) for s in sorted(store.callers_of(o.id), key=lambda s: s.caller.id)]
        for direction, nb in callees + callers:
            p = nb.provenance
            if not nb.grounded or p is None or nb.test or is_test_path(p.file) or p.file in named_files:
                continue
            if nb.id in seen:
                continue
            seen.add(nb.id)
            out.append(HopHit(origin=o.id, node_id=nb.id, file=p.file, direction=direction))
    return out


@dataclass(frozen=True)
class CompletionHit:
    """A file next to a proposed one that search reached. ``rank`` is its 1-based place in the search's
    file order; the link (``edge_kind``, ``src`` → ``dst``) is the reason to read it, and ``at`` is the
    proposed side of that link."""

    file: str
    rank: int
    edge_kind: str
    src: str
    dst: str
    at: str


@dataclass(frozen=True)
class Completion:
    """At most ``k`` candidates, and ``of_n`` — how many neighbours there were before the cut."""

    hits: tuple[CompletionHit, ...]
    of_n: int


def completion_candidates(
    store: FactStore,
    start_files: Iterable[str],
    ranked_files: Sequence[str],
    *,
    k: int = COMPLETION_K,
    exclude: Iterable[str] = (),
) -> Completion:
    """The proposed files' one-hop neighbours, in the order search first reached them, first ``k`` kept.

    A neighbour is a file joined to a proposed file by a :data:`LINK_KINDS` edge in either direction,
    between grounded nodes, neither of them test code. Proposed files, ``exclude`` and files absent
    from ``ranked_files`` are never returned (D24): a file search never reached carries no evidence
    from the ticket. Ties cannot happen — a file's place is its first index.
    """
    start = set(start_files)
    skip = start | set(exclude)
    where = {n.id: n.provenance for n in store.nodes if n.grounded and n.provenance is not None}
    # neighbour file -> (kind order, src, dst, proposed-side location) of every link to the proposed set
    links: dict[str, list[tuple[int, str, str, str]]] = {}
    for order, kind in enumerate(LINK_KINDS):
        for e in store.edges_of_kind(kind):
            ps, pd = where.get(e.src), where.get(e.dst)
            # A link inside one proposed file lands on a proposed file, so the skip below drops it.
            if ps is None or pd is None or is_test_path(ps.file) or is_test_path(pd.file):
                continue
            for near, far_file in ((ps, pd.file), (pd, ps.file)):
                if near.file in start and far_file not in skip:
                    links.setdefault(far_file, []).append((order, e.src, e.dst, f"{near.file}:{near.line}"))
    first: dict[str, int] = {}
    for i, f in enumerate(ranked_files, start=1):
        first.setdefault(f, i)
    reached = sorted((f for f in links if f in first), key=lambda f: (first[f], f))
    hits = []
    for f in reached[: max(k, 0)]:
        order, src, dst, at = min(links[f])
        hits.append(
            CompletionHit(file=f, rank=first[f], edge_kind=LINK_KINDS[order].value, src=src, dst=dst, at=at)
        )
    return Completion(hits=tuple(hits), of_n=len(links))
