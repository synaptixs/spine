"""Resolve a run's tracker metadata — issue type and labels — from the intake plan.

**The defect this closes.** The pipeline is issue-type shaped: `sdlc.profile_select` picks a
workflow profile from the type, `validity` decides whether a ticket must localize by it, and
the `bug`/`enhancement` profiles exist to be selected by it. None of that ever fired. The
only thing intake hands a run is a ``FeatureSpec``, which forbids extra keys and has no field
for a type — so ``spec.get("issue_type")`` was always ``""``, every run took the ``default``
profile, and the localization check never once ran outside its tests.

**Why not a field on ``FeatureSpec``.** A spec says *what to build*; the issue type is a fact
about the ticket, and the spec is written by a model. Putting the type there would let the
model decide which research a ticket gets, which is precisely what `profile_select`'s
docstring exists to prevent — the Evidence would stop being reproducible at a commit.

**So it is resolved, not carried.** ``BacklogPlan`` already holds the documents, the intents
and the specs together, and ``Intent.source_doc_ids`` already links the middle to the left.
The walk ``spec.intent_id → Intent → source_doc_ids → SourceDocument`` is a lookup over data
already fetched: no model, no network, no new request.

**One document or nothing.** Where the walk lands on several documents that disagree, this
returns empty and says why rather than picking. An issue type chosen by tie-break would
select a profile, and a run whose research was decided by list order is not reproducible in
any sense worth the word.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - typing only
    from orchestrator.intake.service import BacklogPlan
    from orchestrator.intake.source import SourceDocument

__all__ = ["TicketMeta", "resolve_ticket_meta"]


@dataclass(frozen=True)
class TicketMeta:
    """What the tracker says about a ticket, and where the answer came from."""

    issue_type: str = ""
    labels: tuple[str, ...] = ()
    #: Provenance, for the run to print: a document id, ``"--issue-type"`` when an operator
    #: overrode it, or a sentence explaining why nothing was resolved. Never empty when
    #: something *could* have been resolved and was not — "why did it choose `default`?" is
    #: the question this whole module exists to keep answerable.
    origin: str = ""
    #: Notes worth emitting: the ambiguity that stopped a resolution, and nothing else.
    detail: str = ""

    @property
    def known(self) -> bool:
        return bool(self.issue_type)


def _spec_field(spec: Any, name: str) -> str:
    if isinstance(spec, Mapping):
        return str(spec.get(name) or "")
    return str(getattr(spec, name, "") or "")


def _documents_for(plan: BacklogPlan, intent_id: str) -> list[SourceDocument]:
    """The documents an intent came from, in plan order.

    Falls back to every document the plan holds when the intent is unknown or names no
    source: the extractor may omit ``source_doc_ids``, and a single-document plan is still
    unambiguous. With more than one document and no link, the caller gets the ambiguity
    rather than a guess.
    """
    by_id = {d.id: d for d in plan.documents}
    intent = next((i for i in plan.intents if i.id == intent_id), None)
    if intent is not None:
        named = [by_id[ref] for ref in intent.source_doc_ids if ref in by_id]
        if named:
            return named
    return list(plan.documents)


def resolve_ticket_meta(plan: BacklogPlan, spec: Any) -> TicketMeta:
    """The tracker metadata behind one spec. Deterministic; no model, no network.

    ``spec`` is a ``FeatureSpec`` or the dict it dumps to — the pipeline carries both shapes.
    """
    intent_id = _spec_field(spec, "intent_id")
    docs = _documents_for(plan, intent_id)
    if not docs:
        return TicketMeta(origin="", detail="the plan carries no source document")
    if len(docs) > 1:
        types = sorted({d.issue_type for d in docs if d.issue_type})
        # Agreement is not ambiguity: three subtasks all filed as Bug answer the question.
        if len(types) != 1:
            return TicketMeta(
                origin="",
                detail=(
                    f"{len(docs)} source documents for intent `{intent_id}`"
                    + (f" disagree on issue type ({', '.join(types)})" if types else " carry no issue type")
                ),
            )
        labels = tuple(sorted({label for d in docs for label in d.labels}))
        return TicketMeta(issue_type=types[0], labels=labels, origin=", ".join(d.id for d in docs))
    doc = docs[0]
    return TicketMeta(
        issue_type=doc.issue_type,
        # Sorted, because a label set is unordered and `select_profile` matches over it. "First
        # match wins" over an arrival-ordered set is not reproducible.
        labels=tuple(sorted(doc.labels)),
        origin=doc.id,
        detail="" if doc.issue_type else f"`{doc.id}` carries no issue type",
    )
