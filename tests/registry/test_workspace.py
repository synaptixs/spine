"""Phase 0: the workspace-root repo-path resolver (scoping capability jobs)."""

from __future__ import annotations

from pathlib import Path

import pytest

from orchestrator.registry.api.config import Settings
from orchestrator.registry.api.workspace import (
    RepoPathError,
    RepoSourceError,
    _validate_git_url,
    resolve_repo_path,
    workspace_root,
)


def _settings(root: Path) -> Settings:
    return Settings(workspace_root=str(root))


# --- SSRF backstop (security review Phase 2): the internal-host guard applies even under
# repo_allowed_hosts="*". git/curl resolve obfuscated IPv4 encodings to real addresses, so
# the guard must normalise them, not just standard dotted-quad. ---
_ANY_HOST = Settings(repo_allowed_hosts="*")


@pytest.mark.parametrize(
    "host",
    [
        "127.0.0.1",  # plain loopback
        "169.254.169.254",  # cloud metadata
        "2130706433",  # integer form of 127.0.0.1
        "0x7f000001",  # hex form
        "0177.0.0.1",  # octal form
        "127.1",  # short form
        "[::1]",  # IPv6 loopback
        "[::ffff:169.254.169.254]",  # IPv4-mapped IPv6 → metadata (bracketed per URL syntax)
        "localhost",
        "foo.internal",
    ],
)
def test_internal_hosts_blocked_even_with_wildcard_allowlist(host: str) -> None:
    with pytest.raises(RepoSourceError, match="internal/loopback"):
        _validate_git_url(f"https://{host}/owner/repo.git", _ANY_HOST)


def test_real_host_still_allowed_under_wildcard() -> None:
    src = _validate_git_url("https://github.com/owner/repo.git", _ANY_HOST)
    assert src.kind == "git"


def test_non_git_scheme_rejected() -> None:
    for url in ("file:///etc/passwd", "http://github.com/x"):
        with pytest.raises(RepoSourceError, match="scheme"):
            _validate_git_url(url, _ANY_HOST)


def test_resolves_root_and_subdir(tmp_path: Path) -> None:
    (tmp_path / "svc").mkdir()
    settings = _settings(tmp_path)
    assert resolve_repo_path(".", settings) == tmp_path.resolve()
    assert resolve_repo_path("", settings) == tmp_path.resolve()
    assert resolve_repo_path(None, settings) == tmp_path.resolve()
    assert resolve_repo_path("svc", settings) == (tmp_path / "svc").resolve()


def test_rejects_parent_traversal(tmp_path: Path) -> None:
    settings = _settings(tmp_path / "root")
    (tmp_path / "root").mkdir()
    (tmp_path / "secret").mkdir()
    with pytest.raises(RepoPathError):
        resolve_repo_path("../secret", settings)


def test_rejects_absolute_path_outside_root(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    with pytest.raises(RepoPathError):
        resolve_repo_path(str(outside), _settings(root))


def test_accepts_absolute_path_inside_root(tmp_path: Path) -> None:
    root = tmp_path / "root"
    (root / "svc").mkdir(parents=True)
    assert resolve_repo_path(str(root / "svc"), _settings(root)) == (root / "svc").resolve()


def test_rejects_missing_path(tmp_path: Path) -> None:
    with pytest.raises(RepoPathError):
        resolve_repo_path("nope", _settings(tmp_path))


def test_rejects_file_not_directory(tmp_path: Path) -> None:
    (tmp_path / "a-file").write_text("x", encoding="utf-8")
    with pytest.raises(RepoPathError):
        resolve_repo_path("a-file", _settings(tmp_path))


def test_unset_root_defaults_to_cwd() -> None:
    assert workspace_root(Settings()) == Path.cwd().resolve()
