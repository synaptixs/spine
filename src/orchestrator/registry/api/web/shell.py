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

# (label, href, icon) — the surfaces in the shared left sidebar. "Docs" points at
# the written guide (the raw OpenAPI schema stays reachable via the Home card).
NAV: tuple[tuple[str, str, str], ...] = (
    ("Home", "/app", "home"),
    ("Inbox", "/app/inbox", "inbox"),
    ("Console", "/console", "table"),
    ("Backlog", "/app/backlog", "list"),
    ("Personas", "/app/personas", "users"),
    ("Docs", "https://github.com/synaptixs/spine/blob/main/USER_GUIDE.md", "book"),
)


def page_shell(*, title: str, active: str, body: str, head: str = "", scripts: str = "") -> str:
    """Wrap a page ``body`` in the shared left-sidebar app shell + stylesheet.

    ``active`` is the NAV label to highlight (or "" for pages outside the nav,
    like a single trace). ``body`` is trusted HTML the caller already escaped.
    ``head`` injects page-specific tags before ``</head>`` (e.g. a page stylesheet);
    ``scripts`` injects tags after ``<main>`` (e.g. a deferred page script) so the
    DOM exists when it runs. Both default to "" → the base pages are unchanged.
    """
    links = "".join(
        f'<a href="{href}" class="navlink{" active" if label == active else ""}">'
        f"{icon(glyph)}<span>{html.escape(label)}</span></a>"
        for label, href, glyph in NAV
    )
    return (
        '<!doctype html><html lang="en"><head>'
        '<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{html.escape(title)} · Spine</title>"
        '<link rel="stylesheet" href="/static/app.css">'
        f"{head}"
        '</head><body><div class="app">'
        f'<aside class="sidebar"><a class="brand" href="/app">{brand_mark()}Spine</a>'
        f'<nav class="nav">{links}</nav>'
        f'<a href="/logout" class="navlink signout">{icon("logout")}<span>Sign out</span></a></aside>'
        f'<main class="content">{body}</main>'
        "</div>"
        f"{scripts}"
        "</body></html>"
    )


__all__ = ["NAV", "page_shell"]
