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


# ---- gated vs trended (the walkthrough figures) ------------------------------


def test_the_binding_figures_are_trended_not_gated() -> None:
    """They move on any commit touching a doc or a symbol, which is nearly every one.

    Gating them would fail a pull request for refreshing seven numbers in a walkthrough — the
    failure that un-gated the doc-drift ratchet. They are still derived, which is what catches
    a figure wrong *when written* as opposed to one that has merely aged.
    """
    mod = _module()
    binding = [c for c in mod.CLAIMS if c.label.startswith("binding:")]
    assert binding, "the walkthrough figures must be derived at all"
    assert all(not c.gated for c in binding)


def test_the_headline_claims_stay_gated() -> None:
    """Trending is for figures that move with ordinary work — not an escape hatch."""
    mod = _module()
    gated = {c.label for c in mod.CLAIMS if c.gated}
    assert {"CLI commands", "source modules", "test functions", "version"} <= gated


def test_an_ungated_mismatch_does_not_fail_the_build(tmp_path: Path) -> None:
    from typing import Any as _Any

    mod = _module()
    doc = tmp_path / "doc.md"
    doc.write_text("| Widgets | 41 |\n", encoding="utf-8")

    stale: _Any = mod.Claim(
        label="trended",
        path=doc,
        pattern=mod.re.compile(r"\| Widgets \| (\d+) \|"),
        derive=lambda: 42,
        gated=False,
    )
    original = mod.CLAIMS
    mod.CLAIMS = (stale,)
    try:
        assert mod.check() == []  # the build's verdict ignores it
        assert mod.check(gated_only=False) != []  # and it is still visible
    finally:
        mod.CLAIMS = original


def test_the_binding_buckets_partition_the_mentions() -> None:
    """A structural invariant, not a snapshot — it holds at every commit.

    The four buckets are disjoint and exhaustive by construction, so a refactor that
    double-counts or drops a mention breaks the sum. This is the check that survives the
    figures themselves being trended rather than gated.
    """
    b = _module()._binding()
    assert b["one_symbol"] + b["many_symbols"] + b["file_only"] + b["nothing"] == b["mentions"]


def test_edges_never_exceed_single_anchor_mentions() -> None:
    """`link_docs` draws an edge only for a mention with exactly one *symbol* anchor.

    The gap is de-duplication. Edges exceeding that count would mean an edge drawn from an
    ambiguous or file-only mention — the conflation that put a wrong table in this repository
    for a day.
    """
    b = _module()._binding()
    assert b["edges"] <= b["one_symbol"]
