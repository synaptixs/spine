"""Greenfield framing (P9a of SSPN-297): one idea → a framing change → one change per capability.

A product idea is too big for one change, and asking its four why-questions again in every
capability would be four answers times N, mostly the same. So the idea becomes a **framing
change** — the ordinary skeleton, named ``<idea-slug>-framing`` — answered once with
``openspec answer``. Once it passes ``check`` (D28: nothing to inherit before it exists), each
capability becomes its own change (D24) that carries the framing's problem, users and non-goal,
and asks only its own outcome, since each capability changes something different (D23).

The copied answers are recorded as what they are: ``origin: stated``, ``channel: inherited``, and
the framing's id (D29) — never as the person's own answer to this change, which they never gave.
They are copied once, at drafting; nothing keeps them in step with a framing answered differently
later (D31; a warning for that is parked as SSPN-318).

Bounds (D30): at most :data:`MAX_CAPABILITIES`, refused with the count rather than clipped; ids
are ``<idea-slug>-<capability-slug>``; and a batch is all or nothing — any change that already
exists means none is written, unless the caller says to overwrite. No model is called here."""

from __future__ import annotations

import datetime
from collections.abc import Sequence
from pathlib import Path

from orchestrator.intake import requirements as rq
from orchestrator.intake.intents import Intent, Resolution, _slug
from orchestrator.intake.openspec_writer import change_id_for, render_change, write_change
from orchestrator.intake.specs import FeatureSpec

__all__ = [
    "FRAMING_SUFFIX",
    "MAX_CAPABILITIES",
    "FramingError",
    "FramingNotReadyError",
    "draft_capabilities",
    "draft_framing",
    "framing_intent",
]

#: Most capability changes one framing drafts. More is refused, never cut (D30).
MAX_CAPABILITIES = 8
#: What the framing change's id ends with; the capability ids share what comes before it.
FRAMING_SUFFIX = "-framing"
#: Words of a capability kept in its id, after the framing's own (at most eight) words.
_CAPABILITY_WORDS = 6
#: The why-fields a capability inherits; its outcome is its own (D23).
_INHERITED = ("problem", "users", "non_goals")


class FramingError(ValueError):
    """An input the framing refuses — nothing was written. Never a defect."""


class FramingNotReadyError(FramingError):
    """The framing change does not pass ``check`` yet, so there is nothing to inherit."""


def framing_intent(idea: str) -> Intent:
    """The framing change for an idea: the skeleton, named for the idea. Deterministic."""
    try:
        skeleton = rq.skeleton(idea)
    except rq.RequirementsError as exc:
        raise FramingError(str(exc)) from exc
    return skeleton.model_copy(update={"id": skeleton.id + FRAMING_SUFFIX})


def draft_framing(root: str | Path, idea: str, *, overwrite: bool = False) -> tuple[str, list[Path]]:
    """Write the framing change under ``<root>/changes/``; returns its id and the files written
    (none when it exists and ``overwrite`` is not set — a person's edits are never clobbered)."""
    intent = framing_intent(idea)
    spec = FeatureSpec(intent_id=intent.id, title=intent.title, summary=intent.description)
    written = write_change(Path(root), intent, render_change(spec, intent), overwrite=overwrite)
    return change_id_for(intent), written


def draft_capabilities(
    root: str | Path,
    framing: rq.LoadedChange,
    capabilities: Sequence[str],
    *,
    overwrite: bool = False,
    at: str = "",
) -> list[str]:
    """Write one change per capability, inheriting the framing's why; returns their ids in order.

    Refused, with nothing written, when there are none, a blank one, more than
    :data:`MAX_CAPABILITIES` or two with one id — the input, checked first — then when the framing
    does not pass ``check``, or any id is already taken (unless ``overwrite``)."""
    texts = [" ".join(c.split()) for c in capabilities]
    if not texts:
        raise FramingError("give at least one capability")
    if not all(texts):
        raise FramingError("a capability needs at least one word")
    if len(texts) > MAX_CAPABILITIES:
        raise FramingError(
            f"{len(texts)} capabilities; a framing drafts at most {MAX_CAPABILITIES} — "
            "drop some, or frame part of the idea separately"
        )
    product = framing.change_id.removesuffix(FRAMING_SUFFIX)
    ids = [f"{product}-{_capability_slug(t)}" for t in texts]
    if len(set(ids)) != len(ids):
        clashing = sorted({i for i in ids if ids.count(i) > 1})
        raise FramingError(f"two capabilities would share an id: {', '.join(clashing)}")
    report = rq.check_intent(framing.intent, change_id=framing.change_id)
    if not report.passes:
        open_items = list(report.unresolved) or [f.message for f in report.findings]
        raise FramingNotReadyError(
            f"{framing.change_id} does not pass `check` yet, so there is nothing to inherit. Answer: "
            + "; ".join(open_items)
        )
    changes = Path(root) / "changes"
    taken = [i for i in ids if (changes / i).exists()]
    if taken and not overwrite:
        raise FramingError(
            f"{', '.join(taken)} already exists; nothing was written (pass --overwrite to replace)"
        )
    inherited = _inherited(framing, at or datetime.date.today().isoformat())
    for change_id, text in zip(ids, texts, strict=True):
        intent = Intent(
            id=f"intent-{change_id}",
            title=text if len(text) <= 80 else text[:77].rstrip() + "...",
            description=text,
            problem=framing.intent.problem,
            users=list(framing.intent.users),
            non_goals=list(framing.intent.non_goals),
            open_questions=list(rq.FIELD_QUESTIONS),
            resolutions=dict(inherited),
            idea_id=framing.intent.idea_id,
        )
        spec = FeatureSpec(intent_id=intent.id, title=intent.title, summary=intent.description)
        write_change(Path(root), intent, render_change(spec, intent), overwrite=True)
    return ids


def _capability_slug(text: str) -> str:
    return "-".join(_slug(text).split("-")[:_CAPABILITY_WORDS]).strip("-") or "capability"


def _inherited(framing: rq.LoadedChange, at: str) -> dict[str, Resolution]:
    """The framing's answers to the inherited why-questions, as this change's resolutions."""
    out: dict[str, Resolution] = {}
    for question, field_name in rq.FIELD_QUESTIONS.items():
        if field_name not in _INHERITED:
            continue
        value = getattr(framing.intent, field_name)
        answer = "; ".join(value) if isinstance(value, list) else str(value)
        out[question] = Resolution(
            status="answered",
            answer=answer,
            origin="stated",
            channel="inherited",
            inherited_from=framing.change_id,
            at=at,
        )
    return out
