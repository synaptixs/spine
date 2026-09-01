"""The gate that re-derives STATE-OF-SPINE's numbers must itself keep working.

A checker that stops finding what it checks is worse than no checker: it reports success while
measuring nothing. Every regex here is matched against a document that people edit, so the
"pattern found nothing" case has to fail loudly rather than pass quietly.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "state-numbers.py"


def _module() -> Any:
    """Load the script as a module.

    Registered in `sys.modules` before exec: the script defines a `@dataclass` under
    `from __future__ import annotations`, and dataclasses resolves those annotations through
    `sys.modules[cls.__module__]`. Without the registration that lookup returns None and the
    class body raises — a failure about the loader, not about anything under test.
    """
    import sys

    spec = importlib.util.spec_from_file_location("state_numbers", _SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["state_numbers"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_every_stated_number_matches_the_source() -> None:
    """The gate itself, run against this tree — the same thing CI runs."""
    assert _module().check() == []


def test_a_pattern_that_stops_matching_is_a_failure_not_a_pass(tmp_path: Path) -> None:
    """The way a check quietly stops checking, refused here.

    Every claim is found by a regex over a document people reword. If a rewrite moved a number
    out from under its pattern and that counted as "nothing to compare", the gate would go green
    for ever while measuring nothing — the exact failure STATE-OF-SPINE §9 catalogues.
    """
    mod = _module()
    doc = tmp_path / "doc.md"
    doc.write_text("nothing resembling the claim here\n", encoding="utf-8")

    claim = mod.Claim(
        label="invented", path=doc, pattern=mod.re.compile(r"\| Nope \| \*\*(\d+)\*\*"), derive=lambda: 1
    )
    original = mod.CLAIMS
    mod.CLAIMS = (claim,)
    try:
        problems = mod.check()
    finally:
        mod.CLAIMS = original

    assert len(problems) == 1
    assert "no longer found" in problems[0]


def test_a_disagreement_is_reported_with_both_numbers(tmp_path: Path) -> None:
    """A gate that says only 'stale' makes the reader go looking for what changed."""
    mod = _module()
    doc = tmp_path / "doc.md"
    doc.write_text("| Widgets | **41** |\n", encoding="utf-8")

    claim = mod.Claim(
        label="widgets",
        path=doc,
        pattern=mod.re.compile(r"\| Widgets \| \*\*([\d,]+)\*\*"),
        derive=lambda: 42,
    )
    original = mod.CLAIMS
    mod.CLAIMS = (claim,)
    try:
        problems = mod.check()
    finally:
        mod.CLAIMS = original

    assert problems == ["widgets: doc.md says 41, the source says 42"]


@pytest.mark.parametrize("label", ["CLI commands", "source modules", "test functions", "version"])
def test_the_headline_claims_are_covered(label: str) -> None:
    """These four went stale during one release cut; none may quietly leave the gate."""
    assert any(c.label == label for c in _module().CLAIMS)
