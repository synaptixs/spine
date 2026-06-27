"""The capability catalog (Phase 0): governed registry the planner selects from.

Hybrid sources, mirroring what already works elsewhere: built-in capabilities
registered in code (the v1 seed below — like ``ENV_GROUPS``), plus optional
declarative entries from a JSON file (like ``mcpServers``). The planner only
ever picks from this set; nothing is invented at runtime.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from orchestrator.catalog.models import Capability, CapabilityKind, CapabilitySelector

# v1 seed — the language × task-type matrix from the roadmap. Order is stable so
# the planner is deterministic.
_SEED: tuple[Capability, ...] = (
    Capability(
        "python-conventions",
        CapabilityKind.SKILL,
        "Match the repo's Python conventions",
        CapabilitySelector(languages=frozenset({"python"}), task_types=frozenset({"feature"})),
    ),
    Capability(
        "java-conventions",
        CapabilityKind.SKILL,
        "Match the repo's Java conventions",
        CapabilitySelector(languages=frozenset({"java"}), task_types=frozenset({"feature"})),
    ),
    Capability(
        "typescript-conventions",
        CapabilityKind.SKILL,
        "Match the repo's TypeScript conventions",
        CapabilitySelector(languages=frozenset({"typescript"}), task_types=frozenset({"feature"})),
    ),
    Capability(
        "csharp-conventions",
        CapabilityKind.SKILL,
        "Match the repo's C# conventions",
        CapabilitySelector(languages=frozenset({"csharp"}), task_types=frozenset({"feature"})),
    ),
    Capability(
        "repo-pkg-grounding",
        CapabilityKind.SKILL,
        "Ground codegen on the repo's knowledge graph",
        CapabilitySelector(task_types=frozenset({"feature"})),
    ),
    Capability(
        "migration-fanout",
        CapabilityKind.WORKFLOW_PARAM,
        "Parallelize across migration sites",
        CapabilitySelector(task_types=frozenset({"migration"})),
        payload={"max_parallel_features": 4},
    ),
    Capability(
        "extra-review",
        CapabilityKind.WORKFLOW_PARAM,
        "Extra adversarial review rounds",
        CapabilitySelector(task_types=frozenset({"migration"})),
        payload={"max_review_iterations": 3},
    ),
    Capability(
        "db-schema-mcp",
        CapabilityKind.MCP_SERVER,
        "Onboard a DB MCP for schema grounding",
        CapabilitySelector(requires_db=True),
        payload={"server": "db"},
    ),
)

# Machine-managed promotion overlay (persona-skill measurement P3). Skills that
# clear the pre-registered A/B bar are appended here by ``scripts/skill_promote.py
# --apply`` — kept separate from the hand-curated ``_SEED`` so a promotion is an
# evidence-backed, reviewable diff (each entry carries its measured score in
# ``payload["eval"]``). Empty until a skill earns its place; "a skill that doesn't
# move a metric doesn't ship."
_PROMOTED: tuple[Capability, ...] = ()


@dataclass
class CapabilityCatalog:
    """An ordered, id-unique set of capabilities."""

    capabilities: list[Capability]

    def all(self) -> list[Capability]:
        return list(self.capabilities)

    def get(self, capability_id: str) -> Capability | None:
        return next((c for c in self.capabilities if c.id == capability_id), None)

    def register(self, capability: Capability) -> None:
        """Add or replace by id (a later entry overrides an earlier same-id one)."""
        self.capabilities = [c for c in self.capabilities if c.id != capability.id]
        self.capabilities.append(capability)

    @classmethod
    def from_sources(cls, declarative_path: str | Path | None = None) -> CapabilityCatalog:
        """The seed catalog, optionally extended by a declarative JSON file."""
        catalog = cls(list(_SEED) + list(_PROMOTED))
        if declarative_path is not None:
            path = Path(declarative_path)
            if path.is_file():
                for cap in _parse_declarative(path.read_text(encoding="utf-8")):
                    catalog.register(cap)
        return catalog


def default_catalog() -> CapabilityCatalog:
    """The built-in v1 catalog (seed + any promoted skills, no declarative overlay)."""
    return CapabilityCatalog(list(_SEED) + list(_PROMOTED))


def _parse_declarative(text: str) -> list[Capability]:
    """Parse a ``{"capabilities": [...]}`` JSON document into Capabilities."""
    data = json.loads(text)
    out: list[Capability] = []
    for entry in data.get("capabilities", []):
        sel = entry.get("selector", {})
        out.append(
            Capability(
                id=entry["id"],
                kind=CapabilityKind(entry["kind"]),
                summary=entry.get("summary", ""),
                selector=CapabilitySelector(
                    languages=frozenset(sel["languages"]) if sel.get("languages") else None,
                    task_types=frozenset(sel["task_types"]) if sel.get("task_types") else None,
                    requires_db=bool(sel.get("requires_db", False)),
                ),
                payload=dict(entry.get("payload", {})),
            )
        )
    return out


__all__ = ["CapabilityCatalog", "default_catalog"]
