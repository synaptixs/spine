"""The plugin manifests + the understand-codebase Agent Skill stay valid and in sync."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_SKILL = _ROOT / "plugins" / "spine" / "skills" / "understand-codebase" / "SKILL.md"
_MANIFESTS = [
    _ROOT / "plugins" / "spine" / ".claude-plugin" / "plugin.json",
    _ROOT / ".claude-plugin" / "marketplace.json",
    _ROOT / "codex-marketplace" / "plugins" / "spine" / ".codex-plugin" / "plugin.json",
]
# The comprehension tools the plugin's pitch should surface.
_COMP_TOOLS = ("map_repo", "blast_radius", "explain_symbol", "investigate", "localize", "regression_gaps")


def test_understand_codebase_skill_has_frontmatter() -> None:
    assert _SKILL.is_file(), f"missing skill file: {_SKILL}"
    text = _SKILL.read_text(encoding="utf-8")
    assert text.startswith("---\n"), "SKILL.md must open with YAML frontmatter"
    _, fm, _body = text.split("---\n", 2)
    assert "name: understand-codebase" in fm
    assert "description:" in fm
    # The description is the trigger — it must name the tools so the skill activates on the
    # right questions.
    assert "blast_radius" in fm and "map_repo" in fm


def test_skill_body_documents_each_tool() -> None:
    body = _SKILL.read_text(encoding="utf-8").split("---\n", 2)[2]
    for tool in _COMP_TOOLS:
        assert tool in body, f"{tool} not mentioned in the skill body"


@pytest.mark.parametrize("manifest", _MANIFESTS, ids=lambda p: p.parent.name)
def test_manifest_is_valid_json(manifest: Path) -> None:
    assert manifest.is_file(), f"missing manifest: {manifest}"
    json.loads(manifest.read_text(encoding="utf-8"))  # raises on invalid JSON


def test_plugin_pitch_leads_with_comprehension_and_go() -> None:
    for manifest in _MANIFESTS:
        blob = manifest.read_text(encoding="utf-8")
        assert "comprehension" in blob, f"{manifest} pitch should surface the comprehension tools"
        assert "Go" in blob, f"{manifest} language list should include Go"
