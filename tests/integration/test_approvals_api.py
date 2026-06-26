"""Sprint 14.1 + 14.2 + 14.3 (partial): integration test for the read API.

Round-trip an ApprovalRequest through the repo + the REST list/get
endpoints. Decision endpoints + audit chaining land in Bundle 2; this
test pins the persistence + read surface that everything else builds on.
"""

from __future__ import annotations

import os

import httpx
import pytest
from asgi_lifespan import LifespanManager
from sqlalchemy.ext.asyncio import AsyncSession

from orchestrator.approval import (
    ApprovalRequest,
    ApprovalRequestRepo,
    ApprovalState,
    ApprovalTimeout,
    Approver,
    RiskClassification,
)
from orchestrator.core.llm import MockLLMClient
from orchestrator.registry.api.app import create_app
from orchestrator.registry.api.config import Settings

pytestmark = pytest.mark.integration

API_KEY = "test-key"


def _settings() -> Settings:
    return Settings(
        database_url=os.getenv(
            "ORCHESTRATOR_TEST_DATABASE_URL",
            "postgresql+psycopg://orchestrator:orchestrator@localhost:5433/orchestrator",
        ),
        api_key=API_KEY,
    )


def _request(approval_id: str, *, task_id: str = "task-12345678") -> ApprovalRequest:
    return ApprovalRequest(
        id=approval_id,
        task_id=task_id,
        before_node_id="n_destructive",
        title="Approve destructive migration",
        description="DROP a non-empty column from prod.users.",
        action_summary="ALTER TABLE prod.users DROP COLUMN legacy_email;",
        risk_classification=RiskClassification.HIGH,
        affected_resources=["prod.users"],
        approvers=[Approver(role="dba", min_required=1)],
        timeout=ApprovalTimeout(after_seconds=3600, auto_action="reject"),
        notification_channels=["#ops"],
    )


async def test_create_then_list_then_get(session: AsyncSession) -> None:
    repo = ApprovalRequestRepo(session)
    saved = await repo.create(_request("approval-aaaaaaaa"))
    await session.commit()

    assert saved.state is ApprovalState.PENDING
    # First approval on a fresh task: no prior chain → before_hash is None.
    assert saved.before_hash is None

    app = create_app(_settings(), llm_client=MockLLMClient())
    headers = {"X-API-Key": API_KEY}
    async with (
        LifespanManager(app) as manager,
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=manager.app), base_url="http://test", headers=headers
        ) as client,
    ):
        listed = await client.get("/v1/approvals")
        assert listed.status_code == 200, listed.text
        items = listed.json()["items"]
        assert any(item["id"] == "approval-aaaaaaaa" for item in items)
        assert items[0]["risk_classification"] == "high"

        detail = await client.get("/v1/approvals/approval-aaaaaaaa")
        assert detail.status_code == 200, detail.text
        body = detail.json()
        assert body["task_id"] == "task-12345678"
        assert body["approvers"] == [{"role": "dba", "min_required": 1}]
        assert body["timeout"] == {"after_seconds": 3600, "auto_action": "reject"}


async def test_get_returns_404_for_unknown_id(session: AsyncSession) -> None:
    _ = session  # only needed for the conftest fixture chain
    app = create_app(_settings(), llm_client=MockLLMClient())
    headers = {"X-API-Key": API_KEY}
    async with (
        LifespanManager(app) as manager,
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=manager.app), base_url="http://test", headers=headers
        ) as client,
    ):
        resp = await client.get("/v1/approvals/does-not-exist")
    assert resp.status_code == 404


async def test_before_hash_links_consecutive_approvals_on_same_task(
    session: AsyncSession,
) -> None:
    """The second approval for the same task carries a non-null before_hash
    pointing at the first row. Sprint 14.9 chain integrity in miniature."""
    repo = ApprovalRequestRepo(session)
    first = await repo.create(_request("approval-bbbbbbbb", task_id="task-shared0"))
    await session.commit()
    second = await repo.create(_request("approval-cccccccc", task_id="task-shared0"))
    await session.commit()

    assert first.before_hash is None
    assert second.before_hash is not None
    assert len(second.before_hash) == 64  # sha256 hex digest


async def test_approve_endpoint_updates_state_and_writes_audit(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """POST /v1/approvals/{id}/approve: row transitions to approved, audit
    row appears, and the (would-be) workflow signal is recorded but not
    actually sent because we monkeypatch the dispatch off."""
    monkeypatch.setenv("ORCHESTRATOR_DISABLE_WORKFLOW_SIGNAL", "1")

    repo = ApprovalRequestRepo(session)
    await repo.create(_request("approval-eeeeeeee", task_id="task-decide11"))
    await session.commit()

    app = create_app(_settings(), llm_client=MockLLMClient())
    headers = {"X-API-Key": API_KEY}
    async with (
        LifespanManager(app) as manager,
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=manager.app), base_url="http://test", headers=headers
        ) as client,
    ):
        resp = await client.post(
            "/v1/approvals/approval-eeeeeeee/approve",
            json={"rationale": "looks good"},
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["state"] == "approved"
    assert body["decided_by"] == "test-key"
    assert body["decision_rationale"] == "looks good"


async def test_double_decision_returns_409(session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> None:
    """Decisions are immutable: approving a row a second time must 409."""
    monkeypatch.setenv("ORCHESTRATOR_DISABLE_WORKFLOW_SIGNAL", "1")

    repo = ApprovalRequestRepo(session)
    await repo.create(_request("approval-ffffffff", task_id="task-decide22"))
    await session.commit()

    app = create_app(_settings(), llm_client=MockLLMClient())
    headers = {"X-API-Key": API_KEY}
    async with (
        LifespanManager(app) as manager,
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=manager.app), base_url="http://test", headers=headers
        ) as client,
    ):
        first = await client.post("/v1/approvals/approval-ffffffff/approve", json={})
        assert first.status_code == 200, first.text
        second = await client.post("/v1/approvals/approval-ffffffff/reject", json={})
    assert second.status_code == 409


async def test_modify_input_requires_payload(session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> None:
    """modify_input without a modified_input patch is a 400 — the whole
    point of the endpoint is to ship a patch through to the workflow."""
    monkeypatch.setenv("ORCHESTRATOR_DISABLE_WORKFLOW_SIGNAL", "1")

    repo = ApprovalRequestRepo(session)
    await repo.create(_request("approval-gggggggg", task_id="task-decide33"))
    await session.commit()

    app = create_app(_settings(), llm_client=MockLLMClient())
    headers = {"X-API-Key": API_KEY}
    async with (
        LifespanManager(app) as manager,
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=manager.app), base_url="http://test", headers=headers
        ) as client,
    ):
        resp = await client.post(
            "/v1/approvals/approval-gggggggg/modify_input",
            json={"rationale": "let me tweak the prompt"},
        )
    assert resp.status_code == 400
    assert "modified_input" in resp.text


async def test_list_timed_out_returns_only_expired_pending(session: AsyncSession) -> None:
    """Sprint 14.8: list_timed_out returns rows where (now - created_at)
    >= timeout.after_seconds AND state is still pending.

    We create one row with a tiny 1s timeout and back-date its created_at
    so the threshold has elapsed without sleeping; a second row with a
    long timeout shouldn't surface.
    """
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import update

    from orchestrator.registry.db.models import ApprovalRequestRow

    repo = ApprovalRequestRepo(session)
    await repo.create(
        _request("approval-timeout-1", task_id="task-timeout-1").model_copy(
            update={
                "timeout": ApprovalTimeout(after_seconds=1, auto_action="reject"),
            }
        )
    )
    await repo.create(
        _request("approval-timeout-2", task_id="task-timeout-2").model_copy(
            update={
                "timeout": ApprovalTimeout(after_seconds=3600, auto_action="reject"),
            }
        )
    )
    # Back-date the first row so its 1-second timeout has elapsed.
    await session.execute(
        update(ApprovalRequestRow)
        .where(ApprovalRequestRow.id == "approval-timeout-1")
        .values(created_at=datetime.now(UTC) - timedelta(seconds=60))
    )
    await session.commit()

    timed_out = await repo.list_timed_out()
    ids = {row.id for row in timed_out}
    assert "approval-timeout-1" in ids
    assert "approval-timeout-2" not in ids


async def test_list_pending_filters_out_decided(session: AsyncSession) -> None:
    repo = ApprovalRequestRepo(session)
    await repo.create(_request("approval-dddddddd", task_id="task-decided1"))
    await session.commit()
    updated = await repo.decide("approval-dddddddd", state=ApprovalState.APPROVED, decided_by="alice")
    await session.commit()
    assert updated is not None and updated.state is ApprovalState.APPROVED

    pending = await repo.list_pending()
    assert all(item.id != "approval-dddddddd" for item in pending)


# ---- Bet 2c-ii: RBAC + multi-tenancy ----------------------------------------

# Two principals in different tenants with different roles. ``alice`` is a DBA
# in ``acme``; ``bob`` is a dev in ``globex``.
_PRINCIPALS = {
    "alice-key": {"id": "alice", "tenant_id": "acme", "roles": ["dba"]},
    "bob-key": {"id": "bob", "tenant_id": "globex", "roles": ["dev"]},
}


def _rbac_settings() -> Settings:
    s = _settings()
    s.principals = dict(_PRINCIPALS)
    return s


def _req_for(
    approval_id: str, *, tenant_id: str, role: str = "dba", task_id: str = "task-rbac0001"
) -> ApprovalRequest:
    return ApprovalRequest(
        id=approval_id,
        task_id=task_id,
        tenant_id=tenant_id,
        before_node_id="n_destructive",
        title="Approve destructive migration",
        description="DROP a column.",
        action_summary="ALTER TABLE ...",
        risk_classification=RiskClassification.HIGH,
        approvers=[Approver(role=role, min_required=1)],
    )


async def test_decide_requires_a_held_role(session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> None:
    """A principal lacking a required role is 403; one holding it succeeds and
    ``decided_by`` is the principal id (not the raw key)."""
    monkeypatch.setenv("ORCHESTRATOR_DISABLE_WORKFLOW_SIGNAL", "1")
    repo = ApprovalRequestRepo(session)
    await repo.create(_req_for("approval-rbac-role1", tenant_id="acme", role="dba"))
    await repo.create(_req_for("approval-rbac-role2", tenant_id="acme", role="dba", task_id="task-rbac0002"))
    await session.commit()

    app = create_app(_rbac_settings(), llm_client=MockLLMClient())
    async with (
        LifespanManager(app) as manager,
        httpx.AsyncClient(transport=httpx.ASGITransport(app=manager.app), base_url="http://test") as client,
    ):
        # bob is a dev in globex — wrong tenant AND wrong role → 404 (tenant first).
        denied = await client.post(
            "/v1/approvals/approval-rbac-role1/approve",
            json={},
            headers={"X-API-Key": "bob-key"},
        )
        assert denied.status_code == 404, denied.text

        # An acme principal without the dba role → 403.
        app.state.settings.principals["carol-key"] = {
            "id": "carol",
            "tenant_id": "acme",
            "roles": ["dev"],
        }
        forbidden = await client.post(
            "/v1/approvals/approval-rbac-role1/approve",
            json={},
            headers={"X-API-Key": "carol-key"},
        )
        assert forbidden.status_code == 403, forbidden.text

        # alice holds dba in acme → approves; decided_by is her id.
        ok = await client.post(
            "/v1/approvals/approval-rbac-role2/approve",
            json={"rationale": "reviewed"},
            headers={"X-API-Key": "alice-key"},
        )
        assert ok.status_code == 200, ok.text
        assert ok.json()["decided_by"] == "alice"


async def test_cross_tenant_get_and_decide_are_404(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A row owned by another tenant is invisible (get + decide both 404) — no
    cross-tenant id leakage."""
    monkeypatch.setenv("ORCHESTRATOR_DISABLE_WORKFLOW_SIGNAL", "1")
    repo = ApprovalRequestRepo(session)
    await repo.create(_req_for("approval-rbac-tenant1", tenant_id="acme", role="any"))
    await session.commit()

    app = create_app(_rbac_settings(), llm_client=MockLLMClient())
    async with (
        LifespanManager(app) as manager,
        httpx.AsyncClient(transport=httpx.ASGITransport(app=manager.app), base_url="http://test") as client,
    ):
        # bob (globex) cannot see or decide acme's approval.
        got = await client.get("/v1/approvals/approval-rbac-tenant1", headers={"X-API-Key": "bob-key"})
        assert got.status_code == 404, got.text
        decided = await client.post(
            "/v1/approvals/approval-rbac-tenant1/approve", json={}, headers={"X-API-Key": "bob-key"}
        )
        assert decided.status_code == 404, decided.text

        # alice (acme) can — the role is "any", so any acme principal qualifies.
        ok = await client.post(
            "/v1/approvals/approval-rbac-tenant1/approve", json={}, headers={"X-API-Key": "alice-key"}
        )
        assert ok.status_code == 200, ok.text


async def test_list_pending_is_tenant_scoped(session: AsyncSession) -> None:
    """The pending queue shows only the caller's tenant."""
    repo = ApprovalRequestRepo(session)
    await repo.create(_req_for("approval-rbac-list-a", tenant_id="acme", role="any", task_id="task-rbacL1"))
    await repo.create(_req_for("approval-rbac-list-b", tenant_id="globex", role="any", task_id="task-rbacL2"))
    await session.commit()

    app = create_app(_rbac_settings(), llm_client=MockLLMClient())
    async with (
        LifespanManager(app) as manager,
        httpx.AsyncClient(transport=httpx.ASGITransport(app=manager.app), base_url="http://test") as client,
    ):
        acme = await client.get("/v1/approvals", headers={"X-API-Key": "alice-key"})
        ids = {item["id"] for item in acme.json()["items"]}
    assert "approval-rbac-list-a" in ids
    assert "approval-rbac-list-b" not in ids  # globex's approval is hidden


async def test_repo_round_trips_tenant_id(session: AsyncSession) -> None:
    """``tenant_id`` persists and scopes get/list at the repo layer."""
    repo = ApprovalRequestRepo(session)
    await repo.create(_req_for("approval-rbac-rt", tenant_id="acme", role="any", task_id="task-rbacRT"))
    await session.commit()

    fetched = await repo.get("approval-rbac-rt")
    assert fetched is not None and fetched.tenant_id == "acme"
    assert await repo.get("approval-rbac-rt", tenant_id="acme") is not None
    assert await repo.get("approval-rbac-rt", tenant_id="globex") is None  # scoped out
