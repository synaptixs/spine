"""Unified-UI home landing (P0): one front door that links the operator surfaces.

Replaces "the operator must know three separate URLs" with a single ``/app`` page
under the shared shell. As P0 folds in more surfaces (backlog, personas, the
delegation inbox), they join the cards + nav here.
"""

from __future__ import annotations

import html

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from orchestrator.registry.api.web.auth import WebPrincipalDep
from orchestrator.registry.api.web.icons import icon
from orchestrator.registry.api.web.shell import page_shell

router = APIRouter(tags=["web"])


@router.get("/", include_in_schema=False)
async def root() -> RedirectResponse:
    """Send the site root to the app (which redirects to /login if signed out)."""
    return RedirectResponse("/app", status_code=307)


@router.get("/favicon.ico", include_in_schema=False)
async def favicon() -> Response:
    return Response(status_code=204)


# (title, href, description, icon) — the surfaces surfaced on the home page.
_CARDS: tuple[tuple[str, str, str, str], ...] = (
    ("Inbox", "/app/inbox", "Delegate work, watch it live, clear its gates.", "inbox"),
    ("Intake studio", "/app/intake", "Preview any source, then delegate a gated run.", "sparkles"),
    ("Console", "/console", "Review approval gates and the runs dashboard.", "table"),
    ("Backlog", "/app/backlog", "Preview a Confluence source as a backlog.", "list"),
    ("Understand", "/app/understand", "Map a repo into a code-true memory bank.", "search"),
    ("Current State", "/app/state", "A code-true report of what the system is.", "file"),
    ("Knowledge graph", "/app/graph", "The module-level knowledge-graph overview.", "branch"),
    ("Personas", "/app/personas", "The personas and skills the engineer uses.", "users"),
    ("Evals", "/app/evals", "Skill quality and the eval harness.", "target"),
    ("Cross-run memory", "/app/memory", "What the engineer learned across runs.", "activity"),
    ("Audit log", "/app/audit", "Every recorded action, filterable.", "shield"),
    ("Policy & budget", "/app/governance", "Per-run spend, policy, and bundle export.", "gate"),
    ("Connections", "/app/connections", "External MCP servers and source status.", "link"),
    ("Registry", "/app/registry", "Agent templates, tool contracts, and glossary.", "cpu"),
    ("System", "/app/system", "Environment checks and database readiness.", "activity"),
    ("API docs", "/docs", "The /v1 JSON API (OpenAPI).", "code"),
)


def _cards_html() -> str:
    return "".join(
        f'<a class="card" href="{href}"><div class="card-icon">{icon(glyph)}</div>'
        f'<div class="card-title">{html.escape(title)}</div>'
        f'<div class="card-desc">{html.escape(desc)}</div></a>'
        for title, href, desc, glyph in _CARDS
    )


# (icon, headline, detail) — what the delegated engineer can build. Rendered as a
# feature grid so the capabilities read at a glance, not as a wall of text.
_CAPS: tuple[tuple[str, str, str], ...] = (
    ("terminal", "Six languages", "Python, Java, TypeScript, C#, C, and C++ (plus SQL data-layer)."),
    (
        "branch",
        "New or existing repos",
        "Scaffold a fresh project, or deliver into an existing codebase — following its conventions.",
    ),
    (
        "target",
        "Grounded in your code",
        "It maps your repo first and reuses what's already there, so output reads like your team wrote it.",
    ),
    (
        "shield",
        "Safe by default",
        "Every run stays local (a branch + a diff) until you approve — "
        "nothing is pushed, merged, or filed to Jira.",
    ),
)
_LANGS = ("Python", "Java", "TypeScript", "C#", "C", "C++", "SQL")


def _capabilities_html() -> str:
    features = "".join(
        f'<div class="feature">{icon(glyph)}<div><strong>{html.escape(head)}</strong>'
        f"<span>{html.escape(detail)}</span></div></div>"
        for glyph, head, detail in _CAPS
    )
    chips = "".join(f'<span class="chip lang">{html.escape(lang)}</span>' for lang in _LANGS)
    return (
        '<div class="panel">'
        f'<div class="panel-head">{icon("sparkles")} What Spine can build</div>'
        '<p class="panel-sub">An AI software engineer you hand a ticket to.</p>'
        f'<div class="features">{features}</div>'
        f'<div class="chips">{chips}</div>'
        "</div>"
    )


_HOWTO = (
    '<div class="howto">'
    "<strong>How it works</strong> — this UI drives the same SDLC pipeline as the "
    "<code>orchestrator</code> CLI; each step below maps to a command."
    "<ol>"
    "<li><strong>Delegate</strong> a feature in the <a href='/app/inbox'>Inbox</a> "
    "(paste a source) — like <code>orchestrator sdlc run --source …</code>.</li>"
    "<li>The engineer extracts intents and <strong>pauses at the intent gate</strong> — "
    "you <strong>Approve</strong> it in the Inbox or <a href='/console'>Console</a>.</li>"
    "<li>It implements + tests the code, then <strong>pauses at the merge gate</strong> — "
    "approve to ship.</li>"
    "<li>Watch every step live in the Inbox; open a run's full <strong>Trace</strong> "
    "for the audit timeline.</li>"
    "</ol>"
    "New here? Try <a href='/app/backlog'>Backlog</a> first — a read-only preview of a Confluence "
    "page as a backlog, nothing written."
    "</div>"
)


@router.get("/app", response_class=HTMLResponse)
async def home(_principal: WebPrincipalDep) -> HTMLResponse:
    body = (
        "<h1>Spine</h1>"
        '<p class="lead">Delegate features to the software-engineer persona, review its work, and ship — '
        "the friendly face on the same pipeline the CLI runs.</p>"
        f"{_capabilities_html()}"
        f"{_HOWTO}"
        f'<div class="cards">{_cards_html()}</div>'
    )
    return HTMLResponse(content=page_shell(title="Home", active="Home", body=body))


@router.get("/app/personas", response_class=HTMLResponse)
async def personas_page(_principal: WebPrincipalDep) -> HTMLResponse:
    body = (
        "<h1>Personas &amp; skills</h1>"
        '<p class="lead">A <strong>persona</strong> is the role the engineer takes on (today: '
        "software engineer) — a model, a set of <strong>skills</strong>, and which pipeline step it runs. "
        "A <strong>skill</strong> is reusable guidance it applies (e.g. match the repo's conventions). "
        "This is a read-only view of what a delegated run will use; nothing to configure here yet. "
        "For what it can actually build — languages, new vs existing repos, the safe→live gate — "
        "see <a href='/app'>Home</a>.</p>"
        '<p class="muted">Each skill shows its <strong>status</strong> — '
        '<span class="pill stat-active">active</span> means it\'s wired into the catalog and the '
        'planner can select it; <span class="pill stat-candidate">candidate</span> means it is defined '
        "but inert (pending the persona-skill measurement before it ships) — and the codegen "
        "<strong>phase(s)</strong> it conditions (implement / author_tests / refine).</p>"
        '<div class="cli">orchestrator catalog list   ·   orchestrator catalog plan . --intent "…"</div>'
        '<h2>Personas</h2><div id="personas"><p class="muted">Loading…</p></div>'
        '<h2>Skills</h2><div id="skills"><p class="muted">Loading…</p></div>'
    )
    return HTMLResponse(
        content=page_shell(
            title="Personas",
            active="Personas",
            body=body,
            scripts='<script src="/static/personas.js"></script>',
        )
    )


__all__ = ["router"]
