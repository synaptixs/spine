"""Operator console (G12): a local-first UI for the human bookends.

``GET /console`` serves a server-rendered page **through the shared web shell**
(one nav, one stylesheet) — its styles and the vanilla-JS that drives it live as
real static files (``/static/console.css``, ``/static/console.js``), not inline
Python strings (unified UI, P0). The page itself carries NO data, so serving it
needs no auth; all data access and every gate decision go through the
authenticated JSON API (``/v1/approvals``, ``/v1/runs``) with the operator's
``X-API-Key`` supplied in the page toolbar and sent on each ``fetch``. So the data
and the actions stay API-key-protected while the browser (which can't set headers
on a plain navigation) still reaches them.

v1 covers the two things a human-in-control operator needs: the **approval gate
queue** (review a gate's detail — risk, description, open questions, escalation —
then approve / reject / answer with clarifications or release notes) and a **runs
dashboard** (SDLC runs, their state, link to the ``/trace`` timeline).
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from orchestrator.registry.api.web.auth import WebPrincipalDep
from orchestrator.registry.api.web.shell import page_shell

router = APIRouter(tags=["console"])

# The console body: a toolbar (Load + live toggle), a status line, and the two
# sections the JS populates. Auth is the session cookie (the page requires login),
# so there's no API-key input. The shared shell supplies nav + base styles.
_BODY = (
    "<h1>Console</h1>"
    '<p class="lead">The operator view — a dense, table-first take on the same runs and gates as '
    "the <a href='/app/inbox'>Inbox</a>, for scanning many at once and for <strong>richer "
    "approvals</strong> (approve with clarifications / release notes, not just approve or reject). "
    "For delegating a feature and watching one run live, use the Inbox; come here to review in "
    "bulk. Click a run's <strong>trace</strong> for its full timeline.</p>"
    '<div class="toolbar">'
    "<button id='refresh' class='primary'>Load</button>"
    "<label class='auto'><input id='auto' type='checkbox'> live</label>"
    "</div>"
    "<div id='msg'></div>"
    "<h2>Pending approvals</h2>"
    "<div id='approvals'><p class='muted'>Loading…</p></div>"
    '<div class="cli">curl -X POST -H "x-api-key: $ORCHESTRATOR_API_KEY" '
    "http://localhost:8000/v1/approvals/&lt;gate-id&gt;/approve</div>"
    "<h2>Runs</h2><div id='runs'></div>"
)


@router.get("/console", response_class=HTMLResponse)
async def console(_principal: WebPrincipalDep) -> HTMLResponse:
    """The operator console shell (requires a session; JS loads data via the API)."""
    return HTMLResponse(
        content=page_shell(
            title="Console",
            active="Console",
            body=_BODY,
            head='<link rel="stylesheet" href="/static/console.css">',
            scripts='<script src="/static/console.js"></script>',
        )
    )
