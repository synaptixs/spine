"""Phase 0: the workspace-root repo-path resolver (scoping capability jobs)."""

from __future__ import annotations

from pathlib import Path

import pytest

from orchestrator.registry.api.config import Settings
from orchestrator.registry.api.workspace import RepoPathError, resolve_repo_path, workspace_root


def _settings(root: Path) -> Settings:
    return Settings(workspace_root=str(root))


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
