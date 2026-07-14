"""Registry browser page (Phase A1): agent templates, tool contracts, glossary.

A read-only window onto the three versioned registries that already have `/v1`
CRUD — no new API, just a page. Mirrors the personas browser: a shell page with
``Loading…`` sections filled by a static script that fetches the list endpoints
with the session cookie and renders each entity as a card with an expandable
spec (the list + detail the roadmap's A1 calls for).
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from orchestrator.registry.api.web.auth import WebPrincipalDep
from orchestrator.registry.api.web.shell import page_shell

router = APIRouter(tags=["web"])


@router.get("/app/registry", response_class=HTMLResponse)
async def registry_page(_principal: WebPrincipalDep) -> HTMLResponse:
    body = (
        "<h1>Registry</h1>"
        '<p class="lead">The versioned building blocks a run draws on: '
        "<strong>agent templates</strong> (reusable agent definitions), "
        "<strong>tool contracts</strong> (the governed tool surface), and the "
        "<strong>glossary</strong> (domain terms the verifier checks against). "
        "Read-only here; each entry expands to its full spec.</p>"
        '<div class="cli">orchestrator template list · orchestrator contract list · '
        "orchestrator glossary list</div>"
        '<h2>Agent templates</h2><div id="agent-templates"><p class="muted">Loading…</p></div>'
        '<h2>Tool contracts</h2><div id="tool-contracts"><p class="muted">Loading…</p></div>'
        '<h2>Glossary</h2><div id="glossary"><p class="muted">Loading…</p></div>'
    )
    return HTMLResponse(
        content=page_shell(
            title="Registry",
            active="Registry",
            body=body,
            scripts='<script src="/static/registry.js"></script>',
        )
    )


__all__ = ["router"]
