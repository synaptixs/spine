"""P9a of SSPN-297 (SSPN-319): a greenfield idea → one framing change → N capability changes.

The framing change asks the four why-questions once. Once it passes `check`, each capability
becomes its own change that inherits the framing's problem, users and non-goal — recorded as
``inherited`` with the framing's id, never as the person's own answer — and asks only its own
outcome (D23, D28, D29). At most eight, all or nothing (D30). No model anywhere here."""

from __future__ import annotations

import subprocess
import sys
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


def test_a_later_batch_that_takes_the_framing_past_eight_is_refused_with_the_total(tmp_path: Path) -> None:
    # B133 (SSPN-322): the bound is per framing, not per call — two calls of five made ten.
    framing = _framed(tmp_path)
    fr.draft_capabilities(tmp_path, framing, [f"Capability number {n}" for n in range(1, 6)])
    before = _names(tmp_path)
    with pytest.raises(fr.FramingError, match=r"already has 5.*9 in all.*at most 8"):
        fr.draft_capabilities(tmp_path, framing, [f"Capability number {n}" for n in range(6, 10)])
    assert _names(tmp_path) == before


def test_a_later_batch_up_to_eight_in_all_is_drafted(tmp_path: Path) -> None:
    framing = _framed(tmp_path)
    fr.draft_capabilities(tmp_path, framing, [f"Capability number {n}" for n in range(1, 6)])
    fr.draft_capabilities(tmp_path, framing, [f"Capability number {n}" for n in range(6, 9)])
    assert len(_names(tmp_path)) == 1 + 8


def test_a_capability_replaced_with_overwrite_counts_once(tmp_path: Path) -> None:
    framing = _framed(tmp_path)
    eight = [f"Capability number {n}" for n in range(1, 9)]
    fr.draft_capabilities(tmp_path, framing, eight)
    assert len(fr.draft_capabilities(tmp_path, framing, eight[:2], overwrite=True)) == 2
    assert len(_names(tmp_path)) == 1 + 8


def test_a_change_that_only_shares_the_prefix_is_not_one_of_the_framings(tmp_path: Path) -> None:
    # The recorded `inherited · from <framing>` decides it, never the id.
    framing = _framed(tmp_path)
    for n in range(1, 9):
        own = tmp_path / "changes" / f"{_PRODUCT}-a-persons-change-{n}"
        own.mkdir(parents=True)
        (own / "proposal.md").write_text("a person's change\n")
    (tmp_path / "changes" / f"{_PRODUCT}-not-a-change").mkdir()
    assert len(fr.draft_capabilities(tmp_path, framing, _CAPS)) == 3


def test_a_capability_change_renamed_since_still_counts_against_its_framing(tmp_path: Path) -> None:
    framing = _framed(tmp_path)
    drafted = fr.draft_capabilities(tmp_path, framing, [f"Capability number {n}" for n in range(1, 9)])
    (tmp_path / "changes" / drafted[0]).rename(tmp_path / "changes" / "renamed-by-hand")
    assert "renamed-by-hand" in fr.drafted_from(tmp_path, _FRAMING)
    with pytest.raises(fr.FramingError, match=r"already has 8.*9 in all"):
        fr.draft_capabilities(tmp_path, framing, ["One capability more"])


def test_a_root_with_no_changes_has_drafted_nothing(tmp_path: Path) -> None:
    assert fr.drafted_from(tmp_path, _FRAMING) == []


def test_changes_drafted_from_another_framing_with_a_longer_id_are_not_counted(tmp_path: Path) -> None:
    # "A chore app" and "A chore app for flats": the second's changes start with the first's prefix.
    def answered(idea: str) -> rq.LoadedChange:
        change_id, _ = fr.draft_framing(tmp_path, idea)
        requests = [rq.AnswerRequest(q, a) for q, a in _ANSWERS.items()]
        rq.record_answers(rq.load_change(change_id, root=tmp_path), requests, channel="cli", at="2026-11-02")
        return rq.load_change(change_id, root=tmp_path)

    short, longer = answered("A chore app"), answered("A chore app for flats")
    assert longer.change_id.startswith(short.change_id.removesuffix(fr.FRAMING_SUFFIX) + "-")
    fr.draft_capabilities(tmp_path, longer, [f"Capability number {n}" for n in range(1, 9)])
    assert fr.drafted_from(tmp_path, short.change_id) == []
    assert len(fr.draft_capabilities(tmp_path, short, _CAPS)) == 3
    assert len(fr.drafted_from(tmp_path, short.change_id)) == 3


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


# ---- the PR 3 review's fix-forward (Y1–Y9, Y17) -------------------------------------------------


def test_a_deferred_non_goal_is_inherited_as_the_deferral_not_an_empty_answer(tmp_path: Path) -> None:
    # Y1: a deferral passes the framing's gate; copying its empty field as an answer made every
    # capability fail `check` on a question it never had.
    change_id, _ = fr.draft_framing(tmp_path, _IDEA)
    requests = [
        rq.AnswerRequest(q, a) for q, a in _ANSWERS.items() if q != "What is explicitly out of scope?"
    ]
    requests.append(rq.AnswerRequest("What is explicitly out of scope?", defer_to="@pat"))
    rq.record_answers(rq.load_change(change_id, root=tmp_path), requests, channel="cli", at="2026-11-02")
    framing = rq.load_change(change_id, root=tmp_path)
    assert _passes(framing)
    [cap_id] = fr.draft_capabilities(tmp_path, framing, ["Assign chores in turn"], at="2026-11-02")
    cap = rq.load_change(cap_id, root=tmp_path)
    empty = [ln for ln in cap.proposal_md.splitlines() if "**Answer**" in ln and ln.rstrip().endswith("):")]
    assert not empty
    res = cap.intent.resolutions["What is explicitly out of scope?"]
    own = framing.intent.resolutions["What is explicitly out of scope?"]
    assert (res.status, res.owner) == ("deferred", own.owner) and own.owner.lstrip("@") == "pat"
    rq.record_answers(cap, [rq.AnswerRequest(_OUTCOME_Q, "Each member sees whose turn it is.")])
    assert _passes(rq.load_change(cap_id, root=tmp_path))
    assert fr.drafted_from(tmp_path, change_id) == [cap_id]


def test_a_capability_with_no_ascii_letters_gets_an_id_of_its_own(tmp_path: Path) -> None:
    # Y4: they all slugged to `intent` and clashed, so a non-Latin framing paid and failed.
    a, b = fr.capability_slug("Регистрация"), fr.capability_slug("Напоминание")
    assert a != b and a.startswith("capability-") and b.startswith("capability-")
    assert fr.capability_slug("Регистрация") == a
    framing = _framed(tmp_path)
    assert len(fr.draft_capabilities(tmp_path, framing, ["Регистрация", "Напоминание"])) == 2


def test_text_mixing_ascii_and_another_script_gets_an_id_of_its_own(tmp_path: Path) -> None:
    # Re-review S1: "API для регистрации" and "API для входа" both slugged to `api`.
    a, b = fr.capability_slug("API для регистрации"), fr.capability_slug("API для входа")
    assert a != b and a.startswith("api-") and b.startswith("api-")
    # Folded as the ASCII part is: case and punctuation do not make a second capability.
    assert fr.capability_slug("Регистрация.") == fr.capability_slug("регистрация")
    # Text that is all ASCII letters keeps its plain slug, an em dash or curly quote included.
    assert fr.capability_slug("Assign chores — in turn") == "assign-chores-in-turn"
    # A lone surrogate is hashed, never raised on.
    assert fr.capability_slug("Вход \ud800").startswith("capability-")


def test_drafted_from_does_not_depend_on_the_working_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Y5: a same-named folder in the current directory was read instead, and the count dropped.
    framing = _framed(tmp_path / "root")
    [cap_id] = fr.draft_capabilities(tmp_path / "root", framing, ["Assign chores in turn"])
    elsewhere = tmp_path / "elsewhere" / cap_id
    elsewhere.mkdir(parents=True)
    (elsewhere / "proposal.md").write_text("# Proposal: not this one\n")
    monkeypatch.chdir(tmp_path / "elsewhere")
    assert fr.drafted_from(tmp_path / "root", _FRAMING) == [cap_id]


def test_a_capability_whose_id_is_the_framings_is_refused_even_with_overwrite(tmp_path: Path) -> None:
    # Y6: "Framing" slugs to the framing's own id; --overwrite replaced the framing with it.
    framing = _framed(tmp_path)
    before = framing.proposal_path.read_text()
    with pytest.raises(fr.FramingError, match="framing's own id"):
        fr.draft_capabilities(tmp_path, framing, ["Framing"], overwrite=True)
    assert framing.proposal_path.read_text() == before


def test_a_framing_with_an_option_not_yet_chosen_is_not_ready(tmp_path: Path) -> None:
    # Y7: readiness is `check`, options included (D28) — not a weaker gate of its own.
    from orchestrator.intake import options as op

    framing = _framed(tmp_path)
    op.write_options(
        framing,
        [
            op.Option(title="A web app", approach="Build it.", source="user"),
            op.Option(title="A chat bot", approach="Build that.", source="user"),
        ],
    )
    framing = rq.load_change(framing.change_id, root=tmp_path)
    assert not rq.check_intent(framing.intent, change_id=_FRAMING, options=op.load_options(framing)).passes
    assert fr.readiness(framing) is not None
    with pytest.raises(fr.FramingNotReadyError):
        fr.draft_capabilities(tmp_path, framing, _CAPS)


def test_a_capability_id_that_is_a_symlink_is_refused_and_nothing_is_written(tmp_path: Path) -> None:
    # Y9: --overwrite wrote through a linked directory; a dangling link failed half-way through.
    framing = _framed(tmp_path)
    target = tmp_path / "someone-elses"
    target.mkdir()
    (tmp_path / "changes" / f"{_PRODUCT}-assign-chores-in-turn").symlink_to(target)
    (tmp_path / "changes" / f"{_PRODUCT}-send-a-reminder-on-the-day").symlink_to(tmp_path / "gone")
    for overwrite in (False, True):
        with pytest.raises(fr.FramingError, match="symlink"):
            fr.draft_capabilities(tmp_path, framing, _CAPS, overwrite=overwrite)
    assert list(target.iterdir()) == []
    assert not (tmp_path / "changes" / f"{_PRODUCT}-sign-up-and-join-a-household").exists()


def test_the_input_is_refused_before_the_framing_is_read_for_readiness(tmp_path: Path) -> None:
    # Y17-2: a bad batch is the caller's input (exit 2), whatever state the framing is in.
    framing = _framed(tmp_path, answered=False)
    with pytest.raises(fr.FramingError) as err:
        fr.draft_capabilities(tmp_path, framing, [f"Capability number {n}" for n in range(1, 10)])
    assert type(err.value) is fr.FramingError


def test_a_crlf_design_reads_and_writes_with_its_own_line_endings(tmp_path: Path) -> None:
    # Y17-3
    framing = _framed(tmp_path)
    framing.design_path.write_bytes(
        b"# Design\r\n\r\n## Capabilities\r\n\r\n- Assign chores\r\n  - Source: user\r\n"
    )
    block = fr.load_capabilities(framing)
    assert block.items == (fr.Capability("Assign chores", "user"),) and not block.problems
    fr.write_capabilities(framing, [*block.items, fr.Capability("Send a reminder")])
    raw = framing.design_path.read_bytes()
    assert raw.count(b"\n") == raw.count(b"\r\n") and b"- Send a reminder\r\n" in raw


def test_a_long_capability_is_cut_to_six_words_in_its_id_and_eighty_characters_in_its_title(
    tmp_path: Path,
) -> None:
    # Y17-4 (D30's truncation)
    framing = _framed(tmp_path)
    long = "Let every member swap a chore turn with another member " + "x" * 60
    [cap_id] = fr.draft_capabilities(tmp_path, framing, [long])
    assert cap_id == f"{_PRODUCT}-let-every-member-swap-a-chore"
    title = rq.load_change(cap_id, root=tmp_path).intent.title
    assert len(title) == 80 and title.endswith("...")


def test_the_inherited_answers_carry_the_date_they_were_drafted(tmp_path: Path) -> None:
    # Y17-5
    framing = _framed(tmp_path)
    [cap_id] = fr.draft_capabilities(tmp_path, framing, ["Assign chores in turn"], at="2026-10-31")
    cap = rq.load_change(cap_id, root=tmp_path)
    inherited = [r for r in cap.intent.resolutions.values() if r.channel == "inherited"]
    assert len(inherited) == 3 and {r.at for r in inherited} == {"2026-10-31"}


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


# ---- the `## Capabilities` block (P9b, SSPN-320, D27) ---------------------------------------------


def test_a_written_block_reads_back_with_each_source(tmp_path: Path) -> None:
    framing = _framed(tmp_path)
    items = [
        fr.Capability("Assign chores in turn", "generated · m · $0.0421"),
        fr.Capability("Send a reminder on the day"),
    ]
    fr.write_capabilities(framing, items)
    block = fr.load_capabilities(framing)
    assert block.present and block.items == tuple(items) and block.problems == ()


def test_a_hand_written_block_parses_and_a_stray_line_is_reported_not_dropped(tmp_path: Path) -> None:
    framing = _framed(tmp_path)
    framing.design_path.write_text(
        "# Design\n\n## Capabilities\n\n- Assign chores in turn\n* Send a reminder\nnot a bullet\n\n"
        "```\n- inside a fence\n```\n\n## Risks\n- none\n"
    )
    block = fr.load_capabilities(framing)
    assert [c.text for c in block.items] == ["Assign chores in turn", "Send a reminder"]
    assert [c.source for c in block.items] == ["", ""]
    assert len(block.problems) == 1 and "not a bullet" in block.problems[0]


def test_writing_replaces_the_block_and_keeps_the_rest_of_design(tmp_path: Path) -> None:
    framing = _framed(tmp_path)
    framing.design_path.write_text("# Design\n\nIntro.\n\n## Capabilities\n\n- Old one\n\n## Risks\n- none\n")
    fr.write_capabilities(framing, [fr.Capability("New one")])
    text = framing.design_path.read_text()
    assert "Old one" not in text and "- New one" in text
    assert "Intro." in text and "## Risks\n- none" in text


def test_a_design_that_is_not_utf8_is_refused_on_write(tmp_path: Path) -> None:
    framing = _framed(tmp_path)
    framing.design_path.write_bytes("# Design\n\nCafé\n".encode("latin-1"))
    with pytest.raises(fr.FramingError, match="UTF-8"):
        fr.write_capabilities(framing, [fr.Capability("A")])


def test_a_capability_that_cannot_read_back_is_refused_before_writing(tmp_path: Path) -> None:
    framing = _framed(tmp_path)
    with pytest.raises(fr.FramingError, match="blank, or not one line"):
        fr.write_capabilities(framing, [fr.Capability("   ")])
    assert not framing.design_path.exists()


def test_capabilities_from_the_block_are_what_frame_drafts_when_none_are_listed(tmp_path: Path) -> None:
    framing = _framed(tmp_path)
    fr.write_capabilities(framing, [fr.Capability("Assign chores in turn"), fr.Capability("Send a reminder")])
    assert fr.listed_capabilities(framing) == ["Assign chores in turn", "Send a reminder"]


def test_no_block_or_a_block_with_problems_lists_nothing_and_says_why(tmp_path: Path) -> None:
    framing = _framed(tmp_path)
    with pytest.raises(fr.FramingError, match="propose some"):
        fr.listed_capabilities(framing)
    framing.design_path.write_text("## Capabilities\n\nstray\n")
    with pytest.raises(fr.FramingError, match="stray"):
        fr.listed_capabilities(framing)


def test_framing_imports_no_model_client() -> None:
    code = (
        "import sys, tempfile, pathlib\n"
        "from orchestrator.intake import framing as fr\n"
        "from orchestrator.intake import requirements as rq\n"
        "root = pathlib.Path(tempfile.mkdtemp())\n"
        "cid, _ = fr.draft_framing(root, 'An idea')\n"
        "fr.write_capabilities(rq.load_change(cid, root=root), [fr.Capability('A')])\n"
        "bad = [m for m in sys.modules if m.split('.')[0] in ('litellm', 'anthropic', 'openai')]\n"
        "print(','.join(bad))\n"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == ""


def test_a_framing_whose_options_cannot_be_read_is_not_ready(tmp_path: Path) -> None:
    # Y7: `check` refuses a design with `## Options` it cannot read; readiness says so, never raises.
    framing = _framed(tmp_path)
    framing.design_path.write_bytes("## Options\n\n### Café\n- **Approach:** x\n".encode("latin-1"))
    reason = fr.readiness(rq.load_change(framing.change_id, root=tmp_path))
    assert reason is not None and "not UTF-8" in reason
