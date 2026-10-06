"""``requirements_check`` and ``requirements_answer`` — deterministic, model-free, honest about
who answered."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from orchestrator.intake import requirements as rq
from orchestrator.intake.openspec_writer import render_change, write_change
from orchestrator.intake.specs import FeatureSpec
from orchestrator.plugin import server
from orchestrator.plugin.auth import SCOPE_PLAN, SCOPE_READ
from orchestrator.plugin.server import (
    _TIER,
    _TOOLS,
    requirements_answer,
    requirements_check,
    scope_denial,
    tool_annotations,
    tool_scope,
)

_PROBLEM_Q = "What problem does this solve?"


def _skeleton(tmp_path: Path) -> Path:
    intent = rq.skeleton("Let finance export invoices as CSV")
    spec = FeatureSpec(intent_id=intent.id, title=intent.title, summary=intent.description)
    write_change(tmp_path, intent, render_change(spec, intent))
    return tmp_path / "changes" / intent.id.removeprefix("intent-")


def test_both_tools_are_registered_with_a_tier_and_an_output_type() -> None:
    from orchestrator.plugin.outputs import OUTPUTS

    names = {fn.__name__ for fn in _TOOLS}
    assert {"requirements_check", "requirements_answer"} <= names
    assert {"requirements_check", "requirements_answer"} <= set(_TIER) & set(OUTPUTS)


def test_check_is_read_only_and_answer_writes_one_file_without_destroying_anything() -> None:
    check, answer = tool_annotations("requirements_check"), tool_annotations("requirements_answer")
    assert check["read_only_hint"] and not check["destructive_hint"]
    assert not answer["read_only_hint"] and not answer["destructive_hint"] and answer["idempotent_hint"]
    assert tool_scope("requirements_check") == SCOPE_READ
    assert tool_scope("requirements_answer") == SCOPE_PLAN


def test_a_read_only_token_cannot_record_an_answer() -> None:
    class _Token:
        scopes = [SCOPE_READ]

    assert scope_denial("requirements_check", tool_scope("requirements_check"), _Token()) is None
    denied = scope_denial("requirements_answer", tool_scope("requirements_answer"), _Token())
    assert denied is not None and denied["needs"] == SCOPE_PLAN


def test_check_reports_the_open_items_and_says_it_is_ungrounded(tmp_path: Path) -> None:
    out = requirements_check(str(_skeleton(tmp_path)))
    assert out["passes"] is False and out["open_items"][0]["rule"] == "problem_stated"
    assert out["code_check"]["grounding"] == "ungrounded"
    assert len(out["questions"]["unresolved"]) == 4


def test_an_answer_over_mcp_is_recorded_as_relayed_never_as_a_person(tmp_path: Path) -> None:
    change = _skeleton(tmp_path)
    out = requirements_answer(str(change), _PROBLEM_Q, answer="Finance cannot reconcile invoices.")
    assert out["recorded"] == [
        {"question": _PROBLEM_Q, "status": "answered", "origin": "relayed", "channel": "mcp"}
    ]
    md = (change / "proposal.md").read_text()
    assert "**Answer** (relayed · mcp ·" in md and "(user ·" not in md
    assert out["passes"] is False and _PROBLEM_Q not in out["unresolved"]


def test_a_deferral_names_its_owner(tmp_path: Path) -> None:
    change = _skeleton(tmp_path)
    out = requirements_answer(str(change), _PROBLEM_Q, defer_to="@pm")
    assert out["recorded"][0]["status"] == "deferred"
    assert "**Deferred** to @pm" in (change / "proposal.md").read_text()


def test_the_loop_a_host_runs_check_answer_check_reaches_a_passing_gate(tmp_path: Path) -> None:
    change = _skeleton(tmp_path)
    answers = {
        _PROBLEM_Q: "Finance cannot reconcile invoices.",
        "Who has this problem?": "Finance operations",
        "How will we know it worked? Describe an outcome someone can observe.": "Closes within 1 day.",
        "What is explicitly out of scope?": "PDF",
    }
    for question in requirements_check(str(change))["questions"]["unresolved"]:
        out = requirements_answer(str(change), question, answer=answers[question])
    assert out["passes"] is True and out["unresolved"] == []
    assert requirements_check(str(change))["passes"] is True


def test_bad_input_is_an_error_not_an_exception(tmp_path: Path) -> None:
    assert "not a change directory" in requirements_check(str(tmp_path / "nope"))["error"]
    change = _skeleton(tmp_path)
    assert (
        "no open question matches"
        in requirements_answer(str(change), "Which colour?", answer="blue")["error"]
    )
    assert "exactly one" in requirements_answer(str(change), _PROBLEM_Q)["error"]
    assert "not a change directory" in requirements_answer(str(tmp_path), _PROBLEM_Q, answer="x")["error"]


def test_a_bad_repository_path_is_an_error(tmp_path: Path) -> None:
    out = requirements_check(str(_skeleton(tmp_path)), str(tmp_path / "no-such-repo"))
    assert "error" in out


def test_check_against_a_repository_names_the_criteria_that_already_exist(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "export.py").write_text("def export_invoices_csv(rows):\n    return ','.join(rows)\n")
    change = tmp_path / "changes" / "exp"
    (change / "specs" / "cap").mkdir(parents=True)
    (change / "proposal.md").write_text(
        "# Proposal: Export\n\n## Why\n### Problem\nFinance cannot reconcile invoices.\n"
        "### Users\n- Finance\n### Outcome\nCloses within 1 day.\n\n"
        "## What Changes\nDo it.\n### Non-goals\n- PDF\n"
    )
    (change / "specs" / "cap" / "spec.md").write_text(
        "## ADDED Requirements\n\n### Requirement: Export\nThe system SHALL call `export_invoices_csv`.\n\n"
        "#### Scenario: ok\n- WHEN run\n- THEN a CSV is returned\n"
    )
    out = requirements_check(str(change), str(repo))
    assert out["passes"] is True
    assert out["code_check"]["grounding"] in ("grounded", "untrusted")
    assert any("export_invoices_csv" in str(c) for c in out["code_check"]["criteria_naming_existing_code"])


def test_neither_tool_can_make_a_model_call(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Every route to a model — the provider client, the budget wrapper, the catalog's resolver —
    is made to raise; the tools still answer. (No model is configured here either, which is the
    same property from the other side: they need none.)"""
    from orchestrator.core.llm import catalog
    from orchestrator.core.llm.budget import BudgetedLLMClient
    from orchestrator.core.llm.litellm_client import LiteLLMClient

    calls: list[str] = []

    async def boom(self: Any, *a: Any, **k: Any) -> Any:
        calls.append("complete")
        raise AssertionError("a model was called")

    def resolve(*a: Any, **k: Any) -> Any:
        calls.append("resolve")
        raise AssertionError("a model was resolved")

    monkeypatch.setattr(LiteLLMClient, "complete", boom)
    monkeypatch.setattr(BudgetedLLMClient, "complete", boom)
    monkeypatch.setattr(catalog, "resolve", resolve)
    for var in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    change = _skeleton(tmp_path)
    requirements_check(str(change))
    requirements_answer(str(change), _PROBLEM_Q, answer="x")
    requirements_check(str(change))
    assert calls == []


def test_the_logic_lives_outside_server_py() -> None:
    """server.py is already 114 KB; the wrappers are thin and the work is in plugin/requirements_tools."""
    import inspect

    assert "requirements_tools" in inspect.getsource(server.requirements_check)
    assert "requirements_tools" in inspect.getsource(server.requirements_answer)
