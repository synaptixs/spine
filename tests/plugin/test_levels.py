"""Adoption levels: ``ORCHESTRATOR_MCP_LEVEL`` decides which tiers a host is shown.

``pkg`` (the default) is comprehension only, ``plan`` adds the plan-and-decide tier, ``build``
adds the run and audit tiers — every tool, the list a host saw before levels existed. A
level is a filter on ``_TIER``, never a second classification: these tests read the expected
set off the tier table, and pin the counts so a re-tiered tool is a visible change.
"""

from __future__ import annotations

import importlib.util
import os
from typing import Any

import pytest

needs_mcp = pytest.mark.skipif(importlib.util.find_spec("mcp") is None, reason="needs the 'mcp' extra")

#: The tier names each level shows — the contract, written out rather than imported.
EXPECTED = {
    "pkg": {"comprehend"},
    "plan": {"comprehend", "plan"},
    "build": {"comprehend", "plan", "run", "audit"},
}


def _tiered(level: str) -> set[str]:
    from orchestrator.plugin.server import _TIER

    return {name for name, tier in _TIER.items() if tier.name in EXPECTED[level]}


async def _listed(server: Any) -> set[str]:
    return {t.name for t in await server.list_tools()}


@pytest.fixture(autouse=True)
def _no_level_in_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ORCHESTRATOR_MCP_LEVEL", raising=False)


@needs_mcp
@pytest.mark.parametrize("level", ["pkg", "plan", "build"])
async def test_each_level_registers_exactly_its_tiers(level: str) -> None:
    from orchestrator.plugin.server import build_server

    assert await _listed(build_server(level=level)) == _tiered(level)


@needs_mcp
async def test_the_counts_are_seventeen_twenty_one_and_all() -> None:
    from orchestrator.plugin.server import _TOOLS, build_server

    counts = {lvl: len(await _listed(build_server(level=lvl))) for lvl in EXPECTED}
    assert counts == {"pkg": 17, "plan": 21, "build": len(_TOOLS)}


@needs_mcp
async def test_the_default_level_is_pkg() -> None:
    """The behaviour change this phase ships: with nothing set, a host sees comprehension only."""
    from orchestrator.plugin.server import build_server

    tools = await _listed(build_server())
    assert tools == _tiered("pkg")
    assert {"doctor", "map_repo", "blast_radius", "docs_for"} <= tools
    assert not {"sdlc_plan", "sdlc_feature", "registry_decide", "audit_repo"} & tools


@needs_mcp
async def test_the_env_setting_chooses_the_level(monkeypatch: pytest.MonkeyPatch) -> None:
    from orchestrator.plugin.server import build_server

    monkeypatch.setenv("ORCHESTRATOR_MCP_LEVEL", "plan")
    tools = await _listed(build_server())
    assert "sdlc_plan" in tools and "sdlc_feature" not in tools


@needs_mcp
async def test_the_level_is_case_and_space_tolerant(monkeypatch: pytest.MonkeyPatch) -> None:
    from orchestrator.plugin.server import build_server

    monkeypatch.setenv("ORCHESTRATOR_MCP_LEVEL", " Plan ")
    assert await _listed(build_server()) == _tiered("plan")


@needs_mcp
async def test_levels_nest_and_build_is_every_tool() -> None:
    from orchestrator.plugin.server import _TOOLS, build_server

    pkg, plan, build = [await _listed(build_server(level=lvl)) for lvl in ("pkg", "plan", "build")]
    assert pkg < plan < build
    assert build == {fn.__name__ for fn in _TOOLS}


@needs_mcp
def test_an_unknown_level_is_refused_naming_the_valid_ones() -> None:
    from orchestrator.plugin.server import build_server

    with pytest.raises(ValueError, match=r"'admin'.*pkg.*plan.*build"):
        build_server(level="admin")


@needs_mcp
def test_an_unknown_level_in_env_is_refused_not_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    """A typo must not fall back to a default silently: ``bulid`` would hide the run tools
    from someone who asked for all of them, with nothing to say why."""
    from orchestrator.plugin.server import build_server

    monkeypatch.setenv("ORCHESTRATOR_MCP_LEVEL", "bulid")
    with pytest.raises(ValueError, match="bulid"):
        build_server()


@needs_mcp
@pytest.mark.parametrize("level", ["pkg", "plan", "build"])
def test_registration_stays_total_at_every_level(level: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """An untiered tool is refused even at a level that would not show it — otherwise
    ``pkg`` would quietly skip the check the tier table exists for."""
    from orchestrator.plugin import server as mod

    def orphan() -> dict[str, Any]:
        return {}

    monkeypatch.setattr(mod, "_TOOLS", (*mod._TOOLS, orphan))
    with pytest.raises(RuntimeError, match="no tier"):
        mod.build_server(level=level)


@needs_mcp
async def test_the_http_server_takes_the_level_too(monkeypatch: pytest.MonkeyPatch) -> None:
    from orchestrator.plugin.server import build_http_server

    for var in ("ORCHESTRATOR_MCP_TOKEN", "ORCHESTRATOR_MCP_INTROSPECTION_URL"):
        monkeypatch.delenv(var, raising=False)
    assert await _listed(build_http_server(level="plan").server) == _tiered("plan")
    assert await _listed(build_http_server().server) == _tiered("pkg")


@needs_mcp
@pytest.mark.parametrize("level", ["pkg", "plan", "build"])
async def test_no_prompt_sends_the_host_to_a_hidden_tool(level: str) -> None:
    from orchestrator.plugin.prompts import PROMPT_TOOLS
    from orchestrator.plugin.server import build_server

    server = build_server(level=level)
    tools = await _listed(server)
    prompts = {p.name for p in await server.list_prompts()}
    for name in prompts:
        assert set(PROMPT_TOOLS[name]) <= tools, (level, name)
    # and every prompt whose tools are all shown is offered
    assert prompts == {n for n, needed in PROMPT_TOOLS.items() if set(needed) <= tools}
    assert "orient" in prompts


def test_doctor_reports_the_level_and_how_to_raise_it(monkeypatch: pytest.MonkeyPatch) -> None:
    from orchestrator.plugin.server import doctor

    level = doctor()["level"]
    assert level["name"] == "pkg" and level["tools"] == 17
    assert "ORCHESTRATOR_MCP_LEVEL=build" in level["hint"]

    monkeypatch.setenv("ORCHESTRATOR_MCP_LEVEL", "build")
    level = doctor()["level"]
    assert level["name"] == "build" and level["tools"] == level["of"]


def test_doctor_names_a_bad_level_instead_of_failing(monkeypatch: pytest.MonkeyPatch) -> None:
    """``doctor`` is the tool you call when something is wrong; it reports the bad value."""
    from orchestrator.plugin.server import doctor

    monkeypatch.setenv("ORCHESTRATOR_MCP_LEVEL", "bulid")
    level = doctor()["level"]
    assert level["name"] == "bulid" and level["tools"] == 0
    assert "pkg" in level["hint"] and "build" in level["hint"]


def test_the_cli_flag_reaches_the_server(monkeypatch: pytest.MonkeyPatch) -> None:
    import orchestrator.plugin.__main__ as entry
    from orchestrator.plugin import server as mod

    seen: dict[str, Any] = {}

    class _Stub:
        def run(self) -> None:
            seen["ran"] = True

    def fake_build_server(level: str | None = None) -> _Stub:
        seen["level"] = level
        return _Stub()

    monkeypatch.setattr(mod, "build_server", fake_build_server)
    monkeypatch.setenv("ORCHESTRATOR_MCP_LEVEL", "pkg")  # the flag wins over the env
    entry.main(["--level", "plan"])
    assert seen == {"level": "plan", "ran": True}
    # …and `doctor`, which reads the env at call time, reports what the flag chose.
    assert os.environ["ORCHESTRATOR_MCP_LEVEL"] == "plan"


def test_the_cli_refuses_an_unknown_level(capsys: pytest.CaptureFixture[str]) -> None:
    import orchestrator.plugin.__main__ as entry

    with pytest.raises(SystemExit):
        entry.main(["--level", "admin"])
    assert "invalid choice: 'admin'" in capsys.readouterr().err


def test_the_cli_refuses_an_unknown_level_from_the_env(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The env is checked by ``resolve_level``, not argparse — it must still stop with a
    usage error (exit 2), never a traceback or a fallback to a default."""
    import orchestrator.plugin.__main__ as entry

    monkeypatch.setenv("ORCHESTRATOR_MCP_LEVEL", "bulid")
    with pytest.raises(SystemExit) as stopped:
        entry.main([])
    assert stopped.value.code == 2
    err = capsys.readouterr().err
    assert "unknown Spine MCP level 'bulid'" in err and "pkg, plan, build" in err


@needs_mcp
async def test_a_host_launching_the_plain_command_gets_the_pkg_list() -> None:
    """End to end over stdio, the way a host starts it: no flag, no env — the default."""
    import sys

    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(command=sys.executable, args=["-m", "orchestrator.plugin"])
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        tools = {t.name for t in (await session.list_tools()).tools}
        assert tools == _tiered("pkg")
        doctor = (await session.call_tool("doctor", {})).structured_content or {}
        assert doctor["level"]["name"] == "pkg"
