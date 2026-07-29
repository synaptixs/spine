"""Where scorecards land — precedence, and why the override exists."""

from __future__ import annotations

from pathlib import Path

import pytest

from orchestrator.evals import evals_dir


def test_defaults_to_docs_evals_under_the_repo(tmp_path: Path) -> None:
    assert evals_dir(tmp_path) == tmp_path / "docs" / "evals"


def test_env_var_redirects_writes_outside_the_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The point of the override: keep scorecards out of the analysed tree.

    Markdown under the repo's own ``docs/`` becomes ``Doc`` nodes that CI never
    sees, which stales ``understand --check``.
    """
    outside = tmp_path / "companion" / "docs" / "evals"
    monkeypatch.setenv("ORCHESTRATOR_EVALS_DIR", str(outside))
    resolved = evals_dir(tmp_path / "repo")
    assert resolved == outside
    assert tmp_path / "repo" not in resolved.parents


def test_explicit_argument_beats_the_env_var(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORCHESTRATOR_EVALS_DIR", str(tmp_path / "from-env"))
    assert evals_dir(tmp_path, out_dir=tmp_path / "explicit") == tmp_path / "explicit"


def test_empty_env_var_falls_back_to_the_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An unset-but-exported var must not resolve writes to the process cwd."""
    monkeypatch.setenv("ORCHESTRATOR_EVALS_DIR", "")
    assert evals_dir(tmp_path) == tmp_path / "docs" / "evals"
