"""Capability catalog data model (Phase 0).

A ``Capability`` is one thing the orchestrator can bring to a run — an internal
skill, an MCP server to onboard, or a bundle of workflow parameters. Each
carries a ``CapabilitySelector`` describing the projects it applies to. The
catalog is a *governed* set: the planner only ever selects from it, never
improvises new capabilities at runtime.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from orchestrator.catalog.profile import ProjectProfile


class CapabilityKind(str, Enum):
    """What a capability contributes when selected."""

    SKILL = "skill"  # an internal codegen/review/convention bundle
    MCP_SERVER = "mcp_server"  # an external server to onboard (governed)
    WORKFLOW_PARAM = "workflow_param"  # a bundle of SDLCWorkflow parameters


@dataclass(frozen=True)
class CapabilitySelector:
    """When a capability applies. Facets are AND-ed; ``None`` means "don't care".

    - ``languages`` — matches if the profile has *any* of these languages.
    - ``task_types`` — matches if the profile's task type is one of these.
    - ``requires_db`` — when true, matches only if the project has a database.
    """

    languages: frozenset[str] | None = None
    task_types: frozenset[str] | None = None
    requires_db: bool = False

    def matches(self, profile: ProjectProfile) -> bool:
        if self.languages is not None and not (self.languages & profile.languages):
            return False
        if self.task_types is not None and profile.task_type not in self.task_types:
            return False
        return not (self.requires_db and not profile.has_db)


@dataclass(frozen=True)
class Capability:
    """One catalog entry."""

    id: str
    kind: CapabilityKind
    summary: str
    selector: CapabilitySelector
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PlanItem:
    """A selected capability plus the reason it was selected."""

    capability_id: str
    kind: CapabilityKind
    rationale: str


@dataclass
class CapabilityPlan:
    """The toolkit assembled for a run: the resolved, gate-ready selection."""

    items: list[PlanItem]
    skills: list[str]
    mcp_servers: list[str]
    workflow_params: dict[str, Any]

    @property
    def is_empty(self) -> bool:
        return not self.items

    def summary_lines(self) -> list[str]:
        """Human-readable one-line-per-selection view (for the gate / CLI)."""
        if not self.items:
            return ["base pipeline — no extra capabilities selected"]
        return [f"{i.capability_id} [{i.kind.value}] — {i.rationale}" for i in self.items]

    def to_dict(self) -> dict[str, Any]:
        """Serializable form for the gate payload + audit log."""
        return {
            "skills": list(self.skills),
            "mcp_servers": list(self.mcp_servers),
            "workflow_params": dict(self.workflow_params),
            "items": [
                {"capability_id": i.capability_id, "kind": i.kind.value, "rationale": i.rationale}
                for i in self.items
            ],
        }


__all__ = [
    "Capability",
    "CapabilityKind",
    "CapabilityPlan",
    "CapabilitySelector",
    "PlanItem",
]
