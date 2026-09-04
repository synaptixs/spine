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
_COMP_TOOLS = (
    "map_repo",
    "blast_radius",
    "explain_symbol",
    "investigate",
    "localize",
    "regression_gaps",
    "root_cause",
    "docs_for",
    "pkg_joins",
)
# Every language the PKG has a front-end for. The pitch is what a user reads before
# installing, so a missing language here is a language they never learn Spine covers.
_LANGUAGES = ("Python", "Java", "TypeScript", "C#", "C", "C++", "Go", "SQL")


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


def test_plugin_pitch_leads_with_comprehension_and_every_language() -> None:
    for manifest in _MANIFESTS:
        blob = manifest.read_text(encoding="utf-8")
        assert "comprehension" in blob, f"{manifest} pitch should surface the comprehension tools"
        for language in _LANGUAGES:
            assert language in blob, f"{manifest} language list should include {language}"


def _pyproject_version() -> str:
    import tomllib

    with (_ROOT / "pyproject.toml").open("rb") as fh:
        version: str = tomllib.load(fh)["project"]["version"]
    return version


@pytest.mark.parametrize("manifest", _MANIFESTS, ids=lambda p: p.parent.name)
def test_manifest_version_tracks_the_package(manifest: Path) -> None:
    """The version a user reads in the plugin list is the version they installed.

    Nothing else ties these together: the manifests are hand-written JSON that no release step
    touches, so without this they simply stop being true — quietly, and for as long as nobody
    happens to look. They stood at 2.5.0 through fifteen releases exactly this way.
    """
    declared = json.loads(manifest.read_text(encoding="utf-8"))
    version = declared["version"] if "version" in declared else declared["plugins"][0]["version"]

    assert version == _pyproject_version(), (
        f"{manifest} declares {version}; pyproject is at {_pyproject_version()}"
    )


def test_the_readme_whats_new_section_names_the_current_version() -> None:
    """The first thing anyone reads about this project should not name a version from three
    releases ago.

    Nothing tied these together, so `README.md` sat at **3.22.0 (current)** through 3.23.0,
    3.24.0 and 3.25.0 — the same way the plugin manifests sat at 2.5.0 through fifteen
    releases, and for the same reason: the release cut touches `pyproject.toml`, the
    manifests, the diagram and two `docs/specs` files, and nothing else knows a release
    happened. This is the check that makes the README part of the cut.
    """
    readme = (_ROOT / "README.md").read_text(encoding="utf-8")
    version = _pyproject_version()

    assert f"**{version} (current)**" in readme, (
        f"README.md's 'What's new' does not name {version} as current; "
        "add the entry and move the previous one down when cutting a release"
    )


def test_every_registered_tool_is_documented_for_a_user() -> None:
    """A tool nobody wrote down is one nobody calls.

    Registration is the source of truth — this reads `_TOOLS` rather than a second list, so a
    new tool fails here until it is documented, instead of shipping invisibly.

    The **guides** are the bar, not the manifests: a marketplace blurb naming twenty tools sells
    nothing, and run-control plumbing (`sdlc_run_status` and friends) belongs in a reference, not
    a pitch. What the manifests owe is the *headline* set, which
    `test_plugin_pitch_leads_with_comprehension_and_every_language` covers.
    """
    from orchestrator.plugin.server import _TOOLS

    documented = "\n".join(
        (_ROOT / doc).read_text(encoding="utf-8") for doc in ("CLAUDE_GUIDE.md", "CODEX_GUIDE.md")
    )
    missing = [fn.__name__ for fn in _TOOLS if fn.__name__ not in documented]

    assert not missing, f"registered but undocumented: {', '.join(missing)}"


def test_the_manifests_pitch_the_comprehension_tools_by_name() -> None:
    """The tools a user picks the plugin *for* — these belong in the blurb they read first."""
    for manifest in _MANIFESTS:
        blob = manifest.read_text(encoding="utf-8")
        named = [tool for tool in _COMP_TOOLS if tool in blob]
        # marketplace.json carries a shorter blurb than the plugin manifests; require the
        # headline pair everywhere and the full set where there is room for it.
        assert "map_repo" in blob or "comprehension" in blob, f"{manifest} names no comprehension tool"
        if manifest.name == "plugin.json":
            assert len(named) >= 6, f"{manifest} names only {named}"


def test_the_skill_tells_an_assistant_how_to_cross_a_repo_boundary() -> None:
    """A single-repo answer about an HTTP handler is confidently wrong, not merely partial."""
    body = _SKILL.read_text(encoding="utf-8")

    assert ".spine/repos.yaml" in body
    assert "repos=" in body
    # The standing block is what separates a reproducible answer from one that only looks it.
    assert "standing" in body and "reproducible" in body


def test_the_skill_and_the_prompts_sequence_the_same_tools() -> None:
    """The skill (Claude Code) and the MCP prompts (every other host) carry the same
    workflow. The prompts module is the source, because the skill is not in the wheel; this
    holds the skill to it. Only the comprehension and plan tools are the skill's remit — the
    operator prompt's `registry_*` tools are documented in the guides instead."""
    from orchestrator.plugin.prompts import PROMPT_TOOLS

    body = _SKILL.read_text(encoding="utf-8")
    for name, tools in PROMPT_TOOLS.items():
        if name == "whats-waiting-on-me":
            continue
        for tool in tools:
            assert tool in body, f"prompt {name!r} sequences {tool}, which the skill never mentions"
