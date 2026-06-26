"""MemoryRepo + recall_memory tool (cross-run semantic memory, Phase 1).

Uses an in-memory async SQLite DB (StaticPool so all sessions share the one
connection) — no Postgres needed. Exercises the read path end to end: seed a
small memory set, recall via the tool, assert ranking + the hit feedback.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import cast

import pytest_asyncio
from sqlalchemy import Table
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from orchestrator.agentic.memory_tools import build_memory_tools
from orchestrator.registry.db.models import MemoryRow
from orchestrator.registry.repositories import MemoryRepo


@pytest_asyncio.fixture
async def factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    # Create only agent_memory — sibling tables use Postgres-only ARRAY columns
    # that don't compile on SQLite.
    async with engine.begin() as conn:
        await conn.run_sync(cast(Table, MemoryRow.__table__).create)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def _seed(factory: async_sessionmaker[AsyncSession], repo_key: str = "acme/app") -> None:
    async with factory() as s:
        repo = MemoryRepo(s)
        await repo.add(
            repo_key=repo_key,
            kind="convention",
            confidence=0.8,
            statement="Use absolute imports throughout the package.",
            evidence={"run_ids": ["run-1"]},
        )
        await repo.add(
            repo_key=repo_key,
            kind="pitfall",
            confidence=0.6,
            statement="Do not shadow the stdlib json module with a local file.",
            evidence={"run_ids": ["run-2", "run-3"]},
        )
        await repo.add(
            repo_key="other/repo",
            kind="convention",
            confidence=0.9,
            statement="Absolute imports only, never relative.",
        )
        await repo.add(
            repo_key="ignored",
            scope="global",
            kind="convention",
            confidence=0.7,
            statement="Always run absolute imports cleanly before committing.",
        )
        await s.commit()


async def test_search_ranks_by_overlap_and_scopes_to_repo(factory: async_sessionmaker[AsyncSession]) -> None:
    await _seed(factory)
    async with factory() as s:
        rows = await MemoryRepo(s).search(query="absolute imports", repo_key="acme/app")
    statements = [r.statement for r in rows]
    # the repo's own convention + the global one match; the other repo's is excluded
    assert "Use absolute imports throughout the package." in statements
    assert any("before committing" in x for x in statements)  # global scope included
    assert all("never relative" not in x for x in statements)  # other repo excluded


async def test_search_kind_filter(factory: async_sessionmaker[AsyncSession]) -> None:
    await _seed(factory)
    async with factory() as s:
        rows = await MemoryRepo(s).search(query="json stdlib", repo_key="acme/app", kind="pitfall")
    assert len(rows) == 1
    assert rows[0].kind == "pitfall"


async def test_recall_tool_returns_and_records_hits(factory: async_sessionmaker[AsyncSession]) -> None:
    await _seed(factory)
    (tool,) = build_memory_tools(factory, repo_key="acme/app")
    assert tool.spec.name == "recall_memory"

    out = await tool.run({"query": "absolute imports"})
    assert "[convention]" in out
    assert "confidence" in out
    assert "runs: run-1" in out  # evidence cited

    # the matched rows had their hit counter bumped
    async with factory() as s:
        rows = await MemoryRepo(s).search(query="absolute imports", repo_key="acme/app")
    assert any(r.hits >= 1 for r in rows)


async def test_recall_tool_empty_and_bad_input(factory: async_sessionmaker[AsyncSession]) -> None:
    (tool,) = build_memory_tools(factory, repo_key="acme/app")
    assert "required" in await tool.run({"query": "  "})
    assert "kind must be one of" in await tool.run({"query": "x", "kind": "bogus"})
    assert await tool.run({"query": "nonexistent topic xyz"}) == "no relevant memories"
