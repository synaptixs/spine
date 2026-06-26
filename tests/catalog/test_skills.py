"""First-class Skill artifacts (persona+skill Phase 0): schema + zero-drift migration."""

from __future__ import annotations

import pytest

from orchestrator.catalog.catalog import default_catalog
from orchestrator.catalog.models import CapabilityKind
from orchestrator.catalog.skills import (
    NATIVE_SKILLS,
    Skill,
    SkillEval,
    SkillOrigin,
    SkillProvenance,
    default_skills,
    get_skill,
    skill_guidance,
    skill_phases,
)

# The exact id→guidance mapping codegen used before the migration. This is the
# zero-behavior-change contract — these strings must not drift (newer native skills
# may be ADDED, but these four migrated fragments stay byte-identical).
_MIGRATED_GUIDANCE = {
    "python-conventions": "Match the repo's Python conventions — naming, import order, type annotations.",
    "java-conventions": "Match the repo's Java conventions and package layout.",
    "typescript-conventions": "Match the repo's TypeScript conventions — layout, imports, strict types.",
    "repo-pkg-grounding": "Reuse existing symbols — use the pkg_* tools to find them before writing code.",
}


class TestSkillSchema:
    def test_skill_requires_id_and_guidance(self) -> None:
        with pytest.raises(ValueError):
            Skill("", "guidance")
        with pytest.raises(ValueError):
            Skill("id", "")

    def test_skill_eval_min_score_bounds(self) -> None:
        SkillEval("e1", 0.0)
        SkillEval("e1", 1.0)
        with pytest.raises(ValueError):
            SkillEval("e1", 1.5)
        with pytest.raises(ValueError):
            SkillEval("e1", -0.1)
        with pytest.raises(ValueError):
            SkillEval("", 0.5)

    def test_native_default_provenance(self) -> None:
        prov = SkillProvenance()
        assert prov.origin is SkillOrigin.NATIVE
        assert prov.source == "" and prov.pin == "" and prov.license == ""

    def test_skill_carries_full_schema(self) -> None:
        s = Skill(
            "sec",
            "Validate inputs.",
            provenance=SkillProvenance(SkillOrigin.CLAUDE_SKILL, "https://x/skill", "v1", "MIT"),
            tools=("mcp.semgrep.scan",),
            verification="semgrep-clean",
            evals=(SkillEval("eval-sec", 0.8),),
            provider_notes="claude-only tool",
        )
        assert s.tools == ("mcp.semgrep.scan",)
        assert s.evals[0].min_score == 0.8
        assert s.provenance.origin is SkillOrigin.CLAUDE_SKILL


class TestNativeSkills:
    def test_migrated_ids_are_present(self) -> None:
        assert set(_MIGRATED_GUIDANCE) <= {s.id for s in default_skills()}

    def test_swe_candidate_skills_authored(self) -> None:
        # Phase 1: authored, available, but pending eval measurement.
        ids = {s.id for s in default_skills()}
        assert {"test-strategy", "security-aware-coding", "convention-digest"} <= ids

    def test_all_natives_are_native_origin_and_unpinned(self) -> None:
        for s in NATIVE_SKILLS:
            assert s.provenance.origin is SkillOrigin.NATIVE
            assert s.provenance.source == "" and s.provenance.pin == ""
            assert s.tools == () and s.evals == () and s.verification is None

    def test_get_skill(self) -> None:
        assert get_skill("python-conventions") is not None
        assert get_skill("does-not-exist") is None


class TestSkillPhases:
    """Persona-skill measurement P0: a skill declares which codegen phase(s) it conditions."""

    def test_default_phase_is_implement(self) -> None:
        # Preserves today's behavior for the conventions/grounding skills.
        assert Skill("x", "do x").phases == ("implement",)
        for skill_id in ("python-conventions", "java-conventions", "repo-pkg-grounding"):
            assert get_skill(skill_id).phases == ("implement",)  # type: ignore[union-attr]

    def test_test_strategy_targets_the_test_phases(self) -> None:
        # The whole point: test-strategy must reach author_tests, not implement.
        assert get_skill("test-strategy").phases == ("author_tests", "refine")  # type: ignore[union-attr]

    def test_implement_skills_declare_implement(self) -> None:
        for skill_id in ("security-aware-coding", "convention-digest"):
            assert "implement" in get_skill(skill_id).phases  # type: ignore[union-attr]

    def test_rejects_empty_and_unknown_phases(self) -> None:
        with pytest.raises(ValueError):
            Skill("x", "g", phases=())
        with pytest.raises(ValueError):
            Skill("x", "g", phases=("deploy",))

    def test_skill_phases_mapping(self) -> None:
        phases = skill_phases()
        assert phases["test-strategy"] == ("author_tests", "refine")
        assert phases["python-conventions"] == ("implement",)


class TestZeroBehaviorChangeMigration:
    def test_migrated_fragments_have_not_drifted(self) -> None:
        # The four migrated strings are pinned verbatim — a drift is a behavior change.
        guidance = skill_guidance()
        for skill_id, text in _MIGRATED_GUIDANCE.items():
            assert guidance[skill_id] == text

    def test_codegen_skill_prompts_resolve_from_the_registry(self) -> None:
        from orchestrator.sdlc import codegen

        # codegen's _SKILL_PROMPTS IS the registry mapping (single source of truth).
        assert skill_guidance() == codegen._SKILL_PROMPTS
        for skill_id, text in _MIGRATED_GUIDANCE.items():
            assert codegen._SKILL_PROMPTS[skill_id] == text


class TestCatalogConsistency:
    def test_every_catalog_skill_has_a_native_skill(self) -> None:
        # The planner only selects SKILL capabilities by id; each must resolve to
        # a Skill artifact, or codegen would select an id with no guidance.
        skill_ids = {s.id for s in default_skills()}
        for cap in default_catalog().all():
            if cap.kind is CapabilityKind.SKILL:
                assert cap.id in skill_ids, f"catalog SKILL {cap.id!r} has no Skill artifact"
