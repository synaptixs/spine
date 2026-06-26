"""`.env.example` is the operator-facing template — keep it from drifting.

Every variable doctor checks (required groups) and every optional hint init
scaffolds must appear in `.env.example`, so a developer copying it has every
knob the tool actually reads.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from orchestrator.doctor import ENV_GROUPS
from orchestrator.init_scaffold import _OPTIONAL_HINTS

_ENV_EXAMPLE = Path(__file__).resolve().parents[1] / ".env.example"


@pytest.fixture(scope="module")
def env_example_text() -> str:
    if not _ENV_EXAMPLE.exists():
        pytest.skip(".env.example not present (packaged install, not a source tree)")
    return _ENV_EXAMPLE.read_text(encoding="utf-8")


def test_every_required_var_is_documented(env_example_text: str) -> None:
    for group in ENV_GROUPS:
        for var in group.variables:
            assert var.name in env_example_text, f"{var.name} missing from .env.example"


def test_every_optional_hint_is_documented(env_example_text: str) -> None:
    for name, _hint in _OPTIONAL_HINTS:
        assert name in env_example_text, f"optional {name} missing from .env.example"


def test_copy_instruction_present(env_example_text: str) -> None:
    # The header tells the developer how to use it.
    assert "cp .env.example .env" in env_example_text
    assert "orchestrator doctor" in env_example_text
