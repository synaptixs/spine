"""B169 (SSPN-385): the opt-in model paths of RCA and design say what they spent.

`rca --llm`, `design --llm` and the MCP tools' `use_llm=true` made paid calls through a bare client, and
nothing reported a figure — while `sdlc plan`, the other planning surface, puts its spend in every
answer (B116). These run the real commands with only the model call replaced, on the client class
itself, so the metering is the real `BudgetedLLMClient` and `RunBudget`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import typer
from click.testing import CliRunner, Result

from orchestrator.cli import app
from orchestrator.core.llm import CompletionResult

_APP = "def validate(order):\n    if not order:\n        raise ValueError('empty')\n    return order\n"


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    (tmp_path / "app.py").write_text(_APP, encoding="utf-8")
    return tmp_path


@pytest.fixture()
def calls(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Every model call, answered at $0.0123 (or with no known price when `cost_known` is flipped)."""
    seen: list[str] = []

    async def _priced(self: Any, *_a: Any, **kwargs: Any) -> CompletionResult:
        seen.append(kwargs.get("model", ""))
        return CompletionResult(
            text="{}",
            model=kwargs.get("model", "m"),
            prompt_tokens=100,
            completion_tokens=50,
            cost_usd=0.0123,
            latency_ms=1.0,
        )

    monkeypatch.setattr("orchestrator.core.llm.litellm_client.LiteLLMClient.complete", _priced)
    monkeypatch.setattr("orchestrator.sdlc.codegen.resolve_codegen_model", lambda *a, **k: "claude-opus-5")
    monkeypatch.setattr("orchestrator.core.env.load_local_env", lambda *a, **k: 0)
    return seen


def _run(*args: str) -> Result:
    """The CLI with stderr kept apart, so the spend line can be told from the report."""
    command: Any = typer.main.get_command(app)
    return CliRunner(mix_stderr=False).invoke(command, list(args))


def _expected(calls: list[str]) -> str:
    assert calls, "no model call was made, so there is nothing to report"
    return f"${0.0123 * len(calls):.4f}"


# ---- CLI ----------------------------------------------------------------------------------------


def test_rca_llm_says_what_it_spent_on_stderr(repo: Path, calls: list[str]) -> None:
    result = _run("rca", str(repo), "--text", "ValueError: empty", "--llm")
    assert result.exit_code == 0, result.output + result.stderr
    assert f"rca --llm spent {_expected(calls)}" in result.stderr
    assert "spent" not in result.stdout, "the report on stdout stays the same for the same input"


def test_design_llm_says_what_it_spent_on_stderr(repo: Path, calls: list[str]) -> None:
    result = _run("design", str(repo), "--title", "Reject an empty order", "--llm")
    assert result.exit_code == 0, result.output + result.stderr
    assert f"design --llm spent {_expected(calls)}" in result.stderr


def test_without_llm_nothing_is_said_about_spend(repo: Path, calls: list[str]) -> None:
    result = _run("rca", str(repo), "--text", "ValueError: empty")
    assert result.exit_code == 0
    assert not calls and "spent" not in result.stderr


# ---- MCP ----------------------------------------------------------------------------------------


async def test_root_cause_reports_spent_usd_when_it_used_the_model(repo: Path, calls: list[str]) -> None:
    from orchestrator.plugin.server import root_cause

    out = await root_cause(str(repo), "ValueError: empty", use_llm=True)
    assert out["used_llm"] is True
    assert out["spent_usd"] == pytest.approx(0.0123 * len(calls)) and calls
    assert out["spent_estimated"] is False


async def test_design_change_reports_spent_usd_when_it_used_the_model(repo: Path, calls: list[str]) -> None:
    from orchestrator.plugin.server import design_change

    out = await design_change(
        str(repo),
        {"intent_id": "X-1", "title": "Reject an empty order", "acceptance_criteria": ["raises"]},
        True,
    )
    assert "error" not in out, out
    assert out["spent_usd"] == pytest.approx(0.0123 * len(calls)) and calls
    assert out["spent_estimated"] is False


async def test_a_deterministic_answer_carries_no_spend_field(repo: Path, calls: list[str]) -> None:
    from orchestrator.plugin.server import root_cause

    out = await root_cause(str(repo), "ValueError: empty")
    assert "spent_usd" not in out and not calls


@pytest.mark.parametrize(
    ("cost", "known", "said"),
    [
        (0.0123, False, "rca --llm spent about $"),
        (0.0, True, "rca --llm spent nothing that was priced"),
    ],
    ids=["estimated", "nothing-priced"],
)
def test_the_spend_line_says_when_it_is_an_estimate_or_nothing(
    repo: Path, monkeypatch: pytest.MonkeyPatch, cost: float, known: bool, said: str
) -> None:
    async def _reply(self: Any, *_a: Any, **kwargs: Any) -> CompletionResult:
        return CompletionResult(
            text="{}",
            model="claude-sonnet-5-5",
            prompt_tokens=100,
            completion_tokens=50,
            cost_usd=cost,
            latency_ms=1.0,
            cost_known=known,
        )

    monkeypatch.setattr("orchestrator.core.llm.litellm_client.LiteLLMClient.complete", _reply)
    monkeypatch.setattr(
        "orchestrator.sdlc.codegen.resolve_codegen_model", lambda *a, **k: "claude-sonnet-5-5"
    )
    monkeypatch.setattr("orchestrator.core.env.load_local_env", lambda *a, **k: 0)
    result = _run("rca", str(repo), "--text", "ValueError: empty", "--llm")
    assert said in result.stderr, result.stderr


def test_a_malformed_unpriced_rate_is_a_usage_error_not_a_traceback(
    repo: Path, calls: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SDLC_UNPRICED_USD_PER_MTOK", "3")
    result = _run("rca", str(repo), "--text", "ValueError: empty", "--llm")
    assert result.exit_code == 2, result.output + result.stderr
    assert "SDLC_UNPRICED_USD_PER_MTOK" in result.stderr
    assert result.exception is None or isinstance(result.exception, SystemExit)
