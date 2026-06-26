"""Integration tests for the repository layer.

These exercise the real Postgres dialects (ARRAY, JSONB, GIN index).
Run with ``pytest -m integration`` after ``docker compose up``.
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from orchestrator.registry._common import LifecycleState
from orchestrator.registry.db.models import AgentTemplateRow, ToolContractRow
from orchestrator.registry.repositories import (
    AlreadyExistsError,
    AuditLogRepo,
    ImmutablePublishedError,
    NotFoundError,
    VersionedRepo,
)

pytestmark = pytest.mark.integration


def _spec_for_agent() -> dict[str, Any]:
    return {
        "outputs": [
            {"name": "confidence", "type": "float"},
            {"name": "caveats", "type": "list[str]"},
        ],
        "model": "claude-opus-4-7",
    }


async def test_create_and_get_by_id_version(session: AsyncSession) -> None:
    repo = VersionedRepo(session, AgentTemplateRow)
    await repo.create(
        id="research.summarizer",
        version="0.1.0",
        description="Summarize.",
        tags=["research"],
        spec=_spec_for_agent(),
    )
    await session.commit()

    fetched = await repo.get_by_id_version("research.summarizer", "0.1.0")
    assert fetched is not None
    assert fetched.tags == ["research"]
    assert fetched.status == LifecycleState.DRAFT.value


async def test_duplicate_id_version_rejected(session: AsyncSession) -> None:
    repo = VersionedRepo(session, AgentTemplateRow)
    await repo.create(id="x.y", version="0.1.0", description="d", tags=[], spec=_spec_for_agent())
    await session.commit()
    with pytest.raises(AlreadyExistsError):
        await repo.create(id="x.y", version="0.1.0", description="d", tags=[], spec=_spec_for_agent())


async def test_publish_and_get_latest_published(session: AsyncSession) -> None:
    repo = VersionedRepo(session, AgentTemplateRow)
    for v in ("0.1.0", "0.2.0", "0.3.0"):
        await repo.create(id="x.y", version=v, description="d", tags=[], spec=_spec_for_agent())
    await session.commit()
    await repo.publish("x.y", "0.2.0")
    await session.commit()

    latest = await repo.get_latest_published("x.y")
    assert latest is not None
    assert latest.version == "0.2.0"


async def test_mark_deprecated(session: AsyncSession) -> None:
    repo = VersionedRepo(session, AgentTemplateRow)
    await repo.create(id="x.y", version="0.1.0", description="d", tags=[], spec=_spec_for_agent())
    await session.commit()
    await repo.mark_deprecated("x.y", "0.1.0")
    await session.commit()
    row = await repo.get_by_id_version("x.y", "0.1.0")
    assert row is not None
    assert row.status == LifecycleState.DEPRECATED.value


async def test_publish_unknown_version_raises(session: AsyncSession) -> None:
    repo = VersionedRepo(session, AgentTemplateRow)
    with pytest.raises(NotFoundError):
        await repo.publish("missing.id", "0.1.0")


async def test_update_spec_if_draft_blocks_published(session: AsyncSession) -> None:
    repo = VersionedRepo(session, AgentTemplateRow)
    await repo.create(id="x.y", version="0.1.0", description="d", tags=[], spec=_spec_for_agent())
    await session.commit()
    await repo.publish("x.y", "0.1.0")
    await session.commit()
    with pytest.raises(ImmutablePublishedError):
        await repo.update_spec_if_draft("x.y", "0.1.0", spec=_spec_for_agent(), description="new", tags=["t"])


async def test_list_paginates_keyset(session: AsyncSession) -> None:
    repo = VersionedRepo(session, ToolContractRow)
    for i in range(5):
        await repo.create(
            id=f"tool.t{i}",
            version="0.1.0",
            description="d",
            tags=["common"],
            spec={
                "purpose": "x",
                "side_effects": "read",
                "idempotent": True,
                "observability": {"audit": True, "trace": True},
            },
        )
    await session.commit()

    page1, cursor = await repo.list_page(limit=2)
    assert len(page1) == 2
    assert cursor is not None
    page2, cursor2 = await repo.list_page(limit=2, after_pk=cursor)
    assert len(page2) == 2
    page3, cursor3 = await repo.list_page(limit=2, after_pk=cursor2)
    assert len(page3) == 1
    assert cursor3 is None
    assert {r.pk for r in page1 + page2 + page3} == {r.pk for r in page1 + page2 + page3}


async def test_audit_log_append_only(session: AsyncSession) -> None:
    repo = AuditLogRepo(session)
    await repo.write(
        actor="dev-key",
        action="register",
        resource_type="agent_template",
        resource_id="x.y@0.1.0",
        after={"description": "d"},
        trace_id="t_1",
    )
    await session.commit()
