"""Regressions from the branch review: an answer is either written where the reader will find it
or refused — never reported as recorded when it was not — and the editor changes only what it
says it changes."""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from typer.testing import CliRunner

from orchestrator.cli import app
from orchestrator.intake import requirements as rq
from orchestrator.intake.intents import Intent, Resolution
from orchestrator.intake.openspec_source import change_to_intent

_PROBLEM_Q = "What problem does this solve?"


def _change(tmp_path: Path, proposal: str, name: str = "c") -> rq.LoadedChange:
    d = tmp_path / "changes" / name
    d.mkdir(parents=True)
    (d / "proposal.md").write_bytes(proposal.encode("utf-8"))
    return rq.load_change(name, root=tmp_path)


def _reread(change: rq.LoadedChange) -> Intent:
    return rq.load_change(change.change_id, root=change.directory.parent.parent).intent


# ---- B1: success means it was written where the reader looks ----------------------------------


@pytest.mark.parametrize(
    "proposal",
    [
        "# P\n\n## Why\nw\n\n## Open Questions\n  - Q?\n",  # an indented question
        "# P\n\n## Why\nw\n\n## Open Questions\n- Old?\n\n## Open Questions\n- Q?\n",  # last one wins
        "# P\n\n## Why\nw\n\n## Open Questions\n\n## Questions\n- Q?\n",  # empty section → the fallback name
    ],
)
def test_an_answer_lands_where_the_reader_will_find_it(tmp_path: Path, proposal: str) -> None:
    change = _change(tmp_path, proposal)
    rq.record_answers(change, [rq.AnswerRequest("Q?", "yes")], at="2026-10-06")
    assert _reread(change).resolutions["Q?"].answer == "yes"


def test_an_answer_that_cannot_be_placed_is_an_error_and_writes_nothing(tmp_path: Path) -> None:
    change = _change(tmp_path, "# P\n\n## Why\nw\n\n## Open Questions\n- Q?\n")
    stale = rq.LoadedChange(
        change.change_id, change.directory, change.proposal_path, "# P\n\n## Why\nw\n", change.intent
    )
    before = change.proposal_path.read_bytes()
    with pytest.raises(rq.RequirementsError, match="no non-empty `## Open Questions`"):
        rq.record_answers(stale, [rq.AnswerRequest("Q?", "yes")])
    assert change.proposal_path.read_bytes() == before


# ---- B2: a deferral names exactly one owner ---------------------------------------------------


@pytest.mark.parametrize("owner", ["bob smith", "bob\n- Injected question?\n## Evil", "@", "  ", "@ x y"])
def test_a_deferral_owner_must_be_one_token(tmp_path: Path, owner: str) -> None:
    change = _change(tmp_path, "# P\n\n## Why\nw\n\n## Open Questions\n- Q?\n")
    with pytest.raises(rq.RequirementsError):
        rq.record_answers(change, [rq.AnswerRequest("Q?", defer_to=owner)])
    assert "Deferred" not in change.proposal_path.read_text()


def test_a_deferral_to_one_owner_reads_back(tmp_path: Path) -> None:
    change = _change(tmp_path, "# P\n\n## Why\nw\n\n## Open Questions\n- Q?\n")
    rq.record_answers(change, [rq.AnswerRequest("Q?", defer_to="@finance-lead")], at="2026-10-06")
    res = _reread(change).resolutions["Q?"]
    assert (res.status, res.owner) == ("deferred", "finance-lead")


# ---- B3: re-answering replaces, even across a blank line ---------------------------------------


def test_a_new_answer_replaces_an_old_one_after_a_blank_line(tmp_path: Path) -> None:
    change = _change(
        tmp_path,
        "# P\n\n## Why\nw\n\n## Open Questions\n- Q?\n\n  - **Answer** (user · cli · 2026-01-01): old\n",
    )
    rq.record_answers(change, [rq.AnswerRequest("Q?", "new")], at="2026-10-06")
    assert _reread(change).resolutions["Q?"].answer == "new"
    assert "old" not in change.proposal_path.read_text()


# ---- B4: an answer cannot write a heading -----------------------------------------------------


@pytest.mark.parametrize("answer", ["## Open Questions\n- x", "# H", "  ### H"])
def test_a_why_answer_cannot_start_a_heading(tmp_path: Path, answer: str) -> None:
    change = _change(tmp_path, f"# P\n\n## Why\nw\n\n## Open Questions\n- {_PROBLEM_Q}\n")
    before = change.proposal_path.read_bytes()
    with pytest.raises(rq.RequirementsError, match="start a line with '#'"):
        rq.record_answers(change, [rq.AnswerRequest(_PROBLEM_Q, answer)])
    assert change.proposal_path.read_bytes() == before


def test_a_hash_in_the_middle_of_an_answer_is_fine(tmp_path: Path) -> None:
    change = _change(tmp_path, f"# P\n\n## Why\nw\n\n## Open Questions\n- {_PROBLEM_Q}\n")
    rq.record_answers(
        change, [rq.AnswerRequest(_PROBLEM_Q, "See issue #42 for the problem")], at="2026-10-06"
    )
    assert _reread(change).problem == "See issue #42 for the problem"


# ---- B5: a legacy proposal reads as it always did ----------------------------------------------


def test_subsection_names_keep_their_case_in_scope_and_description() -> None:
    md = (
        "# P\n\n## Why\n### Problem\nP.\n### Rollout Plan\nSlow.\n\n"
        "## What Changes\nDo it.\n### Rollout\nStaged.\n### Non-goals\n- X\n"
    )
    intent = change_to_intent("p", proposal_md=md)
    assert "### Rollout Plan" in intent.description and "### rollout" not in intent.description
    assert "### Rollout\nStaged." in intent.scope and "### rollout" not in intent.scope
    assert intent.non_goals == ["X"]


def test_a_legacy_what_changes_without_non_goals_is_untouched() -> None:
    md = "# P\n\n## Why\nw\n\n## What Changes\nDo it.\n### Rollout\nStaged.\n"
    assert change_to_intent("p", proposal_md=md).scope == "Do it.\n### Rollout\nStaged."


# ---- S1/S2: fence-aware, in place --------------------------------------------------------------


def test_a_heading_inside_a_code_fence_is_not_a_subsection(tmp_path: Path) -> None:
    proposal = (
        "# P\n\n## Why\nIntro.\n\n```\n### Problem\nthis is sample text\n```\n\n"
        f"## Open Questions\n- {_PROBLEM_Q}\n"
    )
    assert change_to_intent("p", proposal_md=proposal).problem == ""
    change = _change(tmp_path, proposal)
    rq.record_answers(change, [rq.AnswerRequest(_PROBLEM_Q, "A real problem.")], at="2026-10-06")
    md = change.proposal_path.read_text()
    assert "```\n### Problem\nthis is sample text\n```" in md  # the sample survives, fence closed
    assert _reread(change).problem == "A real problem."


def test_answering_changes_only_the_one_subsection(tmp_path: Path) -> None:
    proposal = textwrap.dedent(
        """\
        # P

        ## Why
        Lead text.

        ### Outcome
        Closes in a day.

        ### Background
          indented stays indented

        ## What Changes
        Do it.

        ### Non-goals
        - A
        - B

        ## Open Questions
        - What problem does this solve?
        - What is explicitly out of scope?
        """
    )
    change = _change(tmp_path, proposal)
    rq.record_answers(change, [rq.AnswerRequest(_PROBLEM_Q, "The problem.")], at="2026-10-06")
    after = change.proposal_path.read_text()
    # Problem went in before Outcome; nothing else in Why moved; Non-goals is as it was.
    assert "Lead text.\n\n### Problem\nThe problem.\n### Outcome\nCloses in a day." in after
    assert "### Background\n  indented stays indented" in after
    assert "### Non-goals\n- A\n- B\n" in after


def test_a_missing_what_changes_is_created_after_why_not_before(tmp_path: Path) -> None:
    change = _change(
        tmp_path,
        "# P\n\n## Why\nw\n\n## Open Questions\n- What is explicitly out of scope?\n",
    )
    rq.record_answers(change, [rq.AnswerRequest("What is explicitly out of scope?", "PDF")], at="2026-10-06")
    md = change.proposal_path.read_text()
    assert md.index("## Why") < md.index("## What Changes") < md.index("## Open Questions")
    assert _reread(change).non_goals == ["PDF"]


# ---- S3: line endings and the end of the file --------------------------------------------------


def test_crlf_is_kept(tmp_path: Path) -> None:
    change = _change(tmp_path, "# P\r\n\r\n## Why\r\nw\r\n\r\n## Open Questions\r\n- Q?\r\n")
    rq.record_answers(change, [rq.AnswerRequest("Q?", "yes")], at="2026-10-06")
    raw = change.proposal_path.read_bytes()
    assert b"\r\n" in raw and b"\n" not in raw.replace(b"\r\n", b"")
    assert _reread(change).resolutions["Q?"].answer == "yes"


def test_a_file_with_no_trailing_newline_gets_none_added(tmp_path: Path) -> None:
    change = _change(tmp_path, "# P\n\n## Why\nw\n\n## Open Questions\n- Q?")
    rq.record_answers(change, [rq.AnswerRequest("Q?", "yes")], at="2026-10-06")
    assert not change.proposal_path.read_text().endswith("\n")


def test_a_proposal_that_starts_with_a_heading_gains_no_leading_blank_line(tmp_path: Path) -> None:
    change = _change(tmp_path, "## Why\nw\n\n## Open Questions\n- Q?\n")
    rq.record_answers(change, [rq.AnswerRequest("Q?", "yes")], at="2026-10-06")
    assert change.proposal_path.read_text().startswith("## Why\n")


def test_a_write_replaces_the_file_and_leaves_no_temp_file(tmp_path: Path) -> None:
    change = _change(tmp_path, "# P\n\n## Why\nw\n\n## Open Questions\n- Q?\n")
    rq.record_answers(change, [rq.AnswerRequest("Q?", "yes")], at="2026-10-06")
    assert [p.name for p in change.directory.iterdir()] == ["proposal.md"]


# ---- S5: the answers file --------------------------------------------------------------------


@pytest.mark.parametrize("value", ["yes", "no", "1", "[a, b]", "true"])
def test_an_unquoted_yaml_answer_that_is_not_text_is_refused(tmp_path: Path, value: str) -> None:
    f = tmp_path / "a.yaml"
    f.write_text(f"answers:\n  - question: Q?\n    answer: {value}\n")
    with pytest.raises(rq.RequirementsError, match="must be text"):
        rq.load_answers_file(f)


def test_a_quoted_yes_is_a_valid_answer(tmp_path: Path) -> None:
    f = tmp_path / "a.yaml"
    f.write_text("answers:\n  - question: Q?\n    answer: 'yes'\n")
    assert rq.load_answers_file(f)[0].answer == "yes"


# ---- S6: a broken rules file is exit 2, not the gate's exit 1 ---------------------------------


def test_a_malformed_rules_file_exits_2(tmp_path: Path) -> None:
    change = _change(tmp_path, "# P\n\n## Why\nw\n\n## Open Questions\n- Q?\n", "c")
    for body in ("rules: [", "- not a mapping\n", "rules:\n  - 5\n"):
        rules = tmp_path / "bad.yaml"
        rules.write_text(body)
        res = CliRunner().invoke(
            app, ["openspec", "check", "c", "--root", str(tmp_path), "--rules", str(rules)]
        )
        assert res.exit_code == 2, (body, res.output)
    assert change  # the change itself is fine


# ---- S9: an "answer" with no text is not an answer --------------------------------------------


def test_a_blank_answer_does_not_resolve_a_question() -> None:
    intent = Intent(
        id="i",
        title="t",
        open_questions=["Q?"],
        resolutions={"Q?": Resolution(status="answered", answer="  ")},
    )
    from orchestrator.intake.intents import question_states

    assert question_states(intent) == ([], [], ["Q?"])


# ---- S7: no model, shown from the outside -----------------------------------------------------


def test_no_new_surface_imports_a_model_client(tmp_path: Path) -> None:
    """Run every new surface in a fresh interpreter and look at what it loaded. A patched entry
    point proves only that one route is closed; an import that never happened proves them all."""
    script = textwrap.dedent(
        f"""
        import sys
        from pathlib import Path
        from typer.testing import CliRunner
        from orchestrator.cli import app
        from orchestrator.plugin.server import requirements_answer, requirements_check
        root = Path({str(tmp_path)!r})
        r = CliRunner()
        draft = ["openspec", "draft", "--idea", "Let finance export invoices", "--out", str(root)]
        assert r.invoke(app, draft).exit_code == 0
        change = next((root / "changes").iterdir())
        r.invoke(app, ["openspec", "check", change.name, "--root", str(root)])
        r.invoke(app, ["openspec", "answer", change.name, "--root", str(root),
                       "--question", "What problem does this solve?", "--answer", "x"])
        requirements_check(str(change))
        requirements_answer(str(change), "Who has this problem?", answer="y")
        bad = [m for m in ("litellm", "anthropic", "openai") if m in sys.modules]
        print("LOADED:" + ",".join(bad))
        """
    )
    out = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=300, check=False
    )
    assert out.returncode == 0, out.stderr[-2000:]
    assert out.stdout.strip().endswith("LOADED:"), out.stdout[-500:]
