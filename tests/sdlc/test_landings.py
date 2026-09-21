"""The landing bullet, pinned byte for byte.

`render_landings` was extracted from two callers that had already drifted apart, and one of
them — `evidence` — renders what the **codegen agent reads**, not prose a human skims. So the
property these tests hold is narrow and deliberate: for given facts, the bytes do not move.
A change here is allowed, but it must be a change someone chose and evaluated, not one that
arrived as a side effect of touching the renderer.

The five rows cover every optional clause, because a clause with no test is a clause the next
extraction silently drops: coverage true/false/unknown, the repo prefix, cross-repo reach,
intents past their bound, a weak match with its basis, and a module whose name equals the
symbol's (which must render no "_(in …)_").
"""

from __future__ import annotations

from orchestrator.sdlc.investigate import Landing
from orchestrator.sdlc.landings import render_landing, render_landings

HITS = [
    Landing(
        name="Brief", where="b.py:44", kind="Type", callers=7, module="orchestrator.sdlc.brief", covered=True
    ),
    Landing(
        name="handle_webhook",
        where="h.py:55",
        kind="Function",
        callers=0,
        module="orchestrator.api.hooks",
        covered=False,
        cross_repo=3,
        repo="web",
    ),
    Landing(
        name="Cart",
        where="c.py:14",
        kind="Type",
        callers=12,
        module="app.models",
        covered=True,
        intents=("N-1", "N-2", "N-3", "N-4", "N-5"),
    ),
    Landing(
        name="render",
        where="r.py:9",
        kind="Function",
        callers=3,
        module="app.view",
        weak=True,
        matched=("render", "view"),
    ),
    Landing(name="app.models", where="i.py:1", kind="Module", callers=0, module="app.models"),
]

#: What `investigate` has rendered since before the extraction. Plain location, no backticks.
BRIEF_STYLE = [
    "- `Brief` (Type, 7 caller(s) · reached by tests) _(in orchestrator.sdlc.brief)_ — b.py:44",
    (
        "- **web** · `handle_webhook` (Function, 0 caller(s), **3 dependent(s) in other repos** · "
        "**no test reaches this**) _(in orchestrator.api.hooks)_ — web:h.py:55"
    ),
    (
        "- `Cart` (Type, 12 caller(s) · reached by tests) _(in app.models)_ — c.py:14 — "
        "last changed for N-1, N-2, N-3 +2 more"
    ),
    (
        "- `render` (Function, 3 caller(s)) _(in app.view)_ — r.py:9 — weak: only `render`, `view`, "
        "which other files use too"
    ),
    "- `app.models` (Module, 0 caller(s)) — i.py:1",
]


def test_brief_style_bullets_are_unchanged() -> None:
    assert render_landings(HITS) == BRIEF_STYLE


def test_evidence_style_differs_only_in_the_code_span() -> None:
    """The one real difference between the two callers, stated as one substitution."""
    rendered = render_landings(HITS, location_in_code=True)
    assert rendered == [
        line.replace(" — b.py:44", " — `b.py:44`")
        .replace(" — web:h.py:55", " — `web:h.py:55`")
        .replace(" — c.py:14 ", " — `c.py:14` ")
        .replace(" — r.py:9 ", " — `r.py:9` ")
        .replace(" — i.py:1", " — `i.py:1`")
        for line in BRIEF_STYLE
    ]


def test_a_narrower_row_renders_narrower_without_a_flag() -> None:
    """`evidence`'s `LandingFact` has no repo, coverage, reach or intents.

    It gets the short bullet because those fields are absent, not because it asked for one —
    which is what makes one renderer serve both surfaces honestly.
    """
    bare = Landing(name="Cart", where="c.py:14", kind="Type", callers=12, module="app.models")
    assert render_landing(bare) == "- `Cart` (Type, 12 caller(s)) _(in app.models)_ — c.py:14"


def test_unknown_coverage_is_silence_never_untested() -> None:
    """`covered=None` means the language has no call graph — it is not evidence of no tests."""
    hit = Landing(name="f", where="a.py:1", kind="Function", callers=1, module="m", covered=None)
    assert "no test reaches this" not in render_landing(hit)
    assert "reached by tests" not in render_landing(hit)


def test_a_weak_row_never_claims_coverage() -> None:
    """Coverage on a weak landing was noise that fired on every row; it stays suppressed."""
    hit = Landing(
        name="f",
        where="a.py:1",
        kind="Function",
        callers=1,
        module="m",
        covered=False,
        weak=True,
        matched=("f",),
    )
    out = render_landing(hit)
    assert "no test reaches this" not in out
    assert "weak: only `f`, which other files use too" in out


def test_an_excerpt_is_indented_under_its_own_bullet() -> None:
    """Two entries, not one: the bullet, then the indented block, exactly as `investigate` had it."""
    out = render_landings(HITS[:1], excerpts={0: "```python\nx = 1\n```"})
    assert out == [BRIEF_STYLE[0], "\n  ```python\n  x = 1\n  ```\n"]


def test_a_landing_with_no_location_renders_no_dash() -> None:
    hit = Landing(name="f", where="", kind="Function", callers=0, module="m")
    assert render_landing(hit) == "- `f` (Function, 0 caller(s)) _(in m)_"
    assert render_landing(hit, location_in_code=True) == "- `f` (Function, 0 caller(s)) _(in m)_"
