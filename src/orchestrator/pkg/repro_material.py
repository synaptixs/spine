"""Reproduction material copied into a criterion: whose names are they?

**The defect this closes (B93, SSPN-199).** Intake copies lines of an issue into the stated
acceptance criteria. A bug report has no criteria section, so what gets copied can be the
reporter's *reproduction*: a code line (`def itrs_to_observed_mat(observed_frame):`), an
assignment (`first_filter = {...}`), an error line (`AttributeError: 'custom_coord' object has
no attribute 'prop'`), a SQL schema, a URL. The names in it — `custom_coord`, `first_filter`,
`other_id` — are the reporter's own example, not claims about the repository, yet the binder
treated each as a claim the graph could not hold and, on a bug ticket, the gate parked the run
as `CRITERIA_WRONG`: about 15% of the instances run so far, and the first two of P6.

**Why this is narrow.** The gate is right that a criterion naming code that does not exist is a
false premise ("`OrderTotals` is recalculated" against a repo with no `OrderTotals`). So nothing
here guesses from a criterion's *shape*. A name is the reporter's only when the criterion text
itself shows it: it is **defined** there (an assignment target, a `def`/`class` name or its
parameters), **quoted in an error line** (`'custom_coord' object ...`), part of a **URL**, or
inside a **SQL statement**. A name that is merely *used* (`ghost_helper` in
`result = ghost_helper(x)`) stays a claim, and ownership only ever removes an *unresolved* name:
a repository symbol quoted in an error line still binds.

**One definition.** `is_transcript` is the test the acceptance judge uses (B92, SSPN-197): an
`uncertain` verdict on a copied REPL session, shell session, traceback or error line is not a
blocker. It lives here, below `sdlc/` in the import order, so the gate and the judge cannot
disagree about what reproduction material is.

Pure functions, no I/O: same text in, same answer out.
"""

from __future__ import annotations

import re

__all__ = ["is_transcript", "reporter_owned_names"]

_IDENT = r"[A-Za-z_][A-Za-z0-9_]*"
_IDENT_RE = re.compile(_IDENT)

# A line that opens with a prompt or a header (`>>> `, `In [2]:`, `Out[2]:`, a shell `$ `, a
# traceback), or is itself an error line — `ValueError: ...`, `django.core.exceptions.FieldError: ...`
# — the tail of a traceback. Mid-line text such as "uses the >>> operator" is prose, so the marker
# must open the line, and "raises ValueError when ..." is a requirement, not an error line.
_ERROR_LINE = rf"(?:{_IDENT}\.)*[A-Z][A-Za-z0-9]*(?:Error|Exception|Warning):\s"
_TRANSCRIPT_LINE = re.compile(
    rf"^[ \t]*(?:>>>|In \[\d+\]:|Out\[\d+\]:|\$ |Traceback \(most recent call last\)|{_ERROR_LINE})",
    re.MULTILINE,
)

_ASSIGN = re.compile(rf"^[ \t]*({_IDENT}(?:[ \t]*,[ \t]*{_IDENT})*)[ \t]*(?::[^=\n]+)?=(?!=)", re.MULTILINE)
_DEF = re.compile(rf"\b(?:def|class)[ \t]+({_IDENT})[ \t]*(?:\(([^)\n]*)\))?")
_ERROR_LINE_RE = re.compile(rf"^[ \t]*{_ERROR_LINE}.*$", re.MULTILINE)
_QUOTED = re.compile(r"""['"`]([^'"`\n]+)['"`]""")
_URL = re.compile(r"""https?://[^\s`'"<>)\]]+""")
_SQL = re.compile(r"^[ \t]*(?:CREATE|ALTER|INSERT|DROP|UPDATE|DELETE|SELECT)\b", re.IGNORECASE | re.MULTILINE)


def is_transcript(criterion: str) -> bool:
    """Is this stated criterion a copied REPL session, shell session, traceback or error line?"""
    return bool(_TRANSCRIPT_LINE.search(criterion))


def reporter_owned_names(text: str) -> frozenset[str]:
    """Names the criterion's own text shows to belong to the reporter's example.

    Defined in it (an assignment target, a `def`/`class` name and its parameters), quoted in one
    of its error lines, part of a URL it contains, or inside a SQL statement it contains. A name
    that is only used is not here.
    """
    owned: set[str] = set()
    for match in _ASSIGN.finditer(text):
        owned.update(_IDENT_RE.findall(match.group(1)))
    for match in _DEF.finditer(text):
        owned.add(match.group(1))
        for parameter in (match.group(2) or "").split(","):
            first = _IDENT_RE.search(parameter)
            if first:
                owned.add(first.group(0))
    for line in _ERROR_LINE_RE.finditer(text):
        for quoted in _QUOTED.findall(line.group(0)):
            owned.update(_IDENT_RE.findall(quoted))
    for url in _URL.findall(text):
        owned.add(url)
        owned.update(_IDENT_RE.findall(url))
    sql = _SQL.search(text)
    if sql:
        owned.update(_IDENT_RE.findall(text[sql.start() :]))
    return frozenset(owned)
