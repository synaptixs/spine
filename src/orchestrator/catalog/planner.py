"""The capability planner (Phase 2): profile + catalog → a gate-ready plan.

Deterministic, rule-based selection: every catalog capability whose selector
matches the project profile is included, in catalog order, each with a one-line
rationale. The same profile always yields the same plan — which is what makes
the gate approval and the audit record meaningful.
"""

from __future__ import annotations

from orchestrator.catalog.catalog import CapabilityCatalog, default_catalog
from orchestrator.catalog.models import Capability, CapabilityKind, CapabilityPlan, PlanItem
from orchestrator.catalog.profile import ProjectProfile


def plan_capabilities(
    profile: ProjectProfile,
    catalog: CapabilityCatalog | None = None,
) -> CapabilityPlan:
    """Assemble the capability plan for ``profile`` from ``catalog`` (or the seed)."""
    catalog = catalog or default_catalog()
    items: list[PlanItem] = []
    skills: list[str] = []
    mcp_servers: list[str] = []
    workflow_params: dict[str, object] = {}

    for cap in catalog.all():
        if not cap.selector.matches(profile):
            continue
        items.append(PlanItem(cap.id, cap.kind, _rationale(cap, profile)))
        if cap.kind is CapabilityKind.SKILL:
            skills.append(cap.id)
        elif cap.kind is CapabilityKind.MCP_SERVER:
            mcp_servers.append(str(cap.payload.get("server", cap.id)))
        elif cap.kind is CapabilityKind.WORKFLOW_PARAM:
            workflow_params.update(cap.payload)

    return CapabilityPlan(
        items=items, skills=skills, mcp_servers=mcp_servers, workflow_params=workflow_params
    )


def _rationale(cap: Capability, profile: ProjectProfile) -> str:
    """Why this capability matched — the human-facing reason at the gate."""
    facets: list[str] = []
    sel = cap.selector
    if sel.languages is not None:
        facets.append("/".join(sorted(sel.languages & profile.languages)))
    if sel.task_types is not None:
        facets.append(f"{profile.task_type} task")
    if sel.requires_db:
        facets.append("database present")
    why = ", ".join(f for f in facets if f) or "always applies"
    return f"{cap.summary} ({why})"


__all__ = ["plan_capabilities"]
