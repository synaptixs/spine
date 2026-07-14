"""Shared HTML shell for the registry's web surfaces (unified UI — P0).

One nav and one stylesheet for every operator page, replacing what were three
disconnected inline-HTML blobs (`console`, `trace`, the separate intake app). The
shell is a plain function — no template-engine dependency, matching the project's
dependency-light style — and the CSS/JS live as **real files** under
``web/static`` (served at ``/static``), the fix for "UI living as Python strings".
"""

from __future__ import annotations

import html

from orchestrator.registry.api.web.icons import brand_mark, icon

# The sidebar, grouped into sections (Phase 0). Each section is
# ``(heading, ((label, href, icon), ...))``; a page is highlighted by matching
# ``active`` to its label. Sections give the nav room to grow as the
# capability-breadth roadmap adds Understand / Govern / Connect pages — those
# land as new sections here, no shell change. "Docs" points at the written guide
# (the raw OpenAPI schema stays reachable via the Home card).
NavItem = tuple[str, str, str]
NavSection = tuple[str, tuple[NavItem, ...]]
NAV_SECTIONS: tuple[NavSection, ...] = (
    (
        "Deliver",
        (
            ("Home", "/app", "home"),
            ("Inbox", "/app/inbox", "inbox"),
            ("Intake studio", "/app/intake", "sparkles"),
            ("Backlog", "/app/backlog", "list"),
            ("Console", "/console", "table"),
        ),
    ),
    (
        "Understand",
        (
            ("Understand", "/app/understand", "search"),
            ("Current State", "/app/state", "file"),
            ("Memory bank", "/app/memory-bank", "book"),
            ("Knowledge graph", "/app/graph", "branch"),
            ("Catalog", "/app/catalog", "sparkles"),
        ),
    ),
    (
        "Govern",
        (
            ("Audit log", "/app/audit", "shield"),
            ("Policy & budget", "/app/governance", "gate"),
        ),
    ),
    (
        "Quality",
        (
            ("Evals", "/app/evals", "target"),
            ("Cross-run memory", "/app/memory", "activity"),
            ("Advanced", "/app/advanced", "cpu"),
        ),
    ),
    (
        "Connect",
        (("Connections", "/app/connections", "link"),),
    ),
    (
        "Registry",
        (
            ("Registry", "/app/registry", "cpu"),
            ("Personas", "/app/personas", "users"),
        ),
    ),
    (
        "System",
        (("System", "/app/system", "activity"),),
    ),
    (
        "Help",
        (("Docs", "https://github.com/synaptixs/spine/blob/main/USER_GUIDE.md", "book"),),
    ),
)
# Flat view of every nav item, kept for back-compat with importers of ``NAV``.
NAV: tuple[NavItem, ...] = tuple(item for _, items in NAV_SECTIONS for item in items)


def page_shell(*, title: str, active: str, body: str, head: str = "", scripts: str = "") -> str:
    """Wrap a page ``body`` in the shared left-sidebar app shell + stylesheet.

    ``active`` is the NAV label to highlight (or "" for pages outside the nav,
    like a single trace). ``body`` is trusted HTML the caller already escaped.
    ``head`` injects page-specific tags before ``</head>`` (e.g. a page stylesheet);
    ``scripts`` injects tags after ``<main>`` (e.g. a deferred page script) so the
    DOM exists when it runs. Both default to "" → the base pages are unchanged.
    """

    def _links(items: tuple[NavItem, ...]) -> str:
        return "".join(
            f'<a href="{href}" class="navlink{" active" if label == active else ""}">'
            f"{icon(glyph)}<span>{html.escape(label)}</span></a>"
            for label, href, glyph in items
        )

    nav = "".join(
        f'<div class="nav-section"><p class="nav-heading">{html.escape(heading)}</p>{_links(items)}</div>'
        for heading, items in NAV_SECTIONS
    )
    return (
        '<!doctype html><html lang="en"><head>'
        '<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{html.escape(title)} · Spine</title>"
        '<link rel="stylesheet" href="/static/app.css">'
        f"{head}"
        '</head><body><div class="app">'
        f'<aside class="sidebar"><a class="brand" href="/app">{brand_mark()}Spine</a>'
        f'<nav class="nav">{nav}</nav>'
        f'<a href="/logout" class="navlink signout">{icon("logout")}<span>Sign out</span></a></aside>'
        f'<main class="content">{body}</main>'
        "</div>"
        f"{scripts}"
        "</body></html>"
    )


__all__ = ["NAV", "NAV_SECTIONS", "page_shell"]
