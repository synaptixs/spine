"""A build document without why-fields is byte-identical to before they existed (D26)."""

from __future__ import annotations

from pathlib import Path

from orchestrator.intake.specs import FeatureSpec
from orchestrator.sdlc.builddoc import plan_digest
from tests.sdlc.test_builddoc import _render, _spec

# plan_digest of ``_render(tmp_path)`` measured on develop @ 3a54316f, before the why-fields
# existed. If this moves, every approval already granted on an existing plan goes stale.
_BASE_DIGEST = "ebb16a363b411222"


def test_the_digest_of_a_no_why_plan_has_not_moved(tmp_path: Path) -> None:
    assert plan_digest(_render(tmp_path)) == _BASE_DIGEST


def test_a_spec_dumped_from_the_model_renders_the_same_as_the_legacy_dict(tmp_path: Path) -> None:
    spec = _spec()
    via_model = FeatureSpec(
        intent_id=spec["intent_id"],
        title=spec["title"],
        summary=spec["summary"],
        user_story=spec["user_story"],
        acceptance_criteria=spec["acceptance_criteria"],
    ).model_dump()
    via_model.update({k: v for k, v in spec.items() if k not in via_model})
    assert _render(tmp_path, {**spec, **via_model}) == _render(tmp_path, spec)


def test_a_stated_why_is_rendered_and_labelled(tmp_path: Path) -> None:
    md = _render(
        tmp_path,
        _spec(
            problem="Finance cannot reconcile.",
            users=["finance", "audit"],
            outcome="Month-end closes in a day.",
        ),
    )
    section = md.split("## 1. Requirement", 1)[1].split("\n## 2.", 1)[0]
    assert "**Problem:** Finance cannot reconcile." in section
    assert "**Users:** finance; audit" in section
    assert "**Outcome:** Month-end closes in a day." in section
    assert "Non-goals" not in section
    assert plan_digest(md) != _BASE_DIGEST
