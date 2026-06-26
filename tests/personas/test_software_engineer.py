"""SWE persona + persona→skill binding (persona+skill Phase 2)."""

from __future__ import annotations

from orchestrator.catalog.skills import Skill, SkillEval, SkillOrigin, SkillProvenance, default_skills
from orchestrator.personas.binding import (
    persona_guidance_for_selection,
    persona_skill_guidance,
    resolve_persona_skills,
)
from orchestrator.personas.software_engineer import SOFTWARE_ENGINEER
from orchestrator.registry._common import Metadata
from orchestrator.registry.agent_template import AgentSpec, AgentTemplate, FieldSchema


class TestAgentSpecPersonaFields:
    def test_defaults_keep_non_persona_specs_valid(self) -> None:
        spec = AgentSpec(
            outputs=[
                FieldSchema(name="confidence", type="number"),
                FieldSchema(name="caveats", type="array"),
            ],
            model="claude-sonnet-4-6",
        )
        assert spec.role == "" and spec.skills == [] and spec.workflow_slot == ""

    def test_persona_fields_round_trip_through_json(self) -> None:
        tmpl = AgentTemplate.model_validate(SOFTWARE_ENGINEER.model_dump())
        assert tmpl.spec.workflow_slot == "implement"
        assert "python-conventions" in tmpl.spec.skills


class TestSoftwareEngineerPersona:
    def test_is_a_valid_template_with_a_role_and_slot(self) -> None:
        assert SOFTWARE_ENGINEER.metadata.id == "persona.software_engineer"
        assert SOFTWARE_ENGINEER.spec.role.startswith("You are a senior software engineer")
        assert SOFTWARE_ENGINEER.spec.workflow_slot == "implement"

    def test_referenced_skills_all_resolve(self) -> None:
        known = {s.id for s in default_skills()}
        assert set(SOFTWARE_ENGINEER.spec.skills) <= known


class TestPersonaSkillBinding:
    def test_resolves_proven_native_skills_in_order(self) -> None:
        guidance = persona_skill_guidance(SOFTWARE_ENGINEER)
        # repo-pkg-grounding is listed first; its guidance leads.
        assert guidance and "Reuse existing symbols" in guidance[0]
        assert any("Python conventions" in g for g in guidance)

    def test_guidance_for_selection_intersects_persona_and_plan(self) -> None:
        # The plan selected only python-conventions; the persona endorses more, but
        # only the intersection (profile-relevant + persona-endorsed) contributes.
        guidance = persona_guidance_for_selection(SOFTWARE_ENGINEER, ["python-conventions"])
        assert any("Python conventions" in g for g in guidance)
        assert all("Java conventions" not in g for g in guidance)

    def test_unknown_skill_ids_are_dropped(self) -> None:
        persona = _persona_with_skills(["python-conventions", "does-not-exist"])
        skills = resolve_persona_skills(persona)
        assert [s.id for s in skills] == ["python-conventions"]

    def test_unvetted_imported_skill_is_excluded_until_it_clears_evals(self) -> None:
        imported = Skill(
            "imported-sec",
            "Validate inputs.",
            provenance=SkillProvenance(origin=SkillOrigin.CLAUDE_SKILL, source="x", pin="v1"),
            evals=(SkillEval("eval-sec", 0.8),),
        )
        persona = _persona_with_skills(["python-conventions", "imported-sec"])
        available = [*default_skills(), imported]

        # No score yet → the import is gated out; the native skill remains.
        unvetted = resolve_persona_skills(persona, available=available)
        assert [s.id for s in unvetted] == ["python-conventions"]

        # Clears its bar → the persona picks it up, no edit to the persona.
        vetted = resolve_persona_skills(persona, available=available, scores={"eval-sec": 0.85})
        assert {s.id for s in vetted} == {"python-conventions", "imported-sec"}


def _persona_with_skills(skills: list[str]) -> AgentTemplate:
    return AgentTemplate(
        metadata=Metadata(id="persona.test", version="0.0.1", description="test persona"),
        spec=AgentSpec(
            skills=skills,
            workflow_slot="implement",
            model="m",
            outputs=[
                FieldSchema(name="confidence", type="number"),
                FieldSchema(name="caveats", type="array"),
            ],
        ),
    )
