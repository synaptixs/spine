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
from orchestrator.intake.specs import FeatureSpec

_DRAFT_NOTE = (
    "> ⚠️ **Auto-drafted by Spine** from an unstructured source — review before use: "
    "sharpen each requirement into a SHALL/MUST statement and each scenario into "
    "Given/When/Then, then run `orchestrator sdlc feature --source openspec://{change_id}`.\n"
)

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


def _proposal_md(spec: FeatureSpec, intent: Intent, change_id: str) -> str:
    why = (intent.description or spec.summary or "").strip()
    what = (intent.scope or spec.user_story or "").strip()
    impact = spec.technical_notes.strip()
    parts = [
        f"# Proposal: {spec.title}",
        "",
        _DRAFT_NOTE.format(change_id=change_id),
        "## Why",
        why or "TODO",
    ]
    parts += ["", "## What Changes", what or "TODO"]
    if impact:
        parts += ["", "## Impact", impact]
    if intent.open_questions:
        parts += ["", "## Open Questions"] + [f"- {q}" for q in intent.open_questions]
    return "\n".join(parts).rstrip() + "\n"


def _tasks_md(spec: FeatureSpec) -> str:
    lines = [
        "# Tasks",
        "",
        "## 1. Implementation",
        "- [ ] 1.1 Implement the requirement",
        "- [ ] 1.2 Add tests",
    ]
    return "\n".join(lines) + "\n"


def render_change(spec: FeatureSpec, intent: Intent) -> dict[str, str]:
    """Render one derived spec into OpenSpec change files: ``{relpath: content}``.

    Keys are paths **relative to the change dir** (``proposal.md``, ``tasks.md``,
    ``specs/<cap>/spec.md``). ``write_change`` places them under ``<root>/changes/<id>/``.
    """
    change_id = change_id_for(intent)
    cap = _slug(spec.title) or change_id
    return {
        "proposal.md": _proposal_md(spec, intent, change_id),
        "tasks.md": _tasks_md(spec),
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
