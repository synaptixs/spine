"""`orchestrator init` scaffold (G14): .env templating + safe extend."""

from __future__ import annotations

from pathlib import Path

from orchestrator.doctor import ENV_GROUPS, run_env_checks
from orchestrator.init_scaffold import (
    parse_env_file,
    render_env_template,
    scaffold_env,
)

_ALL_REQUIRED = [v.name for g in ENV_GROUPS for v in g.variables]


def test_render_template_includes_every_required_key() -> None:
    out = render_env_template({})
    for key in _ALL_REQUIRED:
        assert f"{key}=" in out
    # group headers + an optional section are present
    assert "# --- LLM provider ---" in out
    assert "# --- Optional ---" in out


def test_render_template_omits_already_set_keys() -> None:
    out = render_env_template({"ANTHROPIC_API_KEY": "sk-x", "JIRA_BASE_URL": "https://x"})
    assert "ANTHROPIC_API_KEY=" not in out
    assert "JIRA_BASE_URL=" not in out
    # other keys in those groups still appear
    assert "OPENAI_API_KEY=" in out
    assert "JIRA_PROJECT_KEY=" in out


def test_parse_env_file_reads_keys_ignoring_comments() -> None:
    text = "# comment\n\nFOO=bar\nBAZ = qux \n# X=ignored\nNOEQ\n"
    assert parse_env_file(text) == {"FOO": "bar", "BAZ": "qux"}


def test_scaffold_writes_fresh_env_when_absent(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    wrote, added = scaffold_env(env)
    assert wrote is True
    assert set(added) == set(_ALL_REQUIRED)
    assert env.exists()
    assert "ANTHROPIC_API_KEY=" in env.read_text(encoding="utf-8")


def test_scaffold_appends_only_missing_keys(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text(
        "ANTHROPIC_API_KEY=sk-real\nORCHESTRATOR_API_URL=http://localhost:8000\n"
        "ORCHESTRATOR_API_KEY=dev-key\n",
        encoding="utf-8",
    )
    wrote, added = scaffold_env(env)
    assert wrote is True
    # the already-set keys are not re-added; the existing values survive
    assert "ANTHROPIC_API_KEY" not in added
    body = env.read_text(encoding="utf-8")
    assert "ANTHROPIC_API_KEY=sk-real" in body  # preserved verbatim
    assert "CONFLUENCE_BASE_URL=" in body  # missing key appended
    assert body.count("ANTHROPIC_API_KEY=") == 1  # not duplicated


def test_scaffold_noop_when_all_present(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("\n".join(f"{k}=set" for k in _ALL_REQUIRED) + "\n", encoding="utf-8")
    before = env.read_text(encoding="utf-8")
    wrote, added = scaffold_env(env)
    assert wrote is False
    assert added == []
    assert env.read_text(encoding="utf-8") == before  # untouched


def test_scaffold_force_overwrites(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("ANTHROPIC_API_KEY=sk-real\n", encoding="utf-8")
    wrote, added = scaffold_env(env, force=True)
    assert wrote is True
    # fresh template — the prior value is gone, replaced by a blank line
    assert "ANTHROPIC_API_KEY=sk-real" not in env.read_text(encoding="utf-8")


def test_missing_keys_matches_doctor(tmp_path: Path) -> None:
    # A scaffolded-but-unfilled .env fails every *required* doctor group; the
    # optional Orchestrator-API group is skipped (passes), not failed.
    env = tmp_path / ".env"
    scaffold_env(env)
    parsed = {k: v for k, v in parse_env_file(env.read_text(encoding="utf-8")).items() if v}
    results = run_env_checks(parsed)
    failed = {r.name for r in results if not r.passed}
    assert failed == {"LLM provider", "Confluence", "Jira"}
