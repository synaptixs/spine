"""The validity gate, judged against tickets whose right answer is already known.

The corpus is this project's own board. SSPN-3 shipped with an acceptance criterion claiming
eleven entities where the source has seven; SSPN-18 was a design defect written in prose with
no traceback. Both cost a human to notice. These tests assert the gate notices instead.

The bar is deliberately asymmetric: a false *refusal* wastes a human's attention and teaches
people to switch the gate off, so every check here must be answerable from the graph. Where
it cannot be, the gate says PROCEED and lets the run continue.
"""

from __future__ import annotations

from typing import Any

from orchestrator.pkg import FactStore
from orchestrator.pkg.facts import FactBatch, Node, NodeKind, Provenance
from orchestrator.sdlc.validity import Verdict, assess


def _graph(*, entities: int = 0, endpoints: int = 0, modules: int = 1) -> FactStore:
    batch = FactBatch()
    for i in range(modules):
        batch.add_node(Node(f"py:m{i}", NodeKind.MODULE, f"m{i}", "python", Provenance(f"m{i}.py", 1)))
    for i in range(entities):
        batch.add_node(
            Node(f"py:entity:t{i}", NodeKind.ENTITY, f"t{i}", "python", Provenance("models.py", i + 1))
        )
    for i in range(endpoints):
        batch.add_node(
            Node(
                f"py:endpoint:GET /r{i}",
                NodeKind.ENDPOINT,
                f"GET /r{i}",
                "python",
                Provenance("api.py", i + 1),
            )
        )
    return FactStore(batch)


def _spec(*criteria: str, **extra: Any) -> dict[str, Any]:
    return {"title": "t", "summary": "s", "acceptance_criteria": list(criteria), **extra}


# ---- the corpus ------------------------------------------------------------


def test_the_sspn3_criterion_is_refused() -> None:
    """Verbatim from the ticket that shipped with it. The source had seven."""
    assessment = assess(
        _spec("11 `Entity` nodes on this repo, one per `__tablename__`."),
        store=_graph(entities=7),
    )

    assert assessment.verdict is Verdict.CRITERIA_WRONG
    assert "11" in assessment.render() and "7" in assessment.render()
    # The evidence is the criterion itself, so a human is not asked to take our word for it.
    assert "one per `__tablename__`" in assessment.findings[0].evidence


def test_the_corrected_criterion_proceeds() -> None:
    assessment = assess(
        _spec("7 `Entity` nodes on this repo, one per `__tablename__`."), store=_graph(entities=7)
    )
    assert assessment.verdict is Verdict.PROCEED


def test_a_met_at_least_target_proceeds() -> None:
    """SSPN-2's shape: "≥ 70 of 77" is satisfied by more, and refusing it would be the false
    refusal that gets a gate switched off."""
    assessment = assess(
        _spec("On this repo, at least 70 route handlers carry an inbound EXPOSES edge."),
        store=_graph(endpoints=77),
    )
    assert assessment.verdict is Verdict.PROCEED


def test_an_unmet_at_least_target_is_refused() -> None:
    assessment = assess(
        _spec("On this repo, at least 70 endpoints carry an inbound EXPOSES edge."),
        store=_graph(endpoints=12),
    )
    assert assessment.verdict is Verdict.CRITERIA_WRONG
    assert "or more" in assessment.findings[0].detail


def test_a_number_that_is_a_target_not_a_claim_proceeds() -> None:
    """ "Add 3 endpoints" is work to do. Only a claim *about the repo as it is* can be false."""
    assessment = assess(_spec("Add 3 endpoints for the export flow."), store=_graph(endpoints=0))
    assert assessment.verdict is Verdict.PROCEED


def test_a_bug_that_lands_nowhere_is_refused() -> None:
    """SSPN-18's shape: a design defect in prose, with nothing for the run to work from."""
    assessment = assess(_spec("the cache serves a stale graph"), store=_graph(), issue_type="Bug")

    assert assessment.verdict is Verdict.UNLOCALIZED
    assert "traceback" in assessment.findings[0].evidence


def test_a_bug_that_lands_somewhere_proceeds() -> None:
    assessment = assess(
        _spec("the cache serves a stale graph"),
        store=_graph(),
        issue_type="Bug",
        landing=["src/orchestrator/pkg/persistence.py"],
    )
    assert assessment.verdict is Verdict.PROCEED


def test_a_story_that_lands_nowhere_still_proceeds() -> None:
    """A feature can legitimately be about code that does not exist yet — that is the whole
    point of a feature. Only a Bug must resolve to something."""
    assessment = assess(_spec("add a brand new subsystem"), store=_graph(), issue_type="Story")
    assert assessment.verdict is Verdict.PROCEED


# ---- an unbound criterion, and who it is fatal to ---------------------------
#
# The gap: `_check_localization` excuses an enhancement from landing anywhere in the graph —
# a feature has no existing behaviour to localize — while the unbound-criteria check refused
# every ticket alike. One gate, two stances on one question, and the perverse consequence was
# that writing an enhancement's criterion *well* is what parked it.


def _binding(*criteria: str) -> Any:
    """A real `CriteriaBinding` against a graph holding one symbol. No fake to drift."""
    from orchestrator.pkg.facts import FactBatch, Node, NodeKind, Provenance
    from orchestrator.sdlc.criteria_binding import bind_criteria

    batch = FactBatch()
    batch.add_node(Node("py:report", NodeKind.MODULE, "report.py", "python", Provenance("report.py", 1)))
    batch.add_node(
        Node("py:report.render", NodeKind.FUNCTION, "render", "python", Provenance("report.py", 10))
    )
    return bind_criteria({"acceptance_criteria": list(criteria)}, store=FactStore(batch))


def test_a_bug_naming_a_symbol_that_does_not_exist_is_still_refused() -> None:
    """Unchanged, and the reason the check exists: a bug's subject is code that already runs,
    so a criterion naming a symbol nobody can find describes a repository this is not."""
    assessment = assess(
        _spec("`GhostWidget` no longer double-counts"),
        store=_graph(),
        issue_type="Bug",
        landing=["report.py"],
        criteria=_binding("`GhostWidget` no longer double-counts"),
    )

    assert assessment.verdict is Verdict.CRITERIA_WRONG
    assert assessment.findings[0].check == "criterion-unbound"


def test_an_enhancement_naming_the_module_it_will_create_proceeds() -> None:
    """The deliverable is allowed not to exist yet. This is the verdict that changed."""
    assessment = assess(
        _spec("`rule_compiler` returns at least 70 rules"),
        store=_graph(),
        issue_type="Story",
        criteria=_binding("`rule_compiler` returns at least 70 rules"),
    )

    assert assessment.verdict is Verdict.PROCEED


def test_the_enhancement_still_hears_about_it() -> None:
    """Proceeding is not the same as saying nothing. A criterion naming a symbol that will
    never exist — a typo, a renamed module — is worth a reviewer's eye either way."""
    assessment = assess(
        _spec("`rule_compiler` returns at least 70 rules"),
        store=_graph(),
        issue_type="Story",
        criteria=_binding("`rule_compiler` returns at least 70 rules"),
    )

    (finding,) = [f for f in assessment.findings if f.check == "criterion-unbound"]
    assert "rule_compiler" in finding.detail
    assert "expected for something not built yet" in finding.detail
    # One fact, two stages: the design stage lists the same absent name under unverified
    # references, and a reviewer who cannot tell it is one fact discounts both.
    assert "unverified references" in finding.evidence
    assert "**Verdict:** PROCEED" in assessment.render()
    assert "rule_compiler" in assessment.render()


def test_the_precise_criterion_is_no_longer_the_one_that_parks_the_ticket() -> None:
    """The perverse consequence, stated as a test. Both forms describe the same feature; the
    second names its subject, is more testable, and used to be the one that stopped the run."""
    prose = assess(
        _spec("the compiler returns at least 70 rules"),
        store=_graph(),
        issue_type="Story",
        criteria=_binding("the compiler returns at least 70 rules"),
    )
    precise = assess(
        _spec("`rule_compiler` returns at least 70 rules"),
        store=_graph(),
        issue_type="Story",
        criteria=_binding("`rule_compiler` returns at least 70 rules"),
    )

    assert prose.verdict is precise.verdict is Verdict.PROCEED


def test_an_untyped_ticket_is_not_held_to_a_bugs_standard() -> None:
    """Empty is the honest state for a source with no issue types — a wiki page, a `--spec`
    file. Refusing those would make the gate strictest exactly where it knows least."""
    assessment = assess(
        _spec("`rule_compiler` returns at least 70 rules"),
        store=_graph(),
        criteria=_binding("`rule_compiler` returns at least 70 rules"),
    )

    assert assessment.verdict is Verdict.PROCEED


def test_an_incident_is_judged_as_a_bug() -> None:
    """`profile_select` has always mapped `incident` to the bug profile; validity kept its own
    list and did not. One predicate now answers for both — an incident that gets root-cause
    analysis is an incident that must localize."""
    assessment = assess(
        _spec("`GhostWidget` no longer double-counts"),
        store=_graph(),
        issue_type="Incident",
        landing=["report.py"],
        criteria=_binding("`GhostWidget` no longer double-counts"),
    )

    assert assessment.verdict is Verdict.CRITERIA_WRONG


def test_a_bound_criterion_proceeds_whatever_the_issue_type() -> None:
    for issue_type in ("Bug", "Story", ""):
        assessment = assess(
            _spec("`render` keeps its signature"),
            store=_graph(),
            issue_type=issue_type,
            landing=["report.py"],
            criteria=_binding("`render` keeps its signature"),
        )
        assert assessment.verdict is Verdict.PROCEED, issue_type


# ---- size and duplication --------------------------------------------------


def test_a_ticket_with_too_many_criteria_is_refused() -> None:
    assessment = assess(_spec(*[f"criterion {i}" for i in range(15)]), store=_graph())

    assert assessment.verdict is Verdict.TOO_BIG
    assert "15 acceptance criteria" in assessment.findings[0].detail


def test_a_ticket_landing_in_too_many_modules_is_refused() -> None:
    assessment = assess(_spec("do the thing"), store=_graph(), landing=[f"src/m{i}.py" for i in range(20)])
    assert assessment.verdict is Verdict.TOO_BIG


def test_a_ticket_a_previous_run_completed_is_a_duplicate() -> None:
    """Two runs on one ticket produce two branches and two PRs for one piece of work."""

    class _Record:
        run_id = "earlier"
        issue_key = "SSPN-9"
        status = "done"
        pr_url = "https://github.com/x/y/pull/1"

    assessment = assess(_spec("do the thing"), store=_graph(), issue_key="SSPN-9", prior_runs=[_Record()])

    assert assessment.verdict is Verdict.DUPLICATE
    assert "pull/1" in assessment.findings[0].evidence


def test_a_prior_run_that_opened_no_pr_does_not_block_a_retry() -> None:
    """A safe-mode rehearsal changes nothing anyone else can see. Treating it as a duplicate
    made a ticket unworkable after its first dry run — and did it to the first person who
    tried to iterate on one."""

    class _Record:
        run_id = "rehearsal"
        issue_key = "SSPN-14"
        status = "done"
        pr_url = ""

    assessment = assess(_spec("do the thing"), store=_graph(), issue_key="SSPN-14", prior_runs=[_Record()])

    assert assessment.verdict is Verdict.PROCEED


def test_a_failed_previous_run_does_not_block_a_retry() -> None:
    class _Record:
        run_id = "earlier"
        issue_key = "SSPN-9"
        status = "failed"
        pr_url = ""

    assessment = assess(_spec("do the thing"), store=_graph(), issue_key="SSPN-9", prior_runs=[_Record()])
    assert assessment.verdict is Verdict.PROCEED


# ---- the gate does not cry wolf --------------------------------------------


def test_prose_with_numbers_in_it_is_not_a_claim() -> None:
    """Version numbers, HTTP codes and counts of things the graph does not model must not
    trip the gate: a check that fires on ordinary English is a check people disable."""
    store = _graph(entities=7, endpoints=3)
    for criterion in (
        "An unknown --format value exits 2 with a message naming the valid values.",
        "Bump the cache format version to 4.",
        "The endpoint returns 404 when the run is missing.",
        "Add 2 fixtures covering the empty case.",
    ):
        assert assess(_spec(criterion), store=store).verdict is Verdict.PROCEED, criterion


def test_an_empty_ticket_proceeds() -> None:
    """Nothing to contradict. Emptiness is intake's problem, not the gate's."""
    assert assess({}, store=_graph()).verdict is Verdict.PROCEED
