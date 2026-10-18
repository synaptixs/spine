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
from orchestrator.pkg.repro_material import (
    is_reproduction,
    is_transcript,
    reporter_defined_names,
    reporter_owned_names,
)

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
    """In prose, beside the reporter's code, a name that is only used is still a claim."""
    row = _one("the report calls ghost_helper before it renders")
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


# ---- B94 (SSPN-202): a criterion that is only code or output is an example, not a requirement ----
#
# P6 parked 17 of 100 issues on "the criterion names X, which the graph does not hold". In six of
# them the "criterion" was a line of the reporter's code, an interpreter session or a traceback copied
# out by intake (whose prompt says to copy stated criteria VERBATIM, and a bug report states none).
# The strings are the real stored criteria of those runs.

XARRAY_LINE = "good_indexed.loc[{'dim_0': 0}] = set()"
XARRAY_COMMENTS = "# correct\n# good_indexed.values => array([set()], dtype=object)"
SKLEARN_CALL = "voter.fit(X, y, sample_weight=np.ones(y.shape))"
MATPLOTLIB_FENCE = (
    "```python\n"
    '_, bins, _ = plt.hist(np.random.rand(10), "auto", range=(0, 1), density=True)\n'
    "print(bins)\n"
    "```"
)
DJANGO_HEADER = "if value in allowed_values: # This is an array [1, 2]"
PYTEST_OUTPUT = (
    'if hasattr(fixturedef, "cached_param"):\n'
    '>           tw.write("[{}]".format(fixturedef.cached_param))\n'
    "E           BytesWarning: str() on a bytes instance"
)
SYMPY_SESSION = (
    ">>> Poly(0, x).primitive()\n(0, Poly(0, x, domain='ZZ'))\n"
    ">>> bad_poly.primitive()\n(1, Poly(0, x, domain='EX'))"
)
# Prompt lines and a prose line together: not wholly code, so it is judged name by name.
SYMPY_MIXED = '>>> bad_poly.rep\nDMP([EX(0)], EX, None)\n\nwhich should be\n\n>>> Poly(0, x, domain="EX").rep'
SYMPY_TICKET = (
    "A zero polynomial built by multiplication keeps a stale domain.\n\n"
    '    bad_poly = Poly(0, x, domain="EX") * Poly(1, x, domain="ZZ")\n'
)

WHOLLY_CODE = {
    "xarray-line": XARRAY_LINE,
    "xarray-comments": XARRAY_COMMENTS,
    "sklearn-call": SKLEARN_CALL,
    "matplotlib-fence": MATPLOTLIB_FENCE,
    "django-header": DJANGO_HEADER,
    "pytest-output": PYTEST_OUTPUT,
    "sympy-session": SYMPY_SESSION,
}


@pytest.mark.parametrize("text", list(WHOLLY_CODE.values()), ids=list(WHOLLY_CODE))
def test_a_criterion_that_is_only_code_or_output_is_reproduction(text: str) -> None:
    assert is_reproduction(text)


@pytest.mark.parametrize(
    "text",
    [
        "",
        "Foo",
        "for example:",  # a lowercase keyword that ends in a colon is still prose
        "if the input is empty return None",
        "return the sum of the list.",
        "the compiler returns >= 70 rules",
        "`rule_compiler` returns >= 70 rules",
        "The error message should say `random_attr` doesn't exist.",
        "len(items) == 3 after the call",
        "types.JSONObject instead of Dict[str, Any] in both cases.",
        "os.getcwd",  # a lone dotted name is a mention, not a statement
        SYMPY_MIXED,
        MATPLOTLIB_20488,
    ],
)
def test_prose_is_not_reproduction(text: str) -> None:
    assert not is_reproduction(text)


@pytest.mark.parametrize("text", list(WHOLLY_CODE.values()), ids=list(WHOLLY_CODE))
def test_reproduction_never_parks_a_bug(text: str) -> None:
    row = _one(text)
    assert row.status != "unbound" and not row.parks


def test_the_reason_says_it_is_an_example() -> None:
    row = _one(XARRAY_LINE)
    assert row.status == "no-claim"
    assert "reproduction" in row.reason and "not a requirement" in row.reason


def test_reproduction_that_names_the_repository_still_anchors() -> None:
    """Only a would-be park is relaxed: a line that binds keeps its anchors."""
    row = _one("compute_totals(order)")
    assert row.status == "bound" and row.anchors[0].symbol == "compute_totals"


def test_a_prose_claim_beside_a_code_line_still_parks() -> None:
    row = _one("the `ghost_widget` module is removed\n" + XARRAY_LINE)
    assert row.status == "unbound" and "ghost_widget" in row.claims


# ---- the whole ticket shows who owns a name --------------------------------------------


def _one_in(text: str, ticket: str):  # type: ignore[no-untyped-def]
    return bind_criteria({"acceptance_criteria": [text]}, store=_store(), ticket_text=ticket).rows[0]


def test_a_name_the_ticket_defines_is_the_reporters_in_every_criterion() -> None:
    assert _one(SYMPY_MIXED).status == "unbound"  # criterion alone: nothing shows who owns bad_poly
    row = _one_in(SYMPY_MIXED, SYMPY_TICKET)
    assert row.status == "no-claim" and "reporter" in row.reason


def test_a_definition_in_the_ticket_does_not_cover_another_name() -> None:
    row = _one_in(">>> other_ghost.rep\n\nwhich should be fixed", SYMPY_TICKET)
    assert row.status == "unbound" and row.claims == ("other_ghost",)


def test_a_name_only_used_in_the_ticket_is_still_a_claim() -> None:
    row = _one_in("the report calls ghost_helper before it renders", "result = ghost_helper(x)")
    assert row.status == "unbound"


def test_a_repository_symbol_defined_in_the_ticket_still_binds() -> None:
    row = _one_in("compute_totals returns the wrong total", "compute_totals = None")
    assert row.status == "bound"


def test_a_url_in_the_ticket_does_not_make_a_standard_library_name_the_reporters() -> None:
    """Whole-ticket scope counts definitions only: a link to the docs of `None` defines nothing."""
    ticket = (
        "see https://docs.python.org/3/library/constants.html#None and https://x.test/argparse#add_subparsers"
    )
    assert not {"None", "add_subparsers"} & reporter_defined_names(ticket)
    assert "None" in reporter_owned_names(ticket)  # a criterion's own URL still counts (B93)


def test_an_assignment_behind_an_interpreter_prompt_is_a_definition() -> None:
    assert {"coeff", "bad_poly"} <= reporter_defined_names(">>> coeff, bad_poly = f.clear_denoms()")
