"""GraphIR: typed intermediate representation of a workflow.

This is the initial structural model (Sprint 1). Reference resolution,
reachability, termination, and budget sanity checks live in the
separate IR validator (later sprint).
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from orchestrator.registry._common import Metadata, ResourceId, Status

NODE_ID_PATTERN = r"^n_[a-z0-9_]{1,64}$"
NodeId = Annotated[str, StringConstraints(pattern=NODE_ID_PATTERN)]


class WorkflowPattern(str, Enum):
    SINGLE_AGENT = "single_agent"
    SEQUENTIAL = "sequential"
    MANAGER_SPECIALISTS = "manager_specialists"
    ROUTER = "router"
    MIXTURE = "mixture"


class NodeType(str, Enum):
    AGENT = "agent"
    VERIFIER = "verifier"
    APPROVAL = "approval"
    LOOP_GUARD = "loop_guard"
    REFLECTION = "reflection"
    A2A_CALL = "a2a_call"


class GlossaryTerm(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str = Field(min_length=1)
    source: str = Field(min_length=1)


class Node(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: NodeId
    type: NodeType
    template_id: ResourceId | None = None
    template_version: str | None = None
    config: dict[str, Any] = Field(default_factory=dict)


class Edge(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: NodeId
    target: NodeId
    condition: str | None = None

    @model_validator(mode="after")
    def _no_self_loop(self) -> Edge:
        if self.source == self.target:
            raise ValueError(f"Edge self-loop on node {self.source!r}")
        return self


class ApprovalPoint(BaseModel):
    """Sprint 14: IR-level approval gate.

    The runtime workflow scans these before execution. Each ApprovalPoint
    whose ``before_node`` matches an agent in the IR raises an approval
    request, persists it, and pauses until an approver decides via REST.

    All fields beyond ``before_node`` are optional with sensible defaults
    so existing IRs (which only set ``before_node`` + ``description``) keep
    validating. The approval-request defaults are deliberately conservative
    (medium risk, no timeout, generic approver role) — IR authors who care
    about the rich UI surface fill them in.
    """

    model_config = ConfigDict(extra="forbid")

    before_node: NodeId
    description: str = ""

    # Rich fields the approval queue UI surfaces. All optional.
    title: str | None = None
    action_summary: str | None = None
    risk_classification: str = "medium"
    affected_resources: list[str] = Field(default_factory=list)
    approver_roles: list[str] = Field(default_factory=list)  # empty = any authenticated user
    timeout_seconds: int | None = None
    timeout_auto_action: str | None = None  # "escalate" | "reject" | "grant"
    notification_channels: list[str] = Field(default_factory=list)


class Budget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_tokens: int | None = Field(default=None, ge=1)
    max_cost_usd: float | None = Field(default=None, ge=0)
    max_wall_clock_seconds: int | None = Field(default=None, ge=1)
    max_replan_count: int = Field(default=0, ge=0)


class GraphSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    objective: str = Field(min_length=1, max_length=4096)
    workflow_pattern: WorkflowPattern
    task_glossary: dict[str, GlossaryTerm] = Field(default_factory=dict)
    nodes: list[Node] = Field(min_length=1)
    edges: list[Edge] = Field(default_factory=list)
    approval_points: list[ApprovalPoint] = Field(default_factory=list)
    budget: Budget = Field(default_factory=Budget)
    constraints: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _unique_node_ids(self) -> GraphSpec:
        ids = [n.id for n in self.nodes]
        if len(ids) != len(set(ids)):
            dupes = sorted({i for i in ids if ids.count(i) > 1})
            raise ValueError(f"Duplicate node ids: {dupes}")
        return self

    @model_validator(mode="after")
    def _edges_reference_known_nodes(self) -> GraphSpec:
        known = {n.id for n in self.nodes}
        for edge in self.edges:
            missing = {edge.source, edge.target} - known
            if missing:
                raise ValueError(
                    f"Edge {edge.source}->{edge.target} references unknown node(s): {sorted(missing)}"
                )
        return self

    @model_validator(mode="after")
    def _approval_points_reference_known_nodes(self) -> GraphSpec:
        known = {n.id for n in self.nodes}
        for ap in self.approval_points:
            if ap.before_node not in known:
                raise ValueError(f"Approval point references unknown node {ap.before_node!r}")
        return self


class GraphIR(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metadata: Metadata
    spec: GraphSpec
    status: Status = Field(default_factory=Status)
