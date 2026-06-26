"""Migration up/down idempotency tests."""

from __future__ import annotations

import os

import pytest
from alembic import command
from alembic.config import Config

pytestmark = pytest.mark.integration


def _cfg() -> Config:
    cfg = Config("alembic.ini")
    url = os.getenv(
        "ORCHESTRATOR_TEST_DATABASE_URL",
        "postgresql+psycopg://orchestrator:orchestrator@localhost:5433/orchestrator",
    )
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


def test_migrations_round_trip() -> None:
    """upgrade head → downgrade base → upgrade head must all succeed."""
    cfg = _cfg()
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
