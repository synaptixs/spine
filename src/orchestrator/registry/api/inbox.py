"""The delegation inbox (unified UI — P2a): the live front door.

``GET /app/inbox`` renders the inbox under the shared shell. Its JS opens the
``/v1/stream`` SSE feed and reflects run activity live — each run a card whose
stage/state updates as events arrive — and surfaces pending approval gates inline
("needs you") with approve / reject, decided through the existing approvals API.

The page leads with a compose card (delegate a feature) and a small animated
flow that shows what happens after you hit Delegate; the live activity + gates
follow.
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from orchestrator.registry.api.web.auth import WebPrincipalDep
from orchestrator.registry.api.web.icons import icon
from orchestrator.registry.api.web.shell import page_shell

router = APIRouter(tags=["inbox"])

# The four beats shown as a small animated pipeline (a teal dot flows left→right,
# each stage lights as it passes) — the "what happens when I delegate" helper.
_FLOW_STAGES: tuple[tuple[str, str], ...] = (
    ("file", "Requirement"),
    ("search", "Understand"),
    ("cpu", "Build + test"),
    ("gitpr", "Pull request"),
)


def _flow() -> str:
    nodes = "".join(
        f'<div class="fnode"><span class="fico" style="--d:{i * 1.2:.1f}s">{icon(glyph)}</span>'
        f'<span class="flab">{label}</span></div>'
        for i, (glyph, label) in enumerate(_FLOW_STAGES)
    )
    return (
        '<div class="flow" aria-hidden="true">'
        '<span class="flow-line"></span><span class="flow-dot"></span>'
        f"{nodes}</div>"
    )


# First-run onboarding, dismissible — the front door explains itself.
_ONBOARDING = (
    '<details class="howto" open>'
    "<summary><strong>New here? How delegating works</strong></summary>"
    f"{_flow()}"
    "<ol>"
    "<li><strong>Paste a source</strong> below (or click an example) and hit <strong>Delegate</strong>.</li>"
    "<li>The engineer reads it and <strong>pauses at the intent gate</strong> — "
    "you approve what it builds.</li>"
    "<li>It writes and tests the code, then <strong>pauses at the merge gate</strong> — "
    "approve to open the PR.</li>"
    "<li>Watch it live below; open a run's <strong>trace</strong> for the full timeline.</li>"
    "</ol>"
    "</details>"
)

# Plain-language help for the one input, plus the safe-by-default guarantee.
_COMPOSER_HELP = (
    '<p class="help"><strong>What\'s a source?</strong> Where your requirement already '
    "lives — a Confluence page (<code>confluence://&lt;page_id&gt;</code>), a Notion doc "
    "(<code>notion://&lt;page_id&gt;</code>), or a Markdown file (<code>file://./spec.md</code>). "
    "<strong>Safe by default:</strong> Spine builds on a branch and shows you a diff — nothing "
    "is pushed, merged, or filed to Jira until you approve. Tick <em>create Jira</em> only to "
    "file a real ticket.</p>"
)

_BODY = (
    "<h1>Delegate a feature</h1>"
    '<p class="lead">Hand the engineer a requirement — a Confluence page, a Notion doc, or a '
    "Markdown file — and watch it build, live. No flags to remember.</p>"
    '<div class="compose-card">'
    '<div class="statusbar" id="status"><span class="dot"></span>'
    '<span id="status-text">checking backend…</span></div>'
    '<div class="composer">'
    "<input id='src' type='text' placeholder='confluence://&lt;page_id&gt; or file://./spec.md'>"
    "<label class='auto'><input id='jira' type='checkbox'> create Jira</label>"
    "<button id='delegate' class='primary'>Delegate</button>"
    "</div>"
    '<div class="examples"><span class="muted">Try:</span> '
    "<button class='ex' data-src='file://./examples/intake/sample-spec.md'>the sample spec</button>"
    "</div>"
    f"{_COMPOSER_HELP}"
    '<details class="cli-details"><summary>Advanced — the exact CLI this runs</summary>'
    '<div class="cli" id="cli-hint">orchestrator sdlc run --source &lt;your source&gt;</div>'
    "</details>"
    "<div id='cmsg'></div>"
    "</div>"
    f"{_ONBOARDING}"
    '<h2>Gates <span class="muted" style="font-size:0.85rem;font-weight:400">'
    "— approvals waiting for you</span></h2>"
    '<div id="gates"></div>'
    '<h2>Activity <span class="muted" style="font-size:0.85rem;font-weight:400">'
    "— runs, newest first</span></h2>"
    '<div id="feed"><p class="muted">Loading…</p></div>'
    '<p class="muted" style="margin-top:1.5rem">Reviewing many runs or gates at once? The '
    '<a href="/console">Console</a> shows the same data as dense tables, with richer approval '
    "controls (approve with clarifications). The Inbox is the place to delegate and watch live.</p>"
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
