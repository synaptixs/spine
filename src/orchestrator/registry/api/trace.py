"""Trace UI v0: a thin read-only surface over the audit log.

JSON endpoint ``GET /v1/tasks/{task_id}/trace`` returns every audit row
recorded under the task plus a node-by-node summary suitable for a UI.

HTML endpoint ``GET /trace/{task_id}`` renders the same data as a simple
timeline. No JS, no framework — just enough surface for a human to scan
what happened. LangSmith link appears when ``LANGSMITH_PROJECT`` is set
in the env (the spec's "LangSmith fallback for deep traces").
"""

from __future__ import annotations

import html
import os
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

from orchestrator.registry.api.deps import ApiKeyDep, SessionDep
from orchestrator.registry.api.web.auth import WebPrincipalDep
from orchestrator.registry.db.models import AuditLogRow

router = APIRouter(tags=["trace"])


class AuditEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timestamp: str
    actor: str
    action: str
    resource_type: str
    resource_id: str
    trace_id: str | None
    before: dict[str, Any] | None = None
    after: dict[str, Any] | None = None


class TraceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str
    workflow_pattern: str | None
    verifier_outcome: str | None
    planner_justification: str | None
    templates: list[dict[str, str]]
    audit: list[AuditEntry]
    tool_invocations: list[AuditEntry]
    langsmith_link: str | None
    replan_count: int = 0
    replan_budget: int = 0


@router.get("/v1/tasks/{task_id}/trace", response_model=TraceResponse)
async def task_trace_json(task_id: str, session: SessionDep, _actor: ApiKeyDep) -> TraceResponse:
    rows = await _fetch_task_audit(session, task_id)
    if not rows:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"No audit rows for task {task_id!r}."
        )
    return _build_trace_response(task_id, rows)


@router.get("/trace/{task_id}", response_class=HTMLResponse)
async def task_trace_html(
    task_id: str, request: Request, session: SessionDep, _principal: WebPrincipalDep
) -> HTMLResponse:
    """Read-only HTML page. Requires a web session — it embeds the run's audit
    timeline server-side, so unlike the data-free shells it must be authed (P0b)."""
    _ = request
    rows = await _fetch_task_audit(session, task_id)
    if not rows:
        return HTMLResponse(
            status_code=404,
            content=_html_page(
                title=f"trace {task_id}",
                body=f"<p>No audit rows for task <code>{html.escape(task_id)}</code>.</p>",
            ),
        )
    trace = _build_trace_response(task_id, rows)
    return HTMLResponse(content=_render_trace_html(trace))


async def _fetch_task_audit(session: Any, task_id: str) -> list[AuditLogRow]:
    """All audit rows whose trace_id matches the task or whose resource_id is the task itself."""
    submit_stmt = select(AuditLogRow).where(
        AuditLogRow.resource_type == "task", AuditLogRow.resource_id == task_id
    )
    submit_rows = list((await session.execute(submit_stmt)).scalars().all())
    trace_id: str | None = None
    for row in submit_rows:
        if row.trace_id:
            trace_id = row.trace_id
            break

    if trace_id is None:
        # Fall back to action-scan; covers in-flight or aborted tasks.
        return submit_rows

    related_stmt = select(AuditLogRow).where(AuditLogRow.trace_id == trace_id).order_by(AuditLogRow.timestamp)
    return list((await session.execute(related_stmt)).scalars().all())


def _build_trace_response(task_id: str, rows: list[AuditLogRow]) -> TraceResponse:
    submit_after: dict[str, Any] | None = None
    for row in rows:
        if row.action == "task_submit" and row.resource_id == task_id and row.after_json:
            submit_after = row.after_json
            break

    audit_entries = [_row_to_entry(r) for r in rows if r.action != "tool_invocation"]
    tool_entries = [_row_to_entry(r) for r in rows if r.action == "tool_invocation"]

    langsmith_link = None
    project = os.getenv("LANGSMITH_PROJECT")
    if project:
        langsmith_link = (
            f"https://smith.langchain.com/o/default/projects/{project}/traces?filter=tag:task_id={task_id}"
        )

    return TraceResponse(
        task_id=task_id,
        workflow_pattern=(submit_after or {}).get("workflow_pattern"),
        verifier_outcome=(submit_after or {}).get("verifier_outcome"),
        planner_justification=(submit_after or {}).get("planner_justification"),
        templates=list((submit_after or {}).get("templates") or []),
        audit=audit_entries,
        tool_invocations=tool_entries,
        langsmith_link=langsmith_link,
        replan_count=int((submit_after or {}).get("replan_count") or 0),
        replan_budget=int((submit_after or {}).get("replan_budget") or 0),
    )


def _row_to_entry(row: AuditLogRow) -> AuditEntry:
    return AuditEntry(
        timestamp=row.timestamp.isoformat() if row.timestamp else "",
        actor=row.actor,
        action=row.action,
        resource_type=row.resource_type,
        resource_id=row.resource_id,
        trace_id=row.trace_id,
        before=row.before_json,
        after=row.after_json,
    )


# --- HTML rendering (timeline body only; chrome + styles come from the shell) --


def _outcome_class(outcome: str | None) -> str:
    if outcome in {"pass", "warn", "fail"}:
        return f"outcome-{outcome}"
    return "outcome-unknown"


def _render_trace_html(trace: TraceResponse) -> str:
    body_parts: list[str] = []
    body_parts.append(f"<h1>Task <code>{html.escape(trace.task_id)}</code></h1>")
    body_parts.append(
        '<p class="meta">'
        f"workflow_pattern: <strong>{html.escape(trace.workflow_pattern or '—')}</strong> · "
        f'verifier_outcome: <span class="{_outcome_class(trace.verifier_outcome)}">'
        f"{html.escape(trace.verifier_outcome or '—')}</span>"
        + (
            f" · replans: <strong>{trace.replan_count}/{trace.replan_budget}</strong>"
            if trace.replan_budget
            else ""
        )
        + "</p>"
    )

    if trace.planner_justification:
        body_parts.append(f"<p><em>Planner:</em> {html.escape(trace.planner_justification)}</p>")

    if trace.templates:
        templates_html = ", ".join(
            f"<code>{html.escape(t['id'])}@{html.escape(t['version'])}</code>" for t in trace.templates
        )
        body_parts.append(f"<p><em>Templates:</em> {templates_html}</p>")

    if trace.langsmith_link:
        body_parts.append(
            f'<p><a href="{html.escape(trace.langsmith_link)}" target="_blank">Open in LangSmith →</a></p>'
        )

    body_parts.append("<h2>Timeline</h2>")
    if not trace.audit and not trace.tool_invocations:
        body_parts.append("<p><em>No audit rows.</em></p>")
    else:
        for entry in trace.audit + trace.tool_invocations:
            body_parts.append(_render_entry_html(entry))

    return _html_page(title=f"trace {trace.task_id}", body="\n".join(body_parts))


def _render_entry_html(entry: AuditEntry) -> str:
    css_class = entry.action.replace(".", "_")
    after_block = ""
    if entry.after:
        import json as _json

        after_block = f"<pre>{html.escape(_json.dumps(entry.after, indent=2, default=str))}</pre>"
    return (
        f'<div class="row {html.escape(css_class)}">'
        '<div class="head">'
        f'<span class="action">{html.escape(entry.action)}</span>'
        f'<span class="ts">{html.escape(entry.timestamp)}</span>'
        f"<span>{html.escape(entry.resource_type)}/{html.escape(entry.resource_id)}</span>"
        "</div>"
        f"{after_block}"
        "</div>"
    )


def _html_page(*, title: str, body: str) -> str:
    # Render through the shared shell (one nav + one stylesheet); a single trace
    # isn't a nav destination, so no nav item is marked active.
    from orchestrator.registry.api.web.shell import page_shell

    return page_shell(title=title, active="", body=body)
