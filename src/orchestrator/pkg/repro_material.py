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

**A criterion that is only code or output (B94, SSPN-202).** P6 parked 17 of 100 issues on a criterion
naming a symbol the graph does not hold; in six the "criterion" was a line of the reporter's code, an
interpreter session or a traceback. Intake is told to copy stated criteria VERBATIM, a bug report states
none, and the model copies what looks like a requirement. A line of code is an example, never a
requirement, so `is_reproduction` says when a whole criterion is nothing but code, output or comments.
It is deliberately conservative: *every* non-blank line must be code-like, so a sentence beside a code
line keeps the criterion a requirement, and a prose claim in it still parks name by name.

Pure functions, no I/O: same text in, same answer out.
"""

from __future__ import annotations

import ast
import re

__all__ = ["is_reproduction", "is_transcript", "reporter_defined_names", "reporter_owned_names"]

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

# An assignment may sit behind an interpreter prompt (`>>> coeff, bad_poly = f()`).
_PROMPT_PREFIX = r"(?:(?:>>>|\.\.\.|In \[\d+\]:)[ \t]*)?"
_ASSIGN = re.compile(
    rf"^[ \t]*{_PROMPT_PREFIX}({_IDENT}(?:[ \t]*,[ \t]*{_IDENT})*)[ \t]*(?::[^=\n]+)?=(?!=)", re.MULTILINE
)
_DEF = re.compile(rf"\b(?:def|class)[ \t]+({_IDENT})[ \t]*(?:\(([^)\n]*)\))?")
_ERROR_LINE_RE = re.compile(rf"^[ \t]*{_ERROR_LINE}.*$", re.MULTILINE)
_QUOTED = re.compile(r"""['"`]([^'"`\n]+)['"`]""")
_URL = re.compile(r"""https?://[^\s`'"<>)\]]+""")
_SQL = re.compile(r"^[ \t]*(?:CREATE|ALTER|INSERT|DROP|UPDATE|DELETE|SELECT)\b", re.IGNORECASE | re.MULTILINE)


def is_transcript(criterion: str) -> bool:
    """Is this stated criterion a copied REPL session, shell session, traceback or error line?"""
    return bool(_TRANSCRIPT_LINE.search(criterion))


def reporter_defined_names(text: str) -> frozenset[str]:
    """Names ``text`` *defines*: assignment targets, `def`/`class` names and their parameters.

    The narrow half of :func:`reporter_owned_names`, for text that is not one criterion. Run over a
    whole ticket, quotes in error lines, URLs and SQL would also mark a standard-library name that
    the ticket merely links to (`.../constants.html#None`) as the reporter's, so only a definition
    counts there (B94).
    """
    defined: set[str] = set()
    for match in _ASSIGN.finditer(text):
        defined.update(_IDENT_RE.findall(match.group(1)))
    for match in _DEF.finditer(text):
        defined.add(match.group(1))
        for parameter in (match.group(2) or "").split(","):
            first = _IDENT_RE.search(parameter)
            if first:
                defined.add(first.group(0))
    return frozenset(defined)


def reporter_owned_names(text: str) -> frozenset[str]:
    """Names the criterion's own text shows to belong to the reporter's example.

    Defined in it (an assignment target, a `def`/`class` name and its parameters), quoted in one
    of its error lines, part of a URL it contains, or inside a SQL statement it contains. A name
    that is only used is not here.
    """
    owned: set[str] = set(reporter_defined_names(text))
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


_FENCE = re.compile(r"^[ \t]*```")
_PROMPT = re.compile(r"^[ \t]*(?:>>>|\.\.\.|In \[\d+\]:|Out\[\d+\]:|\$ )")
# pytest's own failure output: `E   BytesWarning: ...` and `>   statement`.
_PYTEST_OUTPUT = re.compile(r"^(?:E|>)[ \t]{2,}\S")
# Node types that make a parsed line *code* rather than a terse sentence such as `result is correct`.
_CODE_NODES = (
    ast.Call,
    ast.Assign,
    ast.AugAssign,
    ast.AnnAssign,
    ast.Subscript,
    ast.Attribute,
    ast.FunctionDef,
    ast.AsyncFunctionDef,
    ast.ClassDef,
    ast.Import,
    ast.ImportFrom,
    ast.If,
    ast.For,
    ast.While,
    ast.With,
    ast.Try,
    ast.Return,
    ast.Dict,
    ast.List,
    ast.Set,
    ast.Tuple,
    ast.Lambda,
    ast.Delete,
)


def _parses_as_code(source: str) -> bool:
    """Does ``source`` parse as Python *and* contain more than a bare name, constant or comparison?"""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return False
    body = tree.body
    if len(body) == 1 and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Attribute):
        # A lone dotted name (`os.getcwd`) is a mention of something, not a statement.
        return not all(isinstance(n, ast.Attribute | ast.Name | ast.Load) for n in ast.walk(body[0].value))
    return any(isinstance(node, _CODE_NODES) for node in ast.walk(tree))


def _is_code_line(line: str, *, session: bool) -> bool:
    stripped = line.strip()
    if not stripped:
        return True
    if stripped.startswith("#") or _FENCE.match(line) or _PROMPT.match(line) or _PYTEST_OUTPUT.match(line):
        return True
    if _TRANSCRIPT_LINE.match(line):
        return True
    if _parses_as_code(stripped):
        return True
    if stripped.endswith(":") or re.search(r":[ \t]*#", stripped):
        # `if value in allowed: # note` — a block header with its comment; a dummy body makes it parse.
        header = re.sub(r"[ \t]*#.*$", "", stripped).rstrip()
        if header.endswith(":") and _parses_as_code(header + "\n pass"):
            return True
    # Inside a session or a fence the lines between prompts are output: values, reprs, words.
    # Three plain words in a row is a sentence, not output: `which should be` ends the session.
    words = [w for w in stripped.split() if re.fullmatch(r"[A-Za-z]+[,.]?", w)]
    return session and len(words) <= 2


def is_reproduction(criterion: str) -> bool:
    """Is this stated criterion *only* code, interpreter output or comments copied from the report?

    Every non-blank line must be code-like, so one sentence of prose makes it a requirement.
    """
    text = criterion.strip()
    if not text:
        return False
    if _parses_as_code(text):
        return True
    lines = text.splitlines()
    session = any(_PROMPT.match(line) or _FENCE.match(line) for line in lines)
    inside = False
    saw_code = False
    for line in lines:
        if _FENCE.match(line):
            inside = not inside
            saw_code = True
            continue
        if inside:
            saw_code = saw_code or bool(line.strip())
            continue
        if not _is_code_line(line, session=session):
            return False
        saw_code = saw_code or bool(line.strip())
    return saw_code
