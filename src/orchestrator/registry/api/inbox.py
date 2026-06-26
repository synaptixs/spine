"""The delegation inbox (unified UI — P2a): the live front door.

``GET /app/inbox`` renders the inbox under the shared shell. Its JS opens the
``/v1/stream`` SSE feed and reflects run activity live — each run a card whose
stage/state updates as events arrive — and surfaces pending approval gates inline
("needs you") with approve / reject, decided through the existing approvals API.

This is the "watch the engineer's progress and clear its gates" half of the inbox;
the delegate-a-task composer (which starts a run) is the next slice (P2b).
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from orchestrator.registry.api.web.auth import WebPrincipalDep
from orchestrator.registry.api.web.shell import page_shell

router = APIRouter(tags=["inbox"])

_BODY = (
    "<h1>Inbox</h1>"
    '<p class="lead">Delegate work to the engineer, watch its progress live, and clear its gates — '
    "no flags to remember. Each action here runs the same thing the CLI does (shown below).</p>"
    '<div class="statusbar" id="status"><span class="dot"></span>'
    '<span id="status-text">checking backend…</span></div>'
    '<div class="composer">'
    "<input id='src' type='text' placeholder='confluence://&lt;page_id&gt; or file://./spec.md'>"
    "<label class='auto'><input id='jira' type='checkbox'> create Jira</label>"
    "<button id='delegate' class='primary'>Delegate</button>"
    "</div>"
    '<div class="cli" id="cli-hint">orchestrator sdlc run --source &lt;your source&gt;</div>'
    "<div id='cmsg'></div>"
    '<h2>Gates <span class="muted" style="font-size:0.85rem;font-weight:400">'
    "— approvals waiting for you</span></h2>"
    '<div id="gates"></div>'
    '<h2>Activity <span class="muted" style="font-size:0.85rem;font-weight:400">'
    "— runs, newest first</span></h2>"
    '<div id="feed"><p class="muted">Loading…</p></div>'
)


@router.get("/app/inbox", response_class=HTMLResponse)
async def inbox_page(_principal: WebPrincipalDep) -> HTMLResponse:
    return HTMLResponse(
        content=page_shell(
            title="Inbox",
            active="Inbox",
            body=_BODY,
            head='<link rel="stylesheet" href="/static/inbox.css">',
            scripts='<script src="/static/inbox.js"></script>',
        )
    )
