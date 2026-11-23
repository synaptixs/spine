"""The model catalog: what the pipeline can be pointed at, and what each stage uses."""

from __future__ import annotations

import os
import subprocess
import sys

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

    def test_the_models_command_lists_every_stage(self) -> None:
        """A stage the command does not list is one nobody learns can be moved (SSPN-302 added
        `ideation`; the command named three stages from a tuple of its own)."""
        from typer.testing import CliRunner

        from orchestrator.cli import app

        out = CliRunner().invoke(app, ["models", "--provider", "anthropic"])
        assert out.exit_code == 0, out.output
        for stage, names in catalog.STAGE_ENV.items():
            assert f"  {stage} " in out.output, stage
            assert " → ".join(f"${v}" for v in names) in out.output, stage


class TestThePriceMapIsTheInstalledOne:
    """Same commit, same build document — whether or not the network is up.

    LiteLLM fetches its price map from GitHub on import unless told not to, so the §11 cost
    table of a build document moved with the network: CI planned one commit twice, one fetch
    failed over to the bundled map, and the two documents differed. Each check runs in a
    fresh interpreter, because this process may have imported LiteLLM already.
    """

    @staticmethod
    def _run(code: str, **env: str) -> str:
        base = {k: v for k, v in os.environ.items() if k != "LITELLM_LOCAL_MODEL_COST_MAP"}
        proc = subprocess.run(
            [sys.executable, "-c", code], env={**base, **env}, capture_output=True, text=True, check=True
        )
        return proc.stdout.strip()

    @pytest.mark.parametrize(
        "module", ["orchestrator.core.llm.catalog", "orchestrator.core.llm.litellm_client"]
    )
    def test_litellm_reads_the_map_it_ships(self, module: str) -> None:
        """Either way into the package pins it — the client module is imported directly too."""
        out = self._run(
            f"import {module}, json, os, litellm\n"
            "bundled = os.path.join(os.path.dirname(litellm.__file__),"
            " 'model_prices_and_context_window_backup.json')\n"
            "with open(bundled, encoding='utf-8') as f:\n"
            # A subset, not equality: LiteLLM pops one metadata key when it loads the file. The
            # live map is no subset — it carries ~1,200 models the bundled one does not.
            "    print(set(litellm.model_cost) <= set(json.load(f)))\n"
        )
        assert out == "True"

    def test_an_operator_can_still_ask_for_the_live_map(self) -> None:
        out = self._run(
            "import os, orchestrator.core.llm.catalog\nprint(os.environ['LITELLM_LOCAL_MODEL_COST_MAP'])",
            LITELLM_LOCAL_MODEL_COST_MAP="False",
        )
        assert out == "False"


class TestAPrefixedIdIsTheSameModel:
    """LiteLLM dispatches on ``openai/gpt-4o`` and lists it as ``gpt-4o`` (B183).

    An exact-key lookup called the configured model "not in the installed catalog", so the
    plan's cost section priced nine unrelated models and none of the one that would run.
    """

    def test_a_provider_prefix_finds_the_bare_entry(self) -> None:
        bare, prefixed = catalog.describe("gpt-4o"), catalog.describe("openai/gpt-4o")
        assert bare is not None and prefixed == bare

    def test_the_id_returned_is_the_key_the_table_prices(self) -> None:
        info = catalog.describe("anthropic/claude-opus-5")
        assert info is not None and info.id == "claude-opus-5"

    def test_an_exact_key_wins_over_a_stripped_one(self) -> None:
        assert catalog.name_candidates("openai/gpt-4o")[0] == "openai/gpt-4o"

    def test_a_tag_is_stripped_too(self) -> None:
        assert catalog.name_candidates("ollama/llama3:8b") == (
            "ollama/llama3:8b",
            "llama3:8b",
            "ollama/llama3",
            "llama3",
        )

    def test_a_prefixed_unknown_id_is_still_none(self) -> None:
        assert catalog.describe("openai/gpt-does-not-exist-9") is None


class TestCodegenOutputCap:
    """Codegen asked every model for 32,000 output tokens; `gpt-4o` allows 16,384 (B172).

    The whole request was rejected, so an OpenAI-only setup could not build at all.
    """

    def test_the_table_reports_each_models_output_limit(self) -> None:
        info = catalog.describe("gpt-4o")
        assert info is not None and info.max_output_tokens == 16_384

    def test_a_model_that_allows_less_is_asked_for_its_own_limit(self) -> None:
        cap, basis = catalog.codegen_output_cap("gpt-4o", env={})
        assert cap == 16_384
        assert "`gpt-4o` allows 16,384" in basis

    def test_the_prefixed_id_gets_the_same_limit(self) -> None:
        assert catalog.codegen_output_cap("openai/gpt-4o", env={})[0] == 16_384

    def test_the_default_model_still_gets_the_ceiling(self) -> None:
        """Opus allows 128,000; the 32,000 ceiling is a budget, and it holds."""
        cap, basis = catalog.codegen_output_cap(catalog.DEFAULT_MODEL, env={})
        assert cap == catalog.CODEGEN_OUTPUT_CEILING == 32_000
        assert "ceiling" in basis

    def test_an_unknown_model_keeps_the_ceiling_and_says_why(self) -> None:
        cap, basis = catalog.codegen_output_cap("claude-does-not-exist-9", env={})
        assert cap == 32_000
        assert "not in the installed catalog" in basis

    def test_the_override_wins_in_both_directions(self) -> None:
        for value in ("100000", "8000"):
            env = {catalog.CODEGEN_MAX_TOKENS_ENV: value}
            cap, basis = catalog.codegen_output_cap("gpt-4o", env=env)
            assert cap == int(value)
            assert basis == f"SDLC_CODEGEN_MAX_TOKENS={value}"

    @pytest.mark.parametrize("value", ["lots", "0", "-5", "1.5"])
    def test_an_unusable_override_is_ignored_out_loud(self, value: str) -> None:
        cap, basis = catalog.codegen_output_cap("gpt-4o", env={catalog.CODEGEN_MAX_TOKENS_ENV: value})
        assert cap == 16_384
        assert "ignored: not a positive integer" in basis

    def test_the_process_environment_is_read_when_no_env_is_given(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(catalog.CODEGEN_MAX_TOKENS_ENV, "12345")
        assert catalog.codegen_output_cap("gpt-4o")[0] == 12_345
