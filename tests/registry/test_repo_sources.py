"""Repo sources: local path + remote git URL (clone-on-demand) with host policy."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import httpx
import pytest

from orchestrator.registry.api import workspace as ws
from orchestrator.registry.api.app import create_app
from orchestrator.registry.api.config import Settings
from orchestrator.registry.api.workspace import (
    RepoSource,
    RepoSourceError,
    materialize_repo_source,
    resolve_repo_source,
)

_AUTH = {"X-API-Key": "dev-key"}


# --------------------------------------------------------------------------- #
# Classification + host policy (pure)
# --------------------------------------------------------------------------- #
def _settings(tmp: Path, **kw: object) -> Settings:
    return Settings(workspace_root=str(tmp), **kw)  # type: ignore[arg-type]


def test_local_path_classified_and_scoped(tmp_path: Path) -> None:
    (tmp_path / "svc").mkdir()
    src = resolve_repo_source("svc", _settings(tmp_path))
    assert src.kind == "local" and src.path == (tmp_path / "svc").resolve()


def test_github_url_allowed_by_default(tmp_path: Path) -> None:
    src = resolve_repo_source("https://github.com/org/repo", _settings(tmp_path))
    assert src.kind == "git" and src.url == "https://github.com/org/repo"


def test_scp_style_ssh_url_allowed(tmp_path: Path) -> None:
    src = resolve_repo_source("git@github.com:org/repo.git", _settings(tmp_path))
    assert src.kind == "git"


def test_disallowed_host_rejected(tmp_path: Path) -> None:
    with pytest.raises(RepoSourceError, match="allow-list"):
        resolve_repo_source("https://evil.example/org/repo", _settings(tmp_path))


def test_wildcard_allows_any_public_host(tmp_path: Path) -> None:
    src = resolve_repo_source("https://evil.example/x", _settings(tmp_path, repo_allowed_hosts="*"))
    assert src.kind == "git"


def test_enterprise_subdomain_allowed_via_config(tmp_path: Path) -> None:
    s = _settings(tmp_path, repo_allowed_hosts="acme.com")
    assert resolve_repo_source("https://git.acme.com/x/y", s).kind == "git"


def test_file_scheme_rejected(tmp_path: Path) -> None:
    with pytest.raises(RepoSourceError, match="scheme"):
        resolve_repo_source("file:///etc/passwd", _settings(tmp_path, repo_allowed_hosts="*"))


def test_internal_hosts_rejected_even_with_wildcard(tmp_path: Path) -> None:
    s = _settings(tmp_path, repo_allowed_hosts="*")
    for url in (
        "https://localhost/x",
        "https://127.0.0.1/x",
        "https://169.254.169.254/latest/meta-data",
        "https://192.168.1.10/x",
        "https://10.0.0.5/x",
    ):
        with pytest.raises(RepoSourceError):
            resolve_repo_source(url, s)


def test_any_local_flag_allows_absolute_outside_root(tmp_path: Path) -> None:
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    root = tmp_path / "root"
    root.mkdir()
    # off by default → rejected
    with pytest.raises(Exception):
        resolve_repo_source(str(outside), _settings(root))
    # on → allowed
    s = _settings(root, repo_allow_any_local=True)
    assert resolve_repo_source(str(outside), s).path == outside.resolve()


# --------------------------------------------------------------------------- #
# materialize: real local git clone + cleanup
# --------------------------------------------------------------------------- #
def _make_git_repo(root: Path) -> Path:
    repo = root / "origin"
    repo.mkdir()
    git = ["git", "-c", "user.email=t@t", "-c", "user.name=t"]
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "app.py").write_text("def greet(n):\n    return f'hi {n}'\n", encoding="utf-8")
    subprocess.run([*git, "-C", str(repo), "add", "-A"], check=True)
    subprocess.run([*git, "-C", str(repo), "commit", "-qm", "init"], check=True)
    return repo


def test_materialize_git_clones_then_cleans_up(tmp_path: Path) -> None:
    origin = _make_git_repo(tmp_path)
    src = RepoSource(kind="git", display=str(origin), url=str(origin))
    logs: list[str] = []
    with materialize_repo_source(src, log=logs.append) as path:
        assert (path / "app.py").exists()  # the clone has the repo's files
        clone_path = path
    assert not clone_path.exists()  # temp clone removed on exit
    assert any("cloning" in m for m in logs)


def test_materialize_local_yields_path_no_clone(tmp_path: Path) -> None:
    src = RepoSource(kind="local", display=".", path=tmp_path)
    with materialize_repo_source(src) as path:
        assert path == tmp_path


def test_clone_failure_is_sanitized_error(tmp_path: Path) -> None:
    src = RepoSource(kind="git", display="x", url=str(tmp_path / "does-not-exist"))
    with pytest.raises(RepoSourceError, match="clone failed"), materialize_repo_source(src):
        pass


def test_sanitize_scrubs_token() -> None:
    msg = "fatal: unable to access https://x-access-token:SECRET123@github.com/o/r"
    assert "SECRET123" not in ws._sanitize(msg)


# --------------------------------------------------------------------------- #
# Endpoints: URL policy + a clone happy-path (clone monkeypatched)
# --------------------------------------------------------------------------- #
def _app(tmp: Path) -> object:
    app = create_app(Settings(database_url="postgresql+psycopg://stub/stub", workspace_root=str(tmp)))
    app.router.lifespan_context = None  # type: ignore[assignment]
    return app


async def test_endpoint_rejects_disallowed_host(tmp_path: Path) -> None:
    app = _app(tmp_path)
    transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
    async with httpx.AsyncClient(transport=transport, base_url="http://test", headers=_AUTH) as c:
        r = await c.post("/v1/capabilities/understand", json={"repo": "https://evil.example/x"})
        assert r.status_code == 400
        r2 = await c.post("/v1/capabilities/profile", json={"repo": "https://evil.example/x"})
        assert r2.status_code == 400


async def test_profile_clones_url_and_profiles(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # A prepared repo the fake clone copies in (so no real git/network is used).
    origin = tmp_path / "origin"
    origin.mkdir()
    (origin / "app.py").write_text("print('hi')\n", encoding="utf-8")

    def fake_clone(url: str, dest: Path) -> None:
        shutil.copytree(origin, dest)

    monkeypatch.setattr(ws, "_git_clone", fake_clone)
    app = _app(tmp_path)
    transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
    async with httpx.AsyncClient(transport=transport, base_url="http://test", headers=_AUTH) as c:
        r = await c.post("/v1/capabilities/profile", json={"repo": "https://github.com/org/repo"})
    assert r.status_code == 200, r.text
    assert "python" in r.json()["profile"]["languages"]
