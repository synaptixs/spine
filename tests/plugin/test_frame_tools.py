"""P9c of SSPN-297 (SSPN-321): greenfield framing over MCP, and its parity with the CLI (D26, D32).

``requirements_frame`` drafts a framing change, or a framing's capability changes, with no model,
at the plan tier (``spine:plan``). ``requirements_generate_capabilities`` is the one that spends:
one capped call that only proposes the framing's ``## Capabilities``, at a plan level whose token
needs ``spine:run``. Both return what ``openspec frame`` prints."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from orchestrator.core.llm.client import CompletionResult, Message, ToolCall
from orchestrator.intake import framing as fr
from orchestrator.intake import requirements as rq
from orchestrator.plugin.auth import SCOPE_PLAN, SCOPE_RUN
from orchestrator.plugin.outputs import OUTPUTS
from orchestrator.plugin.server import (
    _PATH_ARGS,
    _TIER,
    PLAN,
    PLAN_SPEND,
    requirements_frame,
    requirements_generate_capabilities,
    scope_denial,
    tool_scope,
)
from orchestrator.sdlc import option_report as orp

_MODEL = "fake-ideation-model"
_IDEA = "A small web app where a household shares a chore rota"
_FRAMING = "a-small-web-app-where-a-household-shares-framing"
_PRODUCT = "a-small-web-app-where-a-household-shares"
_ANSWERS = {
    "What problem does this solve?": "Households keep the rota in a group chat, so chores are forgotten.",
    "Who has this problem?": "Adults sharing a home; two to six people",
    "How will we know it worked? Describe an outcome someone can observe.": (
        "Each member sees whose turn each chore is and gets a reminder on the day."
    ),
    "What is explicitly out of scope?": "Payments or expense splitting; native mobile apps",
}
_THREE = ["Sign up and join a household", "Assign chores in turn", "Send a reminder on the day"]


class _Fake:
    def __init__(self, caps: list[str]) -> None:
        self.caps = caps
        self.calls = 0

    async def complete(self, messages: list[Message], *, model: str, **kwargs: Any) -> CompletionResult:
        self.calls += 1
        return CompletionResult(
            text="",
            model=model,
            prompt_tokens=400,
            completion_tokens=80,
            cost_usd=0.0123,
            latency_ms=1.0,
            tool_calls=(ToolCall("t", "submit_capabilities", {"capabilities": self.caps}),),
        )


@pytest.fixture(autouse=True)
def _no_real_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """A test that generates takes `fake`; one that forgets would reach a real, paid model."""

    def refuse() -> Any:
        raise AssertionError("a test reached the real model client: take the `fake` fixture")

    monkeypatch.setattr(orp, "default_client", refuse)
    monkeypatch.setattr("orchestrator.core.env.load_local_env", lambda *a, **k: 0)


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> _Fake:
    client = _Fake(_THREE)
    monkeypatch.setattr(orp, "default_client", lambda: client)
    monkeypatch.setenv("ORCHESTRATOR_IDEATION_MODEL", _MODEL)
    monkeypatch.setenv("SDLC_MODEL_PRICES", json.dumps({_MODEL: {"input_per_mtok": 1, "output_per_mtok": 5}}))
    return client


def _framed(root: Path) -> None:
    out = requirements_frame(str(root), idea=_IDEA)
    assert "error" not in out, out
    requests = [rq.AnswerRequest(q, a) for q, a in _ANSWERS.items()]
    rq.record_answers(rq.load_change(_FRAMING, root=root), requests, at="2026-11-02")


# ---- registration, tiers and scopes (D32) ---------------------------------------------------------


def test_both_tools_are_registered_with_a_tier_an_output_type_and_their_paths() -> None:
    assert _TIER["requirements_frame"] is PLAN
    assert _TIER["requirements_generate_capabilities"] is PLAN_SPEND
    for name in ("requirements_frame", "requirements_generate_capabilities"):
        assert name in OUTPUTS
        assert _PATH_ARGS[name] == {"openspec_root": "inside"}


def test_a_plan_token_can_frame_but_only_a_run_token_can_generate() -> None:
    class _Plan:
        scopes = ["spine:read", "spine:plan"]

    assert tool_scope("requirements_frame") == SCOPE_PLAN
    assert scope_denial("requirements_frame", tool_scope("requirements_frame"), _Plan()) is None
    gen = "requirements_generate_capabilities"
    denied = scope_denial(gen, tool_scope(gen), _Plan())
    assert denied is not None and denied["needs"] == SCOPE_RUN


# ---- requirements_frame (no model) ----------------------------------------------------------------


def test_an_idea_drafts_the_framing_change(tmp_path: Path) -> None:
    out = requirements_frame(str(tmp_path), idea=_IDEA)
    assert out["framing"] == _FRAMING and out["written"]
    assert (tmp_path / "changes" / _FRAMING / "proposal.md").is_file()


def test_a_framing_that_passes_drafts_its_capabilities(tmp_path: Path) -> None:
    _framed(tmp_path)
    out = requirements_frame(str(tmp_path), framing=_FRAMING, capabilities=["Assign chores in turn"])
    assert out["drafted"] == [f"{_PRODUCT}-assign-chores-in-turn"]


def test_with_no_capabilities_the_framing_list_is_drafted(tmp_path: Path) -> None:
    _framed(tmp_path)
    framing = rq.load_change(_FRAMING, root=tmp_path)
    fr.write_capabilities(framing, [fr.Capability(c) for c in _THREE])
    assert len(requirements_frame(str(tmp_path), framing=_FRAMING)["drafted"]) == 3


def test_a_framing_that_does_not_pass_is_an_error_that_says_so(tmp_path: Path) -> None:
    requirements_frame(str(tmp_path), idea=_IDEA)
    out = requirements_frame(str(tmp_path), framing=_FRAMING, capabilities=["Assign chores"])
    assert "does not pass" in out["error"] and out.get("not_ready") is True
    assert sorted(p.name for p in (tmp_path / "changes").iterdir()) == [_FRAMING]


@pytest.mark.parametrize(
    "kwargs",
    [
        {},  # neither
        {"idea": _IDEA, "framing": _FRAMING},  # both
        {"idea": _IDEA, "capabilities": ["A"]},  # capabilities need a framing
        # Not a bare id, though it resolves to the real framing: refused, so no path can steer a write.
        {"framing": f"../changes/{_FRAMING}", "capabilities": ["Assign chores"]},
        {"framing": "nested/id"},
    ],
)
def test_inputs_the_tool_cannot_take_are_errors_not_exceptions(
    tmp_path: Path, kwargs: dict[str, Any]
) -> None:
    _framed(tmp_path)
    assert "error" in requirements_frame(str(tmp_path), **kwargs)


def test_a_framing_that_is_a_symlink_is_not_written_through(tmp_path: Path) -> None:
    _framed(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    (tmp_path / "changes" / _FRAMING).rename(elsewhere)
    (tmp_path / "changes" / _FRAMING).symlink_to(elsewhere, target_is_directory=True)
    out = requirements_frame(str(tmp_path), framing=_FRAMING, capabilities=["Assign chores"])
    assert "symlink" in out["error"]


def test_drafting_the_list_again_drafts_only_what_is_new_and_keeps_the_answers(tmp_path: Path) -> None:
    # Y8: after the list grew, a second draft was refused (or, with --overwrite, wiped answers).
    _framed(tmp_path)
    framing = rq.load_change(_FRAMING, root=tmp_path)
    fr.write_capabilities(framing, [fr.Capability(t) for t in _THREE[:2]])
    first = requirements_frame(str(tmp_path), framing=_FRAMING)
    assert len(first["drafted"]) == 2, first
    done = rq.load_change(first["drafted"][0], root=tmp_path)
    outcome_q = "How will we know it worked? Describe an outcome someone can observe."
    rq.record_answers(done, [rq.AnswerRequest(outcome_q, "Members join with a link.")])
    fr.write_capabilities(framing, [fr.Capability(t) for t in _THREE])
    second = requirements_frame(str(tmp_path), framing=_FRAMING)
    assert second["drafted"] == [f"{_PRODUCT}-send-a-reminder-on-the-day"], second
    assert second["already_drafted"] == first["drafted"]
    answered = rq.load_change(first["drafted"][0], root=tmp_path).intent.resolutions[outcome_q]
    assert answered.answer == "Members join with a link."
    again = requirements_frame(str(tmp_path), framing=_FRAMING)
    assert "already drafted" in again["error"] and "--overwrite" not in again["error"]


def test_an_idea_whose_framing_directory_is_a_symlink_is_not_written_through(tmp_path: Path) -> None:
    # Y9: drafting the framing from an idea skipped the symlink check the framing= path has.
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (tmp_path / "changes").mkdir()
    (tmp_path / "changes" / _FRAMING).symlink_to(elsewhere, target_is_directory=True)
    out = requirements_frame(str(tmp_path), idea=_IDEA)
    assert "symlink" in out["error"] and list(elsewhere.iterdir()) == []


def test_a_root_given_with_a_tilde_is_the_home_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Y13: as the options tools do; a literal `~/` was made under the server's directory.
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "cwd").mkdir()
    monkeypatch.chdir(tmp_path / "cwd")  # unexpanded, a literal `~/` lands here, not in the checkout
    out = requirements_frame("~/specs", idea=_IDEA)
    assert out["root"] == str(tmp_path / "specs"), out
    assert (tmp_path / "specs" / "changes" / _FRAMING / "proposal.md").is_file()


# ---- requirements_generate_capabilities (the one that spends) -------------------------------------


def test_a_refusal_names_the_exact_number_that_approves_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Y12: the CLI printed round(x, 6) to four places, which the exact-match check can refuse.
    from orchestrator.sdlc import frame_report
    from orchestrator.sdlc import ideation as idn

    _framed(tmp_path)
    est = idn.CostEstimate(model=_MODEL, prompt_tokens=1, output_tokens=1, usd=0.123449996, priced=True)
    refusal = idn._refusal(est, 0.0, 0.0)
    assert refusal is not None

    async def refused(*_a: Any, **_k: Any) -> idn.GenerationResult:
        return idn.GenerationResult(status="refused", refusal=refusal, model=_MODEL, estimate=est)

    monkeypatch.setattr(orp, "generate_capabilities_for", refused)
    report = asyncio.run(frame_report.generation_report(tmp_path, _FRAMING))
    assert report["approve_usd"] == 0.1234
    assert idn._refusal(est, 0.0, report["approve_usd"]) is None


def test_generation_proposes_the_list_and_drafts_no_change(tmp_path: Path, fake: _Fake) -> None:
    _framed(tmp_path)
    out = asyncio.run(requirements_generate_capabilities(str(tmp_path), _FRAMING))
    assert out["status"] == "written" and out["capabilities"] == _THREE and fake.calls == 1
    assert sorted(p.name for p in (tmp_path / "changes").iterdir()) == [_FRAMING]


def test_a_refused_generation_names_the_estimate_and_calls_nothing(
    tmp_path: Path, fake: _Fake, monkeypatch: pytest.MonkeyPatch
) -> None:
    _framed(tmp_path)
    monkeypatch.setenv(
        "SDLC_MODEL_PRICES", json.dumps({_MODEL: {"input_per_mtok": 1, "output_per_mtok": 100}})
    )
    out = asyncio.run(requirements_generate_capabilities(str(tmp_path), _FRAMING))
    assert out["status"] == "refused" and fake.calls == 0
    assert "approve_usd=" in out["refusal"]


def test_generation_on_a_framing_that_does_not_pass_calls_nothing(tmp_path: Path, fake: _Fake) -> None:
    requirements_frame(str(tmp_path), idea=_IDEA)
    out = asyncio.run(requirements_generate_capabilities(str(tmp_path), _FRAMING))
    assert out.get("not_ready") is True and fake.calls == 0


def test_generation_refuses_a_framing_id_that_is_not_bare(tmp_path: Path, fake: _Fake) -> None:
    assert "error" in asyncio.run(requirements_generate_capabilities(str(tmp_path), "../x"))
    assert fake.calls == 0


# ---- parity with the CLI (D26) --------------------------------------------------------------------


def test_the_cli_and_the_tool_return_the_same_report(tmp_path: Path) -> None:
    from orchestrator.cli import app

    runner = CliRunner()
    cli_root, tool_root = tmp_path / "cli", tmp_path / "tool"

    def cli(*args: str) -> dict[str, Any]:
        out = runner.invoke(app, ["openspec", "frame", *args, "--root", str(cli_root)])
        assert out.exit_code == 0, out.output
        return json.loads(out.stdout)  # type: ignore[no-any-return]

    def same(cli_out: dict[str, Any], tool_out: dict[str, Any]) -> None:
        swap = json.dumps(tool_out).replace(str(tool_root), str(cli_root))
        assert cli_out == json.loads(swap)

    same(cli(_IDEA), requirements_frame(str(tool_root), idea=_IDEA))
    for root in (cli_root, tool_root):
        requests = [rq.AnswerRequest(q, a) for q, a in _ANSWERS.items()]
        rq.record_answers(rq.load_change(_FRAMING, root=root), requests, at="2026-11-02")
    same(
        cli("--from", _FRAMING, "--capability", "Assign chores in turn"),
        requirements_frame(str(tool_root), framing=_FRAMING, capabilities=["Assign chores in turn"]),
    )
