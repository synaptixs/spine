"""B103: a change Spine drafted must read back as the criteria it was drafted from.

`openspec draft` renders the intake's output as an OpenSpec change; `openspec://` reads it back and the
spec writer copies whatever criteria come back into the *stated* ones, the only ones that bind the review.
Before the fix the reader returned the summary paragraph, the writer's placeholder and every scenario
wrapped in `Scenario: <label> — THEN …`, so a ticket that stated nothing came back stating two things.

A change is read as drafted for as long as its proposal still carries the draft banner. A change a person
wrote, or whose owner took the banner out, is read exactly as before.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from orchestrator.intake import requirements
from orchestrator.intake.intents import Intent
from orchestrator.intake.openspec_source import (
    OpenSpecSourceAdapter,
    OpenSpecSourceConfig,
    change_to_intent,
)
from orchestrator.intake.openspec_writer import (
    PLACEHOLDER_CRITERION,
    render_change,
    write_change,
)
from orchestrator.intake.pkg_evidence import ungrounded
from orchestrator.intake.specs import FeatureSpec

#: The shapes of criteria a draft carries, the first two taken from the two R2 tickets that exposed it.
_NONE: list[str] = []  # django__django-14539: the ticket stated no criteria
_PROSE = [  # sympy__sympy-21612: the reporter's two sentences, verbatim
    "Expected is: `((a**3 + b)/c)/(1/(c**2))`.",
    "The missing brackets in the denominator result in a wrong expression.",
]
_SHALL = ["The system SHALL provide a Page with url, status_code, html fields."]
_GWT = ["GIVEN a Page with status_code 200 THEN page.is_ok is True"]


def _drafted(criteria: list[str]) -> tuple[Intent, FeatureSpec, dict[str, str]]:
    intent = Intent(
        id="intent-fix-latex-fraction",
        title="Fix LaTeX fraction parsing",
        description="Parsing a nested fraction drops the brackets around the denominator.",
        problem="Latex parsing of fractions yields a wrong expression.",
        acceptance_criteria=list(criteria),
    )
    spec = FeatureSpec(
        intent_id=intent.id,
        title=intent.title,
        summary="Fix fraction handling so a nested denominator keeps its grouping. " * 3,
        acceptance_criteria=list(criteria),
        proposed_criteria=["Given a nested fraction, when parsed, then it equals the grouped form."],
    )
    return intent, spec, render_change(spec, intent, ungrounded())


def _read_back(files: dict[str, str]) -> Intent:
    return change_to_intent(
        "fix-latex-fraction",
        proposal_md=files["proposal.md"],
        spec_texts=tuple(v for k, v in files.items() if k.startswith("specs/")),
    )


@pytest.mark.parametrize(
    "criteria", [_PROSE, _SHALL, _GWT], ids=["reporter-prose", "plain-shall", "given-when-then"]
)
def test_a_drafted_change_reads_back_as_exactly_the_criteria_it_was_drafted_from(
    criteria: list[str],
) -> None:
    _intent, _spec, files = _drafted(criteria)
    assert _read_back(files).acceptance_criteria == criteria


def test_a_drafted_change_that_stated_nothing_reads_back_as_the_placeholder_alone() -> None:
    """The reader keeps the placeholder, because `openspec check` calls it out; the adapter drops it."""
    _intent, _spec, files = _drafted(_NONE)
    assert _read_back(files).acceptance_criteria == [PLACEHOLDER_CRITERION]


@pytest.mark.parametrize(
    "criteria",
    [_NONE, _PROSE, _SHALL, _GWT],
    ids=["none", "reporter-prose", "plain-shall", "given-when-then"],
)
async def test_the_adapter_hands_autorun_exactly_what_the_source_stated(
    tmp_path: Path, criteria: list[str]
) -> None:
    """The end-to-end property B103 is about: write the draft to disk, read it as `openspec://` does."""
    intent, _spec, files = _drafted(criteria)
    root = tmp_path / "openspec"
    write_change(root, intent, files)
    adapter = OpenSpecSourceAdapter(OpenSpecSourceConfig(root=root))
    tree = await adapter.fetch_tree("fix-latex-fraction")
    [read] = adapter.structured_intents(tree.documents)
    assert read.acceptance_criteria == criteria


def test_the_proposed_criteria_stay_out_of_the_stated_ones() -> None:
    """A guess is not a contract: tasks.md lists the proposed criteria and reading never promotes them."""
    _intent, spec, files = _drafted(_NONE)
    assert spec.proposed_criteria[0] in files["tasks.md"]
    assert not any(spec.proposed_criteria[0] in c for c in _read_back(files).acceptance_criteria)


_PERSON_PROPOSAL = "# Proposal: Login\n\n## Why\nUsers cannot log in.\n\n## What Changes\nAdd a login form.\n"
_PERSON_SPEC = """\
# Delta for login

## ADDED Requirements

### Requirement: Login
The system SHALL let a user log in with a valid password.

#### Scenario: Login
- THEN Login succeeds
"""


def test_a_change_a_person_wrote_is_read_exactly_as_before() -> None:
    """No banner, no change: the SHALL statement is a criterion and the scenario keeps its label."""
    intent = change_to_intent("login", proposal_md=_PERSON_PROPOSAL, spec_texts=(_PERSON_SPEC,))
    assert intent.acceptance_criteria == [
        "Login: The system SHALL let a user log in with a valid password.",
        "Scenario: Login — THEN Login succeeds",
    ]


def test_a_drafted_change_whose_owner_took_the_banner_out_is_read_as_theirs() -> None:
    """Deleting the banner is how an owner says "this is mine now": the statement counts again."""
    _intent, _spec, files = _drafted(_PROSE)
    polished = "\n".join(
        line for line in files["proposal.md"].splitlines() if "Auto-drafted by Spine" not in line
    )
    spec_md = next(v for k, v in files.items() if k.startswith("specs/"))
    back = change_to_intent("fix-latex-fraction", proposal_md=polished, spec_texts=(spec_md,))
    assert back.acceptance_criteria[0].startswith("Fix LaTeX fraction parsing: ")  # the statement is back
    assert any(c.startswith("Scenario: ") for c in back.acceptance_criteria)


def test_the_banner_in_a_persons_prose_does_not_make_their_change_a_draft() -> None:
    """Only the banner at the top of the proposal counts, not the words appearing in the body."""
    proposal = _PERSON_PROPOSAL.replace("Users cannot log in.", "This was Auto-drafted by Spine once.")
    intent = change_to_intent("login", proposal_md=proposal, spec_texts=(_PERSON_SPEC,))
    assert intent.acceptance_criteria[0].startswith("Login: The system SHALL")


def test_check_still_reports_the_placeholder_on_a_drafted_change_with_no_criteria(tmp_path: Path) -> None:
    """`openspec check` reads through `load_change`: it must still see the placeholder to call it out."""
    intent, _spec, files = _drafted(_NONE)
    root = tmp_path / "openspec"
    write_change(root, intent, files)
    loaded = requirements.load_change("fix-latex-fraction", root=root)
    report = requirements.check_intent(loaded.intent, change_id=loaded.change_id)
    assert any("placeholder scenario" in n for n in report.notes)
