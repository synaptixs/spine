"""The OpenSpec layout for the why-fields, recorded answers and the idea id (D27).

Writing then reading must give the same intent back, and writing again must give the same
files. A bare ``## Why`` — every change that exists today — must read exactly as it always did,
and an answer recorded under a question must never be read as another question.
"""

from __future__ import annotations

from orchestrator.intake.intents import Intent, Resolution
from orchestrator.intake.openspec_source import change_to_intent
from orchestrator.intake.openspec_writer import render_change
from orchestrator.intake.specs import FeatureSpec

_Q1, _Q2, _Q3 = "Which currencies?", "Who owns the export format?", "Include voided invoices?"


def _intent(**over: object) -> Intent:
    base: dict[str, object] = {
        "id": "intent-export-invoices",
        "title": "Export invoices",
        "description": "Finance wants a CSV of invoices.",
        "scope": "CSV export of invoices.",
        "acceptance_criteria": [
            "The system SHALL write a CSV.",
            "GIVEN a ledger WHEN export runs THEN a CSV exists",
        ],
        "problem": "Finance can't reconcile invoices against payouts at month end.",
        "users": ["Finance operations", "Audit"],
        "outcome": "Month-end reconciliation completes within 1 day.",
        "non_goals": ["PDF export", "Email delivery"],
        "idea_id": "idea-invoice-reconciliation",
        "open_questions": [_Q1, _Q2, _Q3],
        "resolutions": {
            _Q1: Resolution(
                status="answered", answer="EUR and USD only.", origin="user", channel="cli", at="2026-10-02"
            ),
            _Q2: Resolution(status="deferred", owner="finance-lead", at="2026-10-02"),
            _Q3: Resolution(status="answered", answer="No", origin="relayed", channel="mcp"),
        },
    }
    base.update(over)
    return Intent(**base)  # type: ignore[arg-type]


def _render(intent: Intent) -> dict[str, str]:
    spec = FeatureSpec(
        intent_id=intent.id,
        title=intent.title,
        summary=intent.description,
        acceptance_criteria=intent.acceptance_criteria,
    )
    return render_change(spec, intent)


def _read(files: dict[str, str], intent: Intent) -> Intent:
    spec = next(v for k, v in files.items() if k.startswith("specs/"))
    return change_to_intent(
        "export-invoices", proposal_md=files["proposal.md"], spec_texts=(spec,), tasks_md=files["tasks.md"]
    )


def test_the_new_layout_is_written_as_the_plan_shows_it() -> None:
    md = _render(_intent())["proposal.md"]
    assert "\nidea: idea-invoice-reconciliation\n" in md
    assert "## Why\nFinance wants a CSV of invoices.\n\n### Problem\nFinance can't reconcile" in md
    assert "### Users\n- Finance operations\n- Audit\n### Outcome\nMonth-end" in md
    assert "## What Changes\nCSV export of invoices.\n\n### Non-goals\n- PDF export\n- Email delivery" in md
    assert f"- {_Q1}\n  - **Answer** (user · cli · 2026-10-02): EUR and USD only.\n" in md
    assert f"- {_Q2}\n  - **Deferred** to @finance-lead (2026-10-02)\n" in md
    assert f"- {_Q3}\n  - **Answer** (relayed · mcp): No\n" in md


def test_write_read_gives_the_intent_back() -> None:
    intent = _intent()
    back = _read(_render(intent), intent)
    for field in (
        "description",
        "scope",
        "problem",
        "users",
        "outcome",
        "non_goals",
        "idea_id",
        "open_questions",
    ):
        assert getattr(back, field) == getattr(intent, field), field
    assert back.resolutions == intent.resolutions


def test_write_read_write_is_stable() -> None:
    """The proposal — the file this track changes — is. (``tasks.md`` and the delta spec reshape
    criteria on a round trip today; plan §11 leaves that alone.)"""
    intent = _intent()
    first = _render(intent)
    assert _render(_read(first, intent))["proposal.md"] == first["proposal.md"]


def test_resolution_bullets_never_become_questions() -> None:
    intent = _intent()
    back = _read(_render(intent), intent)
    assert back.open_questions == [_Q1, _Q2, _Q3]


def test_a_proposed_answer_keeps_its_status() -> None:
    res = Resolution(status="proposed", answer="EUR", origin="proposed", channel="elicitation")
    intent = _intent(open_questions=[_Q1], resolutions={_Q1: res})
    assert _read(_render(intent), intent).resolutions[_Q1] == res


def test_an_answer_is_one_line() -> None:
    res = Resolution(status="answered", answer="EUR\nand USD", origin="user", channel="cli")
    intent = _intent(open_questions=[_Q1], resolutions={_Q1: res})
    back = _read(_render(intent), intent)
    assert back.open_questions == [_Q1] and back.resolutions[_Q1].answer == "EUR and USD"


def test_a_description_that_is_only_the_problem_is_not_written_twice() -> None:
    intent = _intent(description="Finance can't reconcile invoices against payouts at month end.")
    files = _render(intent)
    assert files["proposal.md"].count("Finance can't reconcile") == 1
    assert _read(files, intent).description == intent.problem
    assert _render(_read(files, intent))["proposal.md"] == files["proposal.md"]


def test_an_intent_with_only_a_problem_still_round_trips() -> None:
    intent = _intent(users=[], outcome="", non_goals=[], idea_id="", open_questions=[], resolutions={})
    back = _read(_render(intent), intent)
    assert (back.problem, back.users, back.outcome, back.non_goals) == (intent.problem, [], "", [])


# ---- legacy proposals read as they always did -----------------------------------------------


def test_a_bare_why_is_the_description_and_sets_no_why_field() -> None:
    back = change_to_intent(
        "x", proposal_md="# Proposal: X\n\n## Why\nBecause reasons.\n\n## What Changes\nDo it.\n"
    )
    assert back.description == "Because reasons." and back.scope == "Do it."
    assert (back.problem, back.users, back.outcome, back.non_goals, back.idea_id) == ("", [], "", [], "")
    assert back.resolutions == {}


def test_other_subsections_under_why_stay_in_the_description() -> None:
    md = "# Proposal: X\n\n## Why\n### Background\nOld context.\n\n## What Changes\nDo it.\n"
    assert change_to_intent("x", proposal_md=md).description == "### Background\nOld context."


def test_unrecognised_text_under_a_question_is_still_a_question() -> None:
    md = "# P\n\n## Why\nw\n\n## Open Questions\n- Q one?\n  - some note\n- Q two?\n"
    assert change_to_intent("x", proposal_md=md).open_questions == ["Q one?", "some note", "Q two?"]


def test_a_resolution_looking_line_at_the_top_level_is_a_question() -> None:
    md = "# P\n\n## Why\nw\n\n## Open Questions\n- **Answer** (user · cli): x\n"
    back = change_to_intent("x", proposal_md=md)
    assert back.open_questions == ["**Answer** (user · cli): x"] and back.resolutions == {}


def test_an_answer_with_an_unknown_origin_is_not_a_resolution() -> None:
    md = "# P\n\n## Why\nw\n\n## Open Questions\n- Q?\n  - **Answer** (somebody · cli): x\n"
    back = change_to_intent("x", proposal_md=md)
    assert back.resolutions == {} and len(back.open_questions) == 2


def test_a_change_with_no_why_fields_writes_the_layout_it_always_did() -> None:
    intent = Intent(
        id="intent-x", title="X", description="Why text.", scope="What text.", open_questions=["Q?"]
    )
    md = _render(intent)["proposal.md"]
    assert "### " not in md and "idea:" not in md and "**Answer**" not in md
    assert "## Why\nWhy text.\n\n## What Changes\nWhat text.\n\n## Open Questions\n- Q?" in md
