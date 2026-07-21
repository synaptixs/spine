from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from orchestrator.cli import app


@pytest.fixture()
def runner() -> CliRunner:
    return CliRunner()


_AUTH_PY = (
    "def authenticate(token):\n    if not token:\n        raise ValueError('empty token')\n    return True\n"
)


def test_top_level_help_lists_command_groups(runner: CliRunner) -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "template" in result.stdout
    assert "contract" in result.stdout


def test_template_help_lists_subcommands(runner: CliRunner) -> None:
    result = runner.invoke(app, ["template", "--help"])
    assert result.exit_code == 0
    for sub in ("register", "list", "show", "publish", "deprecate"):
        assert sub in result.stdout


def test_contract_help_lists_subcommands(runner: CliRunner) -> None:
    result = runner.invoke(app, ["contract", "--help"])
    assert result.exit_code == 0
    for sub in ("register", "list", "show", "publish", "deprecate"):
        assert sub in result.stdout


def test_sdlc_help_lists_run(runner: CliRunner) -> None:
    result = runner.invoke(app, ["sdlc", "--help"])
    assert result.exit_code == 0
    assert "run" in result.stdout


def test_sdlc_feature_rejects_unknown_language(runner: CliRunner) -> None:
    # An unsupported --language must error (historically it silently scaffolded Python).
    result = runner.invoke(app, ["sdlc", "feature", "--source", "jira://X-1", "--language", "rust"])
    assert result.exit_code == 2
    assert "not supported" in result.output and "rust" in result.output


def test_sdlc_feature_accepts_go_language(runner: CliRunner) -> None:
    # `go` is a supported language: validation passes it through (the run then fails
    # later for lack of a configured source/LLM — never the "not supported" error).
    result = runner.invoke(app, ["sdlc", "feature", "--source", "jira://X-1", "--language", "go"])
    assert "not supported" not in result.output


def test_design_requires_a_title(runner: CliRunner, tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    result = runner.invoke(app, ["design", str(tmp_path)])
    assert result.exit_code == 2
    assert "provide --title" in result.output


def test_design_emits_grounded_blast_radius(runner: CliRunner, tmp_path: Path) -> None:
    """`design` grounds the spec in the repo's real modules and annotates the
    blast radius from the knowledge graph (heuristic path — no LLM)."""
    (tmp_path / "report.py").write_text("def render(rows):\n    return rows\n", encoding="utf-8")
    (tmp_path / "web.py").write_text(
        "import report\n\ndef handler(rows):\n    return report.render(rows)\n", encoding="utf-8"
    )
    result = runner.invoke(app, ["design", str(tmp_path), "--title", "Add CSV export"])
    assert result.exit_code == 0, result.output
    assert "# Design — Add CSV export" in result.output
    assert "## Blast radius" in result.output
    assert "report.py" in result.output and "imported by" in result.output


def test_investigate_requires_a_ticket(runner: CliRunner, tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    result = runner.invoke(app, ["investigate", str(tmp_path)])
    assert result.exit_code == 2
    assert "provide --source or --title" in result.output


def test_investigate_grounds_ticket_in_the_codebase(runner: CliRunner, tmp_path: Path) -> None:
    """Inline ticket → brief that locates the matching symbol in the repo's graph."""
    (tmp_path / "auth.py").write_text("def authenticate(token):\n    return bool(token)\n", encoding="utf-8")
    result = runner.invoke(
        app, ["investigate", str(tmp_path), "--title", "authenticate fails on empty token"]
    )
    assert result.exit_code == 0, result.output
    assert "# Investigation — authenticate fails on empty token" in result.output
    assert "## Where it lands in the code" in result.output
    assert "`authenticate`" in result.output


def test_localize_requires_a_trace(runner: CliRunner, tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    # --text empty and no stdin → error
    result = runner.invoke(app, ["localize", str(tmp_path), "--text", ""], input="")
    assert result.exit_code == 2
    assert "provide a trace" in result.output


def test_localize_resolves_traceback_to_repo_symbol(runner: CliRunner, tmp_path: Path) -> None:
    (tmp_path / "auth.py").write_text(_AUTH_PY, encoding="utf-8")
    trace = (
        "Traceback (most recent call last):\n"
        f'  File "{tmp_path / "auth.py"}", line 3, in authenticate\n'
        "    raise ValueError('empty token')\n"
        "ValueError: empty token\n"
    )
    result = runner.invoke(app, ["localize", str(tmp_path), "--text", trace])
    assert result.exit_code == 0, result.output
    assert "# Fault localization" in result.output
    assert "authenticate" in result.output and "auth.py:3" in result.output


def test_rca_requires_a_bug(runner: CliRunner, tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    result = runner.invoke(app, ["rca", str(tmp_path), "--text", ""], input="")
    assert result.exit_code == 2
    assert "provide the bug" in result.output


def test_rca_produces_grounded_analysis(runner: CliRunner, tmp_path: Path) -> None:
    (tmp_path / "auth.py").write_text(_AUTH_PY, encoding="utf-8")
    trace = (
        "Traceback (most recent call last):\n"
        f'  File "{tmp_path / "auth.py"}", line 3, in authenticate\n'
        "    raise ValueError('empty token')\n"
        "ValueError: empty token\n"
    )
    result = runner.invoke(app, ["rca", str(tmp_path), "--text", trace])
    assert result.exit_code == 0, result.output
    assert "# Root-cause analysis" in result.output
    assert "authenticate at auth.py:3" in result.output
    assert "no code is changed" in result.output.lower()


def test_regression_requires_symbol_or_trace(runner: CliRunner, tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    result = runner.invoke(app, ["regression", str(tmp_path)])
    assert result.exit_code == 2
    assert "provide --symbol" in result.output


def test_regression_flags_untested_caller(runner: CliRunner, tmp_path: Path) -> None:
    """A change to `validate` — the test exercises it, but `handler` in the blast
    radius has no covering test → flagged as a regression gap (uses Python CALLS)."""
    (tmp_path / "core.py").write_text("def validate(x):\n    return bool(x)\n", encoding="utf-8")
    (tmp_path / "web.py").write_text(
        "import core\n\ndef handler(x):\n    return core.validate(x)\n", encoding="utf-8"
    )
    (tmp_path / "test_core.py").write_text(
        "import core\n\ndef test_validate():\n    assert core.validate(1)\n", encoding="utf-8"
    )
    result = runner.invoke(app, ["regression", str(tmp_path), "--symbol", "validate"])
    assert result.exit_code == 0, result.output
    assert "# Regression coverage" in result.output
    assert "Regression gaps" in result.output and "handler" in result.output


def test_sdlc_run_rejects_unsupported_source_kind(runner: CliRunner) -> None:
    """Source-kind guard (validated against SUPPORTED_SOURCE_KINDS) fires before
    any Temporal connection is attempted. ``github`` is neither confluence nor
    notion, so it's refused."""
    result = runner.invoke(app, ["sdlc", "run", "--source", "github://owner/repo"])
    assert result.exit_code == 2
    assert "Unsupported source kind" in result.output


def test_doctor_reads_credentials_from_env_file(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """doctor bridges .env (like ingest/sdlc) so the report reflects what the
    pipeline will actually see — not just the exported shell environment."""
    for key in ("CONFLUENCE_BASE_URL", "CONFLUENCE_EMAIL", "CONFLUENCE_API_TOKEN"):
        monkeypatch.delenv(key, raising=False)  # ensure .env is the only source
    (tmp_path / ".env").write_text(
        "CONFLUENCE_BASE_URL=https://x.atlassian.net/wiki\n"
        "CONFLUENCE_EMAIL=e@x.io\n"
        "CONFLUENCE_API_TOKEN=tok\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["doctor"])
    assert "Loaded" in result.output
    assert "[OK ] Confluence" in result.output


def test_init_creates_env_and_prompts_to_fill_and_rerun(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fresh dir: init creates .env from the template, then exits non-zero with
    a fill-in-and-re-run prompt (the setup loop)."""
    # Clear creds so the scaffolded-but-empty .env is the only source → not ready.
    for key in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "OLLAMA_API_BASE"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 1
    assert (tmp_path / ".env").exists()
    assert "Created" in result.output
    assert "Re-run `orchestrator init`" in result.output
    assert ".env.example" in result.output


def test_init_reports_ready_when_required_vars_set(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A .env with every required var set → init exits 0 and confirms readiness."""
    from orchestrator.doctor import ENV_GROUPS

    for group in ENV_GROUPS:
        for var in group.variables:
            monkeypatch.delenv(var.name, raising=False)
    required = [v.name for g in ENV_GROUPS for v in g.variables]
    (tmp_path / ".env").write_text("\n".join(f"{k}=set" for k in required) + "\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0
    assert "Environment ready" in result.output


def test_catalog_list_shows_seed_capabilities(runner: CliRunner) -> None:
    result = runner.invoke(app, ["catalog", "list"])
    assert result.exit_code == 0
    assert "python-conventions" in result.output
    assert "db-schema-mcp" in result.output


def test_profile_and_plan_for_a_python_repo(runner: CliRunner, tmp_path: Path) -> None:
    (tmp_path / "main.py").write_text("print(1)\n", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text("[project]\ndependencies=['sqlalchemy']\n", encoding="utf-8")
    prof = runner.invoke(app, ["profile", str(tmp_path)])
    assert prof.exit_code == 0
    assert "python" in prof.output and "database:       yes" in prof.output
    plan = runner.invoke(app, ["catalog", "plan", str(tmp_path)])
    assert plan.exit_code == 0
    assert "python-conventions" in plan.output
    assert "onboard MCP:     db" in plan.output  # sqlalchemy → has_db → db MCP


def test_issue_key_derived_from_feature_branch() -> None:
    from orchestrator.cli import _issue_key_from_branch

    assert _issue_key_from_branch("feat/f32ef54d82f34aae/PROJ-27") == "PROJ-27"
    assert _issue_key_from_branch("main") is None
    assert _issue_key_from_branch("feat/only-two") is None
    assert _issue_key_from_branch("") is None


def test_register_rejects_missing_file(runner: CliRunner, tmp_path: Path) -> None:
    missing = tmp_path / "nope.json"
    result = runner.invoke(app, ["template", "register", str(missing)])
    assert result.exit_code != 0


def test_register_loads_json_payload(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Smoke: payload parsing succeeds; we stub the HTTP client so no real call goes out."""
    from orchestrator import cli as cli_module

    f = tmp_path / "t.json"
    f.write_text(json.dumps({"metadata": {}, "spec": {}}))

    class DummyResponse:
        status_code = 201
        text = "{}"

        def json(self) -> dict[str, str]:
            return {"ok": "true"}

    class DummyClient:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        def __enter__(self) -> DummyClient:
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def post(self, *args: object, **kwargs: object) -> DummyResponse:
            return DummyResponse()

    monkeypatch.setattr(cli_module, "_client", lambda: DummyClient())

    result = runner.invoke(app, ["template", "register", str(f)])
    assert result.exit_code == 0
    assert "ok" in result.stdout


# --------------------------------------------------------------------------- #
# Repo-analysis commands accept a local path OR a git URL (parity with the UI).
# --------------------------------------------------------------------------- #
def test_analysis_commands_reject_disallowed_or_plaintext_url(runner: CliRunner) -> None:
    """The SSRF guard + host allow-list fire before any clone is attempted."""
    for spec in ("http://localhost:8000/x", "https://evil.example.test/r.git"):
        result = runner.invoke(app, ["profile", spec])
        assert result.exit_code == 2
        assert "ERROR" in result.output


def test_analysis_command_accepts_local_path(runner: CliRunner, tmp_path: Path) -> None:
    (tmp_path / "m.py").write_text("x = 1\n", encoding="utf-8")
    result = runner.invoke(app, ["profile", str(tmp_path)])
    assert result.exit_code == 0
    assert "languages:" in result.output


def test_repo_arg_classifies_local_vs_git(tmp_path: Path) -> None:
    from orchestrator.cli import _repo_arg

    with _repo_arg(str(tmp_path)) as (path, is_remote):
        assert path == tmp_path.resolve()
        assert is_remote is False
