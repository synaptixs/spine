"""LangGraph node that runs a VerifierChain and dispatches on the result.

Sprint 10.6 + 10.8. The node:

1. Reads the upstream agent's output from ``state.node_outputs[target]``.
2. Runs the configured ``VerifierChain``.
3. Applies the ``FailurePolicy`` via the on-failure dispatcher.
4. Writes the chain result + dispatch decision into
   ``state.node_outputs[verifier_id]`` so the audit log captures every
   verifier's outcome plus the routing decision in one place.
5. Optionally writes one ``audit_log`` row per verifier execution when
   an ``AuditLogger`` is supplied (Sprint 10.8). The runtime injects the
   logger via a callable so this module doesn't depend on the registry
   database session directly.

Termination is signalled by ``state.current_node_id == "__terminate__"``
plus ``state.node_outputs[verifier_id].dispatch.next_step == "terminate"``.
The graph builder reads this to decide whether to route to END or continue
to the next configured node. (Sprint 10 ships the contract; full
multi-edge fan-out lives in Sprint 11+.)
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from orchestrator.registry.agent_template import AgentTemplate
from orchestrator.runtime.artifacts import ArtifactStore
from orchestrator.runtime.failure_dispatch import FailurePolicy, NextStep, dispatch
from orchestrator.runtime.verifiers import (
    VerifierChain,
    VerifierContext,
)

# Signature: ``await audit_logger(verifier_id, outcome, elapsed_ms, payload, trace_id, task_id)``.
# Returning ``None`` keeps the contract async-friendly.
AuditLogger = Callable[[str, str, float, dict[str, Any], str | None, str | None], Awaitable[None]]


class VerifierChainNode:
    """One per-edge chain runner backed by a ``VerifierChain``."""

    def __init__(
        self,
        *,
        template: AgentTemplate,
        chain: VerifierChain,
        target_node: str,
        verifier_id: str | None = None,
        policy: FailurePolicy | None = None,
        artifact_store: ArtifactStore | None = None,
        audit_logger: AuditLogger | None = None,
    ) -> None:
        self._template = template
        self._chain = chain
        self._target_node = target_node
        self._verifier_id = verifier_id or f"chain_{target_node}"
        self._policy = policy or FailurePolicy()
        self._artifact_store = artifact_store
        self._audit_logger = audit_logger

    @property
    def verifier_id(self) -> str:
        return self._verifier_id

    async def __call__(self, state: dict[str, Any]) -> dict[str, Any]:
        upstream_outputs = (state.get("node_outputs") or {}).get(self._target_node, {})
        task_metadata = state.get("task_metadata") or {}
        task_id = str(task_metadata.get("task_id") or "")
        trace_id = str(task_metadata.get("trace_id") or "") or None

        ctx = VerifierContext(
            template=self._template,
            node_id=self._target_node,
            task_id=task_id,
            trace_id=trace_id or "",
            artifact_store=self._artifact_store,
            task_glossary=dict(state.get("task_glossary") or {}),
        )

        chain_result = await self._chain.run(upstream_outputs, ctx)
        decision = dispatch(chain_result, policy=self._policy)

        # Sprint 10.8: one audit row per verifier execution, plus one for the
        # aggregate dispatch decision.
        if self._audit_logger is not None:
            for vid, per in chain_result.per_verifier.items():
                await self._audit_logger(
                    vid,
                    per.outcome.value,
                    per.elapsed_ms,
                    per.to_state_value(),
                    trace_id,
                    task_id or None,
                )
            await self._audit_logger(
                self._verifier_id,
                chain_result.outcome.value,
                chain_result.aggregate.elapsed_ms,
                {
                    "chain": chain_result.to_state_value(),
                    "dispatch": decision.to_state_value(),
                },
                trace_id,
                task_id or None,
            )

        state_value = {
            "verifier_id": self._verifier_id,
            "outcome": chain_result.outcome.value,
            "chain": chain_result.to_state_value(),
            "dispatch": decision.to_state_value(),
            "failures": chain_result.to_state_value()["failures"],
        }

        # Sprint 12.1: when the dispatcher decides REPLAN, emit a structured
        # request the orchestration layer reads after invoke returns. We can't
        # do the replan from inside the graph (the planner needs a DB session
        # + the chance to rebuild the graph), so we surface intent in state
        # and rely on the /v1/tasks loop to act on it.
        if decision.next_step is NextStep.REPLAN:
            state_value["replan_request"] = {
                "failing_node": self._target_node,
                "verifier_id": self._verifier_id,
                "outcome": chain_result.outcome.value,
                "rationale": decision.rationale,
                "failures": chain_result.to_state_value()["failures"],
            }

        if decision.next_step is NextStep.TERMINATE:
            sentinel: str = "__terminate__"
        elif decision.next_step is NextStep.REPLAN:
            sentinel = "__replan__"
        else:
            sentinel = self._verifier_id

        update: dict[str, Any] = {
            "node_outputs": {self._verifier_id: state_value},
            "current_node_id": sentinel,
        }
        return update
