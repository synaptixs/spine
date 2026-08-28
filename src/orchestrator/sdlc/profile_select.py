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

**Labels are the second question, never the first.** A tracker's labels are fetched with every
issue and were read by nothing. They are consulted **only** where the issue type resolves to
nothing — a renamed dropdown, a tracker with no type field, an issue filed as `Escalation` — and
a known type always wins outright. That ordering is the whole safety property: a team that
labels a feature request `bug` for triage reasons must not thereby get root-cause analysis on a
ticket their tracker plainly calls a Story.

Labels arrive as an unordered set, so they are **sorted before matching**. "First match wins"
over an arrival-ordered set would make the profile depend on iteration order, and a run whose
research was decided that way is not reproducible at a commit — which is the guarantee this
module exists to protect.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["Selection", "is_bug", "select_profile"]

# Issue type → profile. Lowercased and stripped before lookup, so `Bug`, `bug` and ` BUG ` agree.
#
# This table is the single answer to "is this a bug?" — `validity` asks it through `is_bug`
# below rather than keeping a list of its own. It used to keep one, and the two had already
# drifted: `incident` selected the bug profile here while `validity` did not treat it as a
# bug, so an incident got root-cause analysis and was then excused from having to localize.
_BY_TYPE: dict[str, str] = {
    "bug": "bug",
    "defect": "bug",
    "incident": "bug",  # `is_bug` too: it selects the bug profile, so it localizes like one
    "story": "enhancement",
    "task": "enhancement",
    "feature": "enhancement",
    "new feature": "enhancement",
    "enhancement": "enhancement",
    "improvement": "enhancement",
}

DEFAULT = "default"

#: The profile a bug-shaped ticket gets. Named so `is_bug` and the table cannot drift.
BUG = "bug"
ENHANCEMENT = "enhancement"

# Label → profile, for the fallback only. Deliberately short and literal: these are the forms
# teams actually type, not a pattern language. A label nobody listed simply does not match, and
# the run says `default` — which is the honest answer, and a cheaper failure than a regex that
# reads `not-a-bug` as `bug`.
#
# Not a place to be clever. Every entry here can override nothing: by the time this table is
# consulted the tracker has already failed to say what kind of ticket this is.
_BY_LABEL: dict[str, str] = {
    "bug": BUG,
    "defect": BUG,
    "incident": BUG,
    "type:bug": BUG,
    "type/bug": BUG,
    "kind/bug": BUG,
    "enhancement": ENHANCEMENT,
    "feature": ENHANCEMENT,
    "story": ENHANCEMENT,
    "type:feature": ENHANCEMENT,
    "type/feature": ENHANCEMENT,
    "kind/feature": ENHANCEMENT,
    "type:enhancement": ENHANCEMENT,
    "kind/enhancement": ENHANCEMENT,
}


def is_bug(issue_type: str) -> bool:
    """Does this issue type describe something that already exists?

    The question two stages need and must not answer separately: `validity` refuses a bug it
    cannot localize (there is no fault site to work from) and parks a bug whose criteria name
    symbols the graph does not hold (a bug's subject is existing code, so an unbound claim is
    a false premise). Neither is true of an enhancement, whose subject is the deliverable.

    Unknown and empty types answer **False**. A run that cannot say what kind of ticket it has
    should not be held to a bug's standard of evidence — and empty is the honest state for a
    source with no such notion (a wiki page, a `--spec` file), not a reason to refuse it.
    """
    return _BY_TYPE.get((issue_type or "").strip().lower()) == BUG


@dataclass(frozen=True)
class Selection:
    """The profile chosen, and why — so a run can say it out loud."""

    profile: str
    issue_type: str
    matched: bool  # False when nothing mapped and `default` was used
    #: Which question answered: ``"issue-type"``, ``"label"``, or ``""`` for the fallback. A
    #: reader has to be able to tell a profile chosen from the tracker's own type from one
    #: inferred off a label, because the second is a guess the first is not.
    via: str = ""
    #: The label that answered, when ``via`` is ``"label"``. Named, not counted: "matched a
    #: label" is not something a reader can check.
    label: str = ""

    @property
    def reason(self) -> str:
        if self.via == "label":
            preamble = (
                f"issue type `{self.issue_type}` is not one this repo maps"
                if self.issue_type
                else "no issue type on the ticket"
            )
            return f"{preamble}; label `{self.label}` → `{self.profile}`"
        if not self.issue_type:
            return f"no issue type on the ticket — using `{self.profile}`"
        if self.matched:
            return f"issue type `{self.issue_type}` → `{self.profile}`"
        return f"issue type `{self.issue_type}` is not one this repo maps — using `{self.profile}`"


def _from_labels(labels: tuple[str, ...]) -> tuple[str, str]:
    """The first profile any label maps to, and the label that did it. Sorted, so reproducible."""
    for label in sorted({(x or "").strip().lower() for x in labels if (x or "").strip()}):
        name = _BY_LABEL.get(label)
        if name is not None:
            return name, label
    return "", ""


def select_profile(
    issue_type: str, *, labels: tuple[str, ...] = (), available: tuple[str, ...] | None = None
) -> Selection:
    """Map an issue type — or, failing that, a label — to a profile name.

    ``labels`` is the ticket's own label set, consulted **only** when the issue type maps to
    nothing. It is a fallback for a misconfigured or type-less tracker, never an override: a
    ticket the tracker calls a Story stays an enhancement however it is labelled.

    ``available`` is the set of profiles that actually exist — shipped plus any the repo carries.
    A mapping that names a profile nobody has falls back rather than raising: a repo may delete
    or rename a profile, and the run should continue on the default and say what happened. That
    applies to a label's profile too, and the type is not re-asked afterwards — one question is
    answered per selection, so the reason a run prints is the reason it chose.
    """
    raw = (issue_type or "").strip()
    name = _BY_TYPE.get(raw.lower())
    if name is not None:
        if available is not None and name not in available:
            return Selection(profile=DEFAULT, issue_type=raw, matched=False)
        return Selection(profile=name, issue_type=raw, matched=True, via="issue-type")

    by_label, label = _from_labels(labels)
    if by_label and not (available is not None and by_label not in available):
        return Selection(profile=by_label, issue_type=raw, matched=True, via="label", label=label)
    return Selection(profile=DEFAULT, issue_type=raw, matched=False)
