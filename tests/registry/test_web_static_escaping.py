"""Regression guard for the web/static HTML escaper (security review Phase 2, JS surface).

Confirmed finding: every `esc()` helper escaped only & < > (the textContent→innerHTML
trick, or a /[&<>]/ regex). That is safe for element *content* but not inside a quoted
HTML attribute — `data-path="${esc(x)}"` or `onclick="decide('${esc(id)}')"` — where a
" or ' in an untrusted value (e.g. a cloned-repo file name) breaks out of the attribute
and injects markup/handlers. The fix escapes " and ' as well.

There is no JS test runner in this repo (vanilla JS, no npm — see the shell.py preamble),
so this asserts the invariant at the source level: every escaper must escape both quotes.
It fails if anyone reintroduces a &<>-only escaper.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_STATIC = Path(__file__).resolve().parents[2] / "src" / "orchestrator" / "registry" / "api" / "web" / "static"
# A real definition assigns esc to an escaping expression (`.replace(...)`); this
# skips comments and call sites that merely mention esc.
_ESC_DEF = re.compile(r"(?:const|let|var|function)\s+esc\b.*\.replace\(")


def _esc_definition_lines() -> list[tuple[str, int, str]]:
    out: list[tuple[str, int, str]] = []
    for js in sorted(_STATIC.rglob("*.js")):
        for i, line in enumerate(js.read_text(encoding="utf-8").splitlines(), 1):
            if _ESC_DEF.search(line):
                out.append((js.name, i, line))
    return out


def test_at_least_one_escaper_exists() -> None:
    # guards against the regex silently matching nothing (e.g. if files move)
    assert _esc_definition_lines(), "no esc() definitions found under web/static"


@pytest.mark.parametrize(
    "name,lineno,line", _esc_definition_lines(), ids=lambda v: v if isinstance(v, str) else ""
)
def test_every_esc_escapes_both_quotes(name: str, lineno: int, line: str) -> None:
    assert "&quot;" in line, f"{name}:{lineno} esc() does not escape double quotes (attribute breakout)"
    assert "&#39;" in line, f"{name}:{lineno} esc() does not escape single quotes (attribute breakout)"
