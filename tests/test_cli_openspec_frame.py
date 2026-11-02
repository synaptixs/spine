"""P9a of SSPN-297 (SSPN-319): ``orchestrator openspec frame`` — through the real command line.

``frame "<idea>"`` drafts the framing change; ``frame --from <framing> --capability …`` drafts one
change per capability once the framing passes ``check``. Exit 0 written · 1 the framing does not
pass yet · 2 an input the command refuses. No model is called."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from orchestrator.cli import app

_IDEA = "A small web app where a household shares a chore rota"
_FRAMING = "a-small-web-app-where-a-household-shares-framing"
_PRODUCT = "a-small-web-app-where-a-household-shares"
_ANSWERS = (
    "answers:\n"
    "  - question: What problem does this solve?\n"
    "    answer: Households keep the rota in a group chat, so chores are forgotten.\n"
    "  - question: Who has this problem?\n"
    "    answer: Adults sharing a home; two to six people\n"
    "  - question: How will we know it worked? Describe an outcome someone can observe.\n"
    "    answer: Each member sees whose turn each chore is and gets a reminder on the day.\n"
    "  - question: What is explicitly out of scope?\n"
    "    answer: Payments or expense splitting; native mobile apps\n"
)


@pytest.fixture()
def runner() -> CliRunner:
    return CliRunner()


def _json(output: str) -> dict[str, object]:
    return json.loads(output[output.index("{") : output.rindex("}") + 1])  # type: ignore[no-any-return]


def _frame(runner: CliRunner, root: Path, *, answered: bool = True) -> None:
    out = runner.invoke(app, ["openspec", "frame", _IDEA, "--root", str(root)])
    assert out.exit_code == 0, out.output
    if answered:
        answers = root / "answers.yaml"
        answers.write_text(_ANSWERS)
        done = runner.invoke(
            app, ["openspec", "answer", _FRAMING, "--root", str(root), "--answers", str(answers)]
        )
        assert done.exit_code == 0, done.output


def test_frame_drafts_the_framing_change_and_says_what_to_answer(runner: CliRunner, tmp_path: Path) -> None:
    out = runner.invoke(app, ["openspec", "frame", _IDEA, "--root", str(tmp_path)])
    assert out.exit_code == 0, out.output
    assert _json(out.stdout)["framing"] == _FRAMING
    assert (tmp_path / "changes" / _FRAMING / "proposal.md").is_file()
    assert "openspec answer" in out.stderr


def test_capabilities_before_the_framing_passes_is_exit_1_and_writes_nothing(
    runner: CliRunner, tmp_path: Path
) -> None:
    _frame(runner, tmp_path, answered=False)
    out = runner.invoke(
        app,
        ["openspec", "frame", "--from", _FRAMING, "--capability", "Assign chores", "--root", str(tmp_path)],
    )
    assert out.exit_code == 1, out.output
    assert sorted(p.name for p in (tmp_path / "changes").iterdir()) == [_FRAMING]


def test_capabilities_from_a_framing_that_passes_are_drafted(runner: CliRunner, tmp_path: Path) -> None:
    _frame(runner, tmp_path)
    args = ["openspec", "frame", "--from", _FRAMING, "--root", str(tmp_path)]
    out = runner.invoke(
        app, [*args, "--capability", "Assign chores in turn", "--capability", "Send a reminder"]
    )
    assert out.exit_code == 0, out.output
    assert _json(out.stdout)["drafted"] == [
        f"{_PRODUCT}-assign-chores-in-turn",
        f"{_PRODUCT}-send-a-reminder",
    ]
    check = runner.invoke(app, ["openspec", "check", f"{_PRODUCT}-send-a-reminder", "--root", str(tmp_path)])
    assert check.exit_code == 1  # its own outcome is still open


@pytest.mark.parametrize(
    "args",
    [
        ["openspec", "frame"],  # neither an idea nor --from
        ["openspec", "frame", _IDEA, "--from", _FRAMING],  # both
        ["openspec", "frame", _IDEA, "--capability", "Assign chores"],  # --capability needs --from
        ["openspec", "frame", "--from", _FRAMING],  # --from needs a capability
    ],
)
def test_inputs_the_command_cannot_take_are_exit_2(
    runner: CliRunner, tmp_path: Path, args: list[str]
) -> None:
    _frame(runner, tmp_path)
    out = runner.invoke(app, [*args, "--root", str(tmp_path)])
    assert out.exit_code == 2, out.output


def test_a_bad_input_is_exit_2_even_before_the_framing_passes(runner: CliRunner, tmp_path: Path) -> None:
    # The input is checked first: a framing not yet answered does not turn a usage error into exit 1.
    _frame(runner, tmp_path, answered=False)
    out = runner.invoke(app, ["openspec", "frame", "--from", _FRAMING, "--root", str(tmp_path)])
    assert out.exit_code == 2, out.output
    assert "lists no capabilities" in out.stderr  # with no --capability, the framing's list is read


def test_too_many_capabilities_is_exit_2_and_says_how_many(runner: CliRunner, tmp_path: Path) -> None:
    _frame(runner, tmp_path)
    caps = [a for n in range(1, 10) for a in ("--capability", f"Capability number {n}")]
    out = runner.invoke(app, ["openspec", "frame", "--from", _FRAMING, *caps, "--root", str(tmp_path)])
    assert out.exit_code == 2, out.output
    assert "9 capabilities" in out.stderr


# ---- P9b (SSPN-320): --generate proposes; --from with no --capability drafts the list --------------

_MODEL = "fake-ideation-model"
_THREE = ["Sign up and join a household", "Assign chores in turn", "Send a reminder on the day"]


class _Fake:
    def __init__(self, caps: list[str]) -> None:
        self.caps = caps
        self.calls = 0

    async def complete(self, messages: list[Any], *, model: str, **kwargs: Any) -> Any:
        from orchestrator.core.llm.client import CompletionResult, ToolCall

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
    """A test that generates installs a fake; one that forgets would reach a real, paid model."""
    from orchestrator.sdlc import option_report as orp

    def refuse() -> Any:
        raise AssertionError("a test reached the real model client: call `_use` first")

    monkeypatch.setattr(orp, "default_client", refuse)
    monkeypatch.setattr("orchestrator.core.env.load_local_env", lambda *a, **k: 0)


def _use(monkeypatch: pytest.MonkeyPatch, client: _Fake, *, out_per_mtok: float = 5) -> None:
    from orchestrator.sdlc import option_report as orp

    monkeypatch.setattr(orp, "default_client", lambda: client)
    monkeypatch.setenv("ORCHESTRATOR_IDEATION_MODEL", _MODEL)
    prices = {_MODEL: {"input_per_mtok": 1, "output_per_mtok": out_per_mtok}}
    monkeypatch.setenv("SDLC_MODEL_PRICES", json.dumps(prices))


def test_generate_proposes_the_list_and_drafts_no_change(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _frame(runner, tmp_path)
    client = _Fake(_THREE)
    _use(monkeypatch, client)
    out = runner.invoke(app, ["openspec", "frame", "--from", _FRAMING, "--generate", "--root", str(tmp_path)])
    assert out.exit_code == 0, out.output
    assert client.calls == 1 and _json(out.stdout)["capabilities"] == _THREE
    assert sorted(p.name for p in (tmp_path / "changes").iterdir()) == [_FRAMING]
    assert "## Capabilities" in (tmp_path / "changes" / _FRAMING / "design.md").read_text()


def test_from_with_no_capability_drafts_the_listed_ones(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _frame(runner, tmp_path)
    _use(monkeypatch, _Fake(_THREE))
    args = ["openspec", "frame", "--from", _FRAMING, "--root", str(tmp_path)]
    assert runner.invoke(app, [*args, "--generate"]).exit_code == 0
    out = runner.invoke(app, args)
    assert out.exit_code == 0, out.output
    assert len(_json(out.stdout)["drafted"]) == 3  # type: ignore[arg-type]


def test_a_refused_generation_is_exit_3_and_calls_nothing(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _frame(runner, tmp_path)
    client = _Fake(_THREE)
    _use(monkeypatch, client, out_per_mtok=100)
    out = runner.invoke(app, ["openspec", "frame", "--from", _FRAMING, "--generate", "--root", str(tmp_path)])
    assert out.exit_code == 3, out.output
    assert client.calls == 0 and "--approve-usd" in out.output


def test_generate_before_the_framing_passes_is_exit_1_and_calls_nothing(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _frame(runner, tmp_path, answered=False)
    client = _Fake(_THREE)
    _use(monkeypatch, client)
    out = runner.invoke(app, ["openspec", "frame", "--from", _FRAMING, "--generate", "--root", str(tmp_path)])
    assert out.exit_code == 1, out.output
    assert client.calls == 0


def test_generate_with_a_listed_capability_or_an_idea_is_exit_2(runner: CliRunner, tmp_path: Path) -> None:
    _frame(runner, tmp_path)
    both = [
        "openspec",
        "frame",
        "--from",
        _FRAMING,
        "--generate",
        "--capability",
        "A",
        "--root",
        str(tmp_path),
    ]
    assert runner.invoke(app, both).exit_code == 2
    assert (
        runner.invoke(app, ["openspec", "frame", _IDEA, "--generate", "--root", str(tmp_path)]).exit_code == 2
    )
