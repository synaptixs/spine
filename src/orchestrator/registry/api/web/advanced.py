"""Advanced & experimental page (Phase E3).

The agentic ReAct loop and the semantic spine are gated OFF by default, their
run traces aren't persisted, and the spine is mostly a client to external
services — so there's no stored history to browse. Rather than fake ReAct-trace
or drift panels, this reports (from `/v1/system/advanced`) which of those
subsystems are wired, and points at how each is triggered/traced.
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from orchestrator.registry.api.web.auth import WebPrincipalDep
from orchestrator.registry.api.web.shell import page_shell

router = APIRouter(tags=["web"])


@router.get("/app/advanced", response_class=HTMLResponse)
async def advanced_page(_p: WebPrincipalDep) -> HTMLResponse:
    body = (
        "<h1>Advanced &amp; experimental</h1>"
        "<p class='lead'>Gated subsystems — the <strong>agentic ReAct loop</strong> and the "
        "<strong>semantic spine</strong> (ontomesh grounding, infodrift drift → remediation). These are "
        "off by default and keep no stored history to browse; this shows whether each is wired, and how "
        "it's triggered or traced.</p>"
        "<div id='status' class='muted'></div>"
        "<div id='features'><p class='muted'>Loading…</p></div>"
        "<div class='panel'><div class='panel-head'>Triggered from the CLI</div>"
        "<p class='panel-sub'>The spine's remediation (drift → guardrailed fixes) runs on demand — "
        "<code>--safe</code> leaves a reviewable diff per entity, <code>--live</code> opens PRs:</p>"
        "<div class='cli'>orchestrator sdlc remediate --report &lt;infodrift.json&gt; "
        "--mappings &lt;spine-mappings.json&gt;</div></div>"
    )
    return HTMLResponse(
        content=page_shell(
            title="Advanced",
            active="Advanced",
            body=body,
            head='<link rel="stylesheet" href="/static/intelligence.css">',
            scripts='<script src="/static/jobrun.js"></script><script src="/static/advanced.js"></script>',
        )
    )


__all__ = ["router"]
