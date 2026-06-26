"""Run-state SSE stream (unified UI — P1b).

``GET /v1/stream`` is a server-sent-events feed of run activity — the one new
*capability* the unified UI needs (the delegation inbox, P2, consumes it for live
stage chips and gate prompts). Runs execute in the Temporal worker process, so the
API can't observe them in memory; the **audit log is the source of truth** and this
endpoint *tails* it: poll for rows newer than a timestamp cursor, map each to a
typed event, emit. The cursor is the row ``timestamp`` (also the SSE ``id``), so
``Last-Event-ID`` resumes where a dropped connection left off.

Transport is the simplest workable one (a short-interval DB tail, per the spec's
decision); it can be swapped for Postgres ``LISTEN/NOTIFY`` or Redis later behind
the same event contract. Scoped to the caller's tenant; optional ``?run_id=``.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from orchestrator.registry.api.deps import PrincipalDep
from orchestrator.registry.api.runs import _TERMINAL_STATE
from orchestrator.registry.db.models import AuditLogRow

router = APIRouter(prefix="/v1", tags=["stream"])

POLL_INTERVAL_S = 1.5
HEARTBEAT_EVERY = 10  # idle polls between keepalive comments (~15s)
_MAX_BATCH = 200


def event_type(action: str, resource_type: str) -> str:
    """The event ``type`` for an audit row's ``action`` / ``resource_type``."""
    if action == "task_submit":
        return "run.created"
    if action in _TERMINAL_STATE:
        return "run.completed"
    if "approval" in action or resource_type == "approval":
        return "approval.updated"
    return "run.stage"


def to_event(row: AuditLogRow) -> dict[str, Any]:
    """An audit row → the unified event envelope."""
    seq = row.timestamp.isoformat() if row.timestamp else ""
    return {
        "type": event_type(row.action, row.resource_type),
        "run_id": row.resource_id,
        "seq": seq,
        "ts": seq,
        "payload": {
            "action": row.action,
            "actor": row.actor,
            "resource_type": row.resource_type,
            "state": _TERMINAL_STATE.get(row.action),
            "after": row.after_json,
        },
    }


def format_sse(event: dict[str, Any]) -> str:
    """Render an event as one SSE message (``id``/``event``/``data`` block)."""
    return f"id: {event['seq']}\nevent: {event['type']}\ndata: {json.dumps(event, default=str)}\n\n"


def parse_cursor(last_event_id: str | None) -> datetime | None:
    if not last_event_id:
        return None
    try:
        return datetime.fromisoformat(last_event_id)
    except ValueError:
        return None


async def _fetch_new(
    factory: Any, tenant_id: str, run_id: str | None, since: datetime | None
) -> list[AuditLogRow]:
    if factory is None:
        return []
    stmt = (
        select(AuditLogRow)
        .where(AuditLogRow.tenant_id == tenant_id)
        .order_by(AuditLogRow.timestamp)
        .limit(_MAX_BATCH)
    )
    if since is not None:
        stmt = stmt.where(AuditLogRow.timestamp > since)
    if run_id is not None:
        stmt = stmt.where(AuditLogRow.resource_id == run_id)
    async with factory() as session:
        return list((await session.execute(stmt)).scalars().all())


async def sse_generator(
    factory: Any, tenant_id: str, run_id: str | None, since: datetime | None
) -> AsyncGenerator[str, None]:
    """Yield SSE messages for new audit rows, with idle keepalives. Endless until
    the client disconnects (Starlette cancels the generator)."""
    yield ": connected\n\n"  # flush headers immediately, before any DB poll
    cursor = since
    idle = 0
    while True:
        try:
            rows = await _fetch_new(factory, tenant_id, run_id, cursor)
        except Exception:  # noqa: BLE001 — a transient DB hiccup shouldn't kill the stream
            rows = []
        if rows:
            idle = 0
            for row in rows:
                if row.timestamp is not None:
                    cursor = row.timestamp
                yield format_sse(to_event(row))
        else:
            idle += 1
            if idle % HEARTBEAT_EVERY == 0:
                yield ": keepalive\n\n"
        await asyncio.sleep(POLL_INTERVAL_S)


@router.get("/stream")
async def stream(request: Request, principal: PrincipalDep, run_id: str | None = None) -> StreamingResponse:
    factory = getattr(request.app.state, "session_factory", None)
    since = parse_cursor(request.headers.get("last-event-id"))
    return StreamingResponse(
        sse_generator(factory, principal.tenant_id, run_id, since),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
