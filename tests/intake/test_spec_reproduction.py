"""B94 (SSPN-202): the reporter's code is not part of the contract.

Intake is told to copy stated acceptance criteria verbatim. A bug report states none, so what got
copied was often a line of the reporter's reproduction — code, a session, a traceback. Those lines
sat in ``acceptance_criteria`` where the binder, the judge, design and codegen all read them as
requirements: 17 of 100 P6 issues were parked on one. A spec now keeps them apart, in
``reproduction``: still shown to codegen and still searched for names, but never the contract.

The strings are the real stored criteria of the runs that parked.
"""

from __future__ import annotations

from typing import Any

from orchestrator.intake.specs import FeatureSpec
from orchestrator.sdlc.codegen import _spec_text
from orchestrator.sdlc.design import _query_text
from orchestrator.sdlc.grounding import _spec_query

REQUIREMENT = "Calling fit with sample_weight should work when an estimator is set to None."
CALL = "voter.fit(X, y, sample_weight=np.ones(y.shape))"
SESSION = '>>> bad_poly.rep\nDMP([EX(0)], EX, None)\n>>> Poly(0, x, domain="EX").rep'


def _spec(*criteria: str, **extra: Any) -> FeatureSpec:
    return FeatureSpec(intent_id="intent-x", title="T", acceptance_criteria=list(criteria), **extra)


def test_a_line_of_the_reporters_code_leaves_the_contract() -> None:
    spec = _spec(CALL, REQUIREMENT, SESSION)
    assert spec.acceptance_criteria == [REQUIREMENT]
    assert spec.reproduction == [CALL, SESSION]


def test_a_spec_with_only_requirements_is_unchanged_and_says_nothing_new() -> None:
    spec = _spec(REQUIREMENT, "`render` returns a string")
    assert spec.acceptance_criteria == [REQUIREMENT, "`render` returns a string"]
    assert spec.reproduction == []
    assert "reproduction" not in spec.model_dump(), "absent when empty: existing specs serialise as before"


def test_reproduction_is_serialised_when_there_is_some() -> None:
    assert _spec(CALL, REQUIREMENT).model_dump()["reproduction"] == [CALL]


def test_the_split_is_stable_when_a_spec_is_loaded_again() -> None:
    once = _spec(CALL, REQUIREMENT)
    again = FeatureSpec.model_validate(once.model_dump())
    assert again == once


def test_a_spec_may_carry_reproduction_it_was_given() -> None:
    spec = _spec(REQUIREMENT, reproduction=[CALL])
    assert spec.reproduction == [CALL] and spec.acceptance_criteria == [REQUIREMENT]


def test_prose_that_mentions_code_stays_a_requirement() -> None:
    spec = _spec("`fit` must accept sample_weight=None", "returns >= 70 rules", REQUIREMENT)
    assert spec.reproduction == []


# ---- what still reads the reproduction ------------------------------------------------------


def test_codegen_is_shown_the_reproduction_as_examples_not_requirements() -> None:
    text = _spec_text(_spec(CALL, REQUIREMENT).model_dump())
    assert "Acceptance criteria:" in text and REQUIREMENT in text
    assert "Reproduction from the report" in text and CALL in text
    assert text.index(REQUIREMENT) < text.index("Reproduction from the report") < text.index(CALL)


def test_codegen_prompt_has_no_reproduction_block_when_there_is_none() -> None:
    assert "Reproduction" not in _spec_text(_spec(REQUIREMENT).model_dump())


def test_the_names_in_the_reproduction_still_guide_retrieval_and_design() -> None:
    spec = _spec(CALL, REQUIREMENT).model_dump()
    assert "sample_weight=np.ones" in _spec_query(spec)
    assert "sample_weight=np.ones" in _query_text(spec)


def test_the_issue_body_shows_the_reproduction_apart_from_the_criteria() -> None:
    from orchestrator.intake.service import spec_to_issue_request

    body = spec_to_issue_request(_spec(CALL, REQUIREMENT)).description
    assert "## Acceptance criteria" in body and REQUIREMENT in body
    heading = "## Reproduction from the report (examples, not requirements)"
    assert heading in body and body.index(heading) > body.index(REQUIREMENT)
    assert CALL in body.split(heading, 1)[1]
