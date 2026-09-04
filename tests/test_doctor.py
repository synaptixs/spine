"""Tests for orchestrator.doctor — run_env_checks and render_report."""

from __future__ import annotations

from orchestrator.doctor import (
    EXTRA_PROBES,
    CheckResult,
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
