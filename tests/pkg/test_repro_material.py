"""B93 (SSPN-199): a name from the reporter's example is not a claim about the repository.

On a bug ticket an unbound claim parks the run (`CRITERIA_WRONG`). SWE-bench-style reports have no
acceptance-criteria section, so intake copies reproduction material into the stated criteria — a
code line, an assignment, an error message, a URL — and the names in it belong to the reporter's
example. The strings below are the real stored criteria that parked runs (P6's start, three pilots).

What stays unchanged matters as much: a criterion that makes a claim about the repository and gets
it wrong must still park, and so must a stray claim sitting next to reproduction material.
"""

from __future__ import annotations

import pytest

from orchestrator.pkg import FactStore
from orchestrator.pkg.criteria_binding import bind_criteria
from orchestrator.pkg.facts import FactBatch, Node, NodeKind, Provenance
from orchestrator.pkg.repro_material import is_transcript, reporter_owned_names

# The real criteria, verbatim from the stored intake caches of the runs that parked.
ASTROPY_14096 = "AttributeError: 'custom_coord' object has no attribute 'prop'"
ASTROPY_13398 = "def itrs_to_observed_mat(observed_frame):"
DJANGO_13346_A = (
    "first_filter = {‘our_field__key__in': [0]}\n"
    "first_items = OurModel.objects.filter(**first_filter)\n"
    "len(first_items)\n"
    "0"
)
DJANGO_13346_B = (
    "second_filter = {'our_field__key': 0}\n"
    "second_items = OurModel.objects.filter(**second_filter)\n"
    "len(second_items )\n"
    "312"
)
DJANGO_15037 = (
    "if you have a DB like that\n"
    "CREATE TABLE foo ( id serial primary key, other_id int UNIQUE);\n"
    "CREATE TABLE bar (\n"
    "\tid serial primary key, other_id int,\n"
    "\tconstraint myconst \n"
    "\tFOREIGN KEY(other_id) references foo(other_id)\n"
    ");\n"
    "the generated model for the bar table will have the other_id be a FK to foo and not foo(other_id)."
)
SPHINX_A = "Navigate to `http://localhost:63342/sphix_svg_bug/docs_build/index.html` see that the links work."
SPHINX_B = (
    "Navigate to `http://localhost:63342/sphix_svg_bug/docs_build/my_package/index.html` "
    "see that the links do not work."
)
SPHINX_C = (
    "Here are the correct links to the files\n```\n"
    "http://localhost:63342/sphix_svg_bug/docs_build/my_package/my_class_1.html\n"
    "http://localhost:63342/sphix_svg_bug/docs_build/my_package/my_class_2.html\n```"
)
# Not reporter material: a claim about the repository (a test id) that is unbound because the fix
# adds that test. A different cause, left parking on purpose (plan decision D4).
MATPLOTLIB_20488 = (
    "`lib/matplotlib/tests/test_image.py::test_huge_range_log` is failing quite a few of the CI runs "
    "with a Value Error."
)

REPORTER_PARKS = {
    "astropy-14096": ASTROPY_14096,
    "astropy-13398": ASTROPY_13398,
    "django-13346-a": DJANGO_13346_A,
    "django-13346-b": DJANGO_13346_B,
    "django-15037": DJANGO_15037,
    "sphinx-10614-a": SPHINX_A,
    "sphinx-10614-b": SPHINX_B,
    "sphinx-10614-c": SPHINX_C,
}


def _store() -> FactStore:
    batch = FactBatch()
    for nid, kind, name, file, line in (
        ("py:report", NodeKind.MODULE, "report.py", "report.py", 1),
        ("py:report.compute_totals", NodeKind.FUNCTION, "compute_totals", "report.py", 10),
        ("py:report.OrderTotals", NodeKind.TYPE, "OrderTotals", "report.py", 30),
    ):
        batch.add_node(
            Node(id=nid, kind=kind, name=name, language="python", provenance=Provenance(file, line))
        )
    return FactStore(batch)


def _one(text: str):  # type: ignore[no-untyped-def]
    return bind_criteria({"acceptance_criteria": [text]}, store=_store()).rows[0]


# ---- the names the criterion itself shows to be the reporter's -----------------------


@pytest.mark.parametrize(
    ("text", "owned"),
    [
        (ASTROPY_14096, {"custom_coord", "prop"}),
        (ASTROPY_13398, {"itrs_to_observed_mat", "observed_frame"}),
        (DJANGO_13346_A, {"first_filter", "first_items"}),
        (DJANGO_13346_B, {"second_filter", "second_items"}),
        (DJANGO_15037, {"other_id"}),
        (SPHINX_A, {"sphix_svg_bug", "docs_build"}),
        (SPHINX_C, {"sphix_svg_bug", "docs_build", "my_package", "my_class_1", "my_class_2"}),
    ],
)
def test_the_names_a_criterion_defines_or_quotes_are_the_reporters(text: str, owned: set[str]) -> None:
    assert owned <= reporter_owned_names(text)


@pytest.mark.parametrize(
    "text",
    [
        "`GhostWidget` is removed",
        "the report calls ghost_helper before it renders",
        "result = ghost_helper(x)  # only the left-hand side is defined here",
        MATPLOTLIB_20488,
        "",
    ],
)
def test_a_name_that_is_only_used_is_not_the_reporters(text: str) -> None:
    owned = reporter_owned_names(text)
    assert "ghost_helper" not in owned and "GhostWidget" not in owned and "test_huge_range_log" not in owned


# ---- the binder: what parks and what no longer does ----------------------------------


@pytest.mark.parametrize("text", list(REPORTER_PARKS.values()), ids=list(REPORTER_PARKS))
def test_reproduction_material_no_longer_parks_a_bug(text: str) -> None:
    row = _one(text)
    assert row.status == "no-claim"
    assert not row.parks
    assert "reporter" in row.reason


def test_a_claim_about_the_repository_still_parks() -> None:
    """The false-premise case the gate exists for — unchanged."""
    row = _one("`GhostWidget` is removed")
    assert row.status == "unbound" and row.parks


def test_a_claim_beside_reproduction_material_still_parks() -> None:
    """Narrow, name by name: a stray claim is not hidden by the code span next to it."""
    row = _one("the `ghost_widget` module is removed\nfirst_filter = {'a': 1}")
    assert row.status == "unbound"
    assert row.claims == ("ghost_widget",)


def test_a_name_that_is_used_but_not_defined_still_parks() -> None:
    row = _one("result = ghost_helper(x)")
    assert row.status == "unbound"
    assert row.claims == ("ghost_helper",)


def test_a_quoted_name_that_exists_still_binds() -> None:
    """Ownership only removes unresolved names: a repo symbol quoted in an error line binds."""
    row = _one("ValueError: 'compute_totals' failed")
    assert row.status == "bound"
    assert row.anchors[0].symbol == "compute_totals"


def test_the_test_id_case_keeps_parking() -> None:
    """Plan decision D4: a different cause, not relaxed here."""
    row = _one(MATPLOTLIB_20488)
    assert row.status == "unbound" and row.parks


def test_the_render_says_why_a_criterion_no_longer_binds() -> None:
    binding = bind_criteria({"acceptance_criteria": [ASTROPY_14096]}, store=_store())
    assert "reporter" in binding.render()


# ---- the shared transcript test (moved from review.py, B92) ---------------------------


@pytest.mark.parametrize(
    "text",
    [
        ">>> from django.db.models import Q\n>>> Q(x=1).deconstruct()",
        "  In [4]: S1\nOut[4]: {n}",
        'Traceback (most recent call last):\n  File "<stdin>", line 1, in <module>\nKeyError: 1',
        "$ python manage.py check\nSystemCheckError",
        # new: a bare exception line is the tail of a traceback
        ASTROPY_14096,
        "ValueError: invalid literal for int() with base 10: 'x'",
        "django.core.exceptions.FieldError: Cannot resolve keyword 'x'",
    ],
)
def test_a_session_or_traceback_or_bare_exception_line_is_a_transcript(text: str) -> None:
    assert is_transcript(text)


@pytest.mark.parametrize(
    "text",
    [
        "uses the >>> operator for the prompt",
        "charges a $ 5 fee",
        "raises ValueError when the input is negative",  # a requirement, not an error line
        "ValueError should be raised",
        "handles empty input",
        "",
    ],
)
def test_prose_about_errors_is_not_a_transcript(text: str) -> None:
    assert not is_transcript(text)
