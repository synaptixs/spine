"""P4 of SSPN-297 (SSPN-301): the choice between options, recorded like any answer (D9).

When a change's ``design.md`` has ``## Options``, the strict gate needs the fixed question
"Which option do we take?" answered with one of them (``option_chosen``). The answer is recorded
under the question in ``proposal.md`` with its provenance, and ``## Decision`` in ``design.md``
shows it. A change with no options checks exactly as it did before."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from orchestrator.intake import options as op
from orchestrator.intake import requirements as rq

_COMPLETE = (
    "# Proposal: Export\n\n## Why\nFinance needs invoices exported as CSV.\n"
    "### Problem\nFinance cannot close.\n### Users\n- Finance\n"
    "### Outcome\nCloses within 1 day.\n\n## What Changes\nDo it.\n### Non-goals\n- PDF\n"
)
_OPTS = [
    op.Option(title="Reuse the editor", approach="Extend it.", anchors=("record_answers",), source="user"),
    op.Option(title="A new command", approach="Add one.", creates=("cmd.py",), source="user"),
]


def _change(
    tmp_path: Path, proposal: str = _COMPLETE, options: list[op.Option] | None = None
) -> rq.LoadedChange:
    directory = tmp_path / "changes" / "c"
    directory.mkdir(parents=True)
    (directory / "proposal.md").write_text(proposal)
    change = rq.load_change("c", root=tmp_path)
    if options:
        op.write_options(change, options)
    return change


def _reload(change: rq.LoadedChange) -> rq.LoadedChange:
    return rq.load_change(str(change.directory))


def _check(change: rq.LoadedChange) -> rq.CheckReport:
    c = _reload(change)
    return rq.check_intent(c.intent, change_id=c.change_id, options=op.load_options(c))


def _rules(report: rq.CheckReport) -> set[str]:
    return {f.rule_id for f in report.findings}


# ---- no options: exactly as before --------------------------------------------------------------


@pytest.mark.parametrize("proposal", [_COMPLETE, "# P\n\n## Why\nw\n\n## Open Questions\n- Q?\n"])
def test_a_change_without_options_checks_exactly_as_before(tmp_path: Path, proposal: str) -> None:
    change = _change(tmp_path, proposal)
    before = rq.check_intent(change.intent, change_id=change.change_id).to_dict()
    after = rq.check_intent(
        change.intent, change_id=change.change_id, options=op.load_options(change)
    ).to_dict()
    assert json.dumps(after) == json.dumps(before)


def test_a_design_md_without_an_options_section_changes_nothing(tmp_path: Path) -> None:
    change = _change(tmp_path)
    change.design_path.write_text("# Design\n\n## Context\nc\n")
    assert _check(change).passes and "option_chosen" not in _rules(_check(change))


# ---- with options: the gate needs a choice -------------------------------------------------------


def test_with_options_and_no_choice_the_gate_does_not_pass(tmp_path: Path) -> None:
    report = _check(_change(tmp_path, options=_OPTS))
    assert not report.passes and "option_chosen" in _rules(report)
    (item,) = [i for i in report.to_dict()["open_items"] if i["rule"] == "option_chosen"]
    assert item["severity"] == "needs_input"
    assert item["fix"]["question"] == rq.CHOICE_QUESTION
    assert item["fix"]["mcp"] == {"tool": "requirements_answer", "question": rq.CHOICE_QUESTION}


def test_choosing_by_title_records_the_answer_and_the_decision(tmp_path: Path) -> None:
    change = _change(tmp_path, options=_OPTS)
    rq.record_answers(change, [rq.AnswerRequest(rq.CHOICE_QUESTION, "a new COMMAND")], at="2026-11-01")
    after = _reload(change)
    res = after.intent.resolutions[rq.CHOICE_QUESTION]
    assert (res.answer, res.origin, res.channel) == ("Option 2 — A new command", "user", "cli")
    design = after.design_path.read_text()
    assert "## Decision" in design and "Option 2 — A new command" in design
    assert design.index("## Options") < design.index("## Decision")
    assert op.load_options(after).options == tuple(_OPTS)  # the options are untouched
    assert _check(change).passes


@pytest.mark.parametrize("answer", ["1", "Option 1", "option 1 — reuse the editor", "Reuse the editor"])
def test_an_option_can_be_named_by_number_or_title(tmp_path: Path, answer: str) -> None:
    change = _change(tmp_path, options=_OPTS)
    rq.record_answers(change, [rq.AnswerRequest(rq.CHOICE_QUESTION, answer)], at="2026-11-01")
    assert _reload(change).intent.resolutions[rq.CHOICE_QUESTION].answer == "Option 1 — Reuse the editor"


@pytest.mark.parametrize("answer", ["3", "Something else", "<option title>"])
def test_a_choice_that_names_no_option_writes_nothing(tmp_path: Path, answer: str) -> None:
    change = _change(tmp_path, options=_OPTS)
    proposal, design = change.proposal_path.read_bytes(), change.design_path.read_bytes()
    with pytest.raises(rq.RequirementsError):
        rq.record_answers(change, [rq.AnswerRequest(rq.CHOICE_QUESTION, answer)])
    assert change.proposal_path.read_bytes() == proposal and change.design_path.read_bytes() == design


def test_the_choice_question_on_a_change_with_no_options_is_an_error(tmp_path: Path) -> None:
    change = _change(tmp_path)
    with pytest.raises(rq.RequirementsError, match="no `## Options`"):
        rq.record_answers(change, [rq.AnswerRequest(rq.CHOICE_QUESTION, "1")])
    assert not change.design_path.exists()


def test_a_deferred_choice_resolves_the_question_but_not_the_choice(tmp_path: Path) -> None:
    change = _change(tmp_path, options=_OPTS)
    rq.record_answers(change, [rq.AnswerRequest(rq.CHOICE_QUESTION, defer_to="@lead")], at="2026-11-01")
    report = _check(change)
    assert "option_chosen" in _rules(report) and "questions_resolved" not in _rules(report)
    (finding,) = [f for f in report.findings if f.rule_id == "option_chosen"]
    assert "none is chosen" in finding.message  # a deferral is not a choice, and is not called one
    assert "## Decision" not in _reload(change).design_path.read_text()


def test_a_choice_of_an_option_since_removed_does_not_count(tmp_path: Path) -> None:
    change = _change(tmp_path, options=_OPTS)
    rq.record_answers(change, [rq.AnswerRequest(rq.CHOICE_QUESTION, "2")], at="2026-11-01")
    op.write_options(_reload(change), _OPTS[:1])
    report = _check(change)
    (item,) = [i for i in report.findings if i.rule_id == "option_chosen"]
    assert "A new command" in item.message and "no longer" in item.message


def test_choosing_again_replaces_the_decision(tmp_path: Path) -> None:
    change = _change(tmp_path, options=_OPTS)
    rq.record_answers(change, [rq.AnswerRequest(rq.CHOICE_QUESTION, "2")], at="2026-11-01")
    rq.record_answers(_reload(change), [rq.AnswerRequest(rq.CHOICE_QUESTION, "1")], at="2026-11-02")
    design = _reload(change).design_path.read_text()
    assert design.count("## Decision") == 1 and "Option 1 — Reuse the editor" in design
    assert "Option 2 — A new command" not in design.split("## Decision", 1)[1]


def test_a_choice_over_mcp_is_recorded_as_relayed(tmp_path: Path) -> None:
    change = _change(tmp_path, options=_OPTS)
    rq.record_answers(change, [rq.AnswerRequest(rq.CHOICE_QUESTION, "1")], channel="mcp", at="2026-11-01")
    res = _reload(change).intent.resolutions[rq.CHOICE_QUESTION]
    assert (res.origin, res.channel) == ("relayed", "mcp")
    assert "relayed · mcp" in _reload(change).design_path.read_text()


def test_problems_in_the_options_block_are_named_in_the_notes(tmp_path: Path) -> None:
    change = _change(tmp_path)
    change.design_path.write_text("## Options\n\n### T\n- **Colour:** red\n")
    notes = _check(change).notes
    assert any("## Options" in n and "no Approach" in n for n in notes)


# ---- the three production callers pass the options ----------------------------------------------


def test_the_cli_check_and_answer_carry_the_choice(tmp_path: Path) -> None:
    from typer.testing import CliRunner

    from orchestrator.cli import app

    change = _change(tmp_path, options=_OPTS)
    runner = CliRunner()
    checked = runner.invoke(app, ["openspec", "check", "c", "--root", str(tmp_path)])
    assert checked.exit_code == 1 and "option_chosen" in checked.stdout
    done = runner.invoke(
        app,
        [
            "openspec",
            "answer",
            "c",
            "--root",
            str(tmp_path),
            "--question",
            rq.CHOICE_QUESTION,
            "--answer",
            "1",
        ],
    )
    assert done.exit_code == 0, done.output
    again = runner.invoke(app, ["openspec", "check", "c", "--root", str(tmp_path)])
    assert again.exit_code == 0, again.output
    assert change.design_path.read_text().count("## Decision") == 1


def test_the_mcp_check_and_answer_carry_the_choice(tmp_path: Path) -> None:
    from orchestrator.plugin.server import requirements_answer, requirements_check

    change = _change(tmp_path, options=_OPTS)
    assert "option_chosen" in {i["rule"] for i in requirements_check(str(change.directory))["open_items"]}
    deferred = requirements_answer(str(change.directory), rq.CHOICE_QUESTION, defer_to="@lead")
    assert deferred["passes"] is False  # the gate standing it returns counts the options too
    out = requirements_answer(str(change.directory), rq.CHOICE_QUESTION, answer="Reuse the editor")
    assert out["passes"] is True, out
    assert requirements_check(str(change.directory))["passes"] is True


# ---- P7a (SSPN-310, D16): the chosen option reaches the build ------------------------------------

_MARKED = [
    op.Option(title="Reuse the editor", approach="Extend it.", anchors=("record_answers",), source="user"),
    op.Option(
        title="A new command",
        approach="CHOSEN-APPROACH-MARKER add one command.",
        anchors=("openspec_check", "src/cli/build.py"),
        creates=("cmd.py",),
        risks="Two surfaces.",
        source="user",
    ),
]


def _chosen(tmp_path: Path, answer: str | None = "2") -> rq.LoadedChange:
    change = _change(tmp_path, options=_MARKED)
    if answer is not None:
        rq.record_answers(change, [rq.AnswerRequest(rq.CHOICE_QUESTION, answer)], at="2026-11-02")
    return _reload(change)


def _scope_without_design(change: rq.LoadedChange) -> str:
    from orchestrator.intake.openspec_source import change_to_intent

    return change_to_intent(change.change_id, proposal_md=change.proposal_md).scope


def test_the_chosen_options_approach_anchors_and_creates_join_the_scope(tmp_path: Path) -> None:
    scope = _chosen(tmp_path).intent.scope
    assert "Approach (chosen: Option 2 — A new command): CHOSEN-APPROACH-MARKER add one command." in scope
    assert "Anchors: openspec_check, src/cli/build.py" in scope
    assert "Creates: cmd.py" in scope
    assert "Reuse the editor" not in scope  # only the chosen one


async def test_the_adapter_hands_the_build_the_chosen_option(tmp_path: Path) -> None:
    """What `sdlc plan --source openspec://` reads, and what the spec writer is shown."""
    from orchestrator.intake.openspec_source import OpenSpecSourceAdapter, OpenSpecSourceConfig
    from orchestrator.intake.specs import SpecWriter

    _chosen(tmp_path)
    adapter = OpenSpecSourceAdapter(OpenSpecSourceConfig(root=tmp_path))
    tree = await adapter.fetch_tree("c")
    [intent] = adapter.structured_intents(tree.documents)
    assert "CHOSEN-APPROACH-MARKER" in intent.scope
    assert "CHOSEN-APPROACH-MARKER" in SpecWriter(llm=None)._build_user_message(intent)  # type: ignore[arg-type]


def test_the_check_reads_the_choice_the_way_the_build_does(tmp_path: Path) -> None:
    import asyncio

    from orchestrator.intake.openspec_source import OpenSpecSourceAdapter, OpenSpecSourceConfig

    checked = _chosen(tmp_path).intent
    adapter = OpenSpecSourceAdapter(OpenSpecSourceConfig(root=tmp_path))
    tree = asyncio.run(adapter.fetch_tree("c"))
    [built] = adapter.structured_intents(tree.documents)
    assert checked.scope == built.scope


@pytest.mark.parametrize("answer", [None, "deferred"])
def test_no_choice_adds_nothing_to_the_scope(tmp_path: Path, answer: str | None) -> None:
    change = _change(tmp_path, options=_MARKED)
    if answer == "deferred":
        rq.record_answers(change, [rq.AnswerRequest(rq.CHOICE_QUESTION, defer_to="@lead")], at="2026-11-02")
    change = _reload(change)
    assert change.intent.scope == _scope_without_design(change)
    assert "chosen" not in change.intent.scope


def test_a_choice_of_an_option_since_removed_adds_nothing(tmp_path: Path) -> None:
    change = _chosen(tmp_path)
    op.write_options(change, [_MARKED[0], op.Option(title="Something else", approach="x", source="user")])
    change = _reload(change)
    assert "CHOSEN-APPROACH-MARKER" not in change.intent.scope
    assert change.intent.scope == _scope_without_design(change)


def test_a_change_without_options_reads_exactly_as_before(tmp_path: Path) -> None:
    change = _reload(_change(tmp_path))
    change.design_path.write_text("# Design\n\nProse only, no options.\n")
    change = _reload(change)
    assert change.intent.scope == _scope_without_design(change)


def test_a_proposed_choice_is_not_a_choice_and_adds_nothing() -> None:
    """Only an answered choice counts, as the gate counts it: a proposal is not one."""
    from orchestrator.intake.intents import Resolution
    from orchestrator.intake.openspec_source import _chosen_option

    design = op.set_options("# Design\n", _MARKED)
    label = "Option 2 — A new command"
    assert _chosen_option(design, {rq.CHOICE_QUESTION: Resolution(status="proposed", answer=label)}) == []
    assert _chosen_option(design, {rq.CHOICE_QUESTION: Resolution(status="answered", answer=label)})
