"""Fixtures for integration tests against a live Postgres.

Run with::

    docker compose -f docker-compose.dev.yml up -d
    pytest -m integration

Override the target database via ``ORCHESTRATOR_TEST_DATABASE_URL``.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

DEFAULT_URL = "postgresql+psycopg://orchestrator:orchestrator@localhost:5433/orchestrator"


def _database_url() -> str:
    return os.getenv("ORCHESTRATOR_TEST_DATABASE_URL", DEFAULT_URL)


@pytest.fixture(scope="session")
def _migrated_database() -> Iterator[str]:
    url = _database_url()
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", url.replace("+psycopg", "+psycopg", 1))
    command.upgrade(cfg, "head")
    yield url
    command.downgrade(cfg, "base")


@pytest_asyncio.fixture()
async def session(_migrated_database: str) -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(_migrated_database, future=True)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        await s.execute(
            text(
                "TRUNCATE agent_templates, tool_contracts, glossary_entries, "
                "calibration_history, approval_requests, audit_log CASCADE"
            )
        )
        await s.commit()
        yield s
    await engine.dispose()
