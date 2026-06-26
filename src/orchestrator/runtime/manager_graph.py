"""Manager-with-specialists graph builder.

Topology (three LangGraph nodes):

  manager_dispatch  →  run_specialists  →  manager_synthesize  →  verify  →  END

- ``manager_dispatch`` is one LLM call: the manager template emits a
  ``DispatchPlan`` listing which specialists to run and what inputs each
  receives.
- ``run_specialists`` runs every specialist concurrently (or serially when
  ``parallelism_max == 1``), each as an isolated SingleAgentNode invocation
  with its own slice of state. Each specialist's full output is persisted
  to the artifact store; a ``SpecialistReturn`` is appended to a fixed
  state channel so the manager only ever sees small payloads.
- ``manager_synthesize`` is a second LLM call: the manager fits glossary,
  handoffs, and each ``SpecialistReturn`` into its ``ContextBudget`` and
  produces the final structured output.
- A terminal ``SchemaVerifierNode`` checks the manager's synthesis output
  against its template's declared schema.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from langgraph.graph import END, START, StateGraph

from orchestrator.core.llm import LLMClient, Message
from orchestrator.core.state import OrchestratorState
from orchestrator.registry.agent_template import AgentTemplate
from orchestrator.runtime.agent_node import (
    AgentNodeError,
    SingleAgentNode,
    _parse_json_object,
)
from orchestrator.runtime.artifacts import (
    ArtifactStore,
    InMemoryArtifactStore,
    make_artifact_id,
)
from orchestrator.runtime.post_conditions import MinConfidenceRule, PostCondition
from orchestrator.runtime.specialist import (
    ClaimSummary,
    CompletionStatus,
    ContextBudget,
    SpecialistReturn,
    fit_to_budget,
)
from orchestrator.runtime.verifier import SchemaVerifierNode


@dataclass(frozen=True)
class SpecialistSpec:
    """One specialist available to the manager.

    ``node_id`` is the LangGraph node id and the key under which the
    specialist's full output lands in artifact storage. ``handoffs``
    captures inter-specialist links the manager planned at dispatch time.
    """

    node_id: str
    template: AgentTemplate
    post_conditions: list[PostCondition] = field(default_factory=list)
    min_confidence: MinConfidenceRule | None = None


@dataclass(frozen=True)
class ManagerSpec:
    """The manager template plus its budget and parallelism cap."""

    node_id: str
    template: AgentTemplate
    context_budget: ContextBudget = field(default_factory=ContextBudget)
    parallelism_max: int = 4


@dataclass(frozen=True)
class DispatchedSpecialist:
    """A specialist the manager picked up at dispatch."""

    specialist_id: str
    inputs: dict[str, Any]
    handoff_from: str | None = None


def build_manager_specialists_graph(
    *,
    manager: ManagerSpec,
    specialists: list[SpecialistSpec],
    llm: LLMClient,
    artifact_store: ArtifactStore | None = None,
    checkpointer: Any = None,
) -> Any:
    """Compile a manager-with-specialists graph."""
    if not specialists:
        raise ValueError("build_manager_specialists_graph: at least one specialist is required")
    ids = [s.node_id for s in specialists]
    if len(ids) != len(set(ids)):
        raise ValueError(f"manager graph: duplicate specialist node_ids in {ids}")
    if manager.parallelism_max < 1:
        raise ValueError("manager graph: parallelism_max must be >= 1")

    store: ArtifactStore = artifact_store or InMemoryArtifactStore()
    specialist_by_id = {s.node_id: s for s in specialists}

    dispatch_node = _ManagerDispatchNode(manager=manager, specialists=specialists, llm=llm)
    run_node = _RunSpecialistsNode(
        manager=manager,
        specialist_by_id=specialist_by_id,
        llm=llm,
        artifact_store=store,
    )
    synth_node = _ManagerSynthesizeNode(manager=manager, llm=llm)
    verify_node = SchemaVerifierNode(
        manager.template,
        target_node=manager.node_id,
        verifier_id=f"verify_{manager.node_id}",
    )

    builder: StateGraph[OrchestratorState] = StateGraph(OrchestratorState)
    builder.add_node("manager_dispatch", dispatch_node)  # type: ignore[type-var]
    builder.add_node("run_specialists", run_node)  # type: ignore[type-var]
    builder.add_node(manager.node_id, synth_node)  # type: ignore[type-var]
    builder.add_node(f"verify_{manager.node_id}", verify_node)  # type: ignore[type-var]

    builder.add_edge(START, "manager_dispatch")
    builder.add_edge("manager_dispatch", "run_specialists")
    builder.add_edge("run_specialists", manager.node_id)
    builder.add_edge(manager.node_id, f"verify_{manager.node_id}")
    builder.add_edge(f"verify_{manager.node_id}", END)
    return builder.compile(checkpointer=checkpointer)


# --- Internal node implementations ----------------------------------------


class _ManagerDispatchNode:
    """LLM call: pick which specialists to run and what each gets as inputs."""

    def __init__(
        self,
        *,
        manager: ManagerSpec,
        specialists: list[SpecialistSpec],
        llm: LLMClient,
    ) -> None:
        self._manager = manager
        self._specialists = specialists
        self._llm = llm

    async def __call__(self, state: dict[str, Any]) -> dict[str, Any]:
        objective = _objective_from_state(state)
        catalogue = "\n".join(
            f"- {s.node_id}: {s.template.metadata.id}@{s.template.metadata.version}"
            f"\n    description: {s.template.metadata.description.strip()}"
            f"\n    required_inputs: "
            f"{[f.name for f in s.template.spec.inputs if f.required]}"
            for s in self._specialists
        )
        messages = [
            Message(
                role="system",
                content=(
                    f"You are the manager agent {self._manager.template.metadata.id}. "
                    "Decompose the user objective into specialist dispatches.\n\n"
                    "Respond with a single JSON object:\n"
                    '  {"dispatches": [{"specialist_id": str, '
                    '"inputs": {...}, "handoff_from": str | null}, ...]}\n\n'
                    "Rules:\n"
                    "- specialist_id must match one of the catalogue ids below.\n"
                    "- inputs must satisfy the specialist's required_inputs.\n"
                    "- handoff_from is optional; it points at an earlier "
                    "specialist_id whose artifact the new specialist should read.\n"
                    "Output JSON only — no code fences, no commentary."
                ),
            ),
            Message(
                role="user",
                content=(f"Objective: {objective}\n\nSpecialist catalogue:\n{catalogue}"),
            ),
        ]
        result = await self._llm.complete(messages, model=self._manager.template.spec.model)
        plan_raw = _parse_json_object(result.text)
        dispatches: list[dict[str, Any]] = list(plan_raw.get("dispatches") or [])
        known_ids = {s.node_id for s in self._specialists}
        for d in dispatches:
            sid = d.get("specialist_id")
            if sid not in known_ids:
                raise AgentNodeError(
                    f"manager_dispatch: unknown specialist_id {sid!r} (catalogue: {sorted(known_ids)})"
                )

        return {
            "node_outputs": {"manager_dispatch": {"dispatches": dispatches}},
            "current_node_id": "manager_dispatch",
            "budget_consumed": {
                "tokens": result.prompt_tokens + result.completion_tokens,
                "cost_usd": result.cost_usd,
            },
        }


class _RunSpecialistsNode:
    """Run each dispatched specialist with bounded concurrency. Writes full
    outputs to the artifact store and appends a small SpecialistReturn per
    specialist into ``node_outputs``."""

    def __init__(
        self,
        *,
        manager: ManagerSpec,
        specialist_by_id: dict[str, SpecialistSpec],
        llm: LLMClient,
        artifact_store: ArtifactStore,
    ) -> None:
        self._manager = manager
        self._specialist_by_id = specialist_by_id
        self._llm = llm
        self._store = artifact_store

    async def __call__(self, state: dict[str, Any]) -> dict[str, Any]:
        dispatches = ((state.get("node_outputs") or {}).get("manager_dispatch") or {}).get("dispatches") or []
        if not dispatches:
            raise AgentNodeError("run_specialists: manager_dispatch produced no dispatches")

        task_metadata = state.get("task_metadata") or {}
        task_id = str(task_metadata.get("task_id", "unknown"))
        glossary = state.get("task_glossary") or {}

        semaphore = asyncio.Semaphore(max(1, self._manager.parallelism_max))

        async def _run_one(dispatch: dict[str, Any]) -> tuple[str, dict[str, Any], SpecialistReturn]:
            specialist_id = str(dispatch["specialist_id"])
            spec = self._specialist_by_id[specialist_id]
            inputs = dict(dispatch.get("inputs") or {})

            async with semaphore:
                # Build an isolated state slice for this specialist so it
                # cannot observe other specialists' partial outputs.
                isolated_state: dict[str, Any] = {
                    "task_metadata": {**task_metadata, **inputs},
                    "task_glossary": glossary,
                }
                node = SingleAgentNode(
                    spec.template,
                    self._llm,
                    node_id=specialist_id,
                    inputs_from={k: f"task_metadata.{k}" for k in inputs},
                )
                update = await node(isolated_state)

            full_output = (update.get("node_outputs") or {}).get(specialist_id) or {}
            artifact_id = make_artifact_id(task_id=task_id, node_id=specialist_id)
            await self._store.put_json(artifact_id, full_output)
            summary = _summarise(specialist_id, full_output, artifact_id)
            return specialist_id, full_output, summary

        results = await asyncio.gather(*[_run_one(d) for d in dispatches])

        specialist_returns: list[dict[str, Any]] = []
        full_outputs: dict[str, Any] = {}
        budget_tokens = 0
        budget_cost = 0.0
        for specialist_id, full_output, ret in results:
            specialist_returns.append(ret.model_dump(mode="json"))
            full_outputs[specialist_id] = full_output
            budget_tokens += int(full_output.get("__tokens__", 0) or 0)
            budget_cost += float(full_output.get("__cost_usd__", 0.0) or 0.0)

        return {
            "node_outputs": {
                "run_specialists": {
                    "specialist_returns": specialist_returns,
                    "specialist_outputs": full_outputs,  # kept in state for the verifier to spot-check
                }
            },
            "artifacts": {ret["specialist_id"]: ret["artifact_id"] for ret in specialist_returns},
            "current_node_id": "run_specialists",
            "budget_consumed": {"tokens": budget_tokens, "cost_usd": budget_cost},
        }


class _ManagerSynthesizeNode:
    """LLM call: read the specialist returns + glossary and produce the manager's final output."""

    def __init__(self, *, manager: ManagerSpec, llm: LLMClient) -> None:
        self._manager = manager
        self._llm = llm

    async def __call__(self, state: dict[str, Any]) -> dict[str, Any]:
        objective = _objective_from_state(state)
        glossary = state.get("task_glossary") or {}
        run = (state.get("node_outputs") or {}).get("run_specialists") or {}
        specialist_returns: list[dict[str, Any]] = list(run.get("specialist_returns") or [])

        sections: list[tuple[str, str, int]] = []
        if self._manager.context_budget.pin_glossary and glossary:
            sections.append(("glossary", _render_glossary(glossary), 0))
        for ret in specialist_returns:
            sections.append(
                (
                    f"specialist:{ret['specialist_id']}",
                    _render_specialist_return(ret),
                    2,
                )
            )

        fitted, budget_stats = fit_to_budget(sections, self._manager.context_budget)

        system_prompt = (
            f"You are agent {self._manager.template.metadata.id} "
            f"v{self._manager.template.metadata.version}. "
            f"{self._manager.template.metadata.description.strip()}\n\n"
            "Synthesize a final answer from the specialist returns below. "
            "Cite specialist_id alongside any claim you adopt. Treat each "
            "specialist's artifact_id as opaque — you do not fetch it here.\n\n"
            "Respond with a single JSON object satisfying the schema below. "
            "Do not wrap the JSON in code fences. Do not add commentary.\n\n"
            f"Required output fields:\n{_render_required_outputs(self._manager.template)}"
        )
        user_message = f"Objective: {objective}\n\n" + "\n\n".join(
            f"## {name}\n{body}" for name, body in fitted
        )
        if budget_stats["dropped_count"]:
            user_message += (
                f"\n\n[note: {budget_stats['dropped_count']} section(s) dropped "
                f"due to context budget: {budget_stats['dropped_names']}]"
            )

        constraint_temp = self._manager.template.spec.constraints.get("temperature")
        kwargs: dict[str, Any] = {"model": self._manager.template.spec.model}
        if constraint_temp is not None:
            kwargs["temperature"] = float(constraint_temp)
        result = await self._llm.complete(
            [Message(role="system", content=system_prompt), Message(role="user", content=user_message)],
            **kwargs,
        )
        output = _parse_json_object(result.text)

        confidence = output.get("confidence")
        confidence_history: list[dict[str, Any]] = (
            [{"node": self._manager.node_id, "value": float(confidence)}]
            if isinstance(confidence, (int, float))
            else []
        )

        return {
            "node_outputs": {
                self._manager.node_id: output,
                "manager_context": budget_stats,
            },
            "claims": list(output.get("claims") or []),
            "confidence_history": confidence_history,
            "budget_consumed": {
                "tokens": result.prompt_tokens + result.completion_tokens,
                "cost_usd": result.cost_usd,
            },
            "current_node_id": self._manager.node_id,
        }


# --- Helpers --------------------------------------------------------------


def _objective_from_state(state: dict[str, Any]) -> str:
    metadata = state.get("task_metadata") or {}
    objective = metadata.get("objective")
    if not isinstance(objective, str) or not objective.strip():
        raise AgentNodeError("OrchestratorState.task_metadata.objective is required")
    return objective


def _summarise(specialist_id: str, full_output: dict[str, Any], artifact_id: str) -> SpecialistReturn:
    claims = list(full_output.get("claims") or [])[:8]
    key_claims = [
        ClaimSummary(
            id=str(c.get("id", f"c_{specialist_id}_{i}")),
            statement=str(c.get("statement", ""))[:1024] or "(unstated)",
            artifact_id=artifact_id,
            confidence=float(c.get("confidence", 0.0))
            if isinstance(c.get("confidence"), (int, float))
            else 0.0,
        )
        for i, c in enumerate(claims)
        if isinstance(c, dict)
    ]
    summary_text = str(
        full_output.get("findings")
        or full_output.get("narrative")
        or full_output.get("summary")
        or "(no narrative)"
    )[:2048]
    confidence = (
        float(full_output.get("confidence", 0.0))
        if isinstance(full_output.get("confidence"), (int, float))
        else 0.0
    )
    caveats = [str(c) for c in (full_output.get("caveats") or []) if c]
    status = CompletionStatus.SUCCESS
    if not summary_text or summary_text == "(no narrative)":
        status = CompletionStatus.PARTIAL
    return SpecialistReturn(
        specialist_id=specialist_id,
        artifact_id=artifact_id,
        summary=summary_text or "(no narrative)",
        key_claims=key_claims,
        confidence=max(0.0, min(1.0, confidence)),
        caveats=caveats,
        completion_status=status,
    )


def _render_glossary(glossary: dict[str, Any]) -> str:
    lines = []
    for term, value in glossary.items():
        v = value.get("value", "") if isinstance(value, dict) else value
        lines.append(f"- {term}: {v}")
    return "\n".join(lines)


def _render_specialist_return(ret: dict[str, Any]) -> str:
    parts: list[str] = [
        f"specialist_id: {ret['specialist_id']}",
        f"artifact_id: {ret['artifact_id']}",
        f"confidence: {ret['confidence']}",
        f"completion_status: {ret['completion_status']}",
        "",
        ret["summary"],
    ]
    if ret.get("key_claims"):
        parts.append("")
        parts.append("Key claims:")
        for c in ret["key_claims"]:
            parts.append(f"  - {c['id']}: {c['statement']} (confidence {c['confidence']})")
    if ret.get("caveats"):
        parts.append("")
        parts.append("Caveats:")
        for cav in ret["caveats"]:
            parts.append(f"  - {cav}")
    return "\n".join(parts)


def _render_required_outputs(template: AgentTemplate) -> str:
    return "\n".join(
        f"- {f.name} ({f.type})" + ("" if f.required else " [optional]") for f in template.spec.outputs
    )


__all__ = [
    "DispatchedSpecialist",
    "ManagerSpec",
    "SpecialistSpec",
    "build_manager_specialists_graph",
]
