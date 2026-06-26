"""Cross-run semantic memory wired into codegen (Phase 1b).

Verifies the adapter exposes recall_memory + primes the task only when both the
memory deps are supplied AND ORCHESTRATOR_SEMANTIC_MEMORY is set — and is inert
otherwise (prior behavior preserved).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import cast

import pytest
import pytest_asyncio
from sqlalchemy import Table
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from orchestrator.core.llm import MockLLMClient
from orchestrator.registry.db.models import MemoryRow
from orchestrator.registry.repositories import MemoryRepo
from orchestrator.sdlc.codegen import LLMCodegenAdapter

REPO_KEY = "acme/app"


@pytest_asyncio.fixture
async def factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(cast(Table, MemoryRow.__table__).create)
    f = async_sessionmaker(engine, expire_on_commit=False)
    async with f() as s:
        await MemoryRepo(s).add(
            repo_key=REPO_KEY,
            kind="convention",
            confidence=0.9,
            statement="Use absolute imports throughout the package.",
            evidence={"run_ids": ["run-1"]},
        )
        await s.commit()
    yield f
    await engine.dispose()


def _adapter(factory: async_sessionmaker[AsyncSession] | None) -> LLMCodegenAdapter:
    return LLMCodegenAdapter(
        MockLLMClient(),
        model="m",
        agentic=True,
        memory_factory=factory,
        memory_repo_key=REPO_KEY if factory else None,
    )


def test_disabled_when_flag_off(
    monkeypatch: pytest.MonkeyPatch, factory: async_sessionmaker[AsyncSession]
) -> None:
    monkeypatch.delenv("ORCHESTRATOR_SEMANTIC_MEMORY", raising=False)
    assert _adapter(factory)._memory_enabled() is False


def test_disabled_when_no_deps(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORCHESTRATOR_SEMANTIC_MEMORY", "1")
    assert _adapter(None)._memory_enabled() is False


def test_enabled_with_flag_and_deps(
    monkeypatch: pytest.MonkeyPatch, factory: async_sessionmaker[AsyncSession]
) -> None:
    monkeypatch.setenv("ORCHESTRATOR_SEMANTIC_MEMORY", "1")
    assert _adapter(factory)._memory_enabled() is True


async def test_priming_block_present_when_enabled(
    monkeypatch: pytest.MonkeyPatch, factory: async_sessionmaker[AsyncSession]
) -> None:
    monkeypatch.setenv("ORCHESTRATOR_SEMANTIC_MEMORY", "1")
    block = await _adapter(factory)._memory_priming("absolute imports please")
    assert "LEARNED FROM PAST RUNS" in block
    assert "absolute imports" in block.lower()


async def test_priming_empty_when_disabled(
    monkeypatch: pytest.MonkeyPatch, factory: async_sessionmaker[AsyncSession]
) -> None:
    monkeypatch.delenv("ORCHESTRATOR_SEMANTIC_MEMORY", raising=False)
    assert await _adapter(factory)._memory_priming("absolute imports") == ""


async def test_recall_tool_present_in_agentic_toolset(
    monkeypatch: pytest.MonkeyPatch, factory: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    monkeypatch.setenv("ORCHESTRATOR_SEMANTIC_MEMORY", "1")
    (tmp_path / "m.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    from orchestrator.agentic.codegen_tools import CodegenSession

    adapter = _adapter(factory)
    tools = await adapter._agentic_tools(tmp_path, None, CodegenSession(tracker={}))
    names = {t.spec.name for t in tools}
    assert "recall_memory" in names

    # off → tool absent
    monkeypatch.delenv("ORCHESTRATOR_SEMANTIC_MEMORY", raising=False)
    tools_off = await adapter._agentic_tools(tmp_path, None, CodegenSession(tracker={}))
    assert "recall_memory" not in {t.spec.name for t in tools_off}
