"""Each candidate option of a change, measured against the graph (SSPN-297, P3 — D7).

``intake.options`` holds what an option *says*: its approach, the existing code it names
(``anchors``) and what it would add (``creates``). This module says what the **graph** holds about
those anchors, with no model and no judgement:

* **resolved / unresolved** — which anchors name code the graph has. A symbol is looked up the
  way ``blast_radius`` looks one up (``FactStore.resolve``: an id, a qualified name, a short name);
  a path names the functions and types defined in that file.
* **callers** — distinct code that calls an anchor, as ``blast_radius`` counts it: **a floor**,
  since a call whose receiver type the source does not declare is not in the graph. Calls from one
  anchor to another are inside the option, not callers of it.
* **untested** — the production code a change to an anchor reaches that no test reaches, from the
  same plan ``regression_gaps`` builds (``sdlc.coverage.build_regression_plan``).
* **docs** — the ``Doc`` pages that describe an anchor (``FactStore.docs_for``).
* **areas** — the owning modules touched, found by walking ``CONTAINS`` upward and falling back to
  the source file — never a symbol id (invariant 6).
* **reuse** — resolved anchors against everything the option touches (resolved anchors plus what
  it creates); an unresolved anchor counts neither way, since nobody knows yet what it is.

**Bound honestly** (invariant 7): an anchor naming many symbols is measured on the first
``MAX_MATCHES`` (a path on the first ``MAX_NODES_PER_ANCHOR``), and the result says it was clipped.

**Never a clean bill by default.** An option with no anchors, a change with no repository and an
empty graph are ``ungrounded`` — their measures are ``None``, not zero, so no ranking can read an
option nobody grounded as the low-risk one. A dirty tree is measured but ``untrusted``.

It lives in ``sdlc`` because the coverage plan does, and ``intake`` stays free of ``sdlc``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

from orchestrator.intake.options import Option
from orchestrator.pkg import FactStore
from orchestrator.pkg.facts import Node, NodeKind
from orchestrator.sdlc.coverage import build_regression_plan

__all__ = [
    "BIAS",
    "MAX_MATCHES",
    "MAX_NODES_PER_ANCHOR",
    "RULE",
    "AnchorMeasure",
    "OptionMeasure",
    "Recommendation",
    "measure_option",
    "measure_options",
    "recommend",
]

#: Symbols measured for one anchor that names several — ``blast_radius`` details the same number.
MAX_MATCHES = 7
#: Symbols measured for one path anchor: a file can hold hundreds.
MAX_NODES_PER_ANCHOR = 25

Grounding = Literal["grounded", "untrusted", "ungrounded"]
_MEASURED_KINDS = (NodeKind.FUNCTION, NodeKind.TYPE)
_NOT_CODE = (NodeKind.DOC, NodeKind.INTENT)


@dataclass(frozen=True)
class AnchorMeasure:
    """One anchor: whether it is a path or a symbol, how many graph nodes it names, and the ones
    measured (sorted ids, at most the cap)."""

    anchor: str
    kind: Literal["symbol", "path"]
    matches: int
    measured: tuple[str, ...]


@dataclass(frozen=True)
class OptionMeasure:
    title: str
    grounding: Grounding
    reason: str = ""
    anchors: tuple[AnchorMeasure, ...] = ()
    resolved: tuple[str, ...] = ()
    unresolved: tuple[str, ...] = ()
    callers: int | None = None
    untested: tuple[str, ...] | None = None
    docs: int | None = None
    areas: tuple[str, ...] = ()
    reuse: float | None = None
    truncated: bool = False

    def to_dict(self) -> dict[str, Any]:
        """JSON-ready, keys in a fixed order: the same option and graph give the same bytes."""
        return {
            "title": self.title,
            "grounding": self.grounding,
            "reason": self.reason,
            "anchors": [
                {"anchor": a.anchor, "kind": a.kind, "matches": a.matches, "measured": list(a.measured)}
                for a in self.anchors
            ],
            "resolved": list(self.resolved),
            "unresolved": list(self.unresolved),
            "callers": None if self.callers is None else {"at_least": self.callers},
            "untested": None if self.untested is None else list(self.untested),
            "docs": self.docs,
            "areas": list(self.areas),
            "reuse": None if self.reuse is None else round(self.reuse, 4),
            "truncated": self.truncated,
        }


def _is_path(anchor: str) -> bool:
    return "/" in anchor or anchor.endswith(
        (".py", ".ts", ".js", ".java", ".cs", ".go", ".rs", ".kt", ".php", ".pl", ".c", ".h", ".cpp", ".hpp")
    )


def _file_of(node: Node) -> str:
    return node.provenance.file if node.provenance else ""


def _anchor_nodes(store: FactStore, anchor: str) -> tuple[AnchorMeasure, list[str]]:
    if _is_path(anchor):
        path = anchor.removeprefix("./")
        hits = sorted(n.id for n in store.nodes if n.kind in _MEASURED_KINDS and _file_of(n) == path)
        if not hits:
            hits = sorted(n.id for n in store.nodes if n.kind is NodeKind.MODULE and _file_of(n) == path)
        measured = hits[:MAX_NODES_PER_ANCHOR]
        return AnchorMeasure(anchor, "path", len(hits), tuple(measured)), measured
    # An anchor names code: a `Doc` page or an `Intent` that shares the name is not what changes.
    found = [n for n in store.resolve(anchor) if n.kind not in _NOT_CODE]
    measured = sorted(n.id for n in found[:MAX_MATCHES])
    return AnchorMeasure(anchor, "symbol", len(found), tuple(measured)), measured


def _area(store: FactStore, node_id: str) -> str:
    """The file of the module ``node_id`` belongs to (``FactStore.owning_module``), else the
    node's own file. A symbol id is never an area (invariant 6)."""
    module = store.owning_module(node_id)
    if module is not None:
        return _file_of(module) or module.name
    node = store.node(node_id)
    return (_file_of(node) if node else "") or node_id.split(":", 1)[-1]


def measure_option(
    option: Option, store: FactStore | None, *, trusted: bool = True, docs_read: bool = True
) -> OptionMeasure:
    """The graph's facts about one option. ``store`` is ``None`` when there is no repository;
    ``trusted`` is false for a dirty working tree (the graph may not be the committed code);
    ``docs_read`` is false when the docs were never read, so their count is unknown, not zero."""
    if store is None:
        return OptionMeasure(option.title, "ungrounded", "no repository — nothing to measure against")
    # A graph of Doc pages alone holds no code: measured on it, every option would come out clean.
    if not any(n.kind not in _NOT_CODE for n in store.nodes):
        return OptionMeasure(
            option.title, "ungrounded", "the graph is empty of code — nothing to measure against"
        )
    if not option.anchors:
        return OptionMeasure(
            option.title,
            "ungrounded",
            "names no anchors — nothing in the code to measure",
            reuse=_reuse(0, option),
        )
    anchors: list[AnchorMeasure] = []
    ids: list[str] = []
    truncated = False
    for anchor in option.anchors:
        fact, measured = _anchor_nodes(store, anchor)
        anchors.append(fact)
        ids.extend(i for i in measured if i not in ids)
        truncated |= fact.matches > len(fact.measured)
    if not ids:
        # Anchors the code does not hold measure nothing: 0 callers would read as the safest option
        # there is, so an option none of whose anchors resolve is not ranked (SSPN-312).
        return OptionMeasure(
            option.title,
            "ungrounded",
            "none of its anchors is in the code — nothing to measure",
            anchors=tuple(anchors),
            unresolved=tuple(a.anchor for a in anchors),
            reuse=_reuse(0, option),
            truncated=truncated,
        )
    inside = set(ids)
    callers = {c.caller.id for i in ids for c in store.callers_of(i)} - inside
    # Keyed by symbol (name and place), not name: `web.handler` and `cli.handler` are two (SSPN-312).
    untested: dict[tuple[str, str], str] = {}
    for i in ids:
        plan = build_regression_plan(store, i)
        truncated |= plan.truncated
        untested.update(((t.name, t.where), t.name) for t in plan.impacted if not t.covered and t.name)
    docs = {d.id for i in ids for d in store.docs_for(i)}
    resolved = tuple(a.anchor for a in anchors if a.matches)
    return OptionMeasure(
        title=option.title,
        grounding="grounded" if trusted else "untrusted",
        reason=""
        if trusted
        else "the working tree has uncommitted changes; the graph may not be the committed code",
        anchors=tuple(anchors),
        resolved=resolved,
        unresolved=tuple(a.anchor for a in anchors if not a.matches),
        callers=len(callers),
        untested=tuple(untested[k] for k in sorted(untested)),
        docs=len(docs) if docs_read else None,
        areas=tuple(sorted({_area(store, i) for i in ids})),
        reuse=_reuse(len(resolved), option),
        truncated=truncated,
    )


def _reuse(resolved: int, option: Option) -> float | None:
    total = resolved + len(option.creates)
    return None if total == 0 else resolved / total


def measure_options(
    options: Sequence[Option], store: FactStore | None, *, trusted: bool = True, docs_read: bool = True
) -> tuple[OptionMeasure, ...]:
    """Every option, in the order written."""
    return tuple(measure_option(o, store, trusted=trusted, docs_read=docs_read) for o in options)


# --- the recommendation (P4, D8) -------------------------------------------------------------------

#: The rule, printed beside every recommendation so a reader can check it by hand.
RULE = (
    "fewest unresolved anchors, then fewest untested symbols reached, then fewest callers, "
    "then most reuse; a tie keeps the order the options are written in"
)
#: What the rule favours, said out loud (D8): it is a risk ordering, not a product judgement.
BIAS = "It favours the safest, smallest change — not the best product. The choice is yours."


@dataclass(frozen=True)
class Recommendation:
    """The option the rule ranks first, or ``None`` when nothing is grounded enough to rank."""

    title: str | None
    reason: str
    ranking: tuple[str, ...] = ()
    not_ranked: tuple[str, ...] = ()
    rule: str = RULE
    bias: str = BIAS
    #: Its place in the block, 1-based: titles can repeat, so the title alone cannot name it.
    number: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "recommended": self.title,
            "number": self.number,
            "reason": self.reason,
            "ranking": list(self.ranking),
            "not_ranked": list(self.not_ranked),
            "rule": self.rule,
            "bias": self.bias,
        }


def recommend(measures: Sequence[OptionMeasure]) -> Recommendation:
    """Rank the grounded options by ``RULE``. An ungrounded option is listed as not ranked — its
    measures are unknown, and unknown is never read as safe."""
    ranked = sorted(
        (m for m in enumerate(measures) if m[1].grounding != "ungrounded"),
        key=lambda im: (
            len(im[1].unresolved),
            len(im[1].untested or ()),
            im[1].callers or 0,
            -(im[1].reuse or 0.0),
            im[0],
        ),
    )
    not_ranked = tuple(m.title for m in measures if m.grounding == "ungrounded")
    if not ranked:
        return Recommendation(
            None, "no option is grounded in the code, so there is nothing to rank", (), not_ranked
        )
    best = ranked[0][1]
    reason = (
        f"{len(best.unresolved)} unresolved anchor(s), "
        f"{len(best.untested or ())} untested symbol(s) reached, "
        f"at least {best.callers or 0} caller(s), reuse {round((best.reuse or 0.0) * 100)}%"
    )
    if best.grounding == "untrusted":
        reason += " — measured on an untrusted (dirty) tree"
    return Recommendation(
        best.title, reason, tuple(m.title for _, m in ranked), not_ranked, number=ranked[0][0] + 1
    )
