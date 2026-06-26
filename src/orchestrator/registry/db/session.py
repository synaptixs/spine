"""Async SQLAlchemy engine and session factory."""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


def make_engine(url: str, *, echo: bool = False) -> AsyncEngine:
    """Create an async engine from a SQLAlchemy URL.

    The URL must use an async-compatible dialect (e.g. ``postgresql+psycopg``,
    which `psycopg` v3 implements with both sync and async support).
    """
    return create_async_engine(url, echo=echo, future=True)


def make_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def yield_session(
    factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    async with factory() as session:
        yield session
