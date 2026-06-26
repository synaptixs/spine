"""AgentTemplate: versioned, registry-published spec for an agent."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from orchestrator.registry._common import Metadata, ResourceId, Status

MANDATORY_OUTPUT_FIELDS: frozenset[str] = frozenset({"confidence", "caveats"})


class FieldSchema(BaseModel):
    """A single input or output field declaration."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    type: str = Field(min_length=1)
    description: str = ""
    required: bool = True


class EvalReference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: ResourceId
    min_score: float = Field(ge=0.0, le=1.0, default=0.0)


class AgentSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    inputs: list[FieldSchema] = Field(default_factory=list)
    outputs: list[FieldSchema]
    allowed_tools: list[ResourceId] = Field(default_factory=list)
    allowed_state_channels: list[str] = Field(default_factory=list)
    policies: list[ResourceId] = Field(default_factory=list)
    known_limitations: list[str] = Field(default_factory=list)
    model: str = Field(min_length=1)
    constraints: dict[str, Any] = Field(default_factory=dict)
    evals: list[EvalReference] = Field(default_factory=list)
    # Persona binding (persona + skill system). A persona is an AgentTemplate that
    # bundles a role, a set of skills, and a workflow slot. ``skills`` are catalog
    # skill ids (hyphenated, e.g. "python-conventions") — plain strings, not
    # ResourceId. All optional/defaulted, so non-persona templates are unaffected.
    role: str = ""
    skills: list[str] = Field(default_factory=list)
    workflow_slot: str = ""

    @model_validator(mode="after")
    def _require_mandatory_outputs(self) -> AgentSpec:
        names = {f.name for f in self.outputs}
        missing = MANDATORY_OUTPUT_FIELDS - names
        if missing:
            raise ValueError(
                f"AgentTemplate outputs must include {sorted(MANDATORY_OUTPUT_FIELDS)}; "
                f"missing: {sorted(missing)}"
            )
        return self


class AgentTemplate(BaseModel):
    """A versioned agent specification published to the registry."""

    model_config = ConfigDict(extra="forbid")

    metadata: Metadata
    spec: AgentSpec
    status: Status = Field(default_factory=Status)
