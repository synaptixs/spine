"""Which non-goals the ticket actually states: a deterministic check on the spec writer's list (B174).

Intake asks the model for non-goals "only when the source states them", and a rule a model can ignore
is not a rule. The measured case (SSPN-400): a ticket that ended "Not blocking — to be picked up later"
got the non-goal "Immediate fix", which tells codegen not to do the work the ticket asks for. The note
was about *when*; the non-goal says *don't*.

D11 rules out a prompt change: it would invalidate every cached intake and every approval built on one.
So the check runs on the spec, where it enters ``builddoc.build_plan`` (the one place a plan reads
non-goals), and only ever removes:

1. **A scheduling or priority note is never a non-goal.** A line with a clearly temporal word
   (immediate, later, urgent, blocking, hotfix…) and otherwise only timing companions (priority,
   sprint, picked…) and words that name no particular work (fix, change, work…) says when, not what,
   and is dropped. A companion alone never makes a note: "Out of scope: schedule changes" and "No
   priority changes" name work, and go on to rule 2.
2. **A non-goal must be supported by the ticket's own text.** Every content word of it has to appear in
   the ticket, up to inflection (``changes``/``changing``). One missing word drops it, naming the word.
   Without ticket text — a hand-written ``--spec`` — nothing can be checked, and only rule 1 applies.

Each dropped line is returned with its reason so the plan can list it: a removal nobody can see is the
silent narrowing this module exists to prevent. Deterministic, no model, order preserved.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

_WORD_RE = re.compile(r"[a-z0-9]+")

#: Words that carry no content of their own in a non-goal line.
_STOPWORDS = frozenset(
    [
        "a",
        "an",
        "and",
        "any",
        "are",
        "as",
        "at",
        "be",
        "been",
        "by",
        "do",
        "does",
        "for",
        "from",
        "has",
        "have",
        "in",
        "into",
        "is",
        "it",
        "its",
        "no",
        "non",
        "not",
        "of",
        "on",
        "or",
        "our",
        "out",
        "scope",
        "so",
        "than",
        "that",
        "the",
        "their",
        "them",
        "then",
        "there",
        "these",
        "this",
        "those",
        "to",
        "up",
        "was",
        "we",
        "were",
        "will",
        "with",
        "without",
        "yet",
    ]
)

#: Clearly about *when*: a scheduling note has at least one of these (B174 review — "priority",
#: "schedule", "time" or "wait" alone are as often the subject of a ticket as a note about its timing).
_WHEN = frozenset(
    [
        "immediate",
        "immediately",
        "urgent",
        "urgently",
        "asap",
        "now",
        "today",
        "tomorrow",
        "soon",
        "later",
        "eventually",
        "hotfix",
        "blocking",
        "blocker",
    ]
)

#: Words that may sit in a scheduling note beside a :data:`_WHEN` word ("to be picked up later", "next
#: sprint, not urgent") but never make one on their own.
_TIMING_COMPANIONS = frozenset(
    [
        "quick",
        "quickly",
        "priority",
        "prioritised",
        "prioritized",
        "prioritise",
        "prioritize",
        "sprint",
        "sprints",
        "deadline",
        "schedule",
        "scheduled",
        "timeline",
        "timeframe",
        "pick",
        "picked",
        "picking",
        "nice",
        "wait",
        "time",
        "permits",
        "next",
        "upcoming",
        "week",
        "weeks",
        "month",
        "quarter",
    ]
)

#: Words that name no particular piece of work: "Immediate fix" excludes nothing specific.
_NO_PARTICULAR_WORK = frozenset(
    [
        "fix",
        "fixes",
        "fixing",
        "fixed",
        "change",
        "changes",
        "work",
        "task",
        "issue",
        "ticket",
        "bug",
        "patch",
    ]
)

#: Leading characters compared when matching a word against the ticket, so an inflection still matches.
_STEM = 5


@dataclass(frozen=True)
class DroppedNonGoal:
    """A non-goal the check removed, and why — for the plan to list beside the kept ones."""

    text: str
    reason: str


def _words(text: str) -> list[str]:
    return _WORD_RE.findall(text.casefold())


def _stem(word: str) -> str:
    return word[:_STEM]


def _is_scheduling_note(words: list[str]) -> bool:
    content = [w for w in words if w not in _STOPWORDS]
    if not content:
        return False
    return any(w in _WHEN for w in content) and all(
        w in _WHEN or w in _TIMING_COMPANIONS or w in _NO_PARTICULAR_WORK for w in content
    )


def screen_non_goals(non_goals: Sequence[str], source_text: str) -> tuple[list[str], list[DroppedNonGoal]]:
    """``(kept, dropped)``: the non-goals the ticket states, and the ones it does not, with reasons.

    ``source_text`` is the ticket as intake read it; ``""`` means there is none to check against.
    """
    ticket = {_stem(w) for w in _words(source_text)} if source_text.strip() else None
    kept: list[str] = []
    dropped: list[DroppedNonGoal] = []
    for raw in non_goals:
        text = str(raw).strip()
        if not text:
            continue
        words = _words(text)
        if _is_scheduling_note(words):
            dropped.append(DroppedNonGoal(text, "a scheduling or priority note, not excluded work"))
            continue
        if ticket is not None:
            missing = [w for w in dict.fromkeys(words) if w not in _STOPWORDS and _stem(w) not in ticket]
            if missing:
                dropped.append(
                    DroppedNonGoal(
                        text, "no support in the ticket's text — it never says " + ", ".join(missing)
                    )
                )
                continue
        kept.append(text)
    return kept, dropped


__all__ = ["DroppedNonGoal", "screen_non_goals"]
