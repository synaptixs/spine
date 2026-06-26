"""Claude Agent Skill importer (persona+skill Phase 1): SKILL.md → normalized Skill."""

from __future__ import annotations

import pytest

from orchestrator.catalog.skill_import import SkillImportError, import_claude_skill
from orchestrator.catalog.skills import SkillEval, SkillOrigin

_SKILL_MD = """\
---
name: Test Strategy
description: Decide what to test — edge cases, error paths, boundaries.
license: MIT
allowed-tools: Read, Bash
---

# Test Strategy

Cover every acceptance criterion with at least one assertion. Test error paths and
boundary values, not just the happy path.
"""


def test_imports_body_as_guidance_and_records_provenance() -> None:
    skill = import_claude_skill(_SKILL_MD, source="https://example/skills/test-strategy")
    assert skill.id == "test-strategy"
    assert skill.guidance.startswith("# Test Strategy")
    assert "boundary values" in skill.guidance
    assert skill.provenance.origin is SkillOrigin.CLAUDE_SKILL
    assert skill.provenance.source == "https://example/skills/test-strategy"
    assert skill.provenance.license == "MIT"


def test_pin_defaults_to_content_digest_and_is_overridable() -> None:
    auto = import_claude_skill(_SKILL_MD)
    assert auto.provenance.pin.startswith("sha256:")
    pinned = import_claude_skill(_SKILL_MD, pin="v1.2.3")
    assert pinned.provenance.pin == "v1.2.3"


def test_declared_tools_are_noted_not_bound() -> None:
    # Honest limit: source tools are recorded but NOT trusted/bound on import.
    skill = import_claude_skill(_SKILL_MD)
    assert skill.tools == ()
    assert "Read" in skill.provider_notes and "Bash" in skill.provider_notes
    assert "pending governed re-bind" in skill.provider_notes


def test_evals_can_be_attached_at_import() -> None:
    skill = import_claude_skill(_SKILL_MD, evals=[SkillEval("eval-test-strategy", 0.7)])
    assert skill.evals == (SkillEval("eval-test-strategy", 0.7),)


def test_missing_frontmatter_is_an_error() -> None:
    with pytest.raises(SkillImportError, match="frontmatter"):
        import_claude_skill("# Just a heading\n\nNo frontmatter here.\n")


def test_missing_name_is_an_error() -> None:
    with pytest.raises(SkillImportError, match="name"):
        import_claude_skill("---\ndescription: x\n---\n\nbody\n")


def test_empty_body_is_an_error() -> None:
    with pytest.raises(SkillImportError, match="empty body"):
        import_claude_skill("---\nname: Empty\n---\n\n   \n")
