"""Catalog-then-compose: assemble the right capabilities per project.

Phase 0–2 (deterministic): a governed ``CapabilityCatalog`` of skills / MCP
servers / workflow-param bundles, a ``ProjectProfile`` extracted from a repo,
and a ``plan_capabilities`` planner that selects from the catalog by profile.
The selection is later surfaced at the intent gate (Phase 3) and wired into the
run (Phase 4).
"""

from orchestrator.catalog.catalog import CapabilityCatalog, default_catalog
from orchestrator.catalog.models import (
    Capability,
    CapabilityKind,
    CapabilityPlan,
    CapabilitySelector,
    PlanItem,
)
from orchestrator.catalog.planner import plan_capabilities
from orchestrator.catalog.profile import ProjectProfile, task_type_from_intent
from orchestrator.catalog.skill_import import SkillImportError, import_claude_skill
from orchestrator.catalog.skills import (
    Skill,
    SkillEval,
    SkillOrigin,
    SkillProvenance,
    default_skills,
    get_skill,
    skill_guidance,
)
from orchestrator.catalog.vetting import (
    VettingStatus,
    approved_skills,
    evaluate_vetting,
    is_approved,
)

__all__ = [
    "Capability",
    "CapabilityCatalog",
    "CapabilityKind",
    "CapabilityPlan",
    "CapabilitySelector",
    "PlanItem",
    "ProjectProfile",
    "Skill",
    "SkillEval",
    "SkillImportError",
    "SkillOrigin",
    "SkillProvenance",
    "VettingStatus",
    "approved_skills",
    "default_catalog",
    "default_skills",
    "evaluate_vetting",
    "get_skill",
    "import_claude_skill",
    "is_approved",
    "plan_capabilities",
    "skill_guidance",
    "task_type_from_intent",
]
