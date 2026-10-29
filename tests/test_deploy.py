"""The shared-server container, read as text: no Docker needed, so these run everywhere.

What a reviewer of a Dockerfile checks by eye — a pinned base, a non-root user, no secret baked
into an ``ENV``, the server started with auth required and at the read + plan level — written
down so the next edit cannot quietly undo one. The image itself is built and smoke-tested by
the ``container`` job in ``ci.yml``.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "deploy"


@pytest.fixture(scope="module")
def dockerfile() -> str:
    return (DEPLOY / "Dockerfile").read_text(encoding="utf-8")


def _instructions(text: str, name: str) -> list[str]:
    joined = re.sub(r"\\\n\s*", " ", text)
    return [
        line.strip()[len(name) :].strip()
        for line in joined.splitlines()
        if line.strip().upper().startswith(name + " ")
    ]


def test_every_base_image_is_pinned_by_digest(dockerfile: str) -> None:
    bases = _instructions(dockerfile, "FROM")
    assert bases, "no FROM"
    for base in bases:
        image = base.split()[0]
        assert "@sha256:" in image, f"base image {image!r} is not pinned by digest"


def test_the_server_runs_as_a_user_that_is_not_root(dockerfile: str) -> None:
    users = _instructions(dockerfile, "USER")
    assert users, "no USER: the server would run as root"
    assert users[-1].split(":")[0] not in ("root", "0")


def test_no_secret_is_baked_into_the_image(dockerfile: str) -> None:
    for env in _instructions(dockerfile, "ENV") + _instructions(dockerfile, "ARG"):
        name = env.split("=")[0].split()[0].upper()
        assert not re.search(r"TOKEN|SECRET|PASSWORD|API_KEY|PRIVATE", name), env


def test_the_server_starts_shared_with_auth_required_at_the_plan_level(dockerfile: str) -> None:
    start = " ".join(_instructions(dockerfile, "ENTRYPOINT") + _instructions(dockerfile, "CMD"))
    assert "orchestrator-mcp" in start and "--http" in start and "0.0.0.0" in start
    assert "--level" in start and "plan" in start
    assert "--allow-unauthenticated" not in start and "--allow-any-repo" not in start


def test_caches_and_the_repository_list_point_at_volumes(dockerfile: str) -> None:
    env = " ".join(_instructions(dockerfile, "ENV"))
    for name in (
        "ORCHESTRATOR_PKG_CACHE_DIR",
        "ORCHESTRATOR_INTAKE_CACHE_DIR",
        "ORCHESTRATOR_DOCS_CACHE_DIR",
        "SDLC_WORKSPACE_ROOT",
    ):
        assert re.search(rf"{name}=/data\b", env), name
    assert "ORCHESTRATOR_MCP_REPOS=" in env


def test_the_build_context_leaves_secrets_and_local_state_out() -> None:
    ignored = (ROOT / ".dockerignore").read_text(encoding="utf-8").split()
    for entry in (".env", ".git", ".venv", "episteme"):
        assert entry in ignored, entry


def test_the_compose_example_mounts_the_three_volumes_and_holds_no_secret() -> None:
    doc = yaml.safe_load((DEPLOY / "compose.example.yml").read_text(encoding="utf-8"))
    (service,) = doc["services"].values()
    mounts = " ".join(str(v) for v in service["volumes"])
    for target in ("/data", "/repos", "/srv/spine/.spine/repos.yaml"):
        assert target in mounts, target
    assert "env_file" in service
    for value in (service.get("environment") or {}).values():
        assert not re.search(r"(?i)secret|token", str(value)), value


def test_the_example_repository_list_is_a_repos_yaml() -> None:
    doc = yaml.safe_load((DEPLOY / "repos.example.yaml").read_text(encoding="utf-8"))
    assert isinstance(doc["repos"], dict) and doc["repos"]
    for path in doc["repos"].values():
        assert str(path).startswith("/repos/"), path


def test_the_guide_covers_what_an_admin_has_to_set() -> None:
    guide = (DEPLOY / "README.md").read_text(encoding="utf-8")
    for needle in (
        "ORCHESTRATOR_MCP_ISSUER_URL",
        "ORCHESTRATOR_MCP_AUDIENCE",
        "ORCHESTRATOR_MCP_ROLE_MAP",
        "ORCHESTRATOR_MCP_REPOS",
        "TLS",
        "audit",
        "/data",
    ):
        assert needle in guide, needle


def test_ci_builds_and_smoke_tests_the_image() -> None:
    ci = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))
    job = ci["jobs"]["container"]
    script = " ".join(str(step.get("run", "")) for step in job["steps"])
    assert "docker build" in script and "deploy/Dockerfile" in script and "deploy/smoke.sh" in script
    smoke = (DEPLOY / "smoke.sh").read_text(encoding="utf-8")
    for needle in (
        "without auth",
        "ORCHESTRATOR_MCP_REPOS",
        '"401"',
        "not a repository this server serves",
        "mcp_repo_refused",
    ):
        assert needle in smoke, needle


def test_the_release_pushes_the_image_to_ghcr() -> None:
    release = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    assert "ghcr.io/synaptixs/spine-mcp" in release
    assert re.search(r"packages:\s*write", release)
    assert "deploy/smoke.sh" in release  # nothing is pushed that failed the smoke test


# ---- the rollout files (deploy/rollout/, D47) ---------------------------------------------------
#
# IT copies these to every machine, so a mistake reaches everyone at once. Each is loaded with
# its own format's parser and held to the rules its app applies, and to the return ports the
# guide tells admins to register: a port that drifts here would fail every sign-in on Okta,
# which matches return addresses exactly.

ROLLOUT = DEPLOY / "rollout"
_CLAUDE_PORT, _CODEX_PORT = 33420, 33421


def test_the_claude_code_entry_passes_the_checks_claude_code_applies() -> None:
    """Claude Code drops a managedMcpServers entry that names a program, expands ``${VAR}``, or
    uses a plain-http URL — silently for the person, with only a ``/status`` notice."""
    import json

    text = (ROLLOUT / "claude-code-managed-settings.json").read_text(encoding="utf-8")
    assert "${" not in text
    servers = json.loads(text)["managedMcpServers"]
    assert isinstance(servers, dict) and set(servers) == {"spine"}
    for name, entry in servers.items():
        assert re.fullmatch(r"[A-Za-z0-9_-]+", name), name
        assert entry["type"] in {"http", "sse", "streamable-http"}
        assert entry["url"].startswith("https://") and entry["url"].endswith("/mcp")
        assert not {"command", "args", "env", "headersHelper", "headers"} & set(entry)
        assert entry["oauth"]["clientId"] and entry["oauth"]["callbackPort"] == _CLAUDE_PORT


def test_the_codex_files_name_one_server_by_the_same_url() -> None:
    """The allowlist matches on name **and** URL: a URL that differs by a character from the
    managed default disables the very server the default adds."""
    import tomllib

    config = tomllib.loads((ROLLOUT / "codex-managed_config.toml").read_text(encoding="utf-8"))
    required = tomllib.loads((ROLLOUT / "codex-requirements.toml").read_text(encoding="utf-8"))
    server = config["mcp_servers"]["spine"]
    assert server["url"].startswith("https://") and server["url"].endswith("/mcp")
    assert server["oauth"]["client_id"] and server["oauth"]["callback_port"] == _CODEX_PORT
    assert "callback_url" not in server["oauth"]  # Codex ignores it; the guide says so
    assert set(required["mcp_servers"]) == set(config["mcp_servers"])
    assert required["mcp_servers"]["spine"]["identity"] == {"url": server["url"]}


def test_the_guide_lists_every_rollout_file_and_the_ports_they_use() -> None:
    guide = (DEPLOY / "README.md").read_text(encoding="utf-8")
    files = sorted(p.name for p in ROLLOUT.iterdir())
    assert files, "deploy/rollout/ is empty"
    for name in files:
        assert f"(rollout/{name})" in guide, name
    assert f"http://localhost:{_CLAUDE_PORT}/callback" in guide
    assert f"http://127.0.0.1:{_CODEX_PORT}/callback/<id>" in guide
