"""Importers: curated external skills → the normalized ``Skill`` artifact.

Phase 1 ships the **Claude Agent Skill** importer (a `SKILL.md` = YAML
frontmatter + markdown body). The split mirrors the honest limit from the spec:

- **Knowledge ports.** The markdown body becomes the Skill's ``guidance`` verbatim.
- **Provenance is recorded.** Origin, source ref, a content-digest ``pin``, and the
  declared ``license`` — so an import is traceable, pinned, and license-checked.
- **Tools do NOT port.** A source skill's declared tools are *noted* but left
  unbound (``Skill.tools`` stays empty); binding to the orchestrator's governed
  tool contracts / MCP allow-list is a later pass, not an implicit trust.
- **Imports start unvetted.** They carry no eval gates unless the onboarder
  attaches them, and ``catalog.vetting`` refuses to select an imported skill until
  it clears those gates (supply-chain discipline — curated ≠ trusted-for-your-pipeline).

Frontmatter is parsed with a minimal flat ``key: value`` reader (Claude `SKILL.md`
frontmatter is flat), keeping the base install dependency-free — no PyYAML.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence

from orchestrator.catalog.skills import Skill, SkillEval, SkillOrigin, SkillProvenance

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", re.S)


class SkillImportError(ValueError):
    """A curated skill could not be normalized into a ``Skill``."""


def import_claude_skill(
    text: str,
    *,
    source: str = "",
    pin: str = "",
    evals: Sequence[SkillEval] = (),
) -> Skill:
    """Normalize a Claude Agent Skill (``SKILL.md`` text) into a ``Skill``.

    ``source`` is the ref it came from (URL / path); ``pin`` defaults to a sha256
    content digest so the import is reproducible. ``evals`` lets the onboarder
    attach the eval bar the import must clear before it is selectable.
    """
    fm, body = _split_frontmatter(text)
    name = fm.get("name", "").strip()
    if not name:
        raise SkillImportError("SKILL.md frontmatter must include a 'name'")
    skill_id = _slug(name)
    if not skill_id:
        raise SkillImportError(f"could not derive a skill id from name {name!r}")
    guidance = body.strip()
    if not guidance:
        raise SkillImportError(f"skill {name!r} has an empty body (no guidance)")

    declared_tools = _parse_list(fm.get("allowed-tools", ""))
    notes = (
        f"source-declared tools (pending governed re-bind): {', '.join(declared_tools)}"
        if declared_tools
        else ""
    )
    return Skill(
        id=skill_id,
        guidance=guidance,
        provenance=SkillProvenance(
            origin=SkillOrigin.CLAUDE_SKILL,
            source=source,
            pin=pin or _digest(text),
            license=fm.get("license", "").strip(),
        ),
        tools=(),  # NOT bound on import — governed re-bind is a later pass
        evals=tuple(evals),
        provider_notes=notes,
    )


def _split_frontmatter(text: str) -> tuple[dict[str, str], str]:
    m = _FRONTMATTER_RE.match(text.lstrip("﻿"))
    if m is None:
        raise SkillImportError("SKILL.md must open with a '---' YAML frontmatter block")
    fm: dict[str, str] = {}
    for raw in m.group(1).splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, _, value = line.partition(":")
        fm[key.strip().lower()] = value.strip()
    return fm, m.group(2)


def _slug(name: str) -> str:
    return re.sub(r"[^0-9a-z]+", "-", name.lower()).strip("-")


def _parse_list(raw: str) -> list[str]:
    """Parse ``allowed-tools: a, b`` or ``[a, b]`` into a clean list."""
    items = raw.strip().strip("[]").split(",")
    return [t.strip().strip("\"'") for t in items if t.strip().strip("\"'")]


def _digest(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


__all__ = ["SkillImportError", "import_claude_skill"]
