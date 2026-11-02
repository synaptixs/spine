"""P6 of SSPN-297 (SSPN-303): the options tools over MCP, and their parity with the CLI (D9, P6-d).

``requirements_options`` reads, measures and recommends with no model, at the read-only tier a
default host sees. ``requirements_generate_options`` is the one that spends and writes, at a plan
tier that says it is not idempotent. Both render what ``openspec options`` renders."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from orchestrator.core.llm.client import CompletionResult, Message, ToolCall
from orchestrator.intake import options as op
from orchestrator.intake import requirements as rq
from orchestrator.plugin.auth import SCOPE_READ, SCOPE_RUN
from orchestrator.plugin.server import (
    _PATH_ARGS,
    _TIER,
    _TOOLS,
    requirements_answer,
    requirements_generate_options,
    requirements_options,
    scope_denial,
    tool_annotations,
    tool_scope,
)
from orchestrator.sdlc import option_report as orp

_MODEL = "fake-ideation-model"
_PROPOSAL = (
    "# Proposal: Validate\n\n## Why\nInput is not validated.\n"
    "### Problem\nBad input reaches the handler.\n### Users\n- Operators\n"
    "### Outcome\nBad input is refused.\n\n## What Changes\nValidate it.\n### Non-goals\n- UI\n"
)
_OPTS = [
    op.Option(title="Reuse validate", approach="Call it.", anchors=("validate",), source="user"),
    op.Option(title="A new checker", approach="Write one.", creates=("app/check.py",), source="user"),
]


def _change(tmp_path: Path, options: list[op.Option] | None = None) -> Path:
    directory = tmp_path / "openspec" / "changes" / "c"
    directory.mkdir(parents=True)
    (directory / "proposal.md").write_text(_PROPOSAL)
    if options:
        op.write_options(rq.load_change(str(directory)), options)
    return directory


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "app").mkdir(parents=True)
    (repo / "app" / "__init__.py").write_text("")
    (repo / "app" / "core.py").write_text("def validate(x):\n    return bool(x)\n")
    (repo / "app" / "web.py").write_text(
        "from app.core import validate\n\n\ndef handler(x):\n    return validate(x)\n"
    )
    (repo / "README.md").write_text("# App\n\n## Validation\n\nInput goes through `validate` first.\n")
    return repo


class _Fake:
    def __init__(self, options: list[dict[str, Any]]) -> None:
        self.options = options
        self.calls = 0

    async def complete(self, messages: list[Message], *, model: str, **kwargs: Any) -> CompletionResult:
        self.calls += 1
        return CompletionResult(
            text="",
            model=model,
            prompt_tokens=900,
            completion_tokens=200,
            cost_usd=0.0042,
            latency_ms=1.0,
            tool_calls=(ToolCall("t", "submit_options", {"options": self.options}),),
        )


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> _Fake:
    client = _Fake(
        [
            {"title": "Generated A", "approach": "Reuse validate.", "anchors": ["validate"]},
            {"title": "Generated B", "approach": "Add a checker.", "creates": ["app/check.py"]},
        ]
    )
    monkeypatch.setattr(orp, "default_client", lambda: client)
    monkeypatch.setenv("ORCHESTRATOR_IDEATION_MODEL", _MODEL)
    monkeypatch.setenv("SDLC_MODEL_PRICES", json.dumps({_MODEL: {"input_per_mtok": 1, "output_per_mtok": 5}}))
    return client


# ---- registration ---------------------------------------------------------------------------------


def test_both_tools_are_registered_with_a_tier_an_output_type_and_their_paths() -> None:
    from orchestrator.plugin.outputs import OUTPUTS

    names = {"requirements_options", "requirements_generate_options"}
    assert names <= {fn.__name__ for fn in _TOOLS}
    assert names <= set(_TIER) & set(OUTPUTS)
    for name in names:
        assert _PATH_ARGS[name] == {"change_path": "inside", "repo_path": "repo_optional"}


def test_the_comparison_is_read_only_and_the_generator_says_it_spends_and_appends() -> None:
    read, gen = tool_annotations("requirements_options"), tool_annotations("requirements_generate_options")
    assert read["read_only_hint"] and read["idempotent_hint"] and not read["destructive_hint"]
    assert not gen["read_only_hint"] and not gen["destructive_hint"]
    assert not gen["idempotent_hint"]  # a second call appends more options
    assert gen["open_world_hint"]  # it calls a model
    assert tool_scope("requirements_options") == SCOPE_READ
    # It spends, so a token needs `spine:run`, as `audit_repo` does; it is still shown at the plan
    # level (P6-d). SSPN-312 (R6): it was `spine:plan`, so a planning-only token could spend.
    assert tool_scope("requirements_generate_options") == SCOPE_RUN
    # The default host level is comprehension only: the comparison is there, the spending is not.
    assert _TIER["requirements_options"].name == "comprehend"
    assert _TIER["requirements_generate_options"].name == "plan"


def test_a_read_only_token_cannot_generate() -> None:
    class _Token:
        scopes = [SCOPE_READ]

    gen = "requirements_generate_options"
    assert scope_denial("requirements_options", tool_scope("requirements_options"), _Token()) is None
    denied = scope_denial(gen, tool_scope(gen), _Token())
    assert denied is not None and denied["needs"] == SCOPE_RUN


# ---- the comparison -------------------------------------------------------------------------------


def test_the_comparison_makes_no_model_call(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from orchestrator.core.llm import catalog
    from orchestrator.core.llm.litellm_client import LiteLLMClient

    async def boom(self: Any, *a: Any, **k: Any) -> Any:
        raise AssertionError("a model was called")

    def resolve(*a: Any, **k: Any) -> Any:
        raise AssertionError("a model was resolved")

    monkeypatch.setattr(LiteLLMClient, "complete", boom)
    monkeypatch.setattr(catalog, "resolve", resolve)
    out = requirements_options(str(_change(tmp_path, _OPTS)), str(_repo(tmp_path)))
    assert "error" not in out
    assert out["recommendation"]["recommended"] == "Reuse validate"


@pytest.fixture(autouse=True)
def _no_dotenv(monkeypatch: pytest.MonkeyPatch) -> None:
    """A generation loads `.env` from the working directory; a test never should (SSPN-312, R12)."""
    monkeypatch.setattr("orchestrator.core.env.load_local_env", lambda *a, **k: 0)


@pytest.fixture(autouse=True)
def _no_real_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """A test that generates takes `fake`; one that forgets would reach a real, paid model with
    whatever key the shell exports (SSPN-312 re-review, N3)."""

    def refuse() -> Any:
        raise AssertionError("a test reached the real model client: take the `fake` fixture")

    monkeypatch.setattr(orp, "default_client", refuse)


def test_a_plan_only_token_cannot_generate() -> None:
    class _Token:
        scopes = ["spine:read", "spine:plan"]

    gen = "requirements_generate_options"
    denied = scope_denial(gen, tool_scope(gen), _Token())
    assert denied is not None and denied["needs"] == SCOPE_RUN


@pytest.mark.parametrize("raised", [OSError("disk gone"), ValueError("bad value")])
def test_a_failure_reading_the_change_is_an_error_not_an_exception(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, raised: Exception, fake: _Fake
) -> None:
    # SSPN-312 (R9): the CLI turns these into a message; the tools let them escape.
    def broken(*a: Any, **k: Any) -> Any:
        raise raised

    monkeypatch.setattr(orp, "options_report", broken)
    change = str(_change(tmp_path, _OPTS))
    assert "error" in requirements_options(change)
    assert "error" in asyncio.run(requirements_generate_options(change))


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads a mode-000 file")
def test_an_unreadable_design_is_refused_before_an_answer_is_written(tmp_path: Path) -> None:
    # SSPN-312 re-review (X5): `answer` wrote the answer, then could not read design.md, and said
    # "error" about a write that had happened.
    change = _change(tmp_path)
    proposal = change / "proposal.md"
    proposal.write_text(_PROPOSAL + "\n## Open Questions\n- Q?\n")
    before = proposal.read_bytes()
    design = change / "design.md"
    design.write_text("# Design\n")
    design.chmod(0)
    try:
        out = requirements_answer(str(change), "Q?", answer="yes")
    finally:
        design.chmod(0o644)
    assert "cannot be read" in out["error"]
    assert proposal.read_bytes() == before


def test_bad_input_is_an_error_not_an_exception(tmp_path: Path) -> None:
    assert "error" in requirements_options(str(tmp_path / "nowhere"))
    assert "error" in requirements_options(str(_change(tmp_path)), str(tmp_path / "no-repo"))


def test_the_cli_and_the_tool_render_the_same_report(tmp_path: Path) -> None:
    from orchestrator.cli import app

    change, repo = _change(tmp_path, _OPTS), _repo(tmp_path)
    out = CliRunner().invoke(app, ["openspec", "options", str(change), str(repo), "--json"])
    assert out.exit_code == 0, out.output
    assert json.loads(out.stdout) == requirements_options(str(change), str(repo))
    md = CliRunner().invoke(app, ["openspec", "options", str(change), str(repo)])
    assert md.stdout.strip() == requirements_options(str(change), str(repo))["markdown"].strip()


# ---- generation -----------------------------------------------------------------------------------


async def test_generation_appends_the_options_and_reports_them_measured(tmp_path: Path, fake: _Fake) -> None:
    change = _change(tmp_path, _OPTS)
    out = await requirements_generate_options(str(change), str(_repo(tmp_path)))
    assert out["generation"]["status"] == "written", out
    assert out["generation"]["cost_usd"] == pytest.approx(0.0042)
    assert [o["title"] for o in out["options"]] == [
        "Reuse validate",
        "A new checker",
        "Generated A",
        "Generated B",
    ]
    assert out["options"][2]["source"] == f"generated · {_MODEL} · $0.0042"
    assert out["options"][2]["measure"]["resolved"] == ["validate"]
    assert fake.calls == 1


async def test_a_refused_generation_writes_nothing_and_says_how_to_approve(
    tmp_path: Path, fake: _Fake, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(
        "SDLC_MODEL_PRICES", json.dumps({_MODEL: {"input_per_mtok": 1, "output_per_mtok": 100}})
    )
    change = _change(tmp_path)
    out = await requirements_generate_options(str(change))
    gen = out["generation"]
    assert gen["status"] == "refused" and fake.calls == 0
    assert gen["refusal"]["choices"] == ["approve", "park"]
    assert not (change / "design.md").exists()
    shown = round(gen["refusal"]["estimate_usd"], 4)
    approved = await requirements_generate_options(str(change), approve_usd=shown)
    assert approved["generation"]["status"] == "written" and fake.calls == 1


async def test_generation_is_only_written_into_a_real_change_directory(tmp_path: Path, fake: _Fake) -> None:
    real = _change(tmp_path)
    link = tmp_path / "elsewhere"
    link.symlink_to(real, target_is_directory=True)
    out = await requirements_generate_options(str(link))
    assert "error" in out and fake.calls == 0
