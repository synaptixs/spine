"""Typed output: every tool advertises what it returns, and the types cannot drift from it."""

from __future__ import annotations

import importlib.util

import pytest

from orchestrator.plugin.outputs import OUTPUTS, Failure, undeclared_keys


def test_every_registered_tool_has_an_output_type_and_nothing_else_does() -> None:
    from orchestrator.plugin.server import _TOOLS

    assert set(OUTPUTS) == {fn.__name__ for fn in _TOOLS}


def test_every_output_type_carries_the_failure_keys() -> None:
    """A tool's error path must fit its own type, or the SDK would reject the error."""
    import typing

    failure = set(typing.get_type_hints(Failure))
    for name, td in OUTPUTS.items():
        assert failure <= set(typing.get_type_hints(td)), name


def test_undeclared_keys_finds_drift_at_any_depth() -> None:
    from orchestrator.plugin.outputs import BlastRadiusOut

    ok = {"symbol": "x", "found": True, "matches": [{"id": "a", "callers": [{"id": "b", "at": "f:1"}]}]}
    assert undeclared_keys(ok, BlastRadiusOut) == []
    drift = {"symbol": "x", "novel": 1, "matches": [{"id": "a", "callers": [{"id": "b", "since": 2}]}]}
    assert undeclared_keys(drift, BlastRadiusOut) == ["novel", "matches[0].callers[0].since"]
    assert undeclared_keys("not a dict", BlastRadiusOut) == []


def test_an_untyped_tool_is_refused_at_registration(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("mcp")
    import orchestrator.plugin.server as mod

    def orphan() -> dict[str, int]:
        return {}

    monkeypatch.setitem(mod._TIER, "orphan", mod.COMPREHEND_LOCAL)
    monkeypatch.setattr(mod, "_TOOLS", (*mod._TOOLS, orphan))
    with pytest.raises(RuntimeError, match="no output type"):
        mod.build_server()


@pytest.mark.skipif(importlib.util.find_spec("mcp") is None, reason="needs the 'mcp' extra")
async def test_every_tool_advertises_its_output_schema_to_the_host() -> None:
    from orchestrator.plugin.server import build_server

    tools = {t.name: t for t in await build_server().list_tools()}
    for name, td in OUTPUTS.items():
        schema = tools[name].output_schema
        assert schema and schema.get("type") == "object", name
        assert schema.get("title") == td.__name__, name
        assert "error" in schema["properties"] and "required" not in schema, name  # no key is required
        assert "ctx" not in tools[name].input_schema["properties"], name  # unchanged by typing


@pytest.mark.skipif(importlib.util.find_spec("mcp") is None, reason="needs the 'mcp' extra")
async def test_a_result_reaches_the_host_structured_with_every_key_kept() -> None:
    """`extra="allow"`: a key the type happened to miss still reaches the host; the drift
    guard, not the runtime, is what enforces the declarations."""
    from orchestrator.plugin.server import build_server

    r = await build_server().call_tool("doctor", {})
    assert r.structured_content and "all_passed" in r.structured_content and "server" in r.structured_content
    assert r.structured_content["server"]["package"] == "synaptixs-spine"
