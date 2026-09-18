"""Grounded retrieval over the PKG — turn raw facts into task-shaped answers.

Where ``store`` exposes primitive graph queries, this layer composes them into
the thing an agent actually asks for: *"I'm about to change these lines — what's
the blast radius, and where do I look for breakage?"*

The headline query is ``diff_impact``: given changed line ranges per file, find
the enclosing symbols (using the function/class spans the extractor records) and
their callers — flagging **cross-file** callers, the ones a diff can silently
break. Deterministic and provenance-carrying; the LLM only consumes the rendered
brief.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass

from orchestrator.pkg.facts import Node, NodeKind
from orchestrator.pkg.store import CallSite, FactStore

_TOKEN_RE = re.compile(r"[a-z0-9]+")
# Words too generic to signal relevance in a spec ("add a function that...").
_STOPWORDS = frozenset(
    [
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "for",
        "from",
        "has",
        "have",
        "in",
        "into",
        "is",
        "it",
        "of",
        "on",
        "or",
        "that",
        "the",
        "this",
        "to",
        "use",
        "uses",
        "using",
        "with",
        "when",
        "where",
        "which",
        "should",
        "must",
        "can",
        "will",
        "new",
        "add",
        "support",
        "file",
        "files",
        "code",
        "data",
        "via",
        "per",
        "each",
        "all",
        "any",
        "one",
        "two",
    ]
)


#: The inflections a ticket's prose puts on a word the code names bare, stripped in this order,
#: one of them at most, then a trailing ``e`` — so ``deletion``/``deleted``/``deleting``/``delete``
#: all read ``delet``. Never below :data:`_MIN_STEM` characters: ``string`` keeps its ``ing`` and
#: ``type`` its ``e``, and ``Reader`` stays apart from ``read``. A Porter stemmer would merge
#: those, over-stems identifiers generally, and is a dependency; this is six lines, applied to
#: both the query and the names, and the NSS-1231 and CB-686 fixtures are its gate.
_STRIPPED = ("ion", "ing", "ed")
_MIN_STEM = 4


def _stem(token: str) -> str:
    """``deletion`` → ``delet``, ``licences`` → ``licenc``, ``policies`` → ``policy``, ``caching`` → ``cach``.

    CB-686 asked for "account deletion"; the screen is ``DeleteAccountScreen``. Only a trailing
    ``s`` was stripped, so ``deletion`` matched nothing and the design ranked the ``reason``
    fields of an unrelated type above the screen the ticket was about.
    """
    if len(token) > 3 and token.endswith("ies"):
        token = token[:-3] + "y"
    elif len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
        token = token[:-1]
    for suffix in _STRIPPED:
        if token.endswith(suffix) and len(token) - len(suffix) >= _MIN_STEM:
            token = token[: -len(suffix)]
            break
    if token.endswith("e") and len(token) - 1 >= _MIN_STEM:
        token = token[:-1]
    return token


def _tokens(text: str) -> set[str]:
    """Lowercase word tokens, splitting snake_case and camelCase, each reduced by :func:`_stem`
    so a spec's prose inflections ("edges", "deletion") match the bare names code uses.
    """
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", text).replace("_", " ").lower()
    out: set[str] = set()
    for t in _TOKEN_RE.findall(spaced):
        if t in _STOPWORDS or len(t) <= 2:
            continue
        out.add(_stem(t))
    return out


def _is_test_file(path: str) -> bool:
    parts = path.split("/")
    return any(p in ("tests", "test") for p in parts) or parts[-1].startswith("test_")


@dataclass(frozen=True)
class ScoredSymbol:
    """A retrieval hit with the evidence it rests on, so a consumer can tell strong from weak.

    ``weak`` is the floor: the words the hit shares with the query, **taken together**, also name
    symbols in other files — it rests on nothing specific. NSS-1231's five proposed files all
    rested on ``client`` (one word, in four files), and ``PsiHoldApiClient`` on ``api`` +
    ``client``, a pair that co-occurs in two; the score alone could not say so once it left this
    module, because nothing downstream carried it. Two things make a hit strong: a word or a
    combination found in one file only (``ebsorder``, ``exporter``), or the query naming the
    symbol's **whole** multi-word name — ``OrderService`` is a named thing even where ``order``
    and ``service`` each appear in twenty files.
    """

    node: Node
    score: float
    matched: tuple[str, ...]
    weak: bool


@dataclass(frozen=True)
class SymbolImpact:
    """A changed symbol and what depends on it."""

    symbol: Node
    callers: list[CallSite]

    def cross_file_callers(self) -> list[CallSite]:
        """Callers that live in a *different* file — the breakage risk."""
        own = self.symbol.provenance.file if self.symbol.provenance else None
        return [c for c in self.callers if c.caller.provenance and c.caller.provenance.file != own]


class GroundedRetriever:
    """Composes ``FactStore`` primitives into review-grade impact answers."""

    def __init__(self, store: FactStore) -> None:
        self._store = store

    def enclosing_symbol(self, file: str, line: int) -> Node | None:
        """The smallest grounded Function/Type in ``file`` whose span covers ``line``."""
        best: Node | None = None
        best_size = 1 << 62
        for node in self._store.nodes:
            prov = node.provenance
            if not node.grounded or prov is None or prov.file != file:
                continue
            end = prov.end_line if prov.end_line is not None else prov.line
            if prov.line <= line <= end:
                size = end - prov.line
                if size < best_size:
                    best, best_size = node, size
        return best

    def symbols_at(self, file: str, lines: set[int]) -> list[Node]:
        """Distinct enclosing symbols for a set of changed lines in one file."""
        found: dict[str, Node] = {}
        for line in lines:
            node = self.enclosing_symbol(file, line)
            if node is not None:
                found[node.id] = node
        return list(found.values())

    def impact_of(self, node_id: str) -> SymbolImpact | None:
        node = self._store.node(node_id)
        if node is None:
            return None
        return SymbolImpact(symbol=node, callers=self._store.callers_of(node_id))

    def diff_impact(self, changed: Mapping[str, set[int]]) -> list[SymbolImpact]:
        """Blast radius for changed lines across files. Sorted by caller count."""
        impacts: dict[str, SymbolImpact] = {}
        for file, lines in changed.items():
            for symbol in self.symbols_at(file, lines):
                impact = self.impact_of(symbol.id)
                if impact is not None:
                    impacts[symbol.id] = impact
        return sorted(impacts.values(), key=lambda i: len(i.callers), reverse=True)

    def relevant_symbols(self, text: str, *, limit: int = 8, include_tests: bool = False) -> list[Node]:
        """Grounded symbols whose names overlap the task text. Deterministic.

        v0 relevance is lexical (name/path token overlap, snake/camel split,
        naive singularisation) — no embeddings. Exact name hits score highest;
        Type/Function outrank Module so the result reads like an API surface.
        Test-file symbols are excluded by default: a spec wants the APIs to
        reuse, not the tests that exercise them.

        The nodes only; :meth:`scored_symbols` carries the evidence too.
        """
        return [s.node for s in self.scored_symbols(text, limit=limit, include_tests=include_tests)]

    def scored_symbols(self, text: str, *, limit: int = 8, include_tests: bool = False) -> list[ScoredSymbol]:
        """:meth:`relevant_symbols` with each hit's score, matched tokens and the ``weak`` floor.

        "Names other symbols" is counted over distinct **files**, over every grounded name in
        the graph: a module and the function inside it sharing the file's name is one thing
        named once, not a generic word — `exporter` in `src/exporter.py` is the thing a ticket
        meant, `client` across four files is not. Measured against the graph in hand, not a
        constant, so the rule holds on a ten-node fixture and a ten-thousand-node repository.
        """
        query = _tokens(text)
        if not query:
            return []
        hits: list[tuple[float, str, Node, frozenset[str], frozenset[str]]] = []
        file_tokens: dict[str, set[str]] = {}
        for node in self._store.nodes:
            if not node.grounded:
                continue
            if not include_tests and node.provenance is not None and _is_test_file(node.provenance.file):
                continue
            name_tokens = _tokens(node.name)
            if not name_tokens:
                continue
            file = node.provenance.file if node.provenance is not None else node.id
            file_tokens.setdefault(file, set()).update(name_tokens)
            overlap = frozenset(query & name_tokens)
            if not overlap:
                continue
            score = 3.0 * (len(overlap) / len(name_tokens)) + 1.0 * len(overlap)
            if node.kind in (NodeKind.TYPE, NodeKind.FUNCTION):
                score += 0.5
            hits.append((score, node.id, node, overlap, frozenset(name_tokens)))
        hits.sort(key=lambda s: (-s[0], s[1]))
        out: list[ScoredSymbol] = []
        for score, _, node, shared, own in hits[:limit]:
            matched = tuple(sorted(shared))
            whole_name = shared == own and len(own) >= 2
            co_occurring = sum(1 for toks in file_tokens.values() if shared <= toks)
            weak = not whole_name and co_occurring >= 2
            out.append(ScoredSymbol(node=node, score=score, matched=matched, weak=weak))
        return out

    def api_surface(self, text: str, *, limit: int = 8) -> list[Node]:
        """``relevant_symbols`` with Module hits expanded into their classes.

        A module that matches the task ("orchestrator.pkg.facts") is usually a
        *container* of the APIs the task needs — so each Module hit is replaced
        by its grounded Type/Function children, keeping overall order and
        de-duplicating against direct hits.
        """
        out: dict[str, Node] = {}
        for node in self.relevant_symbols(text, limit=limit):
            if node.kind is NodeKind.MODULE:
                for child in self._store.children_of(node.id):
                    if child.grounded and child.kind in (NodeKind.TYPE, NodeKind.FUNCTION):
                        out.setdefault(child.id, child)
            else:
                out.setdefault(node.id, node)
        return list(out.values())[:limit]

    def render(self, impacts: list[SymbolImpact], *, cross_file_only: bool = True) -> str:
        """Compact, prompt-injectable brief. Empty string when nothing is at risk."""
        lines: list[str] = []
        for impact in impacts:
            callers = impact.cross_file_callers() if cross_file_only else impact.callers
            if not callers:
                continue
            at = impact.symbol.provenance
            lines.append(
                f"- `{impact.symbol.name}` ({impact.symbol.id}{f' @ {at}' if at else ''}) is called by:"
            )
            for cs in callers[:10]:
                lines.append(f"    - {cs.caller.id}  @ {cs.at}")
            if len(callers) > 10:
                lines.append(f"    - …and {len(callers) - 10} more")
        if not lines:
            return ""
        return (
            "Impact analysis from the Product Knowledge Graph — the changed "
            "symbols below have callers elsewhere in the codebase. Review these "
            "call sites for breakage (signature/behavior changes):\n" + "\n".join(lines)
        )


__all__ = ["GroundedRetriever", "ScoredSymbol", "SymbolImpact"]
