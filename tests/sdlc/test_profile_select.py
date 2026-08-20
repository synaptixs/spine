"""Issue type → workflow profile (Phase 3).

The mapping is a lookup and must stay one: a model choosing which research a ticket gets would
make the Evidence unreproducible at a commit, and that reproducibility is what everything
downstream rests on.
"""

from __future__ import annotations

import pytest

from orchestrator.sdlc.profile_select import select_profile


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
    """`validity._check_localization` already decides that "bug" and "defect" are the types a
    ticket must localize for. Two different answers to "is this a bug?" in one pipeline would be
    a bug of its own."""
    from orchestrator.sdlc.validity import _check_localization

    for issue_type in ("bug", "defect"):
        assert select_profile(issue_type).profile == "bug"
        # A bug that landed nowhere is a finding for the gate; a story is not.
        assert _check_localization({"title": "x", "summary": "y"}, [], issue_type)
    assert not _check_localization({"title": "x", "summary": "y"}, [], "story")
