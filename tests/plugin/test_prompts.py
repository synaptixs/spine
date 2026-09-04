"""MCP prompts: the skill's workflow, through the protocol."""

from __future__ import annotations

import importlib.util
import re

import pytest

from orchestrator.plugin.prompts import (
    _PROMPTS,
    PROMPT_TOOLS,
    investigate_ticket,
    orient,
    plan_then_approve,
    triage_bug,
    whats_waiting_on_me,
)


def test_every_prompt_names_its_tools_in_order() -> None:
    """The text and the parity table cannot disagree: each tool the table lists appears in
    the rendered prompt, in that order — the order is the point of a prompt."""
    rendered = {
        "orient": orient(),
        "investigate-ticket": investigate_ticket("t"),
        "triage-bug": triage_bug("b"),
        "plan-then-approve": plan_then_approve("t"),
        "whats-waiting-on-me": whats_waiting_on_me(),
    }
    assert set(rendered) == set(PROMPT_TOOLS) == {name for name, _ in _PROMPTS}
    for name, tools in PROMPT_TOOLS.items():
        positions = [rendered[name].find(f"`{tool}`") for tool in tools]
        assert all(p >= 0 for p in positions), (name, tools)
        assert positions == sorted(positions), (name, "tools out of order")


def test_every_tool_a_prompt_names_is_registered() -> None:
    from orchestrator.plugin.server import _TOOLS

    registered = {fn.__name__ for fn in _TOOLS}
    for name, tools in PROMPT_TOOLS.items():
        assert set(tools) <= registered, (name, set(tools) - registered)


def test_prompts_carry_their_arguments_into_the_text() -> None:
    assert "repo_path=`/x`" in orient("/x") and "current repository" in orient()
    text = investigate_ticket("Add refunds", "Customers cannot refund", repo_path="/r")
    assert "Add refunds" in text and "Customers cannot refund" in text and "repo_path=`/r`" in text
    assert "Traceback" in triage_bug("Traceback (most recent call last) …")
    text = plan_then_approve("Persist ledger", "to disk", "survives restart")
    assert "Persist ledger" in text and "survives restart" in text


def test_the_gated_prompts_say_who_decides() -> None:
    """The prompts are where a host's model learns the gates; they must not read as
    'approve it and build'."""
    plan = plan_then_approve("t")
    assert "Do not approve it yourself" in plan
    assert "`confirm=true`" in plan and "`live=false`" in plan
    waiting = whats_waiting_on_me()
    assert "Only when I say" in waiting and "rejection ends the run" in waiting


def test_prompts_stop_at_analysis_where_they_should() -> None:
    assert "Change nothing" in investigate_ticket("t")
    assert "do not change code" in triage_bug("b")


@pytest.mark.skipif(importlib.util.find_spec("mcp") is None, reason="needs the 'mcp' extra")
async def test_prompts_reach_the_host_with_their_arguments() -> None:
    from orchestrator.plugin.server import build_server

    server = build_server()
    prompts = {p.name: p for p in await server.list_prompts()}
    assert set(prompts) == set(PROMPT_TOOLS)
    ticket = prompts["investigate-ticket"]
    args = {a.name: a.required for a in ticket.arguments or []}
    assert args == {"title": True, "problem": False, "repo_path": False}
    assert ticket.description and "ticket" in ticket.description.lower()
    got = await server.get_prompt("investigate-ticket", {"title": "Add refunds"})
    assert got.messages[0].role == "user"
    text = got.messages[0].content.text
    assert "Add refunds" in text and "`investigate`" in text
    # No prompt renders a stray Python repr — e.g. `None` for an omitted optional.
    for name in PROMPT_TOOLS:
        p = prompts[name]
        required = {a.name for a in p.arguments or [] if a.required}
        got = await server.get_prompt(name, dict.fromkeys(required, "x"))
        assert not re.search(r"\bNone\b", got.messages[0].content.text)
