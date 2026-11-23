"""`orchestrator init` scaffold (G14): .env templating + safe extend."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from orchestrator.doctor import ENV_GROUPS, run_env_checks
from orchestrator.init_scaffold import (
    ignore_in_git,
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


# --- B155: the settings a first build depends on -------------------------------------------


def test_the_template_names_the_settings_a_first_build_depends_on() -> None:
    """B155: unset, `SDLC_REPO_URL` builds in an empty scratch repo; the token and the dry-run
    switch belong beside the credentials they go with."""
    out = render_env_template({})
    for name in ("SDLC_REPO_URL", "GITHUB_TOKEN", "JIRA_DRY_RUN"):
        assert f"# {name}=" in out, name


# --- B154: the .env init writes is ignored by git -------------------------------------------


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=False)


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q")
    (tmp_path / ".env").write_text("ANTHROPIC_API_KEY=\n", encoding="utf-8")
    return tmp_path


def test_an_unignored_env_in_a_repo_is_ignored_and_said(repo: Path) -> None:
    assert _git(repo, "check-ignore", "-q", ".env").returncode == 1
    said = ignore_in_git(repo / ".env")
    assert said.startswith("Added /.env to ")
    assert _git(repo, "check-ignore", "-q", ".env").returncode == 0


def test_a_second_run_adds_nothing(repo: Path) -> None:
    ignore_in_git(repo / ".env")
    assert ignore_in_git(repo / ".env") == ""
    assert (repo / ".gitignore").read_text(encoding="utf-8").count("/.env") == 1


def test_an_existing_gitignore_is_kept_and_extended(repo: Path) -> None:
    (repo / ".gitignore").write_text("build/", encoding="utf-8")  # no trailing newline
    ignore_in_git(repo / ".env")
    assert (repo / ".gitignore").read_text(encoding="utf-8") == "build/\n/.env\n"


def test_an_env_already_ignored_is_left_alone(repo: Path) -> None:
    (repo / ".gitignore").write_text("*.env\n.env\n", encoding="utf-8")
    assert ignore_in_git(repo / ".env") == ""
    assert (repo / ".gitignore").read_text(encoding="utf-8") == "*.env\n.env\n"


def test_a_tracked_env_is_warned_about_not_ignored(repo: Path) -> None:
    """A .gitignore entry cannot protect a file git already tracks; say so loudly instead."""
    _git(repo, "add", ".env")
    said = ignore_in_git(repo / ".env")
    assert said.startswith("WARNING: git already tracks")
    assert "git rm --cached" in said
    assert not (repo / ".gitignore").exists()


def test_an_env_in_a_subdirectory_is_ignored_by_its_path(repo: Path) -> None:
    sub = repo / "services" / "api"
    sub.mkdir(parents=True)
    (sub / ".env").write_text("X=\n", encoding="utf-8")
    said = ignore_in_git(sub / ".env")
    assert "/services/api/.env" in said
    assert _git(sub, "check-ignore", "-q", ".env").returncode == 0


def test_outside_a_repository_nothing_is_done(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("X=\n", encoding="utf-8")
    assert ignore_in_git(tmp_path / ".env") == ""
    assert not (tmp_path / ".gitignore").exists()
