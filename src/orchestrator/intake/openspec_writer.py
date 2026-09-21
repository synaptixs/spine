"""Write-back: render derived specs as OpenSpec change proposals.

The inverse of ``openspec_source``. Given the ``Intent``/``FeatureSpec`` that the LLM
extractor derived from an **unstructured** source (a Confluence page, a Notion doc), emit
a structured, reviewable **OpenSpec change** (``openspec/changes/<id>/``). A human then
polishes the draft — sharpening requirements into ``SHALL`` statements and criteria into
Given/When/Then scenarios — and Spine implements from the polished ``openspec://`` change
**deterministically** (no more LLM guessing).

This closes the loop: keep "point Spine at a wiki", but land in a durable, versioned,
human-owned spec instead of an ephemeral guess. The rendered files round-trip — reading
one back with ``openspec_source.change_to_intent`` recovers the same acceptance criteria.
"""

from __future__ import annotations

import re
from pathlib import Path

from orchestrator.intake.intents import Intent, _slug
from orchestrator.intake.pkg_evidence import Grounding, absence_section, banner_sentence, fact_section
from orchestrator.intake.specs import FeatureSpec

_DRAFT_NOTE = (
    "> ⚠️ **Auto-drafted by Spine** from an unstructured source — review before use: "
    "sharpen each requirement into a SHALL/MUST statement and each scenario into "
    "Given/When/Then, then run `orchestrator sdlc feature --source openspec://{change_id}`.\n"
)
#: The grounding line rides *in* the banner, not only in its own section. A reader who skims
#: `proposal.md` sees the banner and nothing else, and "which mode produced this page" is
#: exactly the question they must not have to answer by scrolling.
_GROUNDING_NOTE = "> {sentence}\n"

_GWT = re.compile(r"\b(GIVEN|WHEN|THEN|AND|BUT)\b", re.IGNORECASE)
# BDD keywords for *splitting* a criterion into bullets are UPPERCASE by convention —
# case-sensitive so a mid-sentence prose "and"/"then" isn't mistaken for a step keyword.
_BDD_UPPER = re.compile(r"\b(GIVEN|WHEN|THEN|AND|BUT)\b")


def change_id_for(intent: Intent) -> str:
    """The OpenSpec change id for an intent (``intent-foo`` → ``foo``; else slug the title)."""
    cid = intent.id[len("intent-") :] if intent.id.startswith("intent-") else _slug(intent.title)
    return cid or "change"


def _scenario_bullets(criterion: str) -> list[str]:
    """Render one acceptance criterion as scenario bullet lines.

    A criterion already phrased Given/When/Then is split onto one bullet per keyword;
    a plain criterion becomes a single ``- THEN <criterion>`` for the human to expand.
    """
    text = " ".join(criterion.split())
    if _BDD_UPPER.search(text):
        # start a new bullet at each UPPERCASE BDD keyword (prose "and" is left alone)
        chunks = re.split(r"(?=\b(?:GIVEN|WHEN|THEN|AND|BUT)\b)", text)
        bullets = [f"- {c.strip()}" for c in chunks if c.strip()]
        return bullets or [f"- THEN {text}"]
    return [f"- THEN {text}"]


def _spec_md(spec: FeatureSpec) -> str:
    """The delta spec: one ``### Requirement`` with each acceptance criterion as a scenario."""
    lines = [f"# Delta for {spec.title}", "", "## ADDED Requirements", "", f"### Requirement: {spec.title}"]
    statement = (spec.summary or spec.user_story or f"The system SHALL support {spec.title}.").strip()
    lines += [statement, ""]
    criteria = spec.acceptance_criteria or ["The behavior described in the proposal holds."]
    for i, crit in enumerate(criteria, 1):
        label = _short_label(crit) or f"Criterion {i}"
        lines.append(f"#### Scenario: {label}")
        lines += _scenario_bullets(crit)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _short_label(criterion: str) -> str:
    """A concise scenario label from a criterion (its lead clause, before a G/W/T keyword)."""
    text = " ".join(criterion.split())
    head = _GWT.split(text)[0].strip(" :.-—")
    head = head.split(".")[0]
    return head[:60].strip()


def _proposal_md(
    spec: FeatureSpec, intent: Intent, change_id: str, grounding: Grounding | None = None
) -> str:
    why = (intent.description or spec.summary or "").strip()
    what = (intent.scope or spec.user_story or "").strip()
    impact = spec.technical_notes.strip()
    parts = [
        f"# Proposal: {spec.title}",
        "",
        _DRAFT_NOTE.format(change_id=change_id),
    ]
    if grounding is not None:
        parts.append(_GROUNDING_NOTE.format(sentence=banner_sentence(grounding)))
    parts += ["## Why", why or "TODO"]
    parts += ["", "## What Changes", what or "TODO"]
    if impact:
        parts += ["", "## Impact", impact]
    if intent.open_questions:
        parts += ["", "## Open Questions"] + [f"- {q}" for q in intent.open_questions]
    if grounding is not None:
        # Last, and fenced off by its own heading: a fact region must never be interleaved
        # with the derived prose above it, or the citation lends its authority to the sentence
        # beside it rather than to the line it names.
        parts += ["", "## Grounding", absence_section(grounding)]
        if facts := fact_section(grounding):
            parts += ["", facts.rstrip()]
    return "\n".join(parts).rstrip() + "\n"


def _tasks_md(spec: FeatureSpec, grounding: Grounding | None = None) -> str:
    """One checkbox per criterion — not two constants for every change in the world.

    This function took ``spec`` and read nothing from it, emitting *"Implement the
    requirement"* / *"Add tests"* for every change ever drafted. That is a large part of the
    "doesn't give enough clarity" finding this track answers, and unlike the prose it is
    fixable **deterministically**: the criteria already exist, stated and proposed are already
    separate fields, and the binder already says which name code that exists.

    **No task cites a file, ever.** A task is an instruction — *derived* — and "change
    `foo.py:41`" is a derived claim wearing a citation, which is the one failure this track
    exists to prevent. Landing sites belong in the proposal's fact block, where a reader can
    see what they are. The most a task may say is *verify before building*, and why.

    Shape is unchanged (``## N. Group`` + ``- [ ] N.M``), which is what `openspec_source`
    documents — though it takes ``tasks_md`` and never reads it, so criteria round-trip
    through ``specs/<cap>/spec.md`` regardless.
    """
    # Normalised on both sides. The binder strips each criterion before it ever sees the graph
    # (`criteria_binding._criteria_text`), so a raw-text comparison misses any criterion the
    # model emitted with a trailing newline or leading indent — which is most of them. The
    # symptom was silent and exactly inverted: the criterion bound in the Grounding section and
    # the checkbox beside it lost its "verify first", which is the SSPN-49 case this note
    # exists to raise.
    bound = (
        {_one_line(row.text) for row in grounding.binding.rows if row.status == "bound"}
        if grounding and grounding.binding
        else set()
    )
    lines = ["# Tasks", ""]
    group = 1
    if spec.acceptance_criteria:
        lines.append(f"## {group}. Implementation")
        for i, crit in enumerate(spec.acceptance_criteria, 1):
            # `verify first`, not `already done`: the binder found code this criterion names,
            # which is evidence and not a verdict. Ticking it off here would be exactly the
            # SSPN-49 failure — a run reporting a criterion met having changed nothing.
            one_line = _one_line(crit)
            note = (
                " — **verify first:** code this names already exists (see *Grounding*)"
                if one_line in bound
                else ""
            )
            lines.append(f"- [ ] {group}.{i} {one_line}{note}")
        group += 1
    else:
        lines += [f"## {group}. Implementation", f"- [ ] {group}.1 Implement the requirement"]
        group += 1
    if spec.proposed_criteria:
        # Labelled and kept apart, for the same reason the schema keeps the fields apart: a
        # suggestion the spec writer inferred is not a contract the source signed, and a
        # reader deciding what to build must be able to tell them apart at a glance.
        lines += ["", f"## {group}. Proposed — inferred by Spine, not stated by the source"]
        for i, crit in enumerate(spec.proposed_criteria, 1):
            lines.append(f"- [ ] {group}.{i} {_one_line(crit)}")
        group += 1
    lines += ["", f"## {group}. Verification", f"- [ ] {group}.1 Add tests covering each criterion above"]
    return "\n".join(lines) + "\n"


def _one_line(text: str) -> str:
    """A criterion as a single checkbox line. Multi-line Given/When/Then would break the list."""
    return " ".join(text.split())


def render_change(spec: FeatureSpec, intent: Intent, grounding: Grounding | None = None) -> dict[str, str]:
    """Render one derived spec into OpenSpec change files: ``{relpath: content}``.

    Keys are paths **relative to the change dir** (``proposal.md``, ``tasks.md``,
    ``specs/<cap>/spec.md``). ``write_change`` places them under ``<root>/changes/<id>/``.

    ``grounding`` is what the code said about this change, produced by the caller — the CLI is
    the only layer that may read both a repository and an intake plan, and passing it in keeps
    ``intake`` free of any import of ``sdlc``. ``None`` is the ungrounded draft exactly as it
    was rendered before grounding existed, which is what makes the old behaviour the default
    rather than a branch someone has to remember to take.
    """
    change_id = change_id_for(intent)
    cap = _slug(spec.title) or change_id
    return {
        "proposal.md": _proposal_md(spec, intent, change_id, grounding),
        "tasks.md": _tasks_md(spec, grounding),
        f"specs/{cap}/spec.md": _spec_md(spec),
    }


def write_change(root: Path, intent: Intent, files: dict[str, str], *, overwrite: bool = False) -> list[Path]:
    """Write rendered change files under ``<root>/changes/<change-id>/``.

    Skips files that already exist unless ``overwrite`` — so re-running a draft never
    clobbers a change a human has since polished. Returns the paths written.
    """
    change_dir = root / "changes" / change_id_for(intent)
    written: list[Path] = []
    for rel, content in files.items():
        path = change_dir / rel
        if path.exists() and not overwrite:
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        written.append(path)
    return written


__all__ = ["change_id_for", "render_change", "write_change"]
