"""The worklog body and its duration — the shape of the report, without a pipeline."""

from __future__ import annotations

import pytest

from orchestrator.core.llm.client import CompletionResult
from orchestrator.core.llm.recording import TokenLedger
from orchestrator.sdlc.telemetry import jira_duration, render_worklog


def _result(model: str, prompt: int, completion: int, cost: float = 0.01) -> CompletionResult:
    return CompletionResult(
        text="",
        model=model,
        prompt_tokens=prompt,
        completion_tokens=completion,
        cost_usd=cost,
        latency_ms=1200.0,
    )


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (0, "1m"),  # Jira rejects a zero-length worklog, and the run did happen
        (1, "1m"),
        (59, "1m"),
        (60, "1m"),
        (61, "2m"),  # rounded up: the minute was started
        (3600, "1h 0m"),
        (7530, "2h 6m"),
    ],
)
def test_jira_duration(seconds: float, expected: str) -> None:
    assert jira_duration(seconds) == expected


def test_worklog_reports_each_stage_and_a_total() -> None:
    ledger = TokenLedger()
    ledger.record("spec_writing", _result("claude-opus-5", 1000, 200))
    ledger.record("codegen", _result("claude-opus-5", 4000, 900))
    ledger.record("codegen", _result("gpt-5-codex", 500, 100))

    body = render_worklog(ledger, seconds=125, verdict="PASSED")

    assert "**Verdict:** PASSED" in body
    assert "`claude-opus-5`" in body and "`gpt-5-codex`" in body
    assert "**Wall clock:** 3m" in body
    # Per-stage rows plus a total: 5,500 prompt + 1,200 completion = 6,700 tokens.
    assert "| spec_writing | 1 | 1,000 | 200 | 1,200 |" in body
    assert "| codegen | 2 | 4,500 | 1,000 | 5,500 |" in body
    assert "**6,700**" in body


def test_a_run_with_no_llm_calls_says_so() -> None:
    """An empty table would read as a rendering bug; the sentence is the honest report."""
    body = render_worklog(TokenLedger(), seconds=30, verdict="PASSED")
    assert "No LLM calls were recorded" in body
    assert "no LLM call was made" in body
    assert "| Stage |" not in body


# ---- one account of a whole run (SSPN-26) ----------------------------------


def test_a_run_worklog_covers_every_stage() -> None:
    """`render_worklog` describes one feature run. A supervised run has stages before and
    after it — a gate that can refuse the ticket, a review loop whose fixes are LLM calls of
    their own — and a worklog from the middle of that bills part of the history as the total.
    """
    from orchestrator.sdlc.telemetry import render_run_worklog

    ledger = TokenLedger()
    ledger.record("codegen", _result("claude-opus-5", 4000, 900))
    ledger.record("review_fix", _result("claude-opus-5", 800, 200))

    body = render_run_worklog(
        ledger,
        seconds=600,
        verdict="PASSED",
        stages=[
            ("validity", "ok", "PROCEED"),
            ("implement", "ok", "3 file(s) changed"),
            ("review", "ok", "review clean"),
        ],
        review="review clean · 1 round",
    )

    # The totals span both stages that spent tokens, not just the biggest.
    assert "**5,900**" in body
    assert "| validity | ok | PROCEED |" in body
    assert "| review | ok | review clean |" in body
    assert "## Review" in body and "1 round" in body


def test_a_run_worklog_without_review_omits_the_section() -> None:
    from orchestrator.sdlc.telemetry import render_run_worklog

    body = render_run_worklog(
        TokenLedger(), seconds=30, verdict="FAILED", stages=[("intake", "failed", "no specs")]
    )

    assert "## Review" not in body
    assert "| intake | failed | no specs |" in body
