"""Resolving a run's issue type and labels from the intake plan.

The defect behind this module: nothing carried the issue type from the ticket to the run, so
`select_profile` always saw `""`, every run took the `default` profile, and the localization
check never fired outside its own tests. These assertions are what would have caught it.
"""

from __future__ import annotations

from orchestrator.intake.gaps import GapFinding, GapSeverity
from orchestrator.intake.intents import Intent
from orchestrator.intake.service import BacklogPlan
from orchestrator.intake.source import SourceDocument
from orchestrator.intake.specs import FeatureSpec
from orchestrator.intake.ticket_meta import resolve_ticket_meta


def _doc(doc_id: str, *, issue_type: str = "", labels: tuple[str, ...] = ()) -> SourceDocument:
    return SourceDocument(id=doc_id, title=doc_id, body="text", issue_type=issue_type, labels=labels)


def _plan(
    documents: list[SourceDocument],
    *,
    source_doc_ids: list[str] | None = None,
    intent_id: str = "intent-a",
) -> BacklogPlan:
    return BacklogPlan(
        documents=documents,
        intents=[Intent(id=intent_id, title="X", description="do x", source_doc_ids=source_doc_ids or [])],
        gaps=[],
        specs=[FeatureSpec(intent_id=intent_id, title="X")],
    )


def _spec(intent_id: str = "intent-a") -> FeatureSpec:
    return FeatureSpec(intent_id=intent_id, title="X")


def test_resolves_the_type_and_labels_from_the_linked_document() -> None:
    plan = _plan([_doc("PROJ-1", issue_type="Bug", labels=("backend", "sev1"))], source_doc_ids=["PROJ-1"])

    meta = resolve_ticket_meta(plan, _spec())

    assert meta.issue_type == "Bug"
    assert meta.labels == ("backend", "sev1")
    assert meta.origin == "PROJ-1", "the run has to be able to say where the answer came from"
    assert meta.known


def test_follows_the_intent_link_rather_than_taking_the_first_document() -> None:
    """The plan holds every document the walk fetched; only one of them is this ticket."""
    plan = _plan(
        [_doc("PROJ-1", issue_type="Story"), _doc("PROJ-2", issue_type="Bug")],
        source_doc_ids=["PROJ-2"],
    )

    assert resolve_ticket_meta(plan, _spec()).issue_type == "Bug"


def test_a_single_document_resolves_even_with_no_intent_link() -> None:
    """`source_doc_ids` is model-produced and may be missing. One document is still unambiguous."""
    plan = _plan([_doc("PROJ-1", issue_type="Bug")], source_doc_ids=[])

    meta = resolve_ticket_meta(plan, _spec())

    assert meta.issue_type == "Bug" and meta.origin == "PROJ-1"


def test_unlinked_documents_that_disagree_resolve_to_nothing_and_say_why() -> None:
    """A type picked by list order would select a workflow profile. Refusing to guess is the
    whole contract: a run whose research was decided by iteration order is not reproducible."""
    plan = _plan([_doc("PROJ-1", issue_type="Story"), _doc("PROJ-2", issue_type="Bug")], source_doc_ids=[])

    meta = resolve_ticket_meta(plan, _spec())

    assert not meta.known
    assert "Bug" in meta.detail and "Story" in meta.detail
    assert "2 source documents" in meta.detail


def test_documents_that_agree_are_not_an_ambiguity() -> None:
    """Three subtasks all filed as Bug answer the question; only disagreement is ambiguous."""
    plan = _plan(
        [_doc("PROJ-1", issue_type="Bug", labels=("b",)), _doc("PROJ-2", issue_type="Bug", labels=("a",))],
        source_doc_ids=["PROJ-1", "PROJ-2"],
    )

    meta = resolve_ticket_meta(plan, _spec())

    assert meta.issue_type == "Bug"
    assert meta.labels == ("a", "b"), "unioned and sorted, not concatenated in arrival order"


def test_labels_come_back_sorted_whatever_order_they_arrived_in() -> None:
    """`select_profile` matches over this set (labels-as-fallback). First-match-wins over an
    arrival-ordered set is not reproducible, which is the property that must not be lost."""
    forward = _plan([_doc("PROJ-1", labels=("zeta", "alpha"))], source_doc_ids=["PROJ-1"])
    reverse = _plan([_doc("PROJ-1", labels=("alpha", "zeta"))], source_doc_ids=["PROJ-1"])

    assert resolve_ticket_meta(forward, _spec()).labels == ("alpha", "zeta")
    assert resolve_ticket_meta(reverse, _spec()).labels == ("alpha", "zeta")


def test_an_untyped_document_says_so_rather_than_going_quiet() -> None:
    """ "Why did this take the default profile?" must be answerable from the run's output."""
    plan = _plan([_doc("PROJ-1")], source_doc_ids=["PROJ-1"])

    meta = resolve_ticket_meta(plan, _spec())

    assert not meta.known
    assert "PROJ-1" in meta.detail


def test_an_empty_plan_resolves_to_nothing() -> None:
    meta = resolve_ticket_meta(BacklogPlan(), _spec())

    assert not meta.known and meta.detail


def test_accepts_the_dumped_spec_dict_as_well_as_the_model() -> None:
    """The pipeline carries both shapes — `ctx.spec` is the dump, intake holds the model."""
    plan = _plan([_doc("PROJ-1", issue_type="Bug")], source_doc_ids=["PROJ-1"])

    assert resolve_ticket_meta(plan, _spec().model_dump()).issue_type == "Bug"


def test_a_gap_finding_in_the_plan_does_not_disturb_the_walk() -> None:
    plan = _plan([_doc("PROJ-1", issue_type="Bug")], source_doc_ids=["PROJ-1"])
    plan.gaps = [
        GapFinding(rule_id="nfrs_missing", intent_id="intent-a", severity=GapSeverity.WARNING, message="m")
    ]

    assert resolve_ticket_meta(plan, _spec()).issue_type == "Bug"
