"""P5 of SSPN-297 (SSPN-302): the option generator — one capped, ledgered model call (D6, D11).

Every test drives a fake client: nothing here spends. The prompt is built from the change's
why-fields, its landing sites, the doc sections bound to them and a bounded overview, each part in
its own share of the budget with what it cut recorded. The call is refused before it is made when
its estimate is over the per-call cap or would take the change past the session tripwire; a reply
that does not parse is reported and nothing is written."""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any

import pytest

from orchestrator.core.llm import catalog
from orchestrator.core.llm.client import CompletionResult, LLMError, Message, ToolCall
from orchestrator.core.llm.recording import TokenLedger
from orchestrator.intake import options as op
from orchestrator.intake import requirements as rq
from orchestrator.intake.intents import Intent
from orchestrator.pkg import FactStore
from orchestrator.pkg.docs import DocPage
from orchestrator.pkg.facts import Edge, EdgeKind, FactBatch, Node, NodeKind, Provenance
from orchestrator.sdlc import ideation as idn
from orchestrator.sdlc.landings import Landing

_MODEL = "fake-ideation-model"
#: $1 in, $5 out per million tokens: a 2,000-token output cap costs $0.01.
_CHEAP = {"SDLC_MODEL_PRICES": json.dumps({_MODEL: {"input_per_mtok": 1, "output_per_mtok": 5}})}
#: $100 out per million tokens: the output cap alone is $0.20, over the $0.10 cap.
_DEAR = {"SDLC_MODEL_PRICES": json.dumps({_MODEL: {"input_per_mtok": 1, "output_per_mtok": 100}})}

_PROPOSAL = (
    "# Proposal: Export\n\n## Why\nFinance needs invoices exported as CSV.\n"
    "### Problem\nFinance cannot close the month.\n### Users\n- Finance\n"
    "### Outcome\nCloses within 1 day.\n\n## What Changes\nDo it.\n### Non-goals\n- PDF\n"
)


def _intent(**extra: Any) -> Intent:
    fields: dict[str, Any] = {
        "id": "intent-export",
        "title": "Export invoices",
        "description": "Finance needs invoices exported as CSV.",
        "problem": "Finance cannot close the month.",
        "users": ["Finance"],
        "outcome": "Closes within 1 day.",
        "non_goals": ["PDF"],
    }
    fields.update(extra)
    return Intent(**fields)


def _landing(n: int, *, weak: bool = False) -> Landing:
    return Landing(
        name=f"export_{n}",
        where=f"app/export_{n}.py:{n}",
        kind="Function",
        callers=n,
        module=f"app.export_{n}",
        node_id=f"py:app.export_{n}.export_{n}",
        weak=weak,
    )


def _change(tmp_path: Path, options: list[op.Option] | None = None) -> rq.LoadedChange:
    directory = tmp_path / "changes" / "c"
    directory.mkdir(parents=True)
    (directory / "proposal.md").write_text(_PROPOSAL)
    change = rq.load_change("c", root=tmp_path)
    if options:
        op.write_options(change, options)
    return change


def _option(title: str, **extra: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"title": title, "approach": f"Do {title} in two sentences. Then test it."}
    out.update(extra)
    return out


def _reply(
    options: list[dict[str, Any]] | None,
    *,
    cost: float = 0.0123,
    cost_known: bool = True,
    text: str = "",
) -> CompletionResult:
    calls = () if options is None else (ToolCall("t1", "submit_options", {"options": options}),)
    return CompletionResult(
        text=text,
        model=_MODEL,
        prompt_tokens=1000,
        completion_tokens=300,
        cost_usd=cost,
        latency_ms=5.0,
        tool_calls=calls,
        cost_known=cost_known,
    )


class _Fake:
    """An LLM client that records each call and answers with a fixed reply (or raises)."""

    def __init__(self, reply: CompletionResult | Exception) -> None:
        self.reply = reply
        self.calls: list[dict[str, Any]] = []

    async def complete(self, messages: list[Message], *, model: str, **kwargs: Any) -> CompletionResult:
        self.calls.append({"messages": messages, "model": model, **kwargs})
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


def _prompt() -> idn.IdeationPrompt:
    return idn.build_prompt(_intent(), landings=[_landing(1), _landing(2)])


def _ledger_rows(change: rq.LoadedChange) -> list[dict[str, Any]]:
    path = change.directory / idn.LEDGER_NAME
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


# ---- the stage ------------------------------------------------------------------------------------


def test_the_ideation_stage_reads_its_own_variable_then_intake_then_global(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in ("ORCHESTRATOR_IDEATION_MODEL", "ORCHESTRATOR_INTAKE_MODEL", "ORCHESTRATOR_MODEL"):
        monkeypatch.delenv(name, raising=False)
    assert catalog.STAGE_ENV["ideation"] == (
        "ORCHESTRATOR_IDEATION_MODEL",
        "ORCHESTRATOR_INTAKE_MODEL",
        "ORCHESTRATOR_MODEL",
    )
    assert catalog.resolve("ideation") == catalog.DEFAULT_MODEL
    monkeypatch.setenv("ORCHESTRATOR_MODEL", "global")
    assert catalog.resolve("ideation") == "global"
    monkeypatch.setenv("ORCHESTRATOR_INTAKE_MODEL", "intake")
    assert catalog.resolve("ideation") == "intake"
    monkeypatch.setenv("ORCHESTRATOR_IDEATION_MODEL", "ideation")
    assert catalog.resolve("ideation") == "ideation"
    assert catalog.resolve("intake") == "intake"  # moving ideation does not move intake


def test_the_env_skeleton_names_the_ideation_variable() -> None:
    from orchestrator.init_scaffold import render_env_template

    assert "ORCHESTRATOR_IDEATION_MODEL" in render_env_template({})


# ---- the prompt -----------------------------------------------------------------------------------


def test_the_prompt_carries_the_why_fields_landings_docs_and_overview() -> None:
    docs = [idn.DocSection(title="docs/export.md#csv", text="We dropped XML in 2025.", symbol="export_1")]
    overview = {
        "summary": {"nodes": 40, "edges": 90},
        "modules": [{"module": "app/export_1.py", "nodes": 12}],
        "top_symbols": [{"id": "py:app.core.run", "degree": 9}],
        "truncated": {"modules": False},
    }
    prompt = idn.build_prompt(_intent(), landings=[_landing(1)], docs=docs, overview=overview)
    user = prompt.messages[-1].content
    for text in (
        "Finance cannot close the month.",
        "Finance",
        "Closes within 1 day.",
        "PDF",
        "py:app.export_1.export_1",
        "app/export_1.py:1",
        "We dropped XML in 2025.",
        "app/export_1.py",
        "py:app.core.run",
    ):
        assert text in user, text
    assert [p.name for p in prompt.parts] == ["why", "code", "docs", "overview"]


def test_each_part_keeps_to_its_own_share_and_says_what_it_cut() -> None:
    landings = [_landing(n) for n in range(1, 400)]
    docs = [idn.DocSection(title=f"d{n}.md", text="x" * 500, symbol="s") for n in range(200)]
    prompt = idn.build_prompt(_intent(), landings=landings, docs=docs)
    for part in prompt.parts:
        assert part.chars <= part.budget, part.name
        assert part.budget == int(idn.PROMPT_BUDGET_CHARS * idn.SHARES[part.name])
    code, doc = prompt.part("code"), prompt.part("docs")
    assert code.offered == 399 and 0 < code.kept < 399
    assert code.elided == code.offered - code.kept
    assert doc.offered == 200 and 0 < doc.kept < 200
    assert f"{code.kept} of 399" in code.note and "elided" in code.note
    # The elisions are told to the model too, so it does not read the list as the whole repository.
    assert code.note in prompt.messages[-1].content


def test_a_crowded_part_cannot_take_another_parts_share() -> None:
    few = idn.build_prompt(_intent(), landings=[_landing(1), _landing(2)])
    crowded = idn.build_prompt(
        _intent(),
        landings=[_landing(1), _landing(2)],
        docs=[idn.DocSection(title=f"d{n}.md", text="y" * 900, symbol="s") for n in range(100)],
    )
    assert crowded.part("code").kept == few.part("code").kept == 2
    assert crowded.part("code").elided == 0


def test_a_part_that_fits_cuts_nothing_and_says_so() -> None:
    prompt = _prompt()
    code = prompt.part("code")
    assert (code.offered, code.kept, code.elided, code.cut) == (2, 2, 0, False)
    assert prompt.part("docs").offered == 0
    assert "none" in prompt.part("docs").note


def test_the_one_item_that_does_not_fit_whole_is_cut_and_marked() -> None:
    prompt = idn.build_prompt(_intent(problem="p" * 10_000))
    why = prompt.part("why")
    assert why.cut and why.chars <= why.budget
    assert "…[truncated]" in prompt.messages[-1].content


def test_weak_landings_are_left_out_and_counted() -> None:
    prompt = idn.build_prompt(_intent(), landings=[_landing(1), _landing(2, weak=True)])
    assert "py:app.export_2.export_2" not in prompt.messages[-1].content
    assert "1 weak" in prompt.part("code").note


def test_the_same_inputs_give_the_same_prompt() -> None:
    landings = [_landing(n) for n in range(1, 30)]
    first = idn.build_prompt(_intent(), landings=landings)
    second = idn.build_prompt(_intent(), landings=list(landings))
    assert [m.content for m in first.messages] == [m.content for m in second.messages]


def test_doc_sections_are_the_pages_the_graph_binds_to_the_landings() -> None:
    b = FactBatch()
    b.add_node(
        Node("py:app.export_1.export_1", NodeKind.FUNCTION, "export_1", "python", Provenance("a.py", 1))
    )
    b.add_node(
        Node("py:app.export_2.export_2", NodeKind.FUNCTION, "export_2", "python", Provenance("b.py", 1))
    )
    for doc in ("docs/a.md#csv", "docs/b.md#xml", "docs/c.md#unrelated"):
        b.add_node(Node(f"doc:{doc}", NodeKind.DOC, doc, "doc", Provenance(doc.split("#")[0], 1)))
    b.add_edge(Edge("doc:docs/a.md#csv", "py:app.export_1.export_1", EdgeKind.MENTIONS))
    b.add_edge(Edge("doc:docs/b.md#xml", "py:app.export_2.export_2", EdgeKind.MENTIONS))
    b.add_edge(Edge("doc:docs/a.md#csv", "py:app.export_2.export_2", EdgeKind.MENTIONS))
    pages = {"docs/a.md#csv": DocPage(title="docs/a.md#csv", text="CSV only, by decision.")}
    sections = idn.doc_sections(FactStore(b), [_landing(1), _landing(2)], pages)
    assert [s.title for s in sections] == ["docs/a.md#csv", "docs/b.md#xml"]  # once each, landing order
    assert sections[0].text == "CSV only, by decision."
    assert sections[1].text == ""  # bound in the graph, page text not read: the title still goes in


# ---- the estimate ---------------------------------------------------------------------------------


def test_the_estimate_is_the_prompt_plus_the_output_cap_at_the_configured_price() -> None:
    prompt = _prompt()
    est = idn.estimate_call(prompt, _MODEL, env=_CHEAP)
    # Two characters a token, plus the tool's framing (D19): four a token undercounted a real call ~2x.
    tokens_in = -(-prompt.chars // 2) + 500
    assert est.prompt_tokens == tokens_in
    assert est.output_tokens == idn.MAX_OUTPUT_TOKENS
    assert est.usd == pytest.approx((tokens_in * 1 + idn.MAX_OUTPUT_TOKENS * 5) / 1e6)
    assert est.priced


def test_the_estimate_covers_the_prompt_a_real_call_measured() -> None:
    """P8 run 1 (2026-11-02, claude-opus-5): a 13,795-character prompt was billed 6,341 prompt tokens,
    and the four-characters-a-token estimate said 3,449. An estimate the cap refuses on must not be
    below what the call is then charged."""
    from orchestrator.core.llm.client import Message

    tool = idn.IdeationPrompt(messages=(), parts=()).chars
    prompt = idn.IdeationPrompt(messages=(Message(role="user", content="x" * (13_795 - tool)),), parts=())
    assert prompt.chars == 13_795
    assert idn.estimate_call(prompt, _MODEL, env=_CHEAP).prompt_tokens >= 6_341


def test_a_model_with_no_known_price_is_estimated_at_the_unpriced_rate_and_says_so() -> None:
    est = idn.estimate_call(_prompt(), "no-such-model-anywhere", env={"SDLC_UNPRICED_USD_PER_MTOK": "2,10"})
    assert not est.priced
    assert est.usd == pytest.approx((est.prompt_tokens * 2 + idn.MAX_OUTPUT_TOKENS * 10) / 1e6)


# ---- the refusals ---------------------------------------------------------------------------------


async def test_a_call_over_the_cap_is_refused_before_it_is_made(tmp_path: Path) -> None:
    change = _change(tmp_path)
    fake = _Fake(_reply([_option("A"), _option("B")]))
    result = await idn.generate(change, _prompt(), fake, model=_MODEL, env=_DEAR)
    assert result.status == "refused"
    assert fake.calls == []
    assert result.refusal is not None
    assert [b.limit for b in result.refusal.breaches] == ["call_cap"]
    assert result.refusal.choices == ("approve", "park")
    msg = result.refusal.message
    assert f"${result.refusal.estimate_usd:.4f}" in msg and "$0.10" in msg
    assert "approve" in msg and "park" in msg
    assert not change.design_path.exists()
    assert _ledger_rows(change) == []


async def test_an_approval_of_the_shown_estimate_lets_the_call_go(tmp_path: Path) -> None:
    change = _change(tmp_path)
    fake = _Fake(_reply([_option("A"), _option("B")]))
    est = idn.estimate_call(_prompt(), _MODEL, env=_DEAR)
    # The number the refusal showed, to four places, is what a person approves.
    result = await idn.generate(
        change, _prompt(), fake, model=_MODEL, env=_DEAR, approve_usd=round(est.usd, 4)
    )
    assert result.status == "written" and len(fake.calls) == 1


async def test_an_approval_of_less_than_the_estimate_is_still_refused(tmp_path: Path) -> None:
    change = _change(tmp_path)
    fake = _Fake(_reply([_option("A"), _option("B")]))
    est = idn.estimate_call(_prompt(), _MODEL, env=_DEAR)
    result = await idn.generate(change, _prompt(), fake, model=_MODEL, env=_DEAR, approve_usd=est.usd - 0.001)
    assert result.status == "refused" and fake.calls == []


async def test_a_call_exactly_at_the_cap_is_not_over_it(tmp_path: Path) -> None:
    change = _change(tmp_path)
    prompt = _prompt()
    tokens_in = -(-prompt.chars // 4)
    # Price the output so the whole estimate lands on $0.10 to the cent: over means over.
    out_rate = (0.10 * 1e6) / idn.MAX_OUTPUT_TOKENS
    env = {"SDLC_MODEL_PRICES": json.dumps({_MODEL: {"input_per_mtok": 0, "output_per_mtok": out_rate}})}
    assert idn.estimate_call(prompt, _MODEL, env=env).usd == pytest.approx(0.10)
    assert tokens_in > 0
    result = await idn.generate(
        change, prompt, _Fake(_reply([_option("A"), _option("B")])), model=_MODEL, env=env
    )
    assert result.status == "written"


def _spend(change: rq.LoadedChange, *amounts: float) -> None:
    with (change.directory / idn.LEDGER_NAME).open("a") as fh:
        for usd in amounts:
            fh.write(json.dumps({"stage": "ideation", "cost_usd": usd}) + "\n")


async def test_the_session_tripwire_refuses_a_call_that_would_pass_it(tmp_path: Path) -> None:
    change = _change(tmp_path)
    _spend(change, 0.30, 0.285)  # $0.585 spent; any estimate over $0.005 passes $0.59
    fake = _Fake(_reply([_option("A"), _option("B")]))
    result = await idn.generate(change, _prompt(), fake, model=_MODEL, env=_CHEAP)
    assert result.status == "refused" and fake.calls == []
    assert result.refusal is not None
    assert [b.limit for b in result.refusal.breaches] == ["session_tripwire"]
    assert result.session_spent_usd == pytest.approx(0.585)
    assert "$0.59" in result.refusal.message and "$0.5850" in result.refusal.message


async def test_under_the_tripwire_the_call_goes(tmp_path: Path) -> None:
    change = _change(tmp_path)
    _spend(change, 0.40)
    result = await idn.generate(
        change, _prompt(), _Fake(_reply([_option("A"), _option("B")])), model=_MODEL, env=_CHEAP
    )
    assert result.status == "written"
    assert idn.session_spent(change) == pytest.approx(0.40 + 0.0123)


async def test_a_call_that_lands_exactly_on_the_tripwire_is_not_past_it(tmp_path: Path) -> None:
    change = _change(tmp_path)
    est = idn.estimate_call(_prompt(), _MODEL, env=_CHEAP)
    _spend(change, idn.SESSION_TRIPWIRE_USD - est.usd)
    result = await idn.generate(
        change, _prompt(), _Fake(_reply([_option("A"), _option("B")])), model=_MODEL, env=_CHEAP
    )
    assert result.status == "written"


async def test_both_limits_are_named_when_both_are_passed(tmp_path: Path) -> None:
    change = _change(tmp_path)
    _spend(change, 0.55)
    result = await idn.generate(change, _prompt(), _Fake(_reply(None)), model=_MODEL, env=_DEAR)
    assert result.refusal is not None
    assert [b.limit for b in result.refusal.breaches] == ["call_cap", "session_tripwire"]


# ---- the call and what it writes ------------------------------------------------------------------


async def test_one_call_writes_the_options_with_their_source_and_reads_them_back(tmp_path: Path) -> None:
    change = _change(tmp_path)
    reply = [
        _option("Reuse the editor", anchors=["py:app.export_1.export_1"], risks="Two writers."),
        _option("A new command", creates=["app/cmd.py"]),
        _option("Do nothing"),
    ]
    fake = _Fake(_reply(reply, cost=0.0123))
    ledger = TokenLedger()
    result = await idn.generate(change, _prompt(), fake, model=_MODEL, env=_CHEAP, ledger=ledger)
    assert result.status == "written", result.problems
    assert len(fake.calls) == 1
    call = fake.calls[0]
    assert call["model"] == _MODEL
    assert call["tool_choice"] == "submit_options"
    assert call["max_tokens"] == idn.MAX_OUTPUT_TOKENS
    block = op.load_options(rq.load_change(str(change.directory)))
    assert block.problems == ()
    assert [o.title for o in block.options] == ["Reuse the editor", "A new command", "Do nothing"]
    assert block.options[0].anchors == ("py:app.export_1.export_1",)
    assert block.options[1].creates == ("app/cmd.py",)
    assert {o.source for o in block.options} == {f"generated · {_MODEL} · $0.0123"}
    assert block.options == result.options
    assert "## Decision" not in change.design_path.read_text()  # only `answer` writes the choice


async def test_the_call_is_a_ledger_row_in_the_run_and_beside_the_change(tmp_path: Path) -> None:
    change = _change(tmp_path)
    ledger = TokenLedger()
    await idn.generate(
        change,
        _prompt(),
        _Fake(_reply([_option("A"), _option("B")])),
        model=_MODEL,
        env=_CHEAP,
        ledger=ledger,
    )
    usage = ledger.stages["ideation"]
    assert (usage.calls, usage.prompt_tokens, usage.completion_tokens) == (1, 1000, 300)
    assert usage.cost_usd == pytest.approx(0.0123)
    [row] = _ledger_rows(change)
    assert row["stage"] == "ideation" and row["model"] == _MODEL
    assert row["cost_usd"] == pytest.approx(0.0123) and row["estimated"] is False
    assert row["outcome"] == "written"
    assert (row["prompt_tokens"], row["completion_tokens"]) == (1000, 300)


async def test_a_reply_with_no_price_is_charged_an_estimate_and_says_so(tmp_path: Path) -> None:
    change = _change(tmp_path)
    result = await idn.generate(
        change,
        _prompt(),
        _Fake(_reply([_option("A"), _option("B")], cost=0.0, cost_known=False)),
        model=_MODEL,
        env=_CHEAP,
    )
    # 1000 in, 300 out at the unpriced default ($5, $25 per million).
    assert result.cost_usd == pytest.approx((1000 * 5 + 300 * 25) / 1e6)
    assert result.cost_estimated
    [row] = _ledger_rows(change)
    assert row["estimated"] is True and row["cost_usd"] == pytest.approx(result.cost_usd)
    assert all(o.source.endswith("(estimated)") for o in result.options)


async def test_existing_options_are_kept_and_the_generated_ones_follow(tmp_path: Path) -> None:
    mine = [op.Option(title="Mine", approach="By hand.", source="user")]
    change = _change(tmp_path, mine)
    result = await idn.generate(
        change, _prompt(), _Fake(_reply([_option("A"), _option("B")])), model=_MODEL, env=_CHEAP
    )
    assert result.status == "written"
    block = op.load_options(rq.load_change(str(change.directory)))
    assert [o.title for o in block.options] == ["Mine", "A", "B"]
    assert block.options[0] == mine[0]


async def test_an_existing_block_with_problems_is_refused_before_any_spend(tmp_path: Path) -> None:
    change = _change(tmp_path)
    change.design_path.write_text("# D\n\n## Options\n\n### Option 1 — Half\n- **Risks:** none\n")
    fake = _Fake(_reply([_option("A"), _option("B")]))
    result = await idn.generate(change, _prompt(), fake, model=_MODEL, env=_CHEAP)
    assert result.status == "failed" and fake.calls == []
    assert any("no Approach" in p for p in result.problems)
    assert _ledger_rows(change) == []


async def test_a_block_the_writer_cannot_rewrite_is_refused_before_any_spend(tmp_path: Path) -> None:
    change = _change(tmp_path)
    change.design_path.write_text("# D\n\n## Options\n\n### Option 1 — Mine\n- **Approach:** By hand.\n")
    fake = _Fake(_reply([_option("A"), _option("B")]))
    result = await idn.generate(change, _prompt(), fake, model=_MODEL, env=_CHEAP)
    assert result.status == "failed" and fake.calls == []
    assert any("cannot be rewritten" in p for p in result.problems)
    assert _ledger_rows(change) == []


async def test_an_unreadable_ledger_stops_the_call_rather_than_counting_low(tmp_path: Path) -> None:
    change = _change(tmp_path)
    _spend(change, 0.01)
    with (change.directory / idn.LEDGER_NAME).open("a") as fh:
        fh.write("not json\n")
    fake = _Fake(_reply([_option("A"), _option("B")]))
    result = await idn.generate(change, _prompt(), fake, model=_MODEL, env=_CHEAP)
    assert result.status == "failed" and fake.calls == []
    assert any("ledger row cannot be read" in p for p in result.problems)


async def test_a_title_already_taken_is_reported_and_nothing_is_written(tmp_path: Path) -> None:
    change = _change(tmp_path, [op.Option(title="A", approach="Mine.", source="user")])
    before = change.design_path.read_bytes()
    result = await idn.generate(
        change, _prompt(), _Fake(_reply([_option("A"), _option("B")])), model=_MODEL, env=_CHEAP
    )
    assert result.status == "failed"
    assert change.design_path.read_bytes() == before
    assert [r["outcome"] for r in _ledger_rows(change)] == ["not written"]  # spent, so recorded


@pytest.mark.parametrize(
    ("reply", "says"),
    [
        (_reply([_option("Only one")]), "2 to 4"),
        (_reply([_option(f"O{n}") for n in range(5)]), "2 to 4"),
        (_reply(None, text="Here are some ideas, in prose."), "no options"),
        (_reply(None, text='{"options": "not a list"}'), "no options"),
        (_reply([_option("A"), {"title": "B"}]), "approach"),
        (_reply([_option("A"), _option("B", anchors=["a, b"])]), "anchors"),
        (_reply([_option("A"), _option("B", anchors="not-a-list")]), "anchors"),
    ],
)
async def test_a_reply_that_does_not_parse_is_reported_and_nothing_is_written(
    tmp_path: Path, reply: CompletionResult, says: str
) -> None:
    change = _change(tmp_path)
    result = await idn.generate(change, _prompt(), _Fake(reply), model=_MODEL, env=_CHEAP)
    assert result.status == "failed"
    assert any(says in p for p in result.problems), result.problems
    assert result.options == ()
    assert not change.design_path.exists()
    [row] = _ledger_rows(change)
    assert row["outcome"] == "not written" and row["cost_usd"] == pytest.approx(0.0123)


async def test_a_json_text_reply_is_read_when_the_model_did_not_call_the_tool(tmp_path: Path) -> None:
    change = _change(tmp_path)
    text = "```json\n" + json.dumps({"options": [_option("A"), _option("B")]}) + "\n```"
    result = await idn.generate(change, _prompt(), _Fake(_reply(None, text=text)), model=_MODEL, env=_CHEAP)
    assert result.status == "written"
    assert [o.title for o in result.options] == ["A", "B"]


async def test_a_failed_call_writes_nothing_and_is_charged_its_estimate(tmp_path: Path) -> None:
    change = _change(tmp_path)
    est = idn.estimate_call(_prompt(), _MODEL, env=_CHEAP)
    result = await idn.generate(change, _prompt(), _Fake(LLMError("rate limited")), model=_MODEL, env=_CHEAP)
    assert result.status == "failed"
    assert any("rate limited" in p for p in result.problems)
    assert not change.design_path.exists()
    [row] = _ledger_rows(change)
    assert row["outcome"] == "call failed" and row["estimated"] is True
    assert row["cost_usd"] == pytest.approx(est.usd)


async def test_a_line_break_from_the_model_cannot_open_a_section(tmp_path: Path) -> None:
    change = _change(tmp_path)
    reply = [_option("A\n## Decision\n**Chosen:** A"), _option("B")]
    result = await idn.generate(change, _prompt(), _Fake(_reply(reply)), model=_MODEL, env=_CHEAP)
    assert result.status == "written"
    text = change.design_path.read_text()
    assert "\n## Decision" not in text
    assert op.load_options(rq.load_change(str(change.directory))).problems == ()


async def test_the_model_defaults_to_the_ideation_stage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ORCHESTRATOR_IDEATION_MODEL", _MODEL)
    change = _change(tmp_path)
    fake = _Fake(_reply([_option("A"), _option("B")]))
    await idn.generate(change, _prompt(), fake, env=_CHEAP)
    assert fake.calls[0]["model"] == _MODEL


# ---- no model unless asked ------------------------------------------------------------------------


def test_building_a_prompt_and_estimating_it_imports_no_model_client() -> None:
    script = textwrap.dedent(
        """
        import sys
        from orchestrator.intake.intents import Intent
        from orchestrator.sdlc import ideation as idn
        prompt = idn.build_prompt(Intent(id="i", title="t", problem="p"))
        prices = '{"m": {"input_per_mtok": 1, "output_per_mtok": 1}}'
        idn.estimate_call(prompt, "m", env={"SDLC_MODEL_PRICES": prices})
        print("LOADED:" + ",".join(m for m in ("litellm", "anthropic", "openai") if m in sys.modules))
        """
    )
    out = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=120, check=False
    )
    assert out.returncode == 0, out.stderr[-2000:]
    assert out.stdout.strip().endswith("LOADED:"), out.stdout[-500:]
