"""P9b of SSPN-297 (SSPN-320): a framing's capabilities proposed by one capped, ledgered call.

The same machinery as option generation (D22: no second spending path): the estimate before the
call, the refusal over the $0.10 cap or the $0.59 tripwire, approval only by the exact estimate,
and a row in the change's ledger, with the reply kept when nothing is written. The call only
**proposes** (D27): it writes the framing's ``## Capabilities`` and never a change. Every test
drives a fake client: nothing here spends."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from orchestrator.core.llm.client import CompletionResult, LLMError, Message, ToolCall
from orchestrator.intake import framing as fr
from orchestrator.intake import requirements as rq
from orchestrator.sdlc import ideation as idn

_MODEL = "fake-ideation-model"
_CHEAP = {"SDLC_MODEL_PRICES": json.dumps({_MODEL: {"input_per_mtok": 1, "output_per_mtok": 5}})}
_DEAR = {"SDLC_MODEL_PRICES": json.dumps({_MODEL: {"input_per_mtok": 1, "output_per_mtok": 100}})}
_IDEA = "A small web app where a household shares a chore rota"
_ANSWERS = {
    "What problem does this solve?": "Households keep the rota in a group chat, so chores are forgotten.",
    "Who has this problem?": "Adults sharing a home; two to six people",
    "How will we know it worked? Describe an outcome someone can observe.": (
        "Each member sees whose turn each chore is and gets a reminder on the day."
    ),
    "What is explicitly out of scope?": "Payments or expense splitting; native mobile apps",
}
_THREE = ["Sign up and join a household", "Assign chores in turn", "Send a reminder on the day"]


def _framing(tmp_path: Path, *, answered: bool = True) -> rq.LoadedChange:
    change_id, _ = fr.draft_framing(tmp_path, _IDEA)
    if answered:
        requests = [rq.AnswerRequest(q, a) for q, a in _ANSWERS.items()]
        rq.record_answers(rq.load_change(change_id, root=tmp_path), requests, at="2026-11-02")
    return rq.load_change(change_id, root=tmp_path)


def _reply(caps: Any, *, cost: float = 0.0123, text: str = "") -> CompletionResult:
    calls = () if caps is None else (ToolCall("t1", "submit_capabilities", {"capabilities": caps}),)
    return CompletionResult(
        text=text,
        model=_MODEL,
        prompt_tokens=400,
        completion_tokens=80,
        cost_usd=cost,
        latency_ms=5.0,
        tool_calls=calls,
    )


class _Fake:
    def __init__(self, reply: CompletionResult | BaseException) -> None:
        self.reply = reply
        self.calls: list[dict[str, Any]] = []

    async def complete(self, messages: list[Message], *, model: str, **kwargs: Any) -> CompletionResult:
        self.calls.append({"messages": messages, "model": model, **kwargs})
        if isinstance(self.reply, BaseException):
            raise self.reply
        return self.reply


def _rows(change: rq.LoadedChange) -> list[dict[str, Any]]:
    path = change.directory / idn.LEDGER_NAME
    return [json.loads(x) for x in path.read_text().splitlines()] if path.is_file() else []


async def test_the_call_proposes_capabilities_into_the_framing_and_drafts_no_change(tmp_path: Path) -> None:
    framing = _framing(tmp_path)
    fake = _Fake(_reply(_THREE))
    result = await idn.generate_capabilities(framing, fake, model=_MODEL, env=_CHEAP)
    assert result.status == "written" and result.capabilities == tuple(_THREE)
    block = fr.load_capabilities(framing)
    assert [c.text for c in block.items] == _THREE
    assert all(c.source == f"generated · {_MODEL} · $0.0123" for c in block.items)
    assert sorted(p.name for p in (tmp_path / "changes").iterdir()) == [framing.change_id]
    [row] = _rows(framing)
    assert row["outcome"] == "written" and row["cost_usd"] == pytest.approx(0.0123)


async def test_the_prompt_carries_the_framing_why_and_the_capabilities_already_listed(tmp_path: Path) -> None:
    framing = _framing(tmp_path)
    fr.write_capabilities(framing, [fr.Capability("Assign chores in turn")])
    fake = _Fake(_reply(["Sign up and join a household", "Send a reminder", "Swap a turn"]))
    await idn.generate_capabilities(framing, fake, model=_MODEL, env=_CHEAP)
    [call] = fake.calls
    sent = "\n".join(m.content for m in call["messages"])
    assert "group chat" in sent and "two to six people" in sent and "expense splitting" in sent
    assert "Assign chores in turn" in sent
    assert call["tool_choice"] == "submit_capabilities"


async def test_existing_capabilities_are_kept_and_the_proposed_ones_follow(tmp_path: Path) -> None:
    framing = _framing(tmp_path)
    fr.write_capabilities(framing, [fr.Capability("Assign chores in turn")])
    fake = _Fake(_reply(["Sign up and join a household", "Send a reminder", "Swap a turn"]))
    result = await idn.generate_capabilities(framing, fake, model=_MODEL, env=_CHEAP)
    assert result.status == "written"
    assert [c.text for c in fr.load_capabilities(framing).items] == [
        "Assign chores in turn",
        "Sign up and join a household",
        "Send a reminder",
        "Swap a turn",
    ]


async def test_a_call_over_the_cap_is_refused_before_it_is_made(tmp_path: Path) -> None:
    framing = _framing(tmp_path)
    fake = _Fake(_reply(_THREE))
    result = await idn.generate_capabilities(framing, fake, model=_MODEL, env=_DEAR)
    assert result.status == "refused" and fake.calls == [] and _rows(framing) == []
    assert result.refusal is not None and "approve_usd=" in result.refusal.message


async def test_only_the_exact_estimate_shown_approves_it(tmp_path: Path) -> None:
    framing = _framing(tmp_path)
    fake = _Fake(_reply(_THREE))
    refused = await idn.generate_capabilities(framing, fake, model=_MODEL, env=_DEAR, approve_usd=1000.0)
    assert refused.status == "refused" and fake.calls == []
    assert refused.estimate is not None
    shown = round(refused.estimate.usd, 4)
    ok = await idn.generate_capabilities(framing, fake, model=_MODEL, env=_DEAR, approve_usd=shown)
    assert ok.status == "written" and len(fake.calls) == 1


async def test_the_tripwire_counts_what_option_generation_spent_on_the_same_change(tmp_path: Path) -> None:
    framing = _framing(tmp_path)
    with (framing.directory / idn.LEDGER_NAME).open("w") as fh:
        fh.write(json.dumps({"stage": "ideation", "cost_usd": 0.585}) + "\n")
    fake = _Fake(_reply(_THREE))
    result = await idn.generate_capabilities(framing, fake, model=_MODEL, env=_CHEAP)
    assert result.status == "refused" and fake.calls == []
    assert result.refusal is not None and [b.limit for b in result.refusal.breaches] == ["session_tripwire"]


async def test_a_framing_that_does_not_pass_is_refused_before_any_spend(tmp_path: Path) -> None:
    framing = _framing(tmp_path, answered=False)
    fake = _Fake(_reply(_THREE))
    result = await idn.generate_capabilities(framing, fake, model=_MODEL, env=_CHEAP)
    assert result.status == "failed" and fake.calls == [] and _rows(framing) == []
    assert any("does not pass" in p for p in result.problems)


@pytest.mark.parametrize(
    ("caps", "why"),
    [
        (["Only one", "Two"], "held 2 capabilities"),
        ([f"Capability number {n}" for n in range(1, 8)], "held 7 capabilities"),
        (["Assign chores", "assign  CHORES", "Send a reminder"], "repeats a capability"),
        (["Assign chores", "", "Send a reminder"], "blank or not text"),
        ("not a list", "held no capabilities"),
    ],
)
async def test_a_reply_that_is_not_a_list_of_three_to_six_writes_nothing_and_is_kept(
    tmp_path: Path, caps: Any, why: str
) -> None:
    framing = _framing(tmp_path)
    result = await idn.generate_capabilities(framing, _Fake(_reply(caps)), model=_MODEL, env=_CHEAP)
    assert result.status == "failed" and result.capabilities == ()
    assert not fr.load_capabilities(framing).present
    [row] = _rows(framing)
    assert row["outcome"] == "not written" and row["reply"]["tool_calls"]
    assert why in result.problems[0], result.problems
    assert any("kept in" in p for p in result.problems)


async def test_a_list_too_long_for_any_proposal_is_refused_before_any_spend(tmp_path: Path) -> None:
    framing = _framing(tmp_path)
    fr.write_capabilities(framing, [fr.Capability(f"Existing capability {n}") for n in range(1, 7)])
    fake = _Fake(_reply(_THREE))
    result = await idn.generate_capabilities(framing, fake, model=_MODEL, env=_CHEAP)
    assert result.status == "failed" and fake.calls == [] and _rows(framing) == []
    assert any("at most 8" in p for p in result.problems)
    assert len(fr.load_capabilities(framing).items) == 6


async def test_a_proposal_that_lands_past_eight_writes_nothing_and_is_kept(tmp_path: Path) -> None:
    framing = _framing(tmp_path)
    fr.write_capabilities(framing, [fr.Capability(f"Existing capability {n}") for n in range(1, 5)])
    five = ["Sign up", "Assign chores", "Send a reminder", "Swap a turn", "See the history"]
    result = await idn.generate_capabilities(framing, _Fake(_reply(five)), model=_MODEL, env=_CHEAP)
    assert result.status == "failed" and len(fr.load_capabilities(framing).items) == 4
    assert any("9 capabilities in all" in p for p in result.problems)
    [row] = _rows(framing)
    assert row["outcome"] == "not written"


async def test_a_block_with_a_problem_is_refused_before_any_spend(tmp_path: Path) -> None:
    framing = _framing(tmp_path)
    framing.design_path.write_text("## Capabilities\n\n- Assign chores\nstray line\n")
    fake = _Fake(_reply(_THREE))
    result = await idn.generate_capabilities(framing, fake, model=_MODEL, env=_CHEAP)
    assert result.status == "failed" and fake.calls == [] and _rows(framing) == []
    assert any("stray line" in p for p in result.problems)


async def test_a_proposal_naming_one_already_listed_writes_nothing(tmp_path: Path) -> None:
    framing = _framing(tmp_path)
    fr.write_capabilities(framing, [fr.Capability("Assign chores in turn")])
    fake = _Fake(_reply(["assign chores in TURN", "Send a reminder", "Swap a turn"]))
    result = await idn.generate_capabilities(framing, fake, model=_MODEL, env=_CHEAP)
    assert result.status == "failed" and len(fr.load_capabilities(framing).items) == 1


async def test_a_json_text_reply_is_read_when_the_model_did_not_call_the_tool(tmp_path: Path) -> None:
    framing = _framing(tmp_path)
    text = "```json\n" + json.dumps({"capabilities": _THREE}) + "\n```"
    result = await idn.generate_capabilities(
        framing, _Fake(_reply(None, text=text)), model=_MODEL, env=_CHEAP
    )
    assert result.status == "written" and result.capabilities == tuple(_THREE)


async def test_a_failed_call_is_charged_its_estimate_and_writes_nothing(tmp_path: Path) -> None:
    framing = _framing(tmp_path)
    est = idn.estimate_call(idn.capability_prompt(framing), _MODEL, env=_CHEAP)
    result = await idn.generate_capabilities(framing, _Fake(LLMError("boom")), model=_MODEL, env=_CHEAP)
    assert result.status == "failed" and not fr.load_capabilities(framing).present
    [row] = _rows(framing)
    assert row["outcome"] == "call failed" and row["cost_usd"] == pytest.approx(est.usd)


def test_the_estimate_counts_the_schema_of_the_tool_actually_sent(tmp_path: Path) -> None:
    prompt = idn.capability_prompt(_framing(tmp_path))
    sent = sum(len(m.content) for m in prompt.messages)
    assert prompt.chars == sent + len(json.dumps(prompt.tool.parameters)) + len(prompt.tool.description)


async def test_a_block_that_does_not_read_back_as_written_is_not_reported_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    framing = _framing(tmp_path)
    reads = {"n": 0}
    real = fr.load_capabilities

    def drifting(change: rq.LoadedChange) -> fr.CapabilitiesBlock:
        reads["n"] += 1
        return real(change) if reads["n"] == 1 else fr.CapabilitiesBlock(present=True)

    # The generator reads the block twice — before the call, and back after the write.
    monkeypatch.setattr("orchestrator.sdlc.ideation.load_capabilities", drifting)
    result = await idn.generate_capabilities(framing, _Fake(_reply(_THREE)), model=_MODEL, env=_CHEAP)
    assert result.status == "failed" and any("did not read back" in p for p in result.problems)


async def test_an_option_generation_call_is_unchanged_by_the_shared_path(tmp_path: Path) -> None:
    # D22: one spending path. The options call goes through the same code and still estimates its
    # own tool — a prompt now names the tool it is sent with.
    prompt = idn.build_prompt(rq.load_change(_framing(tmp_path).change_id, root=tmp_path).intent)
    assert prompt.tool.name == "submit_options"
    assert idn.capability_prompt(_framing(tmp_path / "b")).tool.name == "submit_capabilities"
