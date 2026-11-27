"""Tests for orchestrator.doctor — run_env_checks and render_report."""

from __future__ import annotations

from pathlib import Path

import pytest

from orchestrator.doctor import (
    EXTRA_PROBES,
    CheckResult,
    check_models,
    check_research_search,
    render_identity,
    render_report,
    run_env_checks,
    server_identity,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

FULL_ENV = {
    "ANTHROPIC_API_KEY": "ant-key",
    "ORCHESTRATOR_API_URL": "http://localhost:8000",
    "ORCHESTRATOR_API_KEY": "dev-key",
    "CONFLUENCE_BASE_URL": "https://example.atlassian.net/wiki",
    "CONFLUENCE_EMAIL": "user@example.com",
    "CONFLUENCE_API_TOKEN": "conf-token",
    "JIRA_BASE_URL": "https://example.atlassian.net",
    "JIRA_EMAIL": "user@example.com",
    "JIRA_API_TOKEN": "jira-token",
    "JIRA_PROJECT_KEY": "PROJ",
}

EMPTY_ENV: dict[str, str] = {}


def test_rust_extra_has_a_parser_probe() -> None:
    assert EXTRA_PROBES["rust"] == "tree_sitter_rust_orchard"


def _result_by_name(results: list[CheckResult], name: str) -> CheckResult:
    for r in results:
        if r.name == name:
            return r
    raise KeyError(f"No result named {name!r}")


# ---------------------------------------------------------------------------
# run_env_checks — individual checks
# ---------------------------------------------------------------------------


class TestLLMProviderCheck:
    def test_passes_with_anthropic_key(self) -> None:
        env = {"ANTHROPIC_API_KEY": "ant-key"}
        results = run_env_checks(env)
        r = _result_by_name(results, "LLM provider")
        assert r.passed is True

    def test_passes_with_openai_key(self) -> None:
        env = {"OPENAI_API_KEY": "oai-key"}
        results = run_env_checks(env)
        r = _result_by_name(results, "LLM provider")
        assert r.passed is True

    def test_passes_with_both_keys(self) -> None:
        env = {"ANTHROPIC_API_KEY": "ant-key", "OPENAI_API_KEY": "oai-key"}
        results = run_env_checks(env)
        r = _result_by_name(results, "LLM provider")
        assert r.passed is True

    def test_passes_with_ollama_base_no_key(self) -> None:
        # Ollama (local or cloud) needs no API key — OLLAMA_API_BASE satisfies it.
        env = {"OLLAMA_API_BASE": "http://localhost:11434"}
        results = run_env_checks(env)
        r = _result_by_name(results, "LLM provider")
        assert r.passed is True

    def test_detail_mentions_found_key(self) -> None:
        env = {"ANTHROPIC_API_KEY": "ant-key"}
        results = run_env_checks(env)
        r = _result_by_name(results, "LLM provider")
        assert "ANTHROPIC_API_KEY" in r.detail

    def test_fails_when_no_llm_key(self) -> None:
        results = run_env_checks(EMPTY_ENV)
        r = _result_by_name(results, "LLM provider")
        assert r.passed is False

    def test_failure_detail_mentions_both_keys(self) -> None:
        results = run_env_checks(EMPTY_ENV)
        r = _result_by_name(results, "LLM provider")
        assert "ANTHROPIC_API_KEY" in r.detail
        assert "OPENAI_API_KEY" in r.detail


class TestOrchestratorAPICheck:
    def test_passes_when_both_set(self) -> None:
        env = {
            "ORCHESTRATOR_API_URL": "http://localhost:8000",
            "ORCHESTRATOR_API_KEY": "dev-key",
        }
        results = run_env_checks(env)
        r = _result_by_name(results, "Orchestrator API")
        assert r.passed is True
        assert r.optional is False  # configured → a real OK, not skipped

    def test_skipped_when_url_missing(self) -> None:
        env = {"ORCHESTRATOR_API_KEY": "dev-key"}
        results = run_env_checks(env)
        r = _result_by_name(results, "Orchestrator API")
        assert r.passed is True and r.optional is True
        assert "ORCHESTRATOR_API_URL" in r.detail

    def test_skipped_when_key_missing(self) -> None:
        env = {"ORCHESTRATOR_API_URL": "http://localhost:8000"}
        results = run_env_checks(env)
        r = _result_by_name(results, "Orchestrator API")
        assert r.passed is True and r.optional is True
        assert "ORCHESTRATOR_API_KEY" in r.detail

    def test_skipped_when_both_missing(self) -> None:
        # Mode-B-only group: optional, so an unset value never blocks readiness.
        results = run_env_checks(EMPTY_ENV)
        r = _result_by_name(results, "Orchestrator API")
        assert r.passed is True and r.optional is True
        assert "Mode B" in r.detail  # the note explains why it's optional


class TestConfluenceCheck:
    def test_passes_when_all_set(self) -> None:
        env = {
            "CONFLUENCE_BASE_URL": "https://example.atlassian.net/wiki",
            "CONFLUENCE_EMAIL": "user@example.com",
            "CONFLUENCE_API_TOKEN": "token",
        }
        results = run_env_checks(env)
        r = _result_by_name(results, "Confluence")
        assert r.passed is True

    def test_fails_when_any_missing(self) -> None:
        env = {
            "CONFLUENCE_BASE_URL": "https://example.atlassian.net/wiki",
            # EMAIL and TOKEN omitted
        }
        results = run_env_checks(env)
        r = _result_by_name(results, "Confluence")
        assert r.passed is False

    def test_failure_detail_mentions_missing_key(self) -> None:
        env = {
            "CONFLUENCE_BASE_URL": "https://example.atlassian.net/wiki",
        }
        results = run_env_checks(env)
        r = _result_by_name(results, "Confluence")
        assert "CONFLUENCE_EMAIL" in r.detail or "CONFLUENCE_API_TOKEN" in r.detail

    def test_fails_when_all_missing(self) -> None:
        results = run_env_checks(EMPTY_ENV)
        r = _result_by_name(results, "Confluence")
        assert r.passed is False


class TestJiraCheck:
    def test_passes_when_all_set(self) -> None:
        env = {
            "JIRA_BASE_URL": "https://example.atlassian.net",
            "JIRA_EMAIL": "user@example.com",
            "JIRA_API_TOKEN": "token",
            "JIRA_PROJECT_KEY": "PROJ",
        }
        results = run_env_checks(env)
        r = _result_by_name(results, "Jira")
        assert r.passed is True

    def test_fails_when_project_key_missing(self) -> None:
        env = {
            "JIRA_BASE_URL": "https://example.atlassian.net",
            "JIRA_EMAIL": "user@example.com",
            "JIRA_API_TOKEN": "token",
        }
        results = run_env_checks(env)
        r = _result_by_name(results, "Jira")
        assert r.passed is False
        assert "JIRA_PROJECT_KEY" in r.detail

    def test_fails_when_all_missing(self) -> None:
        results = run_env_checks(EMPTY_ENV)
        r = _result_by_name(results, "Jira")
        assert r.passed is False


# ---------------------------------------------------------------------------
# run_env_checks — result list shape
# ---------------------------------------------------------------------------


class TestRunEnvChecksResultList:
    def test_returns_four_results(self) -> None:
        results = run_env_checks(EMPTY_ENV)
        assert len(results) == 4

    def test_all_four_groups_present(self) -> None:
        results = run_env_checks(EMPTY_ENV)
        names = {r.name for r in results}
        assert names == {"LLM provider", "Orchestrator API", "Confluence", "Jira"}

    def test_all_pass_with_full_env(self) -> None:
        results = run_env_checks(FULL_ENV)
        assert all(r.passed for r in results)

    def test_only_required_groups_fail_with_empty_env(self) -> None:
        # The optional Orchestrator-API group is skipped (passes); the three
        # required groups fail.
        results = run_env_checks(EMPTY_ENV)
        failed = {r.name for r in results if not r.passed}
        assert failed == {"LLM provider", "Confluence", "Jira"}
        api = _result_by_name(results, "Orchestrator API")
        assert api.passed is True and api.optional is True

    def test_uses_os_environ_when_env_is_none(self, monkeypatch: object) -> None:
        """Passing env=None should not raise and returns CheckResult instances."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test")  # type: ignore[attr-defined]
        results = run_env_checks(None)
        assert all(isinstance(r, CheckResult) for r in results)


# ---------------------------------------------------------------------------
# render_report
# ---------------------------------------------------------------------------


class TestRenderReport:
    def test_contains_header(self) -> None:
        results = run_env_checks(FULL_ENV)
        report = render_report(results)
        assert "Orchestrator environment report" in report

    def test_all_passed_message_when_all_ok(self) -> None:
        results = run_env_checks(FULL_ENV)
        report = render_report(results)
        assert "All checks passed" in report

    def test_failed_summary_when_some_fail(self) -> None:
        results = run_env_checks(EMPTY_ENV)
        report = render_report(results)
        assert "check(s) failed" in report

    def test_ok_status_marker_present(self) -> None:
        results = run_env_checks(FULL_ENV)
        report = render_report(results)
        assert "[OK" in report

    def test_fail_status_marker_present(self) -> None:
        results = run_env_checks(EMPTY_ENV)
        report = render_report(results)
        assert "[FAIL]" in report

    def test_each_result_name_in_report(self) -> None:
        results = run_env_checks(FULL_ENV)
        report = render_report(results)
        for r in results:
            assert r.name in report

    def test_detail_included_in_report(self) -> None:
        results = run_env_checks(FULL_ENV)
        report = render_report(results)
        for r in results:
            if r.detail:
                assert r.detail in report

    def test_failed_names_listed_in_summary(self) -> None:
        results = run_env_checks(EMPTY_ENV)
        report = render_report(results)
        failed_names = [r.name for r in results if not r.passed]
        for name in failed_names:
            assert name in report

    def test_failed_count_in_summary(self) -> None:
        results = run_env_checks(EMPTY_ENV)
        report = render_report(results)
        # 3 required checks fail with empty env (Orchestrator API is optional)
        assert "3 check(s) failed" in report

    def test_optional_group_renders_as_skip(self) -> None:
        # Mode-A env: LLM + sources set, Orchestrator API unset → all pass, with
        # the optional group shown as skipped, not failed.
        results = run_env_checks({k: v for k, v in FULL_ENV.items() if not k.startswith("ORCHESTRATOR_API")})
        report = render_report(results)
        assert "[SKIP] Orchestrator API" in report
        assert "All checks passed." in report
        assert "Optional (not set): Orchestrator API" in report

    def test_separator_lines_present(self) -> None:
        results = run_env_checks(FULL_ENV)
        report = render_report(results)
        assert "=" * 10 in report  # at least part of the separator

    def test_partial_failure_count(self) -> None:
        """Only the Jira check fails when all except Jira vars are present."""
        env = dict(FULL_ENV)
        del env["JIRA_PROJECT_KEY"]
        results = run_env_checks(env)
        report = render_report(results)
        # Exactly 1 check should fail
        assert "1 check(s) failed" in report

    def test_returns_string(self) -> None:
        results = run_env_checks(FULL_ENV)
        assert isinstance(render_report(results), str)

    def test_empty_results_renders_without_error(self) -> None:
        report = render_report([])
        assert isinstance(report, str)
        assert "All checks passed" in report


# ---------------------------------------------------------------------------
# CheckResult dataclass
# ---------------------------------------------------------------------------


class TestCheckResult:
    def test_default_detail_is_empty(self) -> None:
        r = CheckResult(name="test", passed=True)
        assert r.detail == ""

    def test_fields_stored_correctly(self) -> None:
        r = CheckResult(name="foo", passed=False, detail="missing bar")
        assert r.name == "foo"
        assert r.passed is False
        assert r.detail == "missing bar"


# ---- server_identity: which install is answering ------------------------------------


def test_server_identity_names_the_running_install() -> None:
    import sys

    ident = server_identity()
    assert ident["package"] == "synaptixs-spine"
    assert ident["interpreter"] == sys.executable
    assert ident["python"].count(".") == 2
    # The dev checkout is installed as a distribution, so the version is known here.
    assert ident["version"] is not None
    # Every probed extra answers with a bool — never a missing key, never an exception.
    assert set(ident["extras"]) == set(EXTRA_PROBES)
    assert all(isinstance(v, bool) for v in ident["extras"].values())


def test_extra_probes_are_modules_not_distributions() -> None:
    """The probe is an import name (``docx``), not the PyPI name (``python-docx``):
    ``find_spec`` answers for modules, and a probe that names a distribution is a probe
    that always says "absent"."""
    assert all("-" not in module for module in EXTRA_PROBES.values())
    assert EXTRA_PROBES["clang"] == "clang"


def test_render_identity_is_comparable_against_which_and_pip_show() -> None:
    text = render_identity(
        {
            "package": "synaptixs-spine",
            "version": "3.9.3",
            "python": "3.12.4",
            "interpreter": "/old/checkout/.venv/bin/python3",
            "mcp_sdk": None,
            "extras": {"mcp": False, "java": True},
        }
    )
    lines = text.splitlines()
    assert lines[0] == "synaptixs-spine 3.9.3 · python 3.12.4 · mcp sdk not installed"
    assert lines[1] == "interpreter: /old/checkout/.venv/bin/python3"
    assert lines[2] == "extras: java"


def test_render_identity_survives_an_uninstalled_distribution() -> None:
    text = render_identity(
        {
            "package": "synaptixs-spine",
            "version": None,
            "python": "3.12.4",
            "interpreter": "/x",
            "mcp_sdk": "2.1.0",
            "extras": {},
        }
    )
    assert "not installed as a distribution" in text and "extras: none" in text


def test_a_dotted_probe_with_no_parent_package_reads_absent() -> None:
    """``find_spec("opentelemetry.sdk")`` raises when ``opentelemetry`` itself is missing;
    the probe must answer False, not crash the whole report."""
    from orchestrator.doctor import _importable

    assert _importable("no_such_parent_pkg_xyz.child") is False
    assert _importable("os.path") is True


class TestModelsCheck:
    """B172 part 3: what each stage will call, and what codegen will ask it for, before a run.

    An OpenAI-only setup failed every build on `gpt-4o`'s 16,384-token output limit, and nothing
    before the run said so. The row warns, and never blocks: an unknown model still runs, and a
    small limit still builds.
    """

    def test_the_default_setup_is_one_model_with_the_full_cap_and_no_warning(self) -> None:
        result = check_models({})
        assert result.passed and not result.warning
        assert "`claude-opus-5` (every stage)" in result.detail
        assert "codegen output cap 32,000" in result.detail

    def test_a_model_with_a_smaller_limit_warns_and_names_both_settings(self) -> None:
        result = check_models({"SDLC_CODEGEN_MODEL": "gpt-4o"})
        assert result.passed and result.warning
        assert "codegen output cap 16,384" in result.detail
        assert "SDLC_CODEGEN_MODEL" in result.detail and "SDLC_CODEGEN_MAX_TOKENS" in result.detail
        assert "`gpt-4o` (codegen)" in result.detail

    def test_a_prefixed_model_is_known(self) -> None:
        result = check_models({"ORCHESTRATOR_MODEL": "openai/gpt-4o"})
        assert "not in the installed catalog" not in result.detail
        assert "codegen output cap 16,384" in result.detail

    def test_an_unknown_model_warns_and_points_at_the_models_command(self) -> None:
        result = check_models({"SDLC_JUDGE_MODEL": "acme/not-a-model-9"})
        assert result.passed and result.warning
        assert "`acme/not-a-model-9` is not in the installed catalog" in result.detail
        assert "orchestrator models --provider acme" in result.detail

    def test_an_unusable_override_warns(self) -> None:
        result = check_models({"SDLC_CODEGEN_MAX_TOKENS": "lots"})
        assert result.passed and result.warning
        assert "ignored: not a positive integer" in result.detail

    def test_a_warning_never_fails_the_report(self) -> None:
        report = render_report([check_models({"SDLC_CODEGEN_MODEL": "gpt-4o"})])
        assert "[WARN] Models" in report
        assert "All checks passed." in report
        assert "Worth reading before a run: Models" in report


class TestResearchSearchCheck:
    """Which search autorun's research will rank with, before a run (SSPN-430).

    Research ranks with hybrid search, and its meaning leg needs the optional ``semantic`` extra and the
    pinned model's files. Without them a run still ranks, with three legs. The row says which it will be and
    why, and never blocks: a three-leg run is a complete run.
    """

    def test_with_hybrid_search_switched_off_it_says_lexical_and_does_not_warn(self) -> None:
        """Unmarked, so the suite holds hybrid off, as a user's ``SPINE_HYBRID_SEARCH=0`` would."""
        result = check_research_search()
        assert result.name == "Research search"
        assert result.passed and not result.warning
        assert "lexical ranking" in result.detail
        assert "switched off" in result.detail

    @pytest.mark.hybrid_default
    def test_the_autorun_switch_alone_also_reads_as_switched_off(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SPINE_HYBRID_AUTORUN", "0")
        result = check_research_search()
        assert result.passed and not result.warning
        assert "lexical ranking" in result.detail

    @pytest.mark.hybrid_default
    def test_without_the_extra_it_warns_and_names_the_three_legs_and_the_install(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("orchestrator.pkg.embed.semantic_available", lambda: False)
        result = check_research_search()
        assert result.passed and result.warning
        assert "hybrid search (name, content, graph)" in result.detail
        assert "meaning leg is off" in result.detail
        assert "synaptixs-spine[semantic]" in result.detail

    @pytest.mark.hybrid_default
    def test_with_the_extra_but_no_model_files_it_warns_and_names_the_directory(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr("orchestrator.pkg.embed.semantic_available", lambda: True)
        monkeypatch.setenv("SPINE_MODEL_DIR", str(tmp_path / "no-models"))
        result = check_research_search()
        assert result.passed and result.warning
        assert "hybrid search (name, content, graph)" in result.detail
        assert str(tmp_path / "no-models") in result.detail
        assert "SETUP.md" in result.detail

    @pytest.mark.hybrid_default
    def test_a_model_turned_off_on_purpose_is_stated_and_is_not_a_warning(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SPINE_EMBED_MODEL", "none")
        result = check_research_search()
        assert result.passed and not result.warning
        assert "hybrid search (name, content, graph)" in result.detail
        assert "`SPINE_EMBED_MODEL=none`" in result.detail

    @pytest.mark.hybrid_default
    def test_with_the_model_in_place_it_names_all_four_legs_and_the_model(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        from orchestrator.pkg.embed import DEFAULT_MODEL, MODELS

        spec = MODELS[DEFAULT_MODEL]
        directory = spec.directory(tmp_path)
        (directory / spec.weights).parent.mkdir(parents=True, exist_ok=True)
        (directory / spec.weights).write_bytes(b"weights")
        (directory / "tokenizer.json").write_text("{}")
        monkeypatch.setattr("orchestrator.pkg.embed.semantic_available", lambda: True)
        monkeypatch.setenv("SPINE_MODEL_DIR", str(tmp_path))

        result = check_research_search()
        assert result.passed and not result.warning
        assert "hybrid search (name, content, meaning, graph)" in result.detail
        assert f"`{spec.model_id}`" in result.detail

    @pytest.mark.hybrid_default
    def test_a_model_name_nothing_knows_says_the_run_will_stop_instead_of_raising(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A named model is not a fallback: research raises on it, so the row must not promise three legs."""
        monkeypatch.setenv("SPINE_EMBED_MODEL", "no-such-model")
        result = check_research_search()
        assert result.passed and result.warning
        assert "no-such-model" in result.detail
        assert "research will not run" in result.detail
        assert "stops at its investigate stage" in result.detail
        assert "name, content, graph" not in result.detail

    @pytest.mark.hybrid_default
    def test_a_named_model_without_its_files_says_the_run_will_stop(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Only the unnamed default checks for its files; a named one raises when research first embeds."""
        monkeypatch.setattr("orchestrator.pkg.embed.semantic_available", lambda: True)
        monkeypatch.setenv("SPINE_EMBED_MODEL", "bge-small-en-v1.5")
        monkeypatch.setenv("SPINE_MODEL_DIR", str(tmp_path / "no-models"))
        result = check_research_search()
        assert result.passed and result.warning
        assert "research will not run" in result.detail
        assert str(tmp_path / "no-models") in result.detail
        assert "meaning, graph" not in result.detail

    @pytest.mark.hybrid_default
    def test_a_named_model_with_its_files_in_place_names_all_four_legs(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        from orchestrator.pkg.embed import MODELS

        spec = MODELS["bge-small-en-v1.5"]
        directory = spec.directory(tmp_path)
        (directory / spec.weights).parent.mkdir(parents=True, exist_ok=True)
        (directory / spec.weights).write_bytes(b"weights")
        (directory / "tokenizer.json").write_text("{}")
        monkeypatch.setattr("orchestrator.pkg.embed.semantic_available", lambda: True)
        monkeypatch.setenv("SPINE_EMBED_MODEL", "bge-small-en-v1.5")
        monkeypatch.setenv("SPINE_MODEL_DIR", str(tmp_path))
        result = check_research_search()
        assert result.passed and not result.warning
        assert "hybrid search (name, content, meaning, graph)" in result.detail

    @pytest.mark.hybrid_default
    def test_the_warning_never_fails_the_report(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("orchestrator.pkg.embed.semantic_available", lambda: False)
        report = render_report([check_research_search()])
        assert "[WARN] Research search" in report
        assert "All checks passed." in report
