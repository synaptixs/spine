"""The plugin manifests + the understand-codebase Agent Skill stay valid and in sync."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from orchestrator.sdlc.toolchains import TOOLCHAINS

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
# How each registered codegen language is written in prose. Keyed off the registry rather
# than restated, so a new front-end fails here — with a KeyError naming it — instead of
# quietly shipping a pitch that still advertises the previous count. The eight-language
# string survived PHP and Perl exactly because this tuple was hand-written.
_DISPLAY_NAMES = {
    "python": "Python",
    "java": "Java",
    "kotlin": "Kotlin",
    "typescript": "TypeScript",
    "csharp": "C#",
    "c": "C",
    "cpp": "C++",
    "go": "Go",
    "rust": "Rust",
    "php": "PHP",
    "perl": "Perl",
    "sql": "SQL",
}
_LANGUAGES = tuple(_DISPLAY_NAMES[name] for name in TOOLCHAINS)


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


def test_the_operator_home_page_names_every_language() -> None:
    """The same claim, one surface further on: the console's capability grid sold six
    languages while ten front-ends shipped. It is hand-written copy that no release step
    reads, so it goes stale the same way the manifests did."""
    from orchestrator.registry.api.web.home import _CAPS

    copy = " ".join(f"{headline} {detail}" for _icon, headline, detail in _CAPS)
    for language in _LANGUAGES:
        assert language in copy, f"the home page capability grid should name {language}"


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

    The **shared guide** is the bar, not the manifests: a marketplace blurb naming twenty tools sells
    nothing, and run-control plumbing (`sdlc_run_status` and friends) belongs in a reference, not
    a pitch. What the manifests owe is the *headline* set, which
    `test_plugin_pitch_leads_with_comprehension_and_every_language` covers.
    """
    from orchestrator.plugin.server import _TOOLS

    documented = (_ROOT / "AGENT_GUIDE.md").read_text(encoding="utf-8")
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


# The three files that launch the MCP server: the Claude Code plugin, the Codex plugin, and
# this repository's own project config. A host reads them as-is, so the launch line they carry
# *is* the install a user gets.
_SERVER_CONFIGS = [
    _ROOT / "plugins" / "spine" / ".mcp.json",
    _ROOT / "codex-marketplace" / "plugins" / "spine" / ".mcp.json",
    _ROOT / ".mcp.json",
]


def _pyproject_extras() -> dict[str, list[str]]:
    import tomllib

    with (_ROOT / "pyproject.toml").open("rb") as fh:
        extras: dict[str, list[str]] = tomllib.load(fh)["project"]["optional-dependencies"]
    return extras


def _laptop_extras() -> set[str]:
    """The extras the one-line install names: the server, doc ingestion, and every language
    front-end in ``[languages]`` except ``rust``.

    ``rust`` is out because its grammar ships as source only and fails to build on a Mac whose
    SDK the linker rejects — measured 2026-10-26, and why ``[all]`` / ``[languages]`` cannot be
    the line. Read off ``pyproject.toml`` so a new front-end fails here until the line names it.
    """
    (member,) = _pyproject_extras()["languages"]
    languages = set(member.split("[", 1)[1].rstrip("]").split(","))
    return {"mcp", "docs"} | (languages - {"rust"})


def _launch(config: Path) -> list[str]:
    (server,) = json.loads(config.read_text(encoding="utf-8"))["mcpServers"].values()
    return [server["command"], *server["args"]]


@pytest.mark.parametrize("config", _SERVER_CONFIGS, ids=lambda p: str(p.relative_to(_ROOT)))
def test_the_server_launches_through_uvx_at_the_latest_release(config: Path) -> None:
    """``uvx`` needs nothing installed first. ``@latest`` is load-bearing: without it, ``uvx``
    reuses any copy an earlier ``uv tool install synaptixs-spine`` left behind — measured
    serving 3.30.0 to a host while PyPI was at 3.63.1, with no warning."""
    command, flag, spec, entry = _launch(config)

    assert (command, flag, entry) == ("uvx", "--from", "orchestrator-mcp")
    assert spec.startswith("synaptixs-spine[") and spec.endswith("]@latest"), spec


@pytest.mark.parametrize("config", _SERVER_CONFIGS, ids=lambda p: str(p.relative_to(_ROOT)))
def test_the_server_installs_every_language_that_ships_wheels(config: Path) -> None:
    """``[mcp]`` alone maps a TypeScript, Java or Go repository to zero nodes, silently
    (B107); ``[all]`` does not install on every Mac. The line names the set in between."""
    spec = _launch(config)[2]
    named = spec.split("[", 1)[1].split("]", 1)[0].split(",")

    assert set(named) == _laptop_extras(), sorted(set(named) ^ _laptop_extras())
    assert len(named) == len(set(named))
    assert set(named) <= set(_pyproject_extras()), "names an extra pyproject does not define"


#: The hosts AGENT_GUIDE.md §3 gives a one-step recipe for, by their `###` heading.
_HOSTS = {"Claude Code", "Codex", "VS Code (GitHub Copilot)", "Gemini CLI", "Cursor"}


def _install_recipes(guide: str) -> dict[str, str]:
    """§3's host recipes, heading → body: a count over the whole guide would pass with
    three recipes deleted, because the line also appears in §3's preamble and §4."""
    install = guide.split("\n## 3. Install\n", 1)[1].split("\n## 4. ", 1)[0]
    parts = install.split("\n### ")[1:]
    return {part.split("\n", 1)[0].strip(): part for part in parts}


def test_the_vs_code_recipe_is_valid_json() -> None:
    """`code --add-mcp` takes one JSON argument; a stray quote there fails in the shell, not here."""
    import re

    guide = (_ROOT / "AGENT_GUIDE.md").read_text(encoding="utf-8")
    recipe = _install_recipes(guide)["VS Code (GitHub Copilot)"]
    (arg,) = re.findall(r"code --add-mcp '([^']+)'", recipe)
    config = json.loads(arg)
    assert [config["command"], *config["args"]] == _launch(_SERVER_CONFIGS[0]) and config["name"] == "spine"


def test_the_guide_documents_the_line_the_plugins_run() -> None:
    """One line, three manifests and five host recipes: the guide may not drift from what the
    plugins actually launch."""
    lines = {" ".join(_launch(config)) for config in _SERVER_CONFIGS}
    assert len(lines) == 1, lines
    (line,) = lines
    spec = line.split()[2]

    guide = (_ROOT / "AGENT_GUIDE.md").read_text(encoding="utf-8")
    recipes = _install_recipes(guide)
    assert set(recipes) == _HOSTS, sorted(set(recipes) ^ _HOSTS)
    for host, text in recipes.items():
        assert spec in text, f"AGENT_GUIDE.md's {host} recipe should give {spec}"
    assert f"'{spec.removesuffix('@latest')}=={_pyproject_version()}'" in guide, (
        "the pinned variant should name the released version"
    )


def test_the_cursor_install_link_decodes_to_the_same_line() -> None:
    """Cursor's install link carries its config as base64, the one copy of the line nobody can
    read by eye — so it is the copy that would drift unnoticed."""
    import base64
    import re
    from urllib.parse import parse_qs, urlparse

    guide = (_ROOT / "AGENT_GUIDE.md").read_text(encoding="utf-8")
    (link,) = re.findall(r"cursor://anysphere\.cursor-deeplink/mcp/install\?\S+", guide)
    query = parse_qs(urlparse(link).query)
    config = json.loads(base64.b64decode(query["config"][0]))

    assert query["name"] == ["spine"]
    assert [config["command"], *config["args"]] == _launch(_SERVER_CONFIGS[0])
