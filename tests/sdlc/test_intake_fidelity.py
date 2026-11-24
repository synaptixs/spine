"""G2 PR-A through the plan: what the ticket says reaches the build document unchanged.

B174 (SSPN-400), intake half — a scheduling note never becomes a non-goal, and an unanswered open
question is shown in §2 and §12 and keeps validity at WARN. The check sits where the spec enters
``build_plan`` (the one place the plan reads non-goals), so `sdlc plan --spec`, the MCP tool and
the approval gate's re-derivation all get it.

B166 (SSPN-382) — §8 checks a filed criterion against each *sentence* of the ticket, not only each
line; with no acceptance-criteria section the filed criteria are labelled "from the ticket's prose",
and the test strategy builds on the proposed Given/When/Then under "confirm first".

Synthetic fixtures in the field tickets' shape; no field repository's names.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

from orchestrator.sdlc.builddoc import _criteria_block, build_plan

TICKET = """\
# SHOP-41 Basket shows one seller per item

## Root cause
The seller list is built by a single append in src/basket/summary.py, so only the first seller
of each item is kept.

## Open question
Can the basket screen call the seller listing service directly?

Not blocking — to be picked up later.
Changing the checkout flow is out of scope.
"""

QUESTION = "Can the basket screen call the seller listing service directly?"


def _spec(**over: Any) -> dict[str, Any]:
    spec: dict[str, Any] = {
        "intent_id": "SHOP-41",
        "title": "Basket shows one seller per item",
        "summary": "Keep every seller of an item.",
        "description": "The seller list keeps only the first seller.",
        "acceptance_criteria": [],
        "proposed_criteria": [],
        "met_criteria": {},
    }
    spec.update(over)
    return spec


def _section(document: str, number: int) -> str:
    return re.split(r"^## ", document.split(f"## {number}. ", 1)[1], flags=re.MULTILINE)[0]


def _repo(tmp_path: Path) -> Path:
    (tmp_path / "src" / "basket").mkdir(parents=True)
    (tmp_path / "src" / "basket" / "summary.py").write_text(
        "def sellers(item):\n    out = []\n    out.append(item.sellers[0])\n    return out\n",
        encoding="utf-8",
    )
    return tmp_path


# ---- B174: non-goals and open questions ---------------------------------------------------------


@pytest.mark.asyncio
async def test_a_scheduling_note_is_not_a_non_goal_in_the_plan(tmp_path: Path) -> None:
    spec = _spec(non_goals=["Immediate fix", "Changes to the checkout flow"])
    document = await build_plan(spec, root=_repo(tmp_path), issue_type="Bug", source_text=TICKET)

    requirement = _section(document, 1)
    (row,) = [line for line in requirement.splitlines() if line.startswith("- **Non-goals:**")]
    assert "Immediate fix" not in row
    assert "Changes to the checkout flow" in row
    assert "Dropped by the support check" in requirement
    assert "Immediate fix" in requirement.split("Dropped by the support check", 1)[1]
    # Codegen is handed §1: the dropped non-goal must not reach it as an instruction.
    assert document.count("Immediate fix") == 1


@pytest.mark.asyncio
async def test_an_open_question_shows_in_section_2_and_12_and_warns(tmp_path: Path) -> None:
    spec = _spec(open_questions=[QUESTION])
    document = await build_plan(spec, root=_repo(tmp_path), issue_type="Bug", source_text=TICKET)

    assert QUESTION in _section(document, 2)
    confidence = _section(document, 12)
    assert "| Open questions |" in confidence and "1 unanswered" in confidence
    # WARN, not a refusal: the gate's verdict stands and the finding rides with it.
    assert "**Validity:** PROCEED" in document
    assert "open question" in document.split("---", 1)[0].lower()


@pytest.mark.asyncio
async def test_the_check_is_deterministic_and_the_approval_gate_agrees(tmp_path: Path) -> None:
    """Invariant 2 for the new lines, and the re-derivation a human's approval is bound to."""
    from orchestrator.sdlc.builddoc import (
        PlanApproval,
        plan_digest,
        require_approved_plan,
        save_approval,
        save_source_text,
    )

    root = _repo(tmp_path)
    spec = _spec(non_goals=["Immediate fix"], open_questions=[QUESTION])
    first = await build_plan(spec, root=root, source_text=TICKET)
    assert first == await build_plan(spec, root=root, source_text=TICKET)

    save_source_text("SHOP-41", TICKET, root=root)
    save_approval(
        PlanApproval(
            intent_id="SHOP-41",
            digest=plan_digest(first),
            decision="APPROVED",
            decided_by="reviewer",
            decided_at="2026-11-23",
            commit="unknown",
        ),
        root=root,
    )
    assert (await require_approved_plan(spec, root=root)).decided_by == "reviewer"


@pytest.mark.asyncio
async def test_a_linked_page_marker_is_not_support_for_a_non_goal(tmp_path: Path) -> None:
    """#701 puts `=== Linked page: <title> ===` between the ticket and each page `--follow-links`
    read. A page's text is support (intake read it); the marker's own words are not."""
    from orchestrator.intake.follow_links import linked_page_marker

    linked = (
        f"{TICKET}\n\n{linked_page_marker('Seller listing')}\nThe seller listing service is internal only.\n"
    )
    assert "Linked page" in linked  # the marker shape this guards against
    spec = _spec(non_goals=["Linked page changes", "Seller listing service changes"])
    document = await build_plan(spec, root=_repo(tmp_path), source_text=linked)

    why = _section(document, 1)
    assert "“Linked page changes” — no support in the ticket's text" in why
    assert "Seller listing service changes" in why.split("Dropped by the support check")[0]


@pytest.mark.asyncio
async def test_a_plan_with_nothing_to_drop_says_nothing_about_it(tmp_path: Path) -> None:
    document = await build_plan(
        _spec(non_goals=["Changes to the checkout flow"]), root=_repo(tmp_path), source_text=TICKET
    )
    assert "support check" not in document
    assert "Open questions" not in document


# ---- B166: prose criteria -------------------------------------------------------------------------

PROSE_TICKET = """\
# SHOP-52 The summary claims nothing uses the module

The summary says only tests import it, but two product modules do.

So the summary sentence is false. It is the worst kind of false: "count: 0" read as "safe".

Severity: S2. A wrong "nothing uses it" from a tool meant to measure.
"""

FRAGMENTS = [
    "So the summary sentence is false.",
    'It is the worst kind of false: "count: 0" read as "safe".',
    'A wrong "nothing uses it" from a tool meant to measure.',
]

PROPOSED = [
    "Given a module two product modules import, when the summary is generated, then both are listed.",
]


def test_a_sentence_of_a_two_sentence_line_reads_stated() -> None:
    """The measured case: three verbatim sentences read `derived · model` because each shared
    its line with another sentence."""
    block = _criteria_block(_spec(acceptance_criteria=FRAGMENTS), PROSE_TICKET)

    for n, text in enumerate(FRAGMENTS, start=1):
        assert f"| {n} | {text} | stated · from the ticket's prose | — |" in block
    assert "match no line" not in block


def test_a_narrowed_sentence_is_still_not_stated() -> None:
    """Sentence, not substring: dropping the leading word is a rewrite, not a quote."""
    block = _criteria_block(_spec(acceptance_criteria=["the summary sentence is false"]), PROSE_TICKET)

    assert "| 1 | the summary sentence is false | derived · model | — |" in block


def test_no_criteria_section_is_said_so() -> None:
    block = _criteria_block(_spec(acceptance_criteria=FRAGMENTS, proposed_criteria=PROPOSED), PROSE_TICKET)

    assert "no acceptance-criteria section" in block


@pytest.mark.parametrize(
    "heading",
    [
        "## Acceptance criteria",
        "h2. Acceptance Criteria",
        "**Definition of done**",
        "AC: it says why",
        "Done when:",
        # B166 review: forms the first pattern missed.
        "*Acceptance Criteria:*",
        "### AC",
        "## **Acceptance Criteria**",
        "||Acceptance Criteria||",
        "ACs:",
        "## Acceptance criteria (draft)",
        "- **Definition of Done**",
    ],
)
def test_a_criteria_section_is_recognised_in_markdown_and_jira_markup(heading: str) -> None:
    """Jira tickets arrive as wiki markup (``h2. Expected``); a criteria heading there counts too."""
    from orchestrator.sdlc.builddoc import has_criteria_section

    assert has_criteria_section(f"# T\n\n{heading}\n- It says why.\n")
    assert not has_criteria_section("# T\n\nh2. Expected\nIt says why. The expected criteria are unclear.\n")


@pytest.mark.parametrize(
    "line",
    [
        "Done when you get a chance, no rush.",
        "Acceptance criteria are still being written by product.",
        "Accessibility was not reviewed.",
        "ACME's importer is out of scope.",
    ],
)
def test_prose_that_starts_with_the_words_is_not_a_criteria_section(line: str) -> None:
    from orchestrator.sdlc.builddoc import has_criteria_section

    assert not has_criteria_section(f"# T\n\n{line}\nIt says why.\n")


def test_a_ticket_headed_in_jira_bold_keeps_its_own_criterion_and_strategy() -> None:
    """The review's false sentence: a ticket headed ``*Acceptance Criteria:*`` was told it had no
    criteria section, and its own criterion was swapped for the model's in the test strategy."""
    ticket = "h1. T\n\n*Acceptance Criteria:*\n* It stops crashing.\n"
    block = _criteria_block(
        _spec(acceptance_criteria=["It stops crashing."], proposed_criteria=PROPOSED), ticket
    )

    assert "| 1 | It stops crashing. | stated | — |" in block
    assert "no acceptance-criteria section" not in block


def test_a_sentence_does_not_end_at_an_abbreviation() -> None:
    ticket = "# T\n\nAmounts round half-up, e.g. 0.125 becomes 0.13. Totals stay exact.\n"
    block = _criteria_block(
        _spec(acceptance_criteria=["Amounts round half-up, e.g. 0.125 becomes 0.13."]), ticket
    )

    assert (
        "| 1 | Amounts round half-up, e.g. 0.125 becomes 0.13. | stated · from the ticket's prose | — |"
        in block
    )


def test_a_ticket_with_a_criteria_section_keeps_the_plain_label() -> None:
    ticket = "# T\n\n**Acceptance criteria:**\n- It stops crashing. It says why.\n"
    block = _criteria_block(_spec(acceptance_criteria=["It says why."]), ticket)

    assert "| 1 | It says why. | stated | — |" in block
    assert "no acceptance-criteria section" not in block


@pytest.mark.asyncio
async def test_the_test_strategy_uses_the_proposed_criteria_confirm_first(tmp_path: Path) -> None:
    spec = _spec(acceptance_criteria=FRAGMENTS, proposed_criteria=PROPOSED)
    document = await build_plan(spec, root=_repo(tmp_path), source_text=PROSE_TICKET)

    (strategy,) = [
        line for line in _section(document, 6).splitlines() if line.startswith("**Test strategy:**")
    ]
    assert "confirm them first" in strategy
    assert PROPOSED[0] in strategy
    assert FRAGMENTS[0] not in strategy
    # §12 agrees with §8: prose lifted as criteria earns no "stated criteria" credit.
    assert "none in a criteria section" in _section(document, 12)


@pytest.mark.asyncio
async def test_a_ticket_with_criteria_tests_the_stated_ones(tmp_path: Path) -> None:
    ticket = "# T\n\n## Acceptance criteria\n- It stops crashing.\n"
    spec = _spec(acceptance_criteria=["It stops crashing."], proposed_criteria=PROPOSED)
    document = await build_plan(spec, root=_repo(tmp_path), source_text=ticket)

    (strategy,) = [
        line for line in _section(document, 6).splitlines() if line.startswith("**Test strategy:**")
    ]
    assert "It stops crashing." in strategy and "confirm them first" not in strategy
    assert "none in a criteria section" not in _section(document, 12)


def test_the_design_test_strategy_has_the_prose_branch() -> None:
    from orchestrator.sdlc.design import plan_test_strategy

    spec = _spec(acceptance_criteria=FRAGMENTS, proposed_criteria=PROPOSED)
    assert "confirm them first" in plan_test_strategy(spec, criteria_from_prose=True)
    assert FRAGMENTS[0] in plan_test_strategy(spec)  # unchanged without the flag
    # With nothing proposed there is nothing better to offer: the filed ones stay.
    assert FRAGMENTS[0] in plan_test_strategy(_spec(acceptance_criteria=FRAGMENTS), criteria_from_prose=True)
