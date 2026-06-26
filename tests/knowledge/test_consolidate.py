"""Consolidation write path (cross-run semantic memory, Phase 2).

In-memory SQLite + a scripted MockLLM reflector. Covers episode selection,
insert, dedup-reinforce, and SKIP.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import cast

import pytest_asyncio
from sqlalchemy import Table
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from orchestrator.core.llm import CompletionResult, MockLLMClient
from orchestrator.knowledge.consolidate import _select_episodes, consolidate_run
from orchestrator.registry.db.models import MemoryRow
from orchestrator.registry.repositories import MemoryRepo

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


def _reply(text: str) -> CompletionResult:
    return CompletionResult(
        text=text, model="m", prompt_tokens=1, completion_tokens=1, cost_usd=0.0, latency_ms=0.0
    )


def _bundle(blocks: list[dict[str, str]]) -> dict[str, object]:
    return {"policy_blocks": blocks, "trace": []}


def test_select_episodes_maps_actions_and_dedups() -> None:
    eps = _select_episodes(
        _bundle(
            [
                {"tool": "write_file", "action": "rejected", "reason": "wrong dir"},
                {"tool": "write_file", "action": "rejected", "reason": "wrong dir"},  # dup
                {"tool": "shell", "action": "deny", "reason": "no network"},
                {"tool": "x", "action": "allow", "reason": "n/a"},  # ignored
            ]
        )
    )
    kinds = sorted((e["kind"], e["tool"]) for e in eps)
    assert kinds == [("convention", "write_file"), ("pitfall", "shell")]


async def test_consolidate_inserts_new_memory(factory: async_sessionmaker[AsyncSession]) -> None:
    llm = MockLLMClient(script=[_reply("Do not shadow stdlib modules with local files.")])
    async with factory() as s:
        summary = await consolidate_run(
            bundle=_bundle([{"tool": "write_file", "action": "deny", "reason": "shadows json"}]),
            repo_key=REPO,
            session=s,
            llm=llm,
            model="m",
            run_id="run-7",
        )
    assert summary == {
        "episodes": 1,
        "inserted": 1,
        "reinforced": 0,
        "skipped": 0,
        "decayed": 0,
        "deleted": 0,
    }
    async with factory() as s:
        rows = await MemoryRepo(s).search(query="shadow stdlib modules", repo_key=REPO)
    assert len(rows) == 1
    assert rows[0].kind == "pitfall"
    assert rows[0].evidence == {"run_ids": ["run-7"], "tool": "write_file"}
    assert rows[0].confidence == 0.5


async def test_consolidate_reinforces_duplicate(factory: async_sessionmaker[AsyncSession]) -> None:
    async with factory() as s:
        await MemoryRepo(s).add(
            repo_key=REPO,
            kind="convention",
            confidence=0.5,
            statement="Use absolute imports throughout the package.",
            evidence={"run_ids": ["run-1"]},
        )
        await s.commit()
    llm = MockLLMClient(script=[_reply("Use absolute imports across the whole package.")])
    async with factory() as s:
        summary = await consolidate_run(
            bundle=_bundle([{"tool": "write_file", "action": "rejected", "reason": "relative import"}]),
            repo_key=REPO,
            session=s,
            llm=llm,
            model="m",
            run_id="run-2",
        )
    assert summary["reinforced"] == 1 and summary["inserted"] == 0
    async with factory() as s:
        rows = await MemoryRepo(s).search(query="absolute imports package", repo_key=REPO)
    assert len(rows) == 1  # reinforced, not duplicated
    assert rows[0].confidence == 0.6  # 0.5 + delta
    evidence = rows[0].evidence
    assert evidence is not None
    assert set(evidence["run_ids"]) == {"run-1", "run-2"}


async def test_consolidate_skips_when_reflector_says_skip(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    llm = MockLLMClient(script=[_reply("SKIP")])
    async with factory() as s:
        summary = await consolidate_run(
            bundle=_bundle([{"tool": "x", "action": "deny", "reason": "noise"}]),
            repo_key=REPO,
            session=s,
            llm=llm,
            model="m",
            run_id="run-9",
        )
    assert summary["skipped"] == 1 and summary["inserted"] == 0
    async with factory() as s:
        assert await MemoryRepo(s).search(query="noise", repo_key=REPO) == []


async def test_consolidate_no_episodes_is_noop(factory: async_sessionmaker[AsyncSession]) -> None:
    llm = MockLLMClient(script=[])
    async with factory() as s:
        summary = await consolidate_run(
            bundle=_bundle([]),
            repo_key=REPO,
            session=s,
            llm=llm,
            model="m",
            run_id="r",
        )
    assert summary == {
        "episodes": 0,
        "inserted": 0,
        "reinforced": 0,
        "skipped": 0,
        "decayed": 0,
        "deleted": 0,
    }


async def test_select_episodes_includes_tool_errors() -> None:
    bundle = {
        "policy_blocks": [],
        "trace": [
            {
                "calls": [
                    {"name": "run_tests", "blocked": False, "observation": "error: TimeoutError: x"},
                    {"name": "read_file", "blocked": False, "observation": "ok, file contents"},
                    {"name": "write_file", "blocked": True, "observation": "blocked by policy"},
                ]
            }
        ],
    }
    eps = _select_episodes(bundle)
    assert eps == [{"kind": "pitfall", "tool": "run_tests", "reason": "error: TimeoutError: x"}]


async def test_decay_ages_out_stale_and_prunes_below_floor(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    # Seed a stale, low-confidence memory and a fresh one; backdate the stale one.
    async with factory() as s:
        repo = MemoryRepo(s)
        stale = await repo.add(
            repo_key=REPO, kind="pitfall", confidence=0.18, statement="Old stale lesson about caching."
        )
        await repo.add(
            repo_key=REPO, kind="convention", confidence=0.8, statement="Fresh lesson about imports."
        )
        old = datetime(2000, 1, 1, tzinfo=UTC)
        stale.created_at = old
        stale.last_used_at = old
        await s.commit()

    # A new run with no episodes still runs the decay sweep.
    async with factory() as s:
        summary = await consolidate_run(
            bundle={"policy_blocks": [], "trace": []},
            repo_key=REPO,
            session=s,
            llm=MockLLMClient(script=[]),
            model="m",
            run_id="run-now",
            now=datetime(2026, 6, 24, tzinfo=UTC),
        )
    # stale (0.18 - 0.05 = 0.13 < floor 0.15) is pruned; fresh one is spared.
    assert summary["deleted"] == 1 and summary["decayed"] == 0
    async with factory() as s:
        remaining = await MemoryRepo(s).search(query="lesson", repo_key=REPO)
    assert [r.statement for r in remaining] == ["Fresh lesson about imports."]
