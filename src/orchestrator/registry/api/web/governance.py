"""Governance pages (Phase C): the audit-log browser + the policy/budget view.

Makes the "governed autonomy" story visible: every recorded action is browsable
and filterable (C1), and any run's spend-vs-cap + policy/approval decisions are
one lookup away, with a one-click bundle export (C2/C3). Both are thin shells
over ``/v1/audit`` (see ``audit.js`` / ``governance.js``).
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from orchestrator.registry.api.web.auth import WebPrincipalDep
from orchestrator.registry.api.web.shell import page_shell

router = APIRouter(tags=["web"])


@router.get("/app/audit", response_class=HTMLResponse)
async def audit_page(_p: WebPrincipalDep) -> HTMLResponse:
    body = (
        "<h1>Audit log</h1>"
        "<p class='lead'>The append-only record of everything the platform did — every run stage, "
        "tool call, verifier result, approval, and capability job. Filter by actor, action, "
        "resource type, or a run id; click a row for its payload.</p>"
        "<div class='repo-bar'>"
        "<label>run id <input id='run_id' placeholder='sdlc_id / job id' size='18'></label>"
        "<label>actor <input id='actor' size='12'></label>"
        "<label>action <input id='action' size='16'></label>"
        "<label>type <input id='resource_type' size='12'></label>"
        "<button id='load' class='primary'>Search</button>"
        "</div>"
        "<div id='status' class='muted'></div>"
        "<div id='rows'><p class='muted'>Loading…</p></div>"
    )
    return HTMLResponse(
        content=page_shell(
            title="Audit log",
            active="Audit log",
            body=body,
            head='<link rel="stylesheet" href="/static/intelligence.css">',
            scripts='<script src="/static/jobrun.js"></script><script src="/static/audit.js"></script>',
        )
    )


@router.get("/app/governance", response_class=HTMLResponse)
async def governance_page(_p: WebPrincipalDep) -> HTMLResponse:
    body = (
        "<h1>Policy &amp; budget</h1>"
        "<p class='lead'>For any run: how much it spent against the budget cap, the output-policy "
        "verifier outcomes, and the human approvals — plus a one-click <strong>bundle export</strong> "
        "(the run's full receipt). Enter a run id (an <code>sdlc_id</code> or a job id).</p>"
        "<div class='repo-bar'>"
        "<label>run id <input id='run_id' placeholder='sdlc_id / job id' size='22'></label>"
        "<button id='load' class='primary'>Look up</button>"
        "<a id='export' class='muted' href='#' style='display:none'>Export bundle ↓</a>"
        "</div>"
        "<div id='status' class='muted'></div>"
        "<div id='gov'></div>"
    )
    return HTMLResponse(
        content=page_shell(
            title="Policy & budget",
            active="Policy & budget",
            body=body,
            head='<link rel="stylesheet" href="/static/intelligence.css">',
            scripts='<script src="/static/jobrun.js"></script><script src="/static/governance.js"></script>',
        )
    )


__all__ = ["router"]
