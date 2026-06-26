"""Shared HTML shell for the registry's web surfaces (unified UI — P0).

One nav and one stylesheet for every operator page, replacing what were three
disconnected inline-HTML blobs (`console`, `trace`, the separate intake app). The
shell is a plain function — no template-engine dependency, matching the project's
dependency-light style — and the CSS/JS live as **real files** under
``web/static`` (served at ``/static``), the fix for "UI living as Python strings".
"""

from __future__ import annotations

import html

# (label, href) — the surfaces in the shared top nav. Grows as P0 folds in more.
NAV: tuple[tuple[str, str], ...] = (
    ("Home", "/app"),
    ("Inbox", "/app/inbox"),
    ("Console", "/console"),
    ("Backlog", "/app/backlog"),
    ("Personas", "/app/personas"),
    ("Docs", "/docs"),
)


def page_shell(*, title: str, active: str, body: str, head: str = "", scripts: str = "") -> str:
    """Wrap a page ``body`` in the shared nav chrome + stylesheet.

    ``active`` is the NAV label to highlight (or "" for pages outside the nav,
    like a single trace). ``body`` is trusted HTML the caller already escaped.
    ``head`` injects page-specific tags before ``</head>`` (e.g. a page stylesheet);
    ``scripts`` injects tags after ``<main>`` (e.g. a deferred page script) so the
    DOM exists when it runs. Both default to "" → the base pages are unchanged.
    """
    links = "".join(
        f'<a href="{href}" class="navlink{" active" if label == active else ""}">{html.escape(label)}</a>'
        for label, href in NAV
    )
    return (
        '<!doctype html><html lang="en"><head>'
        '<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{html.escape(title)} · Orchestrator</title>"
        '<link rel="stylesheet" href="/static/app.css">'
        f"{head}"
        "</head><body>"
        '<header class="topbar"><a class="brand" href="/app">Orchestrator</a>'
        f'<nav class="nav">{links}</nav>'
        '<a href="/logout" class="navlink signout">Sign out</a></header>'
        f'<main class="wrap">{body}</main>'
        f"{scripts}"
        "</body></html>"
    )


__all__ = ["NAV", "page_shell"]
