"""System / readiness page (Phase A3): a status dashboard over `doctor`.

Renders `GET /v1/system/readiness` — the environment checks + the database probe
— as a traffic-light dashboard so an operator can see at a glance whether the
stack is configured and reachable, without dropping to `orchestrator doctor`.
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from orchestrator.registry.api.web.auth import WebPrincipalDep
from orchestrator.registry.api.web.shell import page_shell

router = APIRouter(tags=["web"])


@router.get("/app/system", response_class=HTMLResponse)
async def system_page(_principal: WebPrincipalDep) -> HTMLResponse:
    body = (
        "<h1>System</h1>"
        '<p class="lead">Is the stack ready? This runs the same environment checks as '
        "<code>orchestrator doctor</code> — which integrations are configured (by variable "
        "<em>presence</em>, never their values) — plus a live database probe. Optional groups "
        "that aren't set show as <span class=\"pill skip\">skipped</span> and don't block readiness.</p>"
        '<div class="cli">orchestrator doctor · GET /readyz</div>'
        '<div id="readiness"><p class="muted">Loading…</p></div>'
    )
    return HTMLResponse(
        content=page_shell(
            title="System",
            active="System",
            body=body,
            scripts='<script src="/static/system.js"></script>',
        )
    )


__all__ = ["router"]
