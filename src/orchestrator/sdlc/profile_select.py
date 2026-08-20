"""Choose a workflow profile from the ticket's issue type. Deterministic, no model.

A tracker's issue type is a string someone typed into a dropdown — `Bug`, `bug`, `Defect`,
`Story`, `New Feature`, `Task`, or whatever a team invented. Mapping it to a profile is a
lookup, not a judgement, and it stays a lookup: the moment a model decides which research a
ticket gets, the research stops being reproducible at a commit and the whole Evidence guarantee
goes with it.

**An unknown type falls back to `default` and says so.** Erroring would fail a run because a
team renamed a dropdown, which is not a reason to refuse work. But a *silent* fallback is how
"why did it skip RCA?" becomes unanswerable, so the reason is returned alongside the choice and
the run prints it. Phase 3 of ``docs/specs/graphir-sdlc-workflow.md``.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["Selection", "select_profile"]

# Issue type → profile. Lowercased and stripped before lookup, so `Bug`, `bug` and ` BUG ` agree.
#
# The bug list matches `validity._check_localization`, which already decides that "bug" and
# "defect" are the types a ticket must localize for. Two different answers to "is this a bug?"
# in one pipeline would be a bug of its own.
_BY_TYPE: dict[str, str] = {
    "bug": "bug",
    "defect": "bug",
    "incident": "bug",
    "story": "enhancement",
    "task": "enhancement",
    "feature": "enhancement",
    "new feature": "enhancement",
    "enhancement": "enhancement",
    "improvement": "enhancement",
}

DEFAULT = "default"


@dataclass(frozen=True)
class Selection:
    """The profile chosen, and why — so a run can say it out loud."""

    profile: str
    issue_type: str
    matched: bool  # False when the type was unknown and `default` was used

    @property
    def reason(self) -> str:
        if not self.issue_type:
            return f"no issue type on the ticket — using `{self.profile}`"
        if self.matched:
            return f"issue type `{self.issue_type}` → `{self.profile}`"
        return f"issue type `{self.issue_type}` is not one this repo maps — using `{self.profile}`"


def select_profile(issue_type: str, *, available: tuple[str, ...] | None = None) -> Selection:
    """Map an issue type to a profile name.

    ``available`` is the set of profiles that actually exist — shipped plus any the repo carries.
    A mapping that names a profile nobody has falls back rather than raising: a repo may delete
    or rename a profile, and the run should continue on the default and say what happened.
    """
    raw = (issue_type or "").strip()
    name = _BY_TYPE.get(raw.lower())
    if name is None:
        return Selection(profile=DEFAULT, issue_type=raw, matched=False)
    if available is not None and name not in available:
        return Selection(profile=DEFAULT, issue_type=raw, matched=False)
    return Selection(profile=name, issue_type=raw, matched=True)
