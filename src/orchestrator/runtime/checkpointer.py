"""LangGraph checkpointer factory.

In-memory ``MemorySaver`` is sufficient for unit tests and one-shot
synchronous task execution. The ``open_postgres_checkpointer`` async
context manager wraps ``AsyncPostgresSaver`` against the registry
database — its ``setup()`` is idempotent and creates the three
checkpoint tables on first use, so no Alembic migration is required.
The migrations folder stays focused on application schema (registry +
audit log).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

__all__ = ["AsyncPostgresSaver", "MemorySaver", "normalise_pg_url", "open_postgres_checkpointer"]


def normalise_pg_url(url: str) -> str:
    """Strip the SQLAlchemy ``+psycopg`` suffix so AsyncPostgresSaver accepts the URL."""
    if url.startswith("postgresql+psycopg://"):
        return url.replace("postgresql+psycopg://", "postgresql://", 1)
    if url.startswith("postgresql+psycopg_async://"):
        return url.replace("postgresql+psycopg_async://", "postgresql://", 1)
    return url


@asynccontextmanager
async def open_postgres_checkpointer(database_url: str) -> AsyncIterator[AsyncPostgresSaver]:
    """Open a Postgres-backed checkpointer for the duration of a context.

    Calls ``setup()`` on entry so the checkpoint tables exist; safe to call
    repeatedly.
    """
    conn_string = normalise_pg_url(database_url)
    async with AsyncPostgresSaver.from_conn_string(conn_string) as saver:
        await saver.setup()
        yield saver
