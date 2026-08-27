"""`.spine/repos.yaml` — the declaration, and every reason to refuse one.

The key here ends up inside every scoped node id and every cache entry, so a bad one caught at
load time names the file it came from. The same key caught later surfaces as a malformed id
with nothing pointing back at its origin — which is why validation lives here and not at merge.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from orchestrator.pkg.repos import (
    RepoConfigError,
    find_repo_config,
    from_mapping,
    load_repo_config,
)


def _repos(tmp_path: Path, *names: str) -> Path:
    for name in names:
        (tmp_path / name).mkdir(parents=True, exist_ok=True)
    return tmp_path


def _write(tmp_path: Path, body: str) -> Path:
    cfg = tmp_path / ".spine" / "repos.yaml"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(body, encoding="utf-8")
    return cfg


# ---- the happy path --------------------------------------------------------


def test_a_declaration_resolves_relative_paths_against_the_config(tmp_path: Path) -> None:
    _repos(tmp_path, "billing", "web")
    cfg = _write(tmp_path, "repos:\n  billing: ../billing\n  web: ../web\n")
    repo_set = load_repo_config(cfg)
    assert repo_set.keys == ("billing", "web")
    assert repo_set.path("billing") == (tmp_path / "billing").resolve()


def test_declaration_order_does_not_change_the_result(tmp_path: Path) -> None:
    """Two people writing the same repos in a different order must get the same graph."""
    _repos(tmp_path, "a", "b", "c")
    first = load_repo_config(_write(tmp_path, "repos:\n  c: ../c\n  a: ../a\n  b: ../b\n"))
    second = load_repo_config(_write(tmp_path, "repos:\n  a: ../a\n  b: ../b\n  c: ../c\n"))
    assert first.keys == second.keys == ("a", "b", "c")


def test_an_absolute_path_is_taken_as_given(tmp_path: Path) -> None:
    _repos(tmp_path, "svc")
    repo_set = from_mapping({"svc": str(tmp_path / "svc")}, base=Path("/nowhere"))
    assert repo_set.path("svc") == (tmp_path / "svc").resolve()


# ---- reasons to refuse -----------------------------------------------------


def test_a_key_that_cannot_be_a_scope_is_refused_at_load(tmp_path: Path) -> None:
    _repos(tmp_path, "svc")
    with pytest.raises(RepoConfigError, match="repo key"):
        from_mapping({"has space": str(tmp_path / "svc")}, base=tmp_path)


def test_two_keys_for_one_checkout_are_refused(tmp_path: Path) -> None:
    """Scoping the same facts twice duplicates every symbol and doubles every count, silently."""
    _repos(tmp_path, "svc")
    with pytest.raises(RepoConfigError, match="both point at"):
        from_mapping({"a": str(tmp_path / "svc"), "b": str(tmp_path / "svc")}, base=tmp_path)


def test_a_path_that_is_not_a_directory_is_refused(tmp_path: Path) -> None:
    with pytest.raises(RepoConfigError, match="not a directory"):
        from_mapping({"svc": str(tmp_path / "missing")}, base=tmp_path)


def test_an_empty_declaration_is_refused(tmp_path: Path) -> None:
    with pytest.raises(RepoConfigError, match="no repositories declared"):
        from_mapping({}, base=tmp_path)


def test_a_repo_with_no_path_is_refused(tmp_path: Path) -> None:
    with pytest.raises(RepoConfigError, match="no path"):
        from_mapping({"svc": ""}, base=tmp_path)


def test_a_file_without_a_repos_key_is_refused(tmp_path: Path) -> None:
    cfg = _write(tmp_path, "workflows:\n  default: x\n")
    with pytest.raises(RepoConfigError, match="top-level 'repos:'"):
        load_repo_config(cfg)


def test_invalid_yaml_names_the_file(tmp_path: Path) -> None:
    cfg = _write(tmp_path, "repos:\n  - [unclosed\n")
    with pytest.raises(RepoConfigError, match="invalid YAML"):
        load_repo_config(cfg)


# ---- finding it ------------------------------------------------------------


def test_the_config_is_found_beside_workflows(tmp_path: Path) -> None:
    _repos(tmp_path, "svc")
    _write(tmp_path, "repos:\n  svc: ../svc\n")
    assert find_repo_config(tmp_path) is not None


def test_no_config_is_not_an_error(tmp_path: Path) -> None:
    assert find_repo_config(tmp_path) is None


def test_the_search_does_not_walk_upward(tmp_path: Path) -> None:
    """A parent's config would silently change what a command in a subdirectory means."""
    _repos(tmp_path, "svc")
    _write(tmp_path, "repos:\n  svc: ../svc\n")
    (tmp_path / "sub").mkdir()
    assert find_repo_config(tmp_path / "sub") is None
