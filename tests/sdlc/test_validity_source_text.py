"""B101 (SSPN-236): a real name the graph holds no node for does not park a bug ticket.

The graph holds modules, classes and functions — not a function's parameters, and not a name the
code registers as a string. Two R2 bug tickets were parked as "the criterion names X, which the
graph does not hold" although X was right there in the repository: a parameter, and a config
option registered by a string literal. D13: before the park, look the name up in the PKG's own
lexical index (the symbol text hybrid search ranks on). A whole-word hit is a WARN, "in the source
text, not a graph node"; a name the index does not hold either still parks.

Synthetic repository, invented names.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from orchestrator.pkg import FactStore, load_or_extract
from orchestrator.pkg.criteria_binding import CriteriaBinding, CriterionBinding
from orchestrator.sdlc.validity import Verdict, assess

NUMFMT = '''\
"""Number formatting for the shop."""


def render_amount(number, separator, places_kept=None, grouping=0):
    """Format ``number``; ``places_kept`` caps the decimal places shown."""
    text = str(number)
    if places_kept is not None:
        text = text[: text.find(".") + places_kept + 1]
    return text.replace(".", separator)
'''

SETTINGS = '''\
"""Where the shop registers its options."""


def setup(app):
    app.add_option("shop_strict_rounding", True, "env")
    app.add_option("shop_currency_symbol", "$", "env")
    return {"version": "1"}
'''


@pytest.fixture
def repo(tmp_path: Path) -> tuple[Path, FactStore]:
    pkg = tmp_path / "shop"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "numfmt.py").write_text(NUMFMT, encoding="utf-8")
    (pkg / "settings.py").write_text(SETTINGS, encoding="utf-8")
    return tmp_path, FactStore(load_or_extract(tmp_path))


def _unbound(text: str, *claims: str) -> CriteriaBinding:
    return CriteriaBinding(rows=(CriterionBinding(text=text, status="unbound", claims=claims),))


def _assess(repo: tuple[Path, FactStore], binding: CriteriaBinding, *, root: bool = True) -> object:
    path, store = repo
    return assess(
        {"title": "t", "summary": "s", "acceptance_criteria": [r.text for r in binding.rows]},
        store=store,
        issue_type="Bug",
        landing=["shop/numfmt.py"],
        root=path if root else None,
        criteria=binding,
    )


def test_a_parameter_name_warns_instead_of_parking(repo: tuple[Path, FactStore]) -> None:
    """The decimal-places shape: the name is a parameter of a function the graph does hold."""
    text = "when `places_kept` is supplied and the number is tiny, the result is 0.000"
    assessment = _assess(repo, _unbound(text, "places_kept"))

    assert assessment.verdict is Verdict.PROCEED  # type: ignore[attr-defined]
    (finding,) = [f for f in assessment.findings if f.check == "criterion-in-source-text"]  # type: ignore[attr-defined]
    assert "places_kept" in finding.detail
    assert "in the source text, not a graph node" in finding.detail
    assert "shop/numfmt.py" in finding.evidence  # where it was found, so a reader can check
    assert not [f for f in assessment.findings if f.check == "criterion-unbound"]  # type: ignore[attr-defined]


def test_a_string_registered_option_warns_instead_of_parking(repo: tuple[Path, FactStore]) -> None:
    """The config-option shape: registered by a string literal, read by attribute elsewhere."""
    assessment = _assess(repo, _unbound("`shop_strict_rounding` picks up every rule", "shop_strict_rounding"))

    assert assessment.verdict is Verdict.PROCEED  # type: ignore[attr-defined]
    assert any(f.check == "criterion-in-source-text" for f in assessment.findings)  # type: ignore[attr-defined]


def test_a_truly_absent_name_still_parks(repo: tuple[Path, FactStore]) -> None:
    """The reason the check exists is unchanged: a name nobody can find is a false premise."""
    assessment = _assess(repo, _unbound("`vanished_option` no longer double-counts", "vanished_option"))

    assert assessment.verdict is Verdict.CRITERIA_WRONG  # type: ignore[attr-defined]
    assert assessment.findings[0].check == "criterion-unbound"  # type: ignore[attr-defined]


def test_a_substring_is_not_a_whole_word_hit(repo: tuple[Path, FactStore]) -> None:
    """`strict_rounding` sits inside `shop_strict_rounding`; that is not the name claimed."""
    assessment = _assess(repo, _unbound("`strict_rounding` is honoured", "strict_rounding"))

    assert assessment.verdict is Verdict.CRITERIA_WRONG  # type: ignore[attr-defined]


def test_a_row_with_one_found_and_one_absent_name_parks_on_the_absent_one(
    repo: tuple[Path, FactStore],
) -> None:
    text = "`places_kept` and `vanished_option` agree"
    assessment = _assess(repo, _unbound(text, "places_kept", "vanished_option"))

    assert assessment.verdict is Verdict.CRITERIA_WRONG  # type: ignore[attr-defined]
    (park,) = assessment.findings  # type: ignore[attr-defined]
    assert "vanished_option" in park.detail and "places_kept" not in park.detail


def test_without_a_root_the_gate_cannot_look_and_parks_as_before(repo: tuple[Path, FactStore]) -> None:
    """A caller that gives no checkout (the agent corpus) has no text to look in: unchanged."""
    assessment = _assess(repo, _unbound("`places_kept` is honoured", "places_kept"), root=False)

    assert assessment.verdict is Verdict.CRITERIA_WRONG  # type: ignore[attr-defined]


def test_an_enhancement_is_unchanged(repo: tuple[Path, FactStore]) -> None:
    """An enhancement never parked on this; its finding keeps its wording."""
    path, store = repo
    binding = _unbound("`rule_compiler` returns 70 rules", "rule_compiler")
    assessment = assess(
        {"title": "t", "summary": "s", "acceptance_criteria": ["`rule_compiler` returns 70 rules"]},
        store=store,
        issue_type="Story",
        root=path,
        criteria=binding,
    )

    assert assessment.verdict is Verdict.PROCEED
    (finding,) = [f for f in assessment.findings if f.check == "criterion-unbound"]
    assert "expected for something not built yet" in finding.detail


def test_end_to_end_through_the_real_binder(repo: tuple[Path, FactStore]) -> None:
    """No hand-built binding: `bind_criteria` reads the two names as unbound claims, and the gate
    then finds them in the source text and the absent one nowhere."""
    from orchestrator.pkg.criteria_binding import bind_criteria

    path, store = repo
    present = [
        "when `places_kept` is supplied and the number is tiny, the result is 0.000",
        "`shop_strict_rounding` can only pick up the first rule",
    ]
    spec = {"title": "t", "summary": "s", "acceptance_criteria": present}
    binding = bind_criteria(spec, store=store, root=path)
    assert [r.status for r in binding.rows] == ["unbound", "unbound"]

    kw = {"store": store, "issue_type": "Bug", "landing": ["shop/numfmt.py"], "root": path}
    assert assess(spec, criteria=binding, **kw).verdict is Verdict.PROCEED  # type: ignore[arg-type]

    absent = {**spec, "acceptance_criteria": ["`vanished_option` no longer double-counts"]}
    parked = assess(absent, criteria=bind_criteria(absent, store=store, root=path), **kw)  # type: ignore[arg-type]
    assert parked.verdict is Verdict.CRITERIA_WRONG
