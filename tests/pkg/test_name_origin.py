"""SSPN-218: a name the repository does not own is not a claim about the repository.

On a bug ticket an unbound claim parks the run. IR17 (the 17 issues P6 parked on a name) found four
parks whose "claim" was a Python builtin (`None`), a standard-library name (`os.getcwd`,
`types.JSONObject`) or a build output (`build/gettext/x.pot`): none of them is code the repository
could hold, so none can be a false premise about it. The criterion strings below are the real stored
ones.

What stays unchanged matters as much: a name that looks like repository code and is missing still
parks, a source-file path that does not exist still parks, and a name the graph holds still binds.
The rule only ever removes an *unresolved* name, so the set of parks can only shrink.
"""

from __future__ import annotations

import pytest

from orchestrator.pkg import FactStore
from orchestrator.pkg import criteria_binding as cb
from orchestrator.pkg.criteria_binding import bind_criteria
from orchestrator.pkg.facts import FactBatch, Node, NodeKind, Provenance
from orchestrator.pkg.repro_material import classify_name

SPHINX_7454 = (
    "That `None` in a type hint links to the documentation for the `None` singleton "
    "regardless of whether 'description' or 'signature' mode is used."
)
PYLINT_7277_A = (
    'I think there should be a check to ensure that the first item is `""`, `"."` or '
    "`os.getcwd()` before removing."
)
PYLINT_7277_B = 'Check if  `""`, `"."` or `os.getcwd()` before removing the first item from sys.path'
SPHINX_10466 = (
    "There should only be ONE instance of `build/gettext/blender_manual.pot` "
    "and there are NO duplications of other locations."
)
SPHINX_8459 = "`types.JSONObject` instead of `Dict[str, Any]` in both cases."

ASTROPY_14096 = (
    "the error message is misleading because it says `prop` doesn't exist, "
    "where it should say `random_attr` doesn't exist"
)
DJANGO_11095 = "So I wished add a hook get_inlines(request, obj=None)"
DJANGO_16454 = (
    "We can correct this by ensuring that the subparser action returned by add_subparsers() "
    "copies the relevant arguments through to constructed subparsers."
)

FLIPS = [
    (SPHINX_7454, "None"),
    (PYLINT_7277_A, "os.getcwd"),
    (PYLINT_7277_B, "os.getcwd"),
    (SPHINX_10466, "build/gettext/blender_manual.pot"),
    (SPHINX_8459, "types.JSONObject"),
]
STAYS = [(ASTROPY_14096, "random_attr"), (DJANGO_11095, "get_inlines"), (DJANGO_16454, "add_subparsers")]


def _store() -> FactStore:
    batch = FactBatch()
    for nid, kind, name, file, line in (
        ("py:report", NodeKind.MODULE, "report.py", "report.py", 1),
        ("py:report.OrderTotals", NodeKind.TYPE, "OrderTotals", "report.py", 30),
        # A repository symbol that shares its name with a builtin: the graph holds it, so it binds.
        ("py:report.format", NodeKind.FUNCTION, "format", "report.py", 40),
    ):
        batch.add_node(
            Node(id=nid, kind=kind, name=name, language="python", provenance=Provenance(file, line))
        )
    return FactStore(batch)


def _one(text: str):  # type: ignore[no-untyped-def]
    return bind_criteria({"acceptance_criteria": [text]}, store=_store()).rows[0]


# ---- the classifier lives below intake, with the same answers --------------------------------


@pytest.mark.parametrize(
    ("name", "origin"),
    [
        ("ValueError", "builtin"),
        ("None", "builtin"),
        ("os.getcwd", "stdlib"),
        ("sys.path", "stdlib"),
        ("types.JSONObject", "stdlib"),
        ("bad_poly", "reporter"),
        ("build/gettext/blender_manual.pot", "path"),
        ("docs/_build/index.html", "path"),
        ("np.random.rand", "unclassified"),
        ("get_inlines", "unclassified"),
        ("add_subparsers", "unclassified"),
        # D3: a source or doc file that does not exist is a false premise, not a build output.
        ("django/db/nope.py", "unclassified"),
        ("lib/matplotlib/tests/test_image.py::test_huge_range_log", "unclassified"),
        ("docs/conf.rst", "unclassified"),
    ],
)
def test_the_origin_of_an_unresolved_name(name: str, origin: str) -> None:
    assert classify_name(name, reporter_defined=frozenset({"bad_poly"})) == origin


# ---- the four IR17 parks that were never claims about the repository -------------------------


@pytest.mark.parametrize(("criterion", "name"), FLIPS)
def test_a_name_that_is_not_the_repositorys_does_not_park_the_run(criterion: str, name: str) -> None:
    row = _one(criterion)
    assert row.status == "no-claim", f"{name!r} still counted as a claim about the repository"
    assert not row.parks
    assert row.claims == ()
    assert "not the repository's" in row.reason


@pytest.mark.parametrize(("criterion", "name"), STAYS)
def test_the_parks_that_are_real_stay_parked(criterion: str, name: str) -> None:
    """astropy-14096, django-11095 and django-16454 name code the graph does not hold."""
    row = _one(criterion)
    assert row.status == "unbound" and row.parks
    assert row.claims == (name,)


# ---- what must still park, and what must still bind ------------------------------------------


def test_a_missing_source_file_is_still_a_false_premise() -> None:
    row = _one("The handler in `django/db/nope.py` should validate its input")
    assert row.status == "unbound" and row.claims == ("django/db/nope.py",)


def test_a_mixed_criterion_parks_on_the_real_claim_only() -> None:
    """`None` is not a claim; `GhostWidget` is. The row parks, and says so about GhostWidget alone."""
    row = _one("`None` is returned when `GhostWidget` is removed")
    assert row.status == "unbound" and row.parks
    assert row.claims == ("GhostWidget",)


def test_a_repository_symbol_named_like_a_builtin_still_binds() -> None:
    """Origin only ever removes an *unresolved* name; one the graph holds keeps its anchor."""
    row = _one("`format` returns a string")
    assert row.status == "bound"
    assert row.anchors[0].symbol == "format"
    assert row.anchors[0].where == "report.py:40"


# ---- the set of parks can only shrink ---------------------------------------------------------


def test_the_rule_can_only_remove_parks(monkeypatch: pytest.MonkeyPatch) -> None:
    """Old behaviour is the classifier answering "unclassified" for everything. Against that, every
    criterion bound before is bound now with the same anchors, and no criterion is unbound now that
    was not unbound before."""
    criteria = [t for t, _ in FLIPS + STAYS] + [
        "`OrderTotals` is recalculated on save",
        "`GhostWidget` is removed",
        "`format` returns a string",
        "`None` is returned when `GhostWidget` is removed",
        "the report should be readable",
    ]
    spec = {"acceptance_criteria": criteria}
    new = bind_criteria(spec, store=_store())
    with monkeypatch.context() as old_rule:
        old_rule.setattr(cb, "classify_name", lambda name, reporter_defined=frozenset(): "unclassified")
        old = bind_criteria(spec, store=_store())
    old_unbound = {r.text for r in old.unbound}
    assert {r.text for r in new.unbound} <= old_unbound
    assert [r for r in new.rows if r.status == "bound"] == [r for r in old.rows if r.status == "bound"]
    assert {r.text for r in old.unbound} - {r.text for r in new.unbound}, "the rule changed nothing"


def test_the_binding_is_the_same_twice() -> None:
    spec = {"acceptance_criteria": [t for t, _ in FLIPS + STAYS]}
    assert bind_criteria(spec, store=_store()).to_dict() == bind_criteria(spec, store=_store()).to_dict()
