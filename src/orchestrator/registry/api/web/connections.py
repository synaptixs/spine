"""Connections page (Phase D1): external MCP servers + source/tracker status.

Renders `GET /v1/connections` — the configured MCP servers (tested live for
reachability + their allow-listed tools) and the integration/tracker checks — as
a read-only management surface. Adding/editing servers (a config write) and
invoking tools are intentionally out of scope here.
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from orchestrator.registry.api.web.auth import WebPrincipalDep
from orchestrator.registry.api.web.shell import page_shell

router = APIRouter(tags=["web"])


@router.get("/app/connections", response_class=HTMLResponse)
async def connections_page(_p: WebPrincipalDep) -> HTMLResponse:
    body = (
        "<h1>Connections</h1>"
        "<p class='lead'>External <strong>MCP servers</strong> Spine can consume (tested live for "
        "reachability and their allow-listed tools) and the <strong>sources &amp; trackers</strong> "
        "the pipeline talks to (Confluence, Jira, Notion, GitHub, the LLM).</p>"
        "<div class='repo-bar'>"
        "<label>config <input id='config' size='36' placeholder='mcp.json path (blank = default)'></label>"
        "<button id='browse'>Browse…</button>"
        "<button id='reload' class='primary'>Test connections</button>"
        "</div>"
        "<div id='fsmodal'></div>"
        "<div id='status' class='muted'></div>"
        "<div id='confbar' class='muted'></div>"
        "<h2>MCP servers</h2><div id='mcp'><p class='muted'>Loading…</p></div>"
        "<div id='editor'></div>"
        "<h2>Sources &amp; trackers</h2><div id='sources'></div>"
    )
    return HTMLResponse(
        content=page_shell(
            title="Connections",
            active="Connections",
            body=body,
            head='<link rel="stylesheet" href="/static/intelligence.css">',
            scripts='<script src="/static/jobrun.js"></script><script src="/static/connections.js"></script>',
        )
    )


__all__ = ["router"]
