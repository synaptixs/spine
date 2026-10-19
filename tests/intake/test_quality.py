"""SSPN-204 (P0 of the intake-quality plan): a deterministic score for an intake output.

Intake used to be patched per failure. This is the other half: a contract for a good intake, scored
without a model, so a change is judged on the same free measure before it is judged on resolution.
Five properties, each from a real defect seen in P6 or IR17. The strings are stored criteria.
"""

from __future__ import annotations

import pytest

from orchestrator.intake.quality import (
    IntakeScore,
    classify_name,
    criterion_form,
    is_exact_span,
    merge,
    score_intake,
    tag_status,
)

REPORT = (
    "Voting estimator will fail at fit if weights are passed and an estimator is None\n"
    "Because we don't check for an estimator to be `None` in `sample_weight` support, `fit` is failing`.\n\n"
    "```python\n    voter.fit(X, y, sample_weight=np.ones(y.shape))\n    voter.set_params(lr=None)\n```\n"
)


# ---- well-formed: a sentence, a fragment, a question, or code -------------------------------


@pytest.mark.parametrize(
    ("text", "form"),
    [
        ("The migrate command currently checks for existence of a __file__ attribute.", "sentence"),
        ("Because we don't check for an estimator to be `None`, `fit` is failing.", "sentence"),
        ("`expr.func(*expr.args) == expr` should hold for any Basic subclass.", "sentence"),
        ("However, working with arrays is not an issue:", "fragment"),
        ("which should be", "fragment"),
        ("Fails", "fragment"),
        ("Shouldn't that be using `saferepr` or something rather than (implicitly) `str()`?", "question"),
        ("voter.fit(X, y, sample_weight=np.ones(y.shape))", "code"),
        (">>> bad_poly.rep\nDMP([EX(0)], EX, None)", "code"),
        ("", "fragment"),
    ],
)
def test_the_form_of_a_stated_criterion(text: str, form: str) -> None:
    assert criterion_form(text) == form


# ---- faithful: an exact span of the report --------------------------------------------------


def test_a_stated_criterion_is_faithful_when_the_report_says_it_word_for_word() -> None:
    assert is_exact_span("Voting estimator will fail at fit if weights are passed", REPORT)
    assert is_exact_span("Voting estimator will fail at fit\nif weights are passed", REPORT), (
        "whitespace is not a difference"
    )
    assert not is_exact_span("fit must skip estimators that are None", REPORT)
    assert not is_exact_span("", REPORT)


# ---- derived criteria: tagged, and the tag is a real quote ----------------------------------


@pytest.mark.parametrize(
    ("proposed", "status"),
    [
        ("Given X, then Y.", "none"),
        ('Given X, then Y. [from: "will fail at fit if weights are passed"]', "verified"),
        ('Given X, then Y. [from: "Voting estimator will fail ... an estimator is None"]', "verified"),
        ('Given X, then Y. [from: "fit must skip estimators that are None"]', "unverified"),
        ("Given X, then Y. [from: paraphrase, not a quote of the report]", "unverified"),
    ],
)
def test_the_provenance_tag_of_a_derived_criterion(proposed: str, status: str) -> None:
    assert tag_status(proposed, REPORT) == status


# ---- grounded: where an unresolved name comes from ------------------------------------------


@pytest.mark.parametrize(
    ("name", "origin"),
    [
        ("ValueError", "builtin"),
        ("None", "builtin"),
        ("os.getcwd", "stdlib"),
        ("sys.path", "stdlib"),
        ("bad_poly", "reporter"),
        ("build/gettext/blender_manual.pot", "path"),
        ("np.random.rand", "unclassified"),
        ("get_inlines", "unclassified"),
        ("add_subparsers", "unclassified"),
    ],
)
def test_the_origin_of_an_unresolved_name(name: str, origin: str) -> None:
    assert classify_name(name, reporter_defined=frozenset({"bad_poly"})) == origin


# ---- the score over one intake output -------------------------------------------------------


def test_one_intake_output_is_scored_on_every_property() -> None:
    stated = [
        "Because we don't check for an estimator to be `None` in `sample_weight` support, `fit` is failing`.",
        "voter.fit(X, y, sample_weight=np.ones(y.shape))",
        "However, working with arrays is not an issue:",
    ]
    proposed = [
        'Given X, then Y. [from: "will fail at fit if weights are passed"]',
        'Given X, then Y. [from: "something the report never says"]',
        "Given X, then Z.",
    ]
    score = score_intake(
        REPORT,
        stated,
        proposed,
        unresolved={stated[1]: ["sample_weight"], stated[2]: ["ValueError", "get_inlines"]},
    )
    assert score.stated == 3 and score.exact_span == 2 and score.code == 1
    assert score.sentence == 1 and score.fragment == 1
    assert (
        score.proposed == 3 and score.tag_none == 1 and score.tag_verified == 1 and score.tag_unverified == 1
    )
    assert score.unresolved == 3 and score.unresolved_classified == 1 and score.unresolved_unclassified == 2


def test_an_empty_intake_scores_zero_and_divides_by_nothing() -> None:
    score = score_intake("", [], [], unresolved={})
    assert score == IntakeScore()
    assert score.rates() == {}


def test_scores_add_up_and_rates_are_over_the_right_denominators() -> None:
    a = score_intake(
        REPORT, ["Because we don't check for an estimator to be `None` in `sample_weight` support."], [], {}
    )
    b = score_intake(REPORT, ["voter.set_params(lr=None)"], ['Given X. [from: "nope nope nope"]'], {})
    total = merge([a, b])
    assert total.stated == 2 and total.code == 1
    rates = total.rates()
    assert rates["code_share"] == 0.5
    assert rates["tag_verified_share"] == 0.0 and total.tag_unverified == 1
    assert "faithful_share" in rates and "well_formed_share" in rates
