"""Cross-run memory browser page (Phase E2).

Renders `GET /v1/memory` — the conventions, pitfalls, and facts the engineer has
consolidated across runs, per repo — so an operator can see what Spine has
learned (and with what confidence / evidence). Browse a repo or search by keyword.
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from orchestrator.registry.api.web.auth import WebPrincipalDep
from orchestrator.registry.api.web.shell import page_shell

router = APIRouter(tags=["web"])


@router.get("/app/memory", response_class=HTMLResponse)
async def memory_page(_p: WebPrincipalDep) -> HTMLResponse:
    body = (
        "<h1>Cross-run memory</h1>"
        "<p class='lead'>What the engineer has learned across runs — conventions it should follow, "
        "pitfalls to avoid, facts about a repo — each with a confidence and the runs that "
        "evidenced it. This is the same memory the codegen loop recalls at build time.</p>"
        "<div class='repo-bar'>"
        "<label>repo <select id='repo'><option value=''>all repos</option></select></label>"
        "<label>kind <input id='kind' size='12' placeholder='convention / pitfall'></label>"
        "<label>search <input id='query' size='18' placeholder='keywords'></label>"
        "<button id='load' class='primary'>Load</button>"
        "</div>"
        "<div id='status' class='muted'></div><div id='mem'></div>"
    )
    return HTMLResponse(
        content=page_shell(
            title="Cross-run memory",
            active="Cross-run memory",
            body=body,
            head='<link rel="stylesheet" href="/static/intelligence.css">',
            scripts='<script src="/static/jobrun.js"></script><script src="/static/memory.js"></script>',
        )
    )


__all__ = ["router"]
