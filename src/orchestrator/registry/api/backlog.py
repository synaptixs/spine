"""Backlog preview, folded into the unified registry app (P0).

Hosts the Block-B Confluence→backlog preview that previously ran as a *separate*
FastAPI app on its own port (``intake/web/app.py``). Now it's one more surface
under the shared shell:

- ``GET  /app/backlog``      → the form, rendered through ``page_shell``.
- ``POST /v1/intake/preview`` → the read-only analyze, reusing ``run_preview``.

The preview is strictly read-only (only ``BacklogService.analyze``); live Jira
creation stays the CLI's job. The service builder defaults to the Confluence
factory but is overridable via ``app.state.intake_service_builder`` (so tests
inject a fake with no env/network).
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from orchestrator.intake.web.app import PreviewRequest, run_preview
from orchestrator.registry.api.deps import PrincipalDep
from orchestrator.registry.api.web.auth import WebPrincipalDep
from orchestrator.registry.api.web.shell import page_shell

router = APIRouter(tags=["backlog"])

_BODY = (
    "<h1>Confluence → backlog preview</h1>"
    '<p class="lead">A safe first step: paste a Confluence page id to see the backlog it derives — '
    "intents, gaps, and draft specs. <strong>Read-only</strong>; nothing is written to Jira. This is the "
    "same analysis the Inbox runs before building.</p>"
    '<form id="f" class="intake-form">'
    "<input type='text' id='source' placeholder='confluence://&lt;page_id&gt;' autofocus>"
    "<button type='submit' id='go' class='primary'>Preview</button>"
    "</form>"
    '<div class="cli">orchestrator ingest --source confluence://&lt;page_id&gt; --dry-run</div>'
    '<div id="status"></div><div id="out"></div>'
)


@router.get("/app/backlog", response_class=HTMLResponse)
async def backlog_page(_principal: WebPrincipalDep) -> HTMLResponse:
    return HTMLResponse(
        content=page_shell(
            title="Backlog",
            active="Backlog",
            body=_BODY,
            head='<link rel="stylesheet" href="/static/intake.css">',
            scripts='<script src="/static/intake.js"></script>',
        )
    )


@router.post("/v1/intake/preview", tags=["preview"])
async def preview(req: PreviewRequest, request: Request, _principal: PrincipalDep) -> dict[str, object]:
    # A test may inject a fixed builder; otherwise the builder is selected by the
    # source scheme inside run_preview (so file:// / openspec:// / notion:// etc.
    # preview too, not just confluence://).
    builder = getattr(request.app.state, "intake_service_builder", None)
    return await run_preview(req, builder)
