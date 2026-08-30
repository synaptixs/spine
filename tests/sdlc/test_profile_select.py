"""Issue type → workflow profile (Phase 3).

The mapping is a lookup and must stay one: a model choosing which research a ticket gets would
make the Evidence unreproducible at a commit, and that reproducibility is what everything
downstream rests on.
"""

from __future__ import annotations

import pytest

from orchestrator.sdlc.profile_select import _BY_TYPE, is_bug, select_profile


@pytest.mark.parametrize(
    ("issue_type", "expected"),
    [
        ("Bug", "bug"),
        ("bug", "bug"),
        ("  BUG  ", "bug"),
        ("Defect", "bug"),
        ("Incident", "bug"),
        ("Story", "enhancement"),
        ("Task", "enhancement"),
        ("New Feature", "enhancement"),
        ("Improvement", "enhancement"),
    ],
)
def test_the_mapping_is_case_and_whitespace_insensitive(issue_type: str, expected: str) -> None:
    """A tracker's issue type is a string someone typed into a dropdown."""
    selection = select_profile(issue_type)
    assert selection.profile == expected
    assert selection.matched


@pytest.mark.parametrize("issue_type", ["", "   ", "Spike", "Chore", "Epic", "whatever-we-invented"])
def test_an_unmapped_type_falls_back_to_default_and_says_so(issue_type: str) -> None:
    """Erroring would fail a run because a team renamed a dropdown, which is not a reason to
    refuse work. A *silent* fallback is how "why did it skip RCA?" becomes unanswerable, so the
    reason travels with the choice."""
    selection = select_profile(issue_type)
    assert selection.profile == "default"
    assert not selection.matched
    assert "default" in selection.reason
    if issue_type.strip():
        assert issue_type.strip() in selection.reason


def test_a_mapping_naming_a_profile_nobody_has_falls_back() -> None:
    """A repo may delete or rename a profile. The run should continue on the default and say
    what happened, rather than raising on a file that is simply absent."""
    selection = select_profile("Bug", available=("default",))
    assert selection.profile == "default"
    assert not selection.matched


def test_bug_agrees_with_the_validity_gate() -> None:
    """Two different answers to "is this a bug?" in one pipeline would be a bug of its own.
    They *had* diverged — `incident` selected the bug profile while the gate's own list did
    not mention it — so both now ask `is_bug`, and this asserts across the whole table."""
    from orchestrator.sdlc.validity import _check_localization

    for issue_type, profile in _BY_TYPE.items():
        assert select_profile(issue_type).profile == profile
        # A bug that landed nowhere is a finding for the gate; anything else is not.
        landed_nowhere = _check_localization({"title": "x", "summary": "y"}, [], issue_type)
        assert bool(landed_nowhere) is (profile == "bug"), issue_type


def test_is_bug_answers_for_the_whole_table() -> None:
    for issue_type, profile in _BY_TYPE.items():
        assert is_bug(issue_type) is (profile == "bug"), issue_type


def test_is_bug_normalises_the_way_the_lookup_does() -> None:
    """`Bug`, `bug` and ` BUG ` are one dropdown value typed three ways."""
    assert is_bug("Bug") and is_bug(" DEFECT ") and is_bug("incident")


def test_an_unknown_or_empty_type_is_not_a_bug() -> None:
    """A run that cannot say what kind of ticket it has must not be held to a bug's standard
    of evidence. Empty is the honest state for a wiki page or a `--spec` file — the gate is
    then at its least informed, and refusing there is how a gate teaches people to switch it
    off."""
    assert not is_bug("") and not is_bug("   ") and not is_bug("Escalation")


# ---- labels: the second question, never the first ---------------------------
#
# A tracker's labels have been fetched with every Jira issue since the adapter was written and
# read by nothing. They matter exactly when the issue type does not answer — a renamed dropdown,
# a tracker with no type field, an issue filed as `Escalation`.


def test_a_label_answers_when_the_issue_type_does_not() -> None:
    selection = select_profile("Escalation", labels=("type:bug", "customer"))

    assert selection.profile == "bug"
    assert selection.via == "label" and selection.label == "type:bug"
    assert "Escalation" in selection.reason and "type:bug" in selection.reason


def test_a_label_answers_when_there_is_no_issue_type_at_all() -> None:
    selection = select_profile("", labels=("enhancement",))

    assert selection.profile == "enhancement"
    assert "no issue type" in selection.reason and "enhancement" in selection.reason


def test_a_known_issue_type_beats_a_contradicting_label() -> None:
    """The safety property. A team that labels a feature request `bug` for triage must not
    thereby get root-cause analysis on a ticket their tracker plainly calls a Story."""
    selection = select_profile("Story", labels=("bug", "defect"))

    assert selection.profile == "enhancement"
    assert selection.via == "issue-type"


def test_contradicting_labels_resolve_the_same_whatever_order_they_arrive_in() -> None:
    """A label set is unordered. "First match wins" over an arrival-ordered set would make the
    profile depend on iteration order, and that run is not reproducible at a commit."""
    forward = select_profile("Escalation", labels=("story", "bug"))
    reverse = select_profile("Escalation", labels=("bug", "story"))

    assert forward.profile == reverse.profile == "bug"  # `bug` sorts before `story`
    assert forward.label == reverse.label == "bug"


def test_no_usable_label_still_falls_back_and_says_so() -> None:
    selection = select_profile("Escalation", labels=("customer", "q3", "not-a-bug"))

    assert selection.profile == "default"
    assert not selection.matched and selection.via == ""
    assert "Escalation" in selection.reason and "not one this repo maps" in selection.reason


def test_a_label_naming_a_profile_nobody_has_falls_back_too() -> None:
    """Same rule as the issue-type path: a repo may carry only some profiles."""
    selection = select_profile("Escalation", labels=("bug",), available=("default",))

    assert selection.profile == "default" and not selection.matched


def test_the_run_can_tell_a_type_from_a_guess() -> None:
    """`via` is why this is not just a boolean: a profile inferred off a label is a guess the
    tracker's own type is not, and the run has to be able to say which it made."""
    assert select_profile("Bug").via == "issue-type"
    assert select_profile("Escalation", labels=("bug",)).via == "label"
    assert select_profile("Escalation").via == ""
