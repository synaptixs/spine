"""B81: a figure the provider did not give must not be recorded as a real zero.

Measured on 2026-10-14 against a local server: a reply with `usage` absent, null or all-zero is
recorded as 0 tokens, indistinguishable from a real zero. litellm itself pads a missing or null
`usage` with `Usage(0, 0, 0)` before `_extract` sees it, so an absence cannot be read from
`response.usage`; what is reliable is that a real call always has at least one prompt token. The
same holds for cost: `completion_cost` raising (every GPT-6 model under the bundled price table) was
swallowed to `$0.0`, so a price that is *unknown* read as one that is *zero*.

The client now says so (`usage_reported`, `cost_known`), the ledger counts the calls, and the run
journal, the worklog and the report show the count next to the figure.
"""

from __future__ import annotations

import logging
import sys
import types
from collections.abc import Iterator
from typing import Any

import pytest

from orchestrator.core.llm import litellm_client
from orchestrator.core.llm.client import CompletionResult, Message
from orchestrator.core.llm.litellm_client import LiteLLMClient
from orchestrator.core.llm.recording import StageUsage, TokenLedger
from orchestrator.intake.report import _audit_table
from orchestrator.sdlc.telemetry import render_worklog


def _install_litellm(monkeypatch: pytest.MonkeyPatch, usage: Any, *, cost: float | Exception = 0.01) -> None:
    mod = types.ModuleType("litellm")

    async def acompletion(**_: Any) -> dict[str, Any]:
        reply: dict[str, Any] = {"choices": [{"message": {"content": "hello"}}]}
        if usage is not _ABSENT:
            reply["usage"] = usage
        return reply

    def completion_cost(**_: Any) -> float:
        if isinstance(cost, Exception):
            raise cost
        return cost

    mod.acompletion = acompletion  # type: ignore[attr-defined]
    mod.completion_cost = completion_cost  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "litellm", mod)


_ABSENT = object()


@pytest.fixture(autouse=True)
def _forget_warnings() -> Iterator[None]:
    """The 'said once per model' sets are module-level and outlive a client, so they outlive a test."""
    litellm_client._USAGE_MISSING_WARNED.clear()
    litellm_client._COST_UNKNOWN_WARNED.clear()
    yield
    litellm_client._USAGE_MISSING_WARNED.clear()
    litellm_client._COST_UNKNOWN_WARNED.clear()


async def _complete(model: str = "gpt-4o") -> CompletionResult:
    return await LiteLLMClient(num_retries=0).complete([Message(role="user", content="hi")], model=model)


# --- the client --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "usage",
    [
        pytest.param(_ABSENT, id="absent"),
        pytest.param(None, id="null"),
        pytest.param({"prompt_tokens": 0, "completion_tokens": 0}, id="all-zero"),
    ],
)
async def test_a_reply_without_a_prompt_count_is_marked_usage_not_reported(
    monkeypatch: pytest.MonkeyPatch, usage: Any
) -> None:
    _install_litellm(monkeypatch, usage)
    result = await _complete()
    assert (result.prompt_tokens, result.completion_tokens) == (0, 0)
    assert result.usage_reported is False


async def test_a_reply_with_a_prompt_count_is_marked_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_litellm(monkeypatch, {"prompt_tokens": 11, "completion_tokens": 7})
    result = await _complete()
    assert (result.prompt_tokens, result.completion_tokens) == (11, 7)
    assert result.usage_reported is True


async def test_a_price_lookup_that_raises_is_marked_cost_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_litellm(
        monkeypatch, {"prompt_tokens": 11, "completion_tokens": 7}, cost=RuntimeError("no price")
    )
    result = await _complete("gpt-6-astra")
    assert result.cost_usd == 0.0
    assert result.cost_known is False


async def test_a_genuinely_free_model_is_priced_not_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    """`completion_cost` returning 0.0 is a known price of zero (a local model); only a raise is unknown."""
    _install_litellm(monkeypatch, {"prompt_tokens": 11, "completion_tokens": 7}, cost=0.0)
    result = await _complete("ollama/qwen2.5-coder")
    assert result.cost_known is True


def _events(caplog: pytest.LogCaptureFixture, event: str) -> list[logging.LogRecord]:
    return [r for r in caplog.records if getattr(r, "event", "") == event]


async def test_each_gap_is_said_once_per_model(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _install_litellm(monkeypatch, None, cost=RuntimeError("no price"))
    with caplog.at_level(logging.WARNING):
        await _complete("model-a")
        await _complete("model-a")
        await _complete("model-b")
    assert [r.model for r in _events(caplog, "llm.usage_missing")] == ["model-a", "model-b"]  # type: ignore[attr-defined]
    assert [r.model for r in _events(caplog, "llm.cost_unknown")] == ["model-a", "model-b"]  # type: ignore[attr-defined]


async def test_a_clean_call_logs_nothing(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _install_litellm(monkeypatch, {"prompt_tokens": 3, "completion_tokens": 2})
    with caplog.at_level(logging.WARNING):
        await _complete()
    assert not _events(caplog, "llm.usage_missing") and not _events(caplog, "llm.cost_unknown")


def test_existing_constructors_are_unchanged() -> None:
    """69 call sites build a CompletionResult by keyword; neither new field may be required."""
    result = CompletionResult(
        text="", model="m", prompt_tokens=0, completion_tokens=0, cost_usd=0.0, latency_ms=0.0
    )
    assert result.usage_reported is True and result.cost_known is True


# --- the ledger ---------------------------------------------------------------------------------------


def _result(*, reported: bool = True, known: bool = True, prompt: int = 10) -> CompletionResult:
    return CompletionResult(
        text="x",
        model="m",
        prompt_tokens=prompt if reported else 0,
        completion_tokens=5 if reported else 0,
        cost_usd=0.01 if known else 0.0,
        latency_ms=1.0,
        usage_reported=reported,
        cost_known=known,
    )


def test_a_stage_counts_the_calls_it_could_not_account_for() -> None:
    usage = StageUsage(stage="codegen")
    for r in (_result(), _result(reported=False), _result(reported=False, known=False)):
        usage.add(r)
    assert (usage.calls, usage.unreported_calls, usage.unpriced_calls) == (3, 2, 1)


def test_the_total_sums_both_counts_across_stages() -> None:
    ledger = TokenLedger()
    ledger.record("intake", _result(reported=False))
    ledger.record("codegen", _result(reported=False, known=False))
    ledger.record("codegen", _result())
    total = ledger.total()
    assert (total.calls, total.unreported_calls, total.unpriced_calls) == (3, 2, 1)


def test_the_notes_say_how_many_calls_and_say_nothing_when_all_reported() -> None:
    clean = StageUsage(stage="s")
    clean.add(_result())
    assert clean.usage_note == "" and clean.cost_note == ""
    gap = StageUsage(stage="s")
    for r in (_result(reported=False, known=False), _result(), _result()):
        gap.add(r)
    assert gap.usage_note == "usage not reported on 1 of 3 calls"
    assert gap.cost_note == "not priced on 1 of 3 calls"


# --- where it is shown --------------------------------------------------------------------------------


def _ledger_with_a_gap() -> TokenLedger:
    ledger = TokenLedger()
    ledger.record("codegen", _result(reported=False, known=False))
    ledger.record("codegen", _result())
    return ledger


def test_the_worklog_states_the_gap_and_stops_claiming_every_figure_is_the_providers() -> None:
    body = render_worklog(_ledger_with_a_gap(), seconds=60, verdict="PASSED")
    assert "usage not reported on 1 of 2 calls" in body
    assert "not priced on 1 of 2 calls" in body
    assert "Token counts come from the provider's own usage figures, not an estimate" not in body


def test_a_clean_worklog_is_unchanged() -> None:
    ledger = TokenLedger()
    ledger.record("codegen", _result())
    body = render_worklog(ledger, seconds=60, verdict="PASSED")
    assert "not reported" not in body and "not priced" not in body
    assert "Token counts come from the provider's own usage figures, not an estimate" in body


def test_the_report_audit_table_states_the_gap_and_a_clean_one_does_not() -> None:
    assert "usage not reported on 1 of 2 calls" in _audit_table(_ledger_with_a_gap())
    clean = TokenLedger()
    clean.record("codegen", _result())
    assert "not reported" not in _audit_table(clean)


def test_the_run_journal_line_carries_the_gap_beside_the_figure() -> None:
    from orchestrator.sdlc.autorun import _journal_outcome

    lines: list[tuple[str, str, str]] = []

    class _Ctx:
        run_id = "r"
        spent_seed = 0.0
        pr_url = ""
        branch = "b"

        def journal(self, stage: str, status: str, detail: str, **_: Any) -> None:
            lines.append((stage, status, detail))

    _journal_outcome(_Ctx(), ledger=_ledger_with_a_gap(), budget=None, verdict="PASSED")  # type: ignore[arg-type]
    detail = lines[0][2]
    assert "usage not reported on 1 of 2 calls" in detail
    assert "not priced on 1 of 2 calls" in detail
