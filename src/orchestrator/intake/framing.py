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

Bounds (D30): at most :data:`MAX_CAPABILITIES` per framing — counting the changes it already
drafted, not only the ones asked for now (B133) — refused with the count rather than clipped; ids
are ``<idea-slug>-<capability-slug>``; and a batch is all or nothing — any change that already
exists means none is written, unless the caller says to overwrite.

**The capability list** (P9b, D27) can live in the framing's ``design.md`` as ``## Capabilities``,
one ``- capability`` bullet each with an optional nested ``- Source:`` line — written by a person,
or proposed by ``frame --generate`` (``sdlc.ideation.generate_capabilities``, the one model call).
The proposal is only a list: the person edits it, and ``frame --from`` drafts from what it then
says. A line in the section that is not a bullet is reported, never dropped. No model is called
here."""

from __future__ import annotations

import datetime
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from orchestrator.intake import requirements as rq
from orchestrator.intake.atomic import write_atomic
from orchestrator.intake.intents import Intent, Resolution, _slug
from orchestrator.intake.openspec_writer import change_id_for, render_change, write_change
from orchestrator.intake.options import _fence_mask, _sections
from orchestrator.intake.specs import FeatureSpec

__all__ = [
    "FRAMING_SUFFIX",
    "MAX_CAPABILITIES",
    "CapabilitiesBlock",
    "Capability",
    "FramingError",
    "FramingNotReadyError",
    "capability_slug",
    "draft_capabilities",
    "draft_framing",
    "drafted_from",
    "framing_intent",
    "listed_capabilities",
    "load_capabilities",
    "parse_capabilities",
    "readiness",
    "render_capabilities",
    "write_capabilities",
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
    does not pass ``check``, any id is already taken (unless ``overwrite``), or the framing would
    then have more than :data:`MAX_CAPABILITIES` in all (:func:`drafted_from`; a change replaced
    with ``overwrite`` counts once)."""
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
    ids = [f"{product}-{capability_slug(t)}" for t in texts]
    if len(set(ids)) != len(ids):
        clashing = sorted({i for i in ids if ids.count(i) > 1})
        raise FramingError(f"two capabilities would share an id: {', '.join(clashing)}")
    if (not_ready := readiness(framing)) is not None:
        raise FramingNotReadyError(not_ready)
    changes = Path(root) / "changes"
    taken = [i for i in ids if (changes / i).exists()]
    if taken and not overwrite:
        raise FramingError(
            f"{', '.join(taken)} already exists; nothing was written (pass --overwrite to replace)"
        )
    # The bound is per framing, not per call (B133): count what it already drafted, and a
    # change this call replaces once.
    drafted = drafted_from(root, framing.change_id)
    if len(total := set(drafted) | set(ids)) > MAX_CAPABILITIES:
        raise FramingError(
            f"{framing.change_id} already has {len(drafted)} capability change(s); these make "
            f"{len(total)} in all, and a framing drafts at most {MAX_CAPABILITIES} — drop some, "
            "or frame part of the idea separately"
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


def drafted_from(root: str | Path, framing_id: str) -> list[str]:
    """The changes already drafted from a framing, sorted: those under ``<root>/changes/`` whose
    answers record it as their source (D29). The recorded source decides, never the id: a change
    renamed since is still the framing's, and a person's change that shares its prefix is not. A
    change that cannot be read is not counted."""
    changes = Path(root) / "changes"
    if not changes.is_dir():
        return []
    out: list[str] = []
    for directory in sorted(changes.iterdir()):
        try:
            resolutions = rq.load_change(directory.name, root=root).intent.resolutions
        except (rq.RequirementsError, OSError, UnicodeDecodeError):
            continue
        if any(r.inherited_from == framing_id for r in resolutions.values()):
            out.append(directory.name)
    return out


def readiness(framing: rq.LoadedChange) -> str | None:
    """Why the framing cannot be built on yet — what to answer — or ``None`` when it passes."""
    report = rq.check_intent(framing.intent, change_id=framing.change_id)
    if report.passes:
        return None
    open_items = list(report.unresolved) or [f.message for f in report.findings]
    return (
        f"{framing.change_id} does not pass `check` yet, so there is nothing to inherit. Answer: "
        + "; ".join(open_items)
    )


def capability_slug(text: str) -> str:
    """The part of a capability change's id its text gives; two capabilities with one slug clash."""
    return "-".join(_slug(text).split("-")[:_CAPABILITY_WORDS]).strip("-") or "capability"


# --- the `## Capabilities` block (P9b, D27) --------------------------------------------------------

_SECTION = "capabilities"
_BULLET = re.compile(r"^[-*]\s+(.*\S)\s*$")
_SOURCE = re.compile(r"^\s+[-*]\s+Source:\s*(.*\S)\s*$")


@dataclass(frozen=True)
class Capability:
    """One line of the list: what to build, and who proposed it (empty: a person, by hand)."""

    text: str
    source: str = ""


@dataclass(frozen=True)
class CapabilitiesBlock:
    present: bool
    items: tuple[Capability, ...] = ()
    problems: tuple[str, ...] = ()


def parse_capabilities(design_md: str) -> CapabilitiesBlock:
    """The ``## Capabilities`` section of a design: its bullets, and every line that is not one."""
    lines = design_md.replace("\r\n", "\n").split("\n")
    mask = _fence_mask(lines)
    found = [s for s in _sections(lines, mask) if s[0] == _SECTION]
    if not found:
        return CapabilitiesBlock(present=False)
    _, start, end = found[-1]
    items: list[Capability] = []
    problems: list[str] = []
    for n in range(start + 1, end):
        line = lines[n]
        if mask[n] or not line.strip():
            continue
        if (source := _SOURCE.match(line)) and items:
            items[-1] = Capability(items[-1].text, source.group(1))
        elif bullet := _BULLET.match(line):
            items.append(Capability(" ".join(bullet.group(1).split())))
        else:
            problems.append(
                f"`## Capabilities` line {n + 1} is not a `- capability` bullet: {line.strip()!r}"
            )
    return CapabilitiesBlock(present=True, items=tuple(items), problems=tuple(problems))


def render_capabilities(items: Sequence[Capability]) -> str:
    """The section for ``items``, refused unless it reads back exactly as given."""
    lines = ["## Capabilities", ""]
    for item in items:
        if not item.text or item.text != " ".join(item.text.split()) or "\n" in item.source:
            raise FramingError(
                f"capability {item.text!r} cannot be written so it reads back the same "
                "(blank, or not one line)"
            )
        lines.append(f"- {item.text}")
        if item.source:
            lines.append(f"  - Source: {item.source}")
    text = "\n".join(lines) + "\n"
    back = parse_capabilities(text)
    if back.items != tuple(items) or back.problems:
        raise FramingError("the capabilities cannot be written so they read back the same")
    return text


def _set_section(design_md: str, block: str) -> str:
    """``design_md`` with its ``## Capabilities`` replaced where it is, else added at the end."""
    crlf = "\r\n" in design_md
    text = design_md.replace("\r\n", "\n")
    if not text.strip():
        out = "# Design\n\n" + block
        return out.replace("\n", "\r\n") if crlf else out
    trailing = text.endswith("\n")
    lines = (text[:-1] if trailing else text).split("\n")
    found = [s for s in _sections(lines, _fence_mask(lines)) if s[0] == _SECTION]
    new = block.rstrip("\n").split("\n")
    if found:
        _, start, end = found[-1]
        lines[start:end] = new + ([""] if end < len(lines) else [])
    else:
        lines += ([""] if lines[-1].strip() else []) + new
    out = "\n".join(lines) + ("\n" if trailing else "")
    return out.replace("\n", "\r\n") if crlf else out


def _read_design(change: rq.LoadedChange) -> str:
    path = change.design_path
    if not path.is_file():
        return ""
    try:
        return path.read_bytes().decode("utf-8")
    except UnicodeDecodeError as exc:
        raise FramingError(f"{path} is not UTF-8 text: {exc}") from exc
    except OSError as exc:
        raise FramingError(f"{path} cannot be read: {exc}") from exc


def load_capabilities(change: rq.LoadedChange) -> CapabilitiesBlock:
    """The framing's ``## Capabilities``; absent (and no problem) when it has no design."""
    return parse_capabilities(_read_design(change))


def write_capabilities(change: rq.LoadedChange, items: Sequence[Capability]) -> None:
    """Set the framing's ``## Capabilities`` to ``items``, creating ``design.md`` if it has none.
    Validated in full before the file is touched, then replaced in one step."""
    block = render_capabilities(items)
    write_atomic(change.design_path, _set_section(_read_design(change), block))


def listed_capabilities(framing: rq.LoadedChange) -> list[str]:
    """What ``frame --from`` drafts when no ``--capability`` is given: the framing's list."""
    block = load_capabilities(framing)
    if block.problems:
        raise FramingError("`## Capabilities` has a problem — fix it first: " + "; ".join(block.problems))
    if not block.items:
        raise FramingError(
            f"{framing.change_id} lists no capabilities: pass --capability, or propose some with "
            "--generate and edit its `## Capabilities`"
        )
    return [item.text for item in block.items]


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
