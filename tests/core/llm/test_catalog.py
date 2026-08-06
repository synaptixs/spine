"""The model catalog: what the pipeline can be pointed at, and what each stage uses."""

from __future__ import annotations

import pytest

from orchestrator.core.llm import catalog


class TestResolve:
    """Per-stage override, then the global one, then the built-in default.

    Three of the four stages were hardcoded constants with no override at all — the
    judge, the intent extractor and the spec writer ran on whatever string was typed
    into their module, and no environment variable could move them.
    """

    def test_explicit_override_wins(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SDLC_JUDGE_MODEL", "from-env")
        assert catalog.resolve("judge", "explicit") == "explicit"

    def test_stage_env_beats_global(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SDLC_JUDGE_MODEL", "judge-specific")
        monkeypatch.setenv("ORCHESTRATOR_MODEL", "global")
        assert catalog.resolve("judge") == "judge-specific"

    def test_global_applies_to_every_stage(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for name in ("SDLC_CODEGEN_MODEL", "SDLC_JUDGE_MODEL", "ORCHESTRATOR_INTAKE_MODEL"):
            monkeypatch.delenv(name, raising=False)
        monkeypatch.setenv("ORCHESTRATOR_MODEL", "one-knob")
        assert {catalog.resolve(s) for s in ("codegen", "judge", "intake")} == {"one-knob"}

    def test_codegen_still_honours_the_intake_variable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A single ORCHESTRATOR_INTAKE_MODEL drove the whole pipeline before this
        existed; it must keep doing so, or a working setup breaks on upgrade."""
        monkeypatch.delenv("SDLC_CODEGEN_MODEL", raising=False)
        monkeypatch.delenv("ORCHESTRATOR_MODEL", raising=False)
        monkeypatch.setenv("ORCHESTRATOR_INTAKE_MODEL", "shared")
        assert catalog.resolve("codegen") == "shared"

    def test_falls_back_to_the_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for name in ("SDLC_CODEGEN_MODEL", "ORCHESTRATOR_INTAKE_MODEL", "ORCHESTRATOR_MODEL"):
            monkeypatch.delenv(name, raising=False)
        assert catalog.resolve("codegen") == catalog.DEFAULT_MODEL


class TestCatalogIsReadNotWritten:
    """Ids come from the installed LiteLLM, so an upgrade brings new models with no
    edit here — and a stale hand-maintained list can't drift out of sync with the
    client actually making the calls."""

    def test_both_providers_are_present(self) -> None:
        providers = {m.provider for m in catalog.catalog()}
        assert {"anthropic", "openai"} <= providers

    def test_the_default_is_a_real_model_the_client_knows(self) -> None:
        info = catalog.describe(catalog.DEFAULT_MODEL)
        assert info is not None, f"{catalog.DEFAULT_MODEL} is not in LiteLLM's catalog"
        assert info.supports_tools, "the default must support the forced tool call"

    def test_an_unknown_id_is_none_not_a_guess(self) -> None:
        assert catalog.describe("claude-does-not-exist-9") is None

    def test_prices_are_per_million_tokens(self) -> None:
        info = catalog.describe(catalog.DEFAULT_MODEL)
        assert info is not None
        # Per-token values are ~1e-6; a plausible per-Mtok rate is single digits.
        assert info.input_usd_per_mtok is not None and 0.01 < info.input_usd_per_mtok < 1000


class TestToolCallingIsARequirement:
    """Codegen forces `submit_files` and the judge forces `submit_verdict`. A model
    without function calling silently drops both back to parsing prose — the failure
    the forced-tool work removed."""

    def test_a_model_without_tools_is_not_usable(self) -> None:
        info = catalog.ModelInfo("x", "openai", 1000, 1.0, 2.0, supports_tools=False)
        assert not info.usable

    def test_render_marks_it_rather_than_hiding_it(self) -> None:
        rows = [catalog.ModelInfo("no-tools", "openai", 1000, 1.0, 2.0, supports_tools=False)]
        table = catalog.render(rows)
        assert "no-tools" in table and "**NO**" in table

    def test_render_points_at_the_current_model(self) -> None:
        rows = [catalog.ModelInfo("a", "anthropic", 1, 1.0, 2.0, supports_tools=True)]
        assert "←" in catalog.render(rows, current="a")


class TestTheScaffoldMentionsThem:
    """A variable nobody can discover is not configuration. Model selection was
    scaffolded nowhere, so `orchestrator init` produced a `.env` that never hinted a
    stage could be pointed somewhere else."""

    def test_every_stage_variable_reaches_the_env_template(self) -> None:
        from orchestrator.init_scaffold import render_env_template

        template = render_env_template({})
        for stage, names in catalog.STAGE_ENV.items():
            for name in names:
                assert name in template, f"{name} ({stage}) missing from the .env scaffold"

    def test_the_default_is_named_so_you_know_what_you_get(self) -> None:
        from orchestrator.init_scaffold import render_env_template

        assert catalog.DEFAULT_MODEL in render_env_template({})
