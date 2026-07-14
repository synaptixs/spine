"""Inline SVG icons for the web UI — dependency-light (no icon-font CDN).

Feather-style 24×24 stroke glyphs that inherit ``currentColor`` and size via the
``.icon`` class, plus a filled ``brand`` mark (a stylized spine). Kept out of the
page string-soup so surfaces can share them: ``icon("inbox")``.
"""

from __future__ import annotations

# name → inner SVG (paths inherit stroke=currentColor from the wrapper).
_GLYPHS: dict[str, str] = {
    "inbox": (
        "<path d='M22 12h-6l-2 3h-4l-2-3H2'/>"
        "<path d='M5.45 5.11 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89"
        "A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z'/>"
    ),
    "table": "<rect x='3' y='3' width='18' height='18' rx='2'/><path d='M3 9h18M3 15h18M9 3v18M15 3v18'/>",
    "list": "<path d='M8 6h13M8 12h13M8 18h13'/><path d='M3 6h.01M3 12h.01M3 18h.01'/>",
    "users": (
        "<path d='M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2'/><circle cx='9' cy='7' r='4'/>"
        "<path d='M23 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75'/>"
    ),
    "code": "<path d='M16 18l6-6-6-6M8 6l-6 6 6 6'/>",
    "terminal": "<path d='M4 17l6-6-6-6M12 19h8'/>",
    "branch": (
        "<circle cx='6' cy='6' r='3'/><circle cx='6' cy='18' r='3'/><path d='M6 9v6'/>"
        "<circle cx='18' cy='6' r='3'/><path d='M18 9a9 9 0 0 1-9 9'/>"
    ),
    "target": "<circle cx='12' cy='12' r='9'/><circle cx='12' cy='12' r='5'/><circle cx='12' cy='12' r='1'/>",
    "shield": "<path d='M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z'/><path d='M9 12l2 2 4-4'/>",
    "gate": "<path d='M22 11.08V12a10 10 0 1 1-5.93-9.14'/><path d='M22 4 12 14.01l-3-3'/>",
    "activity": "<path d='M22 12h-4l-3 9L9 3l-3 9H2'/>",
    "sparkles": (
        "<path d='M12 3l1.9 4.8L18.7 9l-4.8 1.9L12 15.7l-1.9-4.8L5.3 9l4.8-1.2z'/>"
        "<path d='M19 15l.7 1.8L21.5 17.5l-1.8.7L19 20l-.7-1.8L16.5 17.5l1.8-.7z'/>"
    ),
    "file": (
        "<path d='M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z'/>"
        "<path d='M14 2v6h6M9 13h6M9 17h4'/>"
    ),
    "search": "<circle cx='11' cy='11' r='7'/><path d='M21 21l-4.3-4.3'/>",
    "cpu": (
        "<rect x='5' y='5' width='14' height='14' rx='2'/><rect x='9' y='9' width='6' height='6'/>"
        "<path d='M9 2v3M15 2v3M9 19v3M15 19v3M2 9h3M2 15h3M19 9h3M19 15h3'/>"
    ),
    "gitpr": (
        "<circle cx='6' cy='6' r='3'/><circle cx='6' cy='18' r='3'/><path d='M6 9v6'/>"
        "<circle cx='18' cy='18' r='3'/><path d='M18 15V9a3 3 0 0 0-3-3h-4'/><path d='M13 9l-2.5-2.5L13 4'/>"
    ),
    "home": "<path d='M3 12l9-9 9 9M5 10v10a1 1 0 0 0 1 1h4v-6h4v6h4a1 1 0 0 0 1-1V10'/>",
    "book": (
        "<path d='M4 19.5A2.5 2.5 0 0 1 6.5 17H20'/>"
        "<path d='M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z'/>"
    ),
    "logout": "<path d='M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4'/><path d='M16 17l5-5-5-5M21 12H9'/>",
    "link": (
        "<path d='M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71'/>"
        "<path d='M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71'/>"
    ),
}


def icon(name: str, cls: str = "icon") -> str:
    """An inline SVG for ``name`` (empty string if unknown), sized/colored via CSS."""
    inner = _GLYPHS.get(name, "")
    return (
        f'<svg class="{cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
        f"{inner}</svg>"
    )


def brand_mark(cls: str = "brand-mark") -> str:
    """The filled 'spine' mark for the top-bar brand — stacked vertebrae."""
    return (
        f'<svg class="{cls}" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">'
        "<rect x='8' y='2.5' width='8' height='3.4' rx='1.7'/>"
        "<rect x='6.5' y='7.3' width='11' height='3.4' rx='1.7'/>"
        "<rect x='7' y='12.1' width='10' height='3.4' rx='1.7'/>"
        "<rect x='8' y='16.9' width='8' height='3.4' rx='1.7'/>"
        "</svg>"
    )


__all__ = ["brand_mark", "icon"]
