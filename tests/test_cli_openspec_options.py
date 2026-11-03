"""P6 of SSPN-297 (SSPN-303): ``orchestrator openspec options <change> [path] [--generate]``.

Exit 0 when the table is shown, 2 on an input error or a generation that failed, 3 when a
generation was refused before the call — a stop that asks approve-or-park, not an error."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from orchestrator.cli import app
from orchestrator.core.llm.client import CompletionResult, Message, ToolCall
from orchestrator.intake import options as op
from orchestrator.intake import requirements as rq
from orchestrator.sdlc import ideation as idn
from orchestrator.sdlc import option_report as orp

_MODEL = "fake-ideation-model"
_PROPOSAL = (
    "# Proposal: Validate\n\n## Why\nInput is not validated.\n"
    "### Problem\nBad input reaches the handler.\n### Users\n- Operators\n"
    "### Outcome\nBad input is refused.\n\n## What Changes\nValidate it.\n### Non-goals\n- UI\n"
)


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture(autouse=True)
def _no_real_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """A test that generates calls `_use`; one that forgets would reach a real, paid model with
    whatever key the shell exports (SSPN-312 re-review, N3)."""

    def refuse() -> Any:
        raise AssertionError("a test reached the real model client: call `_use` first")

    monkeypatch.setattr(orp, "default_client", refuse)


def _root(tmp_path: Path, options: list[op.Option] | None = None) -> Path:
    root = tmp_path / "openspec"
    directory = root / "changes" / "c"
    directory.mkdir(parents=True)
    (directory / "proposal.md").write_text(_PROPOSAL)
    if options:
        op.write_options(rq.load_change(str(directory)), options)
    return root


class _Fake:
    def __init__(self, options: list[dict[str, Any]] | None) -> None:
        self.options = options
        self.calls = 0

    async def complete(self, messages: list[Message], *, model: str, **kwargs: Any) -> CompletionResult:
        self.calls += 1
        calls = () if self.options is None else (ToolCall("t", "submit_options", {"options": self.options}),)
        return CompletionResult(
            text="no options here",
            model=model,
            prompt_tokens=900,
            completion_tokens=200,
            cost_usd=0.0042,
            latency_ms=1.0,
            tool_calls=calls,
        )


def _use(monkeypatch: pytest.MonkeyPatch, client: _Fake, *, out_per_mtok: float = 5) -> None:
    monkeypatch.setattr(orp, "default_client", lambda: client)
    # A generation loads `.env` from the working directory; a test never should (SSPN-312, R12).
    monkeypatch.setattr("orchestrator.core.env.load_local_env", lambda *a, **k: 0)
    monkeypatch.setenv("ORCHESTRATOR_IDEATION_MODEL", _MODEL)
    prices = {_MODEL: {"input_per_mtok": 1, "output_per_mtok": out_per_mtok}}
    monkeypatch.setenv("SDLC_MODEL_PRICES", json.dumps(prices))


_TWO = [{"title": "A", "approach": "Do A."}, {"title": "B", "approach": "Do B."}]


def test_a_change_with_no_options_says_how_to_get_some(runner: CliRunner, tmp_path: Path) -> None:
    out = runner.invoke(app, ["openspec", "options", "c", "--root", str(_root(tmp_path))])
    assert out.exit_code == 0, out.output
    assert "## Options" in out.stdout and "--generate" in out.stdout


def test_the_table_and_its_json(runner: CliRunner, tmp_path: Path) -> None:
    opts = [
        op.Option(title="One", approach="a", anchors=("x",), source="user"),
        op.Option(title="Two", approach="b", source="user"),
    ]
    root = _root(tmp_path, opts)
    md = runner.invoke(app, ["openspec", "options", "c", "--root", str(root)])
    assert md.exit_code == 0 and "| 1 | One | user |" in md.stdout
    js = runner.invoke(app, ["openspec", "options", "c", "--root", str(root), "--json"])
    data = json.loads(js.stdout)
    assert [o["title"] for o in data["options"]] == ["One", "Two"]
    assert data["grounding"] == "ungrounded"


def test_a_missing_change_is_exit_2(runner: CliRunner, tmp_path: Path) -> None:
    out = runner.invoke(app, ["openspec", "options", "nope", "--root", str(tmp_path)])
    assert out.exit_code == 2


def test_several_repositories_are_not_offered_yet(runner: CliRunner, tmp_path: Path) -> None:
    root = _root(tmp_path)
    out = runner.invoke(app, ["openspec", "options", "c", "--root", str(root), "--repos", "x.yaml"])
    assert out.exit_code == 2 and "one repository" in out.output


def test_generate_writes_the_options_and_shows_them(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _Fake(_TWO)
    _use(monkeypatch, client)
    root = _root(tmp_path)
    out = runner.invoke(app, ["openspec", "options", "c", "--root", str(root), "--generate"])
    assert out.exit_code == 0, out.output
    assert client.calls == 1
    # The source is text from design.md, so its cell is inert: `$` is escaped (GitHub math).
    assert "| 1 | A | generated · fake-ideation-model · \\$0.0042 |" in out.stdout
    block = op.load_options(rq.load_change(str(root / "changes" / "c")))
    assert [o.title for o in block.options] == ["A", "B"]


def test_a_refused_generation_is_exit_3_and_says_how_to_approve(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _Fake(_TWO)
    _use(monkeypatch, client, out_per_mtok=100)
    root = _root(tmp_path)
    out = runner.invoke(app, ["openspec", "options", "c", "--root", str(root), "--generate"])
    assert out.exit_code == 3, out.output
    assert client.calls == 0
    assert "--approve-usd" in out.output and "park" in out.output
    assert not (root / "changes" / "c" / "design.md").exists()


def test_approving_the_shown_estimate_lets_it_run(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _Fake(_TWO)
    _use(monkeypatch, client, out_per_mtok=100)
    root = _root(tmp_path)
    change = rq.load_change(str(root / "changes" / "c"))
    shown = round(idn.estimate_call(orp.generation_prompt(change), _MODEL).usd, 4)
    args = ["openspec", "options", "c", "--root", str(root), "--generate", "--approve-usd", f"{shown:.4f}"]
    out = runner.invoke(app, args)
    assert out.exit_code == 0, out.output
    assert client.calls == 1


def test_a_reply_that_does_not_parse_is_exit_2_and_writes_nothing(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _use(monkeypatch, _Fake(None))
    root = _root(tmp_path)
    out = runner.invoke(app, ["openspec", "options", "c", "--root", str(root), "--generate"])
    assert out.exit_code == 2
    assert "no options" in out.output
    assert not (root / "changes" / "c" / "design.md").exists()
