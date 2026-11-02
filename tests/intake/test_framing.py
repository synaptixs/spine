"""P9a of SSPN-297 (SSPN-319): a greenfield idea → one framing change → N capability changes.

The framing change asks the four why-questions once. Once it passes `check`, each capability
becomes its own change that inherits the framing's problem, users and non-goal — recorded as
``inherited`` with the framing's id, never as the person's own answer — and asks only its own
outcome (D23, D28, D29). At most eight, all or nothing (D30). No model anywhere here."""

from __future__ import annotations

from pathlib import Path

import pytest

from orchestrator.intake import framing as fr
from orchestrator.intake import requirements as rq
from orchestrator.intake.intents import Resolution, question_states
from orchestrator.intake.openspec_source import _parse_resolution
from orchestrator.intake.openspec_writer import _resolution_line

_IDEA = "A small web app where a household shares a chore rota"
_FRAMING = "a-small-web-app-where-a-household-shares-framing"
_PRODUCT = "a-small-web-app-where-a-household-shares"
_ANSWERS = {
    "What problem does this solve?": "Households keep the rota in a group chat, so chores are forgotten.",
    "Who has this problem?": "Adults sharing a home; two to six people",
    "How will we know it worked? Describe an outcome someone can observe.": (
        "Each member sees whose turn each chore is and gets a reminder on the day."
    ),
    "What is explicitly out of scope?": "Payments or expense splitting; native mobile apps",
}
_OUTCOME_Q = "How will we know it worked? Describe an outcome someone can observe."
_CAPS = ["Sign up and join a household", "Assign chores in turn", "Send a reminder on the day"]


def _framed(tmp_path: Path, *, answered: bool = True) -> rq.LoadedChange:
    change_id, _ = fr.draft_framing(tmp_path, _IDEA)
    if answered:
        requests = [rq.AnswerRequest(q, a) for q, a in _ANSWERS.items()]
        rq.record_answers(rq.load_change(change_id, root=tmp_path), requests, channel="cli", at="2026-11-02")
    return rq.load_change(change_id, root=tmp_path)


def _passes(change: rq.LoadedChange) -> bool:
    return rq.check_intent(change.intent, change_id=change.change_id).passes


def _names(root: Path) -> list[str]:
    return sorted(p.name for p in (root / "changes").iterdir())


# ---- the framing change ---------------------------------------------------------------------------


def test_the_framing_change_asks_the_four_why_questions_and_is_named_for_the_idea(tmp_path: Path) -> None:
    change_id, written = fr.draft_framing(tmp_path, _IDEA)
    assert change_id == _FRAMING
    assert written and (tmp_path / "changes" / _FRAMING / "proposal.md").is_file()
    framing = rq.load_change(change_id, root=tmp_path)
    assert framing.intent.open_questions == list(rq.FIELD_QUESTIONS)
    assert not _passes(framing)


def test_the_same_idea_always_gives_the_same_framing() -> None:
    assert fr.framing_intent(_IDEA) == fr.framing_intent("  " + _IDEA.replace(" ", "  ") + " ")


def test_an_existing_framing_is_never_clobbered(tmp_path: Path) -> None:
    fr.draft_framing(tmp_path, _IDEA)
    proposal = tmp_path / "changes" / _FRAMING / "proposal.md"
    proposal.write_text("edited by a person\n")
    _, written = fr.draft_framing(tmp_path, _IDEA)
    assert written == [] and proposal.read_text() == "edited by a person\n"


def test_an_empty_idea_is_refused(tmp_path: Path) -> None:
    with pytest.raises(fr.FramingError):
        fr.draft_framing(tmp_path, "   ")


# ---- order: capabilities only once the framing passes (D28) ---------------------------------------


def test_capabilities_are_refused_until_the_framing_passes_check(tmp_path: Path) -> None:
    framing = _framed(tmp_path, answered=False)
    with pytest.raises(fr.FramingNotReadyError) as err:
        fr.draft_capabilities(tmp_path, framing, _CAPS)
    assert "What problem does this solve?" in str(err.value)
    assert _names(tmp_path) == [_FRAMING]


# ---- the capability changes (D23, D24, D29) -------------------------------------------------------


def test_each_capability_is_its_own_change_that_inherits_and_asks_only_its_outcome(tmp_path: Path) -> None:
    framing = _framed(tmp_path)
    assert _passes(framing)
    ids = fr.draft_capabilities(tmp_path, framing, _CAPS, at="2026-11-02")
    assert ids == [
        f"{_PRODUCT}-sign-up-and-join-a-household",
        f"{_PRODUCT}-assign-chores-in-turn",
        f"{_PRODUCT}-send-a-reminder-on-the-day",
    ]
    for change_id, title in zip(ids, _CAPS, strict=True):
        cap = rq.load_change(change_id, root=tmp_path)
        assert cap.intent.title == title
        assert cap.intent.problem == framing.intent.problem
        assert cap.intent.users == framing.intent.users
        assert cap.intent.non_goals == framing.intent.non_goals
        assert cap.intent.outcome == ""
        answered, _deferred, unresolved = question_states(cap.intent)
        assert unresolved == [_OUTCOME_Q]
        assert len(answered) == 3
        for question in answered:
            res = cap.intent.resolutions[question]
            assert (res.origin, res.channel, res.inherited_from) == ("stated", "inherited", _FRAMING)
        assert not _passes(cap)


def test_a_capability_passes_check_once_its_own_outcome_is_answered(tmp_path: Path) -> None:
    framing = _framed(tmp_path)
    [change_id] = fr.draft_capabilities(tmp_path, framing, ["Assign chores in turn"])
    outcome = "When a member marks a chore done, the next person on the rota sees it is their turn."
    rq.record_answers(rq.load_change(change_id, root=tmp_path), [rq.AnswerRequest(_OUTCOME_Q, outcome)])
    assert _passes(rq.load_change(change_id, root=tmp_path))


def test_the_same_framing_and_capabilities_give_the_same_files(tmp_path: Path) -> None:
    def files(root: Path) -> dict[str, str]:
        framing = _framed(root)
        fr.draft_capabilities(root, framing, _CAPS, at="2026-11-02")
        return {
            str(p.relative_to(root)): p.read_text()
            for p in sorted((root / "changes").rglob("*.md"))
            if _FRAMING not in str(p)
        }

    first = files(tmp_path / "a")
    assert first and first == files(tmp_path / "b")


# ---- bounds and ids (D30) -------------------------------------------------------------------------


def test_more_than_eight_capabilities_is_refused_with_the_count_and_nothing_written(tmp_path: Path) -> None:
    framing = _framed(tmp_path)
    nine = [f"Capability number {n}" for n in range(1, 10)]
    with pytest.raises(fr.FramingError, match=r"9 capabilities.*at most 8"):
        fr.draft_capabilities(tmp_path, framing, nine)
    assert _names(tmp_path) == [_FRAMING]


def test_eight_capabilities_is_not_too_many(tmp_path: Path) -> None:
    framing = _framed(tmp_path)
    assert len(fr.draft_capabilities(tmp_path, framing, [f"Capability number {n}" for n in range(1, 9)])) == 8


@pytest.mark.parametrize("caps", [[], ["   "], ["Assign chores", "assign  CHORES"]])
def test_no_capability_a_blank_one_or_two_with_one_id_is_refused(tmp_path: Path, caps: list[str]) -> None:
    framing = _framed(tmp_path)
    with pytest.raises(fr.FramingError):
        fr.draft_capabilities(tmp_path, framing, caps)
    assert _names(tmp_path) == [_FRAMING]


def test_one_existing_change_means_nothing_is_written_unless_overwrite(tmp_path: Path) -> None:
    framing = _framed(tmp_path)
    taken = tmp_path / "changes" / f"{_PRODUCT}-assign-chores-in-turn"
    taken.mkdir(parents=True)
    (taken / "proposal.md").write_text("a person's change\n")
    with pytest.raises(fr.FramingError, match="already exists"):
        fr.draft_capabilities(tmp_path, framing, _CAPS)
    assert not (tmp_path / "changes" / f"{_PRODUCT}-sign-up-and-join-a-household").exists()
    assert (taken / "proposal.md").read_text() == "a person's change\n"
    assert len(fr.draft_capabilities(tmp_path, framing, _CAPS, overwrite=True)) == 3
    assert (taken / "proposal.md").read_text() != "a person's change\n"


# ---- the inherited provenance line (D29) ----------------------------------------------------------


def test_an_inherited_answer_round_trips_with_the_framing_it_came_from() -> None:
    res = Resolution(
        status="answered",
        answer="Households forget chores.",
        origin="stated",
        channel="inherited",
        inherited_from=_FRAMING,
        at="2026-11-02",
    )
    line = _resolution_line(res)
    assert f"from {_FRAMING}" in line
    assert _parse_resolution(line.strip().lstrip("-").strip()) == res


def test_an_inherited_line_that_names_no_framing_is_not_an_answer() -> None:
    assert (
        _parse_resolution("**Answer** (stated · inherited · 2026-11-02): Households forget chores.") is None
    )


def test_answers_on_other_channels_read_exactly_as_before() -> None:
    res = _parse_resolution("**Answer** (user · cli · 2026-11-02): Finance cannot close the month.")
    assert res is not None and (res.channel, res.at, res.inherited_from) == ("cli", "2026-11-02", "")
