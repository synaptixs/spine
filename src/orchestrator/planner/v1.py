"""Planner v1: single_agent or sequential GraphIR from an objective.

Same shape as v0 with two additions:

1. The LLM may now return a ``sequential`` plan listing two or more
   templates in order, each with an ``inputs_from`` mapping.
2. A ``split_justification`` is required whenever the plan emits more than
   one node. The IR validator surfaces it as a required field for any
   non-``single_agent`` pattern (Sprint 11 / Task 11.7 in the spec).

Manager-with-specialists, router, mixture remain Sprint 9+ patterns and
land alongside their runtime support.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from orchestrator.core.llm import LLMClient, Message
from orchestrator.ir.graph import (
    Budget,
    Edge,
    GraphIR,
    GraphSpec,
    Node,
    NodeType,
    WorkflowPattern,
)
from orchestrator.planner.v0 import PlannerError, _coerce_glossary_term
from orchestrator.registry._common import LifecycleState, Metadata
from orchestrator.registry.calibration import CalibrationHistoryRepo, CalibrationStats
from orchestrator.registry.db.models import AgentTemplateRow, GlossaryEntryRow


class _StepChoice(BaseModel):
    template_id: str
    template_version: str
    node_id: str
    inputs_from: dict[str, str] = Field(default_factory=dict)


class _ManagerSpecialistChoice(BaseModel):
    template_id: str
    template_version: str
    node_id: str


class _InferredGlossaryEntry(BaseModel):
    """One inferred ambiguous term plus its planner-suggested definition."""

    value: str = Field(min_length=1, max_length=2048)
    reason: str = Field(default="", max_length=512)


class _Plan(BaseModel):
    pattern: str  # "single_agent" | "sequential" | "manager_specialists"
    template_id: str | None = None
    template_version: str | None = None
    steps: list[_StepChoice] = Field(default_factory=list)
    manager: _ManagerSpecialistChoice | None = None
    specialists: list[_ManagerSpecialistChoice] = Field(default_factory=list)
    parallelism_max: int = Field(default=4, ge=1)
    justification: str | None = None
    inferred_glossary: dict[str, _InferredGlossaryEntry] = Field(default_factory=dict)


class PlannerV1:
    def __init__(self, *, llm: LLMClient, default_model: str = "claude-opus-4-7") -> None:
        self._llm = llm
        self._default_model = default_model

    async def plan(
        self,
        objective: str,
        *,
        session: AsyncSession,
        glossary: dict[str, Any] | None = None,
        tag_filter: str | None = None,
        force_pattern: WorkflowPattern | None = None,
    ) -> GraphIR:
        candidates = await self._list_candidates(session, tag=tag_filter)
        if not candidates:
            raise PlannerError("no published agent templates available for planning")

        user_glossary = dict(glossary or {})
        org_glossary = await self._fetch_org_glossary(session, objective)

        # Single-candidate shortcut stays — no LLM needed.
        if len(candidates) == 1 and force_pattern is not WorkflowPattern.SEQUENTIAL:
            merged = _merge_glossaries(
                user_specified=user_glossary, org_default=org_glossary, planner_inferred={}
            )
            return _build_single_agent_ir(
                objective=objective,
                row=candidates[0],
                glossary=merged,
                justification="single published candidate",
            )

        calibration_stats = await CalibrationHistoryRepo(session).stats_for_candidates(
            [(c.id, c.version) for c in candidates]
        )
        plan = await self._select_with_llm(
            objective, candidates, force_pattern, org_glossary, calibration_stats
        )
        planner_inferred = {
            term: {"value": entry.value, "reason": entry.reason}
            for term, entry in plan.inferred_glossary.items()
        }
        merged = _merge_glossaries(
            user_specified=user_glossary,
            org_default=org_glossary,
            planner_inferred=planner_inferred,
        )

        if plan.pattern == "single_agent":
            row = _resolve(candidates, plan.template_id or "", plan.template_version or "")
            if row is None:
                raise PlannerError(
                    f"planner returned unknown template {plan.template_id}@{plan.template_version}"
                )
            return _build_single_agent_ir(
                objective=objective,
                row=row,
                glossary=merged,
                justification=plan.justification or "LLM-selected candidate",
            )

        if plan.pattern == "sequential":
            if len(plan.steps) < 2:
                raise PlannerError("sequential plan requires at least two steps")
            resolved: list[tuple[_StepChoice, AgentTemplateRow]] = []
            for step in plan.steps:
                row = _resolve(candidates, step.template_id, step.template_version)
                if row is None:
                    raise PlannerError(
                        f"planner returned unknown template {step.template_id}@{step.template_version}"
                    )
                resolved.append((step, row))
            return _build_sequential_ir(
                objective=objective,
                steps=resolved,
                glossary=merged,
                justification=plan.justification or "LLM-selected sequential plan",
            )

        if plan.pattern == "manager_specialists":
            if plan.manager is None:
                raise PlannerError("manager_specialists plan requires a manager")
            if len(plan.specialists) < 2:
                raise PlannerError("manager_specialists plan requires at least two specialists")
            manager_row = _resolve(candidates, plan.manager.template_id, plan.manager.template_version)
            if manager_row is None:
                raise PlannerError(
                    f"planner returned unknown manager template "
                    f"{plan.manager.template_id}@{plan.manager.template_version}"
                )
            specialist_rows: list[tuple[_ManagerSpecialistChoice, AgentTemplateRow]] = []
            for spec in plan.specialists:
                row = _resolve(candidates, spec.template_id, spec.template_version)
                if row is None:
                    raise PlannerError(
                        f"planner returned unknown specialist template "
                        f"{spec.template_id}@{spec.template_version}"
                    )
                specialist_rows.append((spec, row))
            return _build_manager_ir(
                objective=objective,
                manager=(plan.manager, manager_row),
                specialists=specialist_rows,
                parallelism_max=plan.parallelism_max,
                glossary=merged,
                justification=plan.justification or "LLM-selected manager plan",
            )

        raise PlannerError(f"planner returned unsupported pattern {plan.pattern!r}")

    async def replan(
        self,
        original_ir: GraphIR,
        *,
        session: AsyncSession,
        failing_node_id: str,
        failure_summary: dict[str, Any],
        replan_count: int,
    ) -> GraphIR:
        """Sprint 12.2: emit a revised IR after a verifier-chain replan request.

        The LLM gets the original IR, the failing node, and the verifier
        rationale, and emits a revised IR. Two strategies are explicit in the
        prompt:

          - ``modify_node``:   keep the topology, swap the failing node's
                               template_id / template_version / config.
          - ``replace_downstream``: rebuild the chain from the failing node
                                    onward. Allowed to change workflow_pattern.

        The caller is responsible for re-running the IR validator on the
        revised plan (it's the same ``IRValidator`` we use on first plans).
        """
        candidates = await self._list_candidates(session, tag=None)
        if not candidates:
            raise PlannerError("no published agent templates available for replan")

        failing_node = next((n for n in original_ir.spec.nodes if n.id == failing_node_id), None)
        if failing_node is None:
            raise PlannerError(f"replan: failing_node_id {failing_node_id!r} not in original IR")

        revised = await self._replan_with_llm(
            objective=original_ir.spec.objective,
            original_ir=original_ir,
            failing_node=failing_node,
            failure_summary=failure_summary,
            candidates=candidates,
            replan_count=replan_count,
        )

        if revised.pattern == "single_agent":
            row = _resolve(candidates, revised.template_id or "", revised.template_version or "")
            if row is None:
                raise PlannerError(
                    f"replan: unknown template {revised.template_id}@{revised.template_version}"
                )
            return _build_single_agent_ir(
                objective=original_ir.spec.objective,
                row=row,
                glossary=_glossary_terms_to_plain(original_ir.spec.task_glossary),
                justification=revised.justification or f"replan #{replan_count} (single_agent)",
            )

        if revised.pattern == "sequential":
            if len(revised.steps) < 2:
                raise PlannerError("replan: sequential plan requires at least two steps")
            resolved: list[tuple[_StepChoice, AgentTemplateRow]] = []
            for step in revised.steps:
                row = _resolve(candidates, step.template_id, step.template_version)
                if row is None:
                    raise PlannerError(f"replan: unknown template {step.template_id}@{step.template_version}")
                resolved.append((step, row))
            return _build_sequential_ir(
                objective=original_ir.spec.objective,
                steps=resolved,
                glossary=_glossary_terms_to_plain(original_ir.spec.task_glossary),
                justification=revised.justification or f"replan #{replan_count} (sequential)",
            )

        if revised.pattern == "manager_specialists":
            if revised.manager is None or len(revised.specialists) < 2:
                raise PlannerError("replan: manager_specialists requires a manager and >=2 specialists")
            manager_row = _resolve(candidates, revised.manager.template_id, revised.manager.template_version)
            if manager_row is None:
                raise PlannerError(
                    f"replan: unknown manager {revised.manager.template_id}"
                    f"@{revised.manager.template_version}"
                )
            specialist_rows: list[tuple[_ManagerSpecialistChoice, AgentTemplateRow]] = []
            for spec_choice in revised.specialists:
                row = _resolve(candidates, spec_choice.template_id, spec_choice.template_version)
                if row is None:
                    raise PlannerError(
                        f"replan: unknown specialist {spec_choice.template_id}@{spec_choice.template_version}"
                    )
                specialist_rows.append((spec_choice, row))
            return _build_manager_ir(
                objective=original_ir.spec.objective,
                manager=(revised.manager, manager_row),
                specialists=specialist_rows,
                parallelism_max=revised.parallelism_max,
                glossary=_glossary_terms_to_plain(original_ir.spec.task_glossary),
                justification=revised.justification or f"replan #{replan_count} (manager_specialists)",
            )

        raise PlannerError(f"replan: unsupported pattern {revised.pattern!r}")

    async def _replan_with_llm(
        self,
        *,
        objective: str,
        original_ir: GraphIR,
        failing_node: Node,
        failure_summary: dict[str, Any],
        candidates: list[AgentTemplateRow],
        replan_count: int,
    ) -> _Plan:
        catalogue = "\n".join(
            f"- {c.id}@{c.version}: {c.description}"
            f"\n    inputs: {[f['name'] for f in c.spec_json.get('inputs', [])]}"
            f"\n    outputs: {[f['name'] for f in c.spec_json.get('outputs', [])]}"
            f"\n    known_limitations: {c.spec_json.get('known_limitations', [])}"
            for c in candidates
        )
        original_summary = (
            f"pattern={original_ir.spec.workflow_pattern.value} "
            f"nodes={[(n.id, n.template_id, n.template_version) for n in original_ir.spec.nodes]}"
        )

        messages = [
            Message(
                role="system",
                content=(
                    "You are an orchestration replanner. The previous plan failed at "
                    f"node {failing_node.id!r} (template "
                    f"{failing_node.template_id}@{failing_node.template_version}). "
                    "Emit a revised plan in the same JSON shape the initial planner "
                    "uses (single_agent, sequential, or manager_specialists). Two "
                    "strategies are open to you:\n\n"
                    "  - modify_node: keep the same pattern, swap the failing node's "
                    "template or tweak its config.\n"
                    "  - replace_downstream: change the pattern entirely.\n\n"
                    "Whichever you choose, the response shape is identical to the "
                    "initial planner's. Include a one-sentence justification that "
                    "explains what changed and why it should clear the failure.\n"
                    "Output JSON only — no code fences, no commentary."
                ),
            ),
            Message(
                role="user",
                content=(
                    f"Objective: {objective}\n\n"
                    f"Replan attempt #{replan_count} (already exhausted "
                    f"{replan_count - 1} prior replans for this task).\n\n"
                    f"Original plan: {original_summary}\n\n"
                    f"Failing node config: {failing_node.config}\n\n"
                    f"Verifier summary:\n{json.dumps(failure_summary, indent=2, default=str)}\n\n"
                    f"Available templates:\n{catalogue}"
                ),
            ),
        ]
        result = await self._llm.complete(messages, model=self._default_model)
        try:
            payload = json.loads(result.text.strip())
        except json.JSONDecodeError as exc:
            raise PlannerError(f"replan LLM did not return JSON: {exc}") from exc
        return _Plan.model_validate(payload)

    async def _list_candidates(self, session: AsyncSession, *, tag: str | None) -> list[AgentTemplateRow]:
        stmt = select(AgentTemplateRow).where(AgentTemplateRow.status == LifecycleState.PUBLISHED.value)
        if tag is not None:
            stmt = stmt.where(AgentTemplateRow.tags.contains([tag]))
        result = await session.execute(stmt)
        return list(result.scalars().all())

    async def _fetch_org_glossary(self, session: AsyncSession, objective: str) -> dict[str, dict[str, str]]:
        """Pull org-pinned definitions for any term in the published glossary that
        appears in the objective (case-insensitive substring match).

        The planner doesn't need every glossary entry — only the ones the LLM
        might paraphrase or contradict in this run. Keeping the prompt scoped
        avoids token bloat as the org glossary grows.
        """
        stmt = select(GlossaryEntryRow).where(GlossaryEntryRow.status == LifecycleState.PUBLISHED.value)
        result = await session.execute(stmt)
        rows = list(result.scalars().all())
        objective_lower = objective.lower()
        out: dict[str, dict[str, str]] = {}
        for row in rows:
            term = row.id.lower()
            if term in objective_lower:
                spec = row.spec_json or {}
                out[row.id] = {
                    "value": str(spec.get("canonical_value", "")),
                    "definition": str(spec.get("definition", "")),
                    "source": "org_default",
                }
        return out

    async def _select_with_llm(
        self,
        objective: str,
        candidates: list[AgentTemplateRow],
        force_pattern: WorkflowPattern | None,
        org_glossary: dict[str, dict[str, str]],
        calibration_stats: dict[tuple[str, str], CalibrationStats],
    ) -> _Plan:
        catalogue = "\n".join(
            f"- {c.id}@{c.version}: {c.description}"
            f"\n    inputs: {[f['name'] for f in c.spec_json.get('inputs', [])]}"
            f"\n    outputs: {[f['name'] for f in c.spec_json.get('outputs', [])]}"
            f"\n    known_limitations: {c.spec_json.get('known_limitations', [])}"
            f"\n    tags: {list(c.tags)}"
            f"\n    calibration: {_render_calibration(calibration_stats.get((c.id, c.version)))}"
            for c in candidates
        )
        constraint = ""
        if force_pattern is WorkflowPattern.SEQUENTIAL:
            constraint = (
                "\n\nConstraint: the caller has pre-decided the workflow_pattern must be "
                '"sequential". Emit a sequential plan with two or more steps.'
            )
        elif force_pattern is WorkflowPattern.SINGLE_AGENT:
            constraint = (
                "\n\nConstraint: the caller has pre-decided the workflow_pattern must be "
                '"single_agent". Pick one template.'
            )
        elif force_pattern is WorkflowPattern.MANAGER_SPECIALISTS:
            constraint = (
                "\n\nConstraint: the caller has pre-decided the workflow_pattern must be "
                '"manager_specialists". Pick one manager template and two or more '
                "specialist templates."
            )

        messages = [
            Message(
                role="system",
                content=(
                    "You are an orchestration planner. Choose a workflow_pattern from "
                    "{single_agent, sequential, manager_specialists} for the user's "
                    "objective and emit a JSON plan.\n\n"
                    "Use sequential when the objective has distinct stages and the "
                    "outputs of one stage are the inputs of the next. Use "
                    "manager_specialists when the objective has multiple independent "
                    "subtasks that benefit from concurrent investigation by different "
                    "specialists, with a manager synthesising the results.\n\n"
                    "single_agent plan shape:\n"
                    '  {"pattern": "single_agent", "template_id": str, '
                    '"template_version": str, "justification": str}\n\n'
                    "sequential plan shape:\n"
                    '  {"pattern": "sequential", '
                    '"steps": [{"template_id": str, "template_version": str, '
                    '"node_id": str, "inputs_from": {"<input_field>": "<dotted_state_path>"}}, ...], '
                    '"justification": str}\n\n'
                    "manager_specialists plan shape:\n"
                    '  {"pattern": "manager_specialists", '
                    '"manager": {"template_id": str, "template_version": str, "node_id": str}, '
                    '"specialists": [{"template_id": str, "template_version": str, "node_id": str}, ...], '
                    '"parallelism_max": int (default 4), "justification": str}\n\n'
                    "Rules:\n"
                    "- node_id must be unique within the plan and match ^n_[a-z0-9_]+$.\n"
                    "- inputs_from values are dotted paths into the OrchestratorState: "
                    'e.g. "node_outputs.n_analyst.findings" or "task_metadata.audience".\n'
                    "- Every required input field on a downstream template must be "
                    "covered by inputs_from or read from task_metadata by name.\n"
                    "- manager_specialists requires >=2 specialists.\n"
                    "- Justification must be a single sentence for any shape.\n\n"
                    "Glossary inference: before picking a pattern, identify ambiguous "
                    "terms in the objective whose definition would change the plan or "
                    "an agent's answer (e.g. 'churn', 'ARR', 'active user'). For each "
                    "ambiguous term not already defined by the org glossary below, add "
                    'it to inferred_glossary as {"term": {"value": str, '
                    '"reason": str}}. Skip terms the org glossary already pins.\n\n'
                    "Plan envelope (added to every shape above):\n"
                    '  "inferred_glossary": {"<term>": {"value": str, "reason": str}}\n\n'
                    "Output JSON only — no code fences, no commentary."
                )
                + constraint,
            ),
            Message(
                role="user",
                content=(
                    f"Objective: {objective}\n\n"
                    f"Org glossary (pinned, do not redefine):\n{_render_org_glossary(org_glossary)}\n\n"
                    f"Candidates:\n{catalogue}"
                ),
            ),
        ]
        result = await self._llm.complete(messages, model=self._default_model)
        try:
            payload = json.loads(result.text.strip())
        except json.JSONDecodeError as exc:
            raise PlannerError(f"planner LLM did not return JSON: {exc}") from exc
        return _Plan.model_validate(payload)


def _resolve(candidates: list[AgentTemplateRow], template_id: str, version: str) -> AgentTemplateRow | None:
    for c in candidates:
        if c.id == template_id and c.version == version:
            return c
    return None


def _render_calibration(stats: CalibrationStats | None) -> str:
    """Render a candidate's calibration stats for the planner prompt.

    Empty / new-template case surfaces ``no_history`` so the LLM has an
    explicit signal that no prior runs back this candidate's track record.
    """
    if stats is None:
        return "no_history"
    return (
        f"n={stats.sample_count} pass_rate={stats.pass_rate:.0%} "
        f"mean_confidence={stats.mean_confidence:.0%} "
        f"gap={stats.calibration_gap:+.2f}"
    )


def _glossary_terms_to_plain(terms: dict[str, Any]) -> dict[str, Any]:
    """Convert ``dict[str, GlossaryTerm]`` (off an IR) into the shape
    ``_merge_glossaries`` consumes when we round-trip during replan.
    """
    out: dict[str, Any] = {}
    for term, entry in terms.items():
        if hasattr(entry, "value") and hasattr(entry, "source"):
            out[term] = {"value": entry.value, "source": entry.source}
        elif isinstance(entry, dict):
            out[term] = {
                "value": str(entry.get("value", "")),
                "source": str(entry.get("source", "unknown")),
            }
        else:
            out[term] = {"value": str(entry), "source": "user_specified"}
    return out


def _render_org_glossary(org: dict[str, dict[str, str]]) -> str:
    if not org:
        return "(none)"
    lines = []
    for term, entry in org.items():
        lines.append(f"- {term}: value={entry.get('value', '')!r} definition={entry.get('definition', '')!r}")
    return "\n".join(lines)


def _merge_glossaries(
    *,
    user_specified: dict[str, Any],
    org_default: dict[str, dict[str, str]],
    planner_inferred: dict[str, dict[str, str]],
) -> dict[str, Any]:
    """Sprint 11.3 priority: user_specified > org_default > planner_inferred.

    Each merged entry surfaces in the IR as ``{value, source}`` so the
    runtime + verifiers can read whose definition won.
    """
    merged: dict[str, Any] = {}
    for term, entry in planner_inferred.items():
        merged[term] = {"value": str(entry.get("value", "")), "source": "planner_inferred"}
    for term, entry in org_default.items():
        merged[term] = {"value": entry.get("value", ""), "source": "org_default"}
    for term, value in user_specified.items():
        if isinstance(value, dict):
            merged[term] = {
                "value": str(value.get("value", "")),
                "source": str(value.get("source", "user_specified")),
            }
        else:
            merged[term] = {"value": str(value), "source": "user_specified"}
    return merged


def _build_single_agent_ir(
    *,
    objective: str,
    row: AgentTemplateRow,
    glossary: dict[str, Any],
    justification: str,
) -> GraphIR:
    return GraphIR(
        metadata=Metadata(
            id="plan.single_agent",
            version="0.1.0",
            description=f"single_agent plan for {row.id}@{row.version}",
        ),
        spec=GraphSpec(
            objective=objective,
            workflow_pattern=WorkflowPattern.SINGLE_AGENT,
            task_glossary={k: _coerce_glossary_term(v) for k, v in glossary.items()},
            nodes=[
                Node(
                    id="n_agent",
                    type=NodeType.AGENT,
                    template_id=row.id,
                    template_version=row.version,
                    config={"justification": justification},
                )
            ],
            edges=[],
            budget=Budget(),
        ),
    )


def _build_manager_ir(
    *,
    objective: str,
    manager: tuple[_ManagerSpecialistChoice, AgentTemplateRow],
    specialists: list[tuple[_ManagerSpecialistChoice, AgentTemplateRow]],
    parallelism_max: int,
    glossary: dict[str, Any],
    justification: str,
) -> GraphIR:
    manager_choice, manager_row = manager
    nodes: list[Node] = [
        Node(
            id=manager_choice.node_id,
            type=NodeType.AGENT,
            template_id=manager_row.id,
            template_version=manager_row.version,
            config={
                "role": "manager",
                "parallelism_max": parallelism_max,
                "justification": justification,
            },
        )
    ]
    edges: list[Edge] = []
    for choice, row in specialists:
        nodes.append(
            Node(
                id=choice.node_id,
                type=NodeType.AGENT,
                template_id=row.id,
                template_version=row.version,
                config={"role": "specialist"},
            )
        )
        # No IR edges between manager and specialists — the topology lives in
        # node.config.role and is enforced by the IR validator's pattern check.
        # This keeps the GraphIR DAG-clean while letting the runtime build the
        # actual manager-dispatch -> specialists -> manager-synthesize cycle.

    return GraphIR(
        metadata=Metadata(
            id="plan.manager_specialists",
            version="0.1.0",
            description=f"manager_specialists plan: 1 manager + {len(specialists)} specialists",
        ),
        spec=GraphSpec(
            objective=objective,
            workflow_pattern=WorkflowPattern.MANAGER_SPECIALISTS,
            task_glossary={k: _coerce_glossary_term(v) for k, v in glossary.items()},
            nodes=nodes,
            edges=edges,
            budget=Budget(),
            constraints={"split_justification": justification, "parallelism_max": parallelism_max},
        ),
    )


def _build_sequential_ir(
    *,
    objective: str,
    steps: list[tuple[_StepChoice, AgentTemplateRow]],
    glossary: dict[str, Any],
    justification: str,
) -> GraphIR:
    nodes: list[Node] = []
    edges: list[Edge] = []
    prev_id: str | None = None
    for step, row in steps:
        nodes.append(
            Node(
                id=step.node_id,
                type=NodeType.AGENT,
                template_id=row.id,
                template_version=row.version,
                config={
                    "justification": justification,
                    "inputs_from": dict(step.inputs_from),
                },
            )
        )
        if prev_id is not None:
            edges.append(Edge(source=prev_id, target=step.node_id))
        prev_id = step.node_id

    return GraphIR(
        metadata=Metadata(
            id="plan.sequential",
            version="0.1.0",
            description=f"sequential plan with {len(steps)} steps",
        ),
        spec=GraphSpec(
            objective=objective,
            workflow_pattern=WorkflowPattern.SEQUENTIAL,
            task_glossary={k: _coerce_glossary_term(v) for k, v in glossary.items()},
            nodes=nodes,
            edges=edges,
            budget=Budget(),
            constraints={"split_justification": justification},
        ),
    )
