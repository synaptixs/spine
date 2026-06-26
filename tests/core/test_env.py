"""Unit tests for the .env → os.environ bridge."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from orchestrator.core.env import load_local_env


def test_loads_keys_skips_comments_and_blanks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FOO_A", raising=False)
    monkeypatch.delenv("FOO_B", raising=False)
    env = tmp_path / ".env"
    env.write_text(
        "\n".join(
            [
                "# a comment",
                "",
                "FOO_A=alpha",
                'FOO_B="beta with spaces"',
                "not a kv line",
            ]
        ),
        encoding="utf-8",
    )
    n = load_local_env(env)
    assert n == 2
    assert os.environ["FOO_A"] == "alpha"
    assert os.environ["FOO_B"] == "beta with spaces"  # quotes stripped


def test_does_not_override_existing_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FOO_C", "from-shell")
    env = tmp_path / ".env"
    env.write_text("FOO_C=from-file", encoding="utf-8")
    load_local_env(env)
    assert os.environ["FOO_C"] == "from-shell"  # exported env wins


def test_missing_file_is_noop(tmp_path: Path) -> None:
    assert load_local_env(tmp_path / "nope.env") == 0
