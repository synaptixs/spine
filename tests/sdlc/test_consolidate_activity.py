"""Post-merge consolidation activity (cross-run memory, Phase 2b)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import cast

import pytest
import pytest_asyncio
from sqlalchemy import Table
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from orchestrator.core.llm import CompletionResult, LLMClient, MockLLMClient
from orchestrator.registry.db.models import MemoryRow
from orchestrator.registry.repositories import MemoryRepo
from orchestrator.sdlc.activities import SDLCActivities
from orchestrator.sdlc.deps import SDLCDeps
from orchestrator.sdlc.workspace import WorkspaceManager

REPO = "acme/app"


@pytest_asyncio.fixture
async def factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(cast(Table, MemoryRow.__table__).create)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


def _acts(factory: async_sessionmaker[AsyncSession], *, llm: LLMClient | None) -> SDLCActivities:
    deps = SDLCDeps(
        session_factory=factory,
        workspace=WorkspaceManager(root=Path("/tmp/sdlc-mem-test")),
        llm=llm,
    )
    return SDLCActivities(deps)


def _reply(text: str) -> CompletionResult:
    return CompletionResult(
        text=text, model="m", prompt_tokens=1, completion_tokens=1, cost_usd=0.0, latency_ms=0.0
    )


_PAYLOAD = {
    "sdlc_id": "s-1",
    "repo_key": REPO,
    "policy_blocks": [{"tool": "write_file", "action": "deny", "reason": "shadows json"}],
}


async def test_noop_when_flag_off(
    monkeypatch: pytest.MonkeyPatch, factory: async_sessionmaker[AsyncSession]
) -> None:
    monkeypatch.delenv("ORCHESTRATOR_SEMANTIC_MEMORY", raising=False)
    acts = _acts(factory, llm=MockLLMClient(script=[_reply("x")]))
    assert await acts.consolidate_memory(dict(_PAYLOAD)) == {"skipped": True}


async def test_noop_when_no_llm(
    monkeypatch: pytest.MonkeyPatch, factory: async_sessionmaker[AsyncSession]
) -> None:
    monkeypatch.setenv("ORCHESTRATOR_SEMANTIC_MEMORY", "1")
    monkeypatch.setenv("ORCHESTRATOR_INTAKE_MODEL", "gpt-4o")
    acts = _acts(factory, llm=None)
    assert await acts.consolidate_memory(dict(_PAYLOAD)) == {"skipped": True}


async def test_consolidates_when_enabled(
    monkeypatch: pytest.MonkeyPatch, factory: async_sessionmaker[AsyncSession]
) -> None:
    monkeypatch.setenv("ORCHESTRATOR_SEMANTIC_MEMORY", "1")
    monkeypatch.setenv("ORCHESTRATOR_INTAKE_MODEL", "gpt-4o")
    acts = _acts(factory, llm=MockLLMClient(script=[_reply("Never shadow stdlib modules.")]))
    summary = await acts.consolidate_memory(dict(_PAYLOAD))
    assert summary["inserted"] == 1
    async with factory() as s:
        rows = await MemoryRepo(s).search(query="shadow stdlib modules", repo_key=REPO)
    assert len(rows) == 1 and rows[0].kind == "pitfall"
    assert rows[0].evidence == {"run_ids": ["s-1"], "tool": "write_file"}


async def test_empty_blocks_still_runs_decay_sweep(
    monkeypatch: pytest.MonkeyPatch, factory: async_sessionmaker[AsyncSession]
) -> None:
    monkeypatch.setenv("ORCHESTRATOR_SEMANTIC_MEMORY", "1")
    monkeypatch.setenv("ORCHESTRATOR_INTAKE_MODEL", "gpt-4o")
    acts = _acts(factory, llm=MockLLMClient(script=[]))
    summary = await acts.consolidate_memory({"sdlc_id": "s", "repo_key": REPO, "policy_blocks": []})
    # no episodes, but the decay sweep ran (nothing stale → zero counts)
    assert summary == {
        "episodes": 0,
        "inserted": 0,
        "reinforced": 0,
        "skipped": 0,
        "decayed": 0,
        "deleted": 0,
    }
