"""The software-engineer persona (persona + skill system — Phase 2).

A persona is a published ``AgentTemplate`` that bundles a role, a set of skills, a
model, and a workflow slot. ``SOFTWARE_ENGINEER`` re-expresses today's codegen role
as that first persona. Its ``skills`` are resolved through the vetting gate at
selection time (``personas.binding``), so measured imported skills — and the SWE
candidates (test-strategy / security-aware-coding / convention-digest) once they
clear their eval bar — slot in without editing this definition.

This is the binding model; driving an actual codegen run *as* this persona (the
run-driver that calls ``persona_skill_guidance`` instead of the capability plan's
skill list) is the next increment (Phase 2b).
"""

from __future__ import annotations

from orchestrator.registry._common import Metadata
from orchestrator.registry.agent_template import AgentSpec, AgentTemplate, FieldSchema

SOFTWARE_ENGINEER = AgentTemplate(
    metadata=Metadata(
        id="persona.software_engineer",
        version="0.1.0",
        description="Software-engineer persona: implements features as runnable, tested code.",
        tags=["persona", "sdlc", "software-engineer"],
    ),
    spec=AgentSpec(
        role=(
            "You are a senior software engineer. Implement the feature as runnable, "
            "well-tested code that matches the repository's existing conventions."
        ),
        # Proven, catalog-wired skills only. The SWE candidate skills join this list
        # once the eval measurement approves them — no change to this file needed.
        skills=[
            "repo-pkg-grounding",
            "python-conventions",
            "java-conventions",
            "typescript-conventions",
        ],
        workflow_slot="implement",
        model="claude-sonnet-4-6",
        outputs=[
            FieldSchema(name="confidence", type="number", description="0–1 self-assessed confidence"),
            FieldSchema(name="caveats", type="array", description="known limitations / risks"),
        ],
    ),
)


__all__ = ["SOFTWARE_ENGINEER"]
