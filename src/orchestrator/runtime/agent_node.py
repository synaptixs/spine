"""SingleAgentNode: one LLM call against the agent template's contract.

The same class serves the single-agent pattern and the sequential pattern's
intermediate nodes. Sequential graphs pass a ``node_id`` and an
``inputs_from`` mapping per node so each agent reads its inputs from
prior nodes' outputs (or from initial task metadata) via state-channel
dotted paths.

A tool-using ReAct-style loop lands in Sprint 9 (manager-with-specialists).
"""

from __future__ import annotations

import json
import re
from typing import Any

from orchestrator.core.llm import LLMClient, Message
from orchestrator.core.prompt_safety import fence_untrusted
from orchestrator.registry.agent_template import AgentTemplate


class AgentNodeError(RuntimeError):
    """Raised when the agent node can't construct a valid output."""


class SingleAgentNode:
    def __init__(
        self,
        template: AgentTemplate,
        llm: LLMClient,
        *,
        node_id: str = "agent",
        inputs_from: dict[str, str] | None = None,
    ) -> None:
        self._template = template
        self._llm = llm
        self._node_id = node_id
        # Maps each declared input field of the template to a dotted path in
        # the OrchestratorState. e.g. ``{"findings": "node_outputs.n_analyst.findings"}``.
        # Defaults to reading from task_metadata.<input_name>.
        self._inputs_from = dict(inputs_from or {})

    @property
    def node_id(self) -> str:
        return self._node_id

    async def __call__(self, state: dict[str, Any]) -> dict[str, Any]:
        objective = _objective_from_state(state)
        glossary = state.get("task_glossary") or {}
        resolved_inputs = self._resolve_inputs(state)

        messages = [
            Message(role="system", content=self._build_system_prompt(glossary)),
            Message(role="user", content=self._build_user_message(objective, resolved_inputs)),
        ]

        # Only forward temperature when the template explicitly requests it.
        # Newer reasoning models (claude-opus-4-7 et al.) reject the parameter.
        constraint_temp = self._template.spec.constraints.get("temperature")
        kwargs: dict[str, Any] = {"model": self._template.spec.model}
        if constraint_temp is not None:
            kwargs["temperature"] = float(constraint_temp)
        result = await self._llm.complete(messages, **kwargs)
        output = _parse_json_object(result.text)

        claims = list(output.get("claims") or [])
        confidence = output.get("confidence")
        confidence_history: list[dict[str, Any]] = (
            [{"node": self._node_id, "value": float(confidence)}]
            if isinstance(confidence, (int, float))
            else []
        )

        return {
            "node_outputs": {self._node_id: output},
            "claims": claims,
            "confidence_history": confidence_history,
            "budget_consumed": {
                "tokens": result.prompt_tokens + result.completion_tokens,
                "cost_usd": result.cost_usd,
            },
            "current_node_id": self._node_id,
        }

    def _resolve_inputs(self, state: dict[str, Any]) -> dict[str, Any]:
        resolved: dict[str, Any] = {}
        required_str_inputs = [f for f in self._template.spec.inputs if f.required and f.type == "str"]
        for field in self._template.spec.inputs:
            path = self._inputs_from.get(field.name, f"task_metadata.{field.name}")
            value = _read_path(state, path)
            if value is None and field.required:
                # Affordance: when a template has exactly one required str input
                # and the caller only sent ``objective`` (the common /v1/tasks
                # shape), feed the objective into that slot. Avoids forcing every
                # caller to know each template's input field name.
                if (
                    field in required_str_inputs
                    and len(required_str_inputs) == 1
                    and field.name not in self._inputs_from
                ):
                    objective = _read_path(state, "task_metadata.objective")
                    if isinstance(objective, str) and objective.strip():
                        resolved[field.name] = objective
                        continue
                raise AgentNodeError(
                    f"agent {self._template.metadata.id}: required input {field.name!r} "
                    f"not resolvable from path {path!r}"
                )
            if value is not None:
                resolved[field.name] = value
        return resolved

    def _build_user_message(self, objective: str, inputs: dict[str, Any]) -> str:
        if not inputs:
            return objective
        rendered = json.dumps(inputs, indent=2, default=str)
        return f"Objective: {objective}\n\nInputs:\n{rendered}"

    def _build_system_prompt(self, glossary: dict[str, Any]) -> str:
        required = [
            f"- {f.name} ({f.type})" + ("" if f.required else " [optional]")
            for f in self._template.spec.outputs
        ]
        glossary_block = ""
        if glossary:
            terms = "\n".join(
                f"- {k}: {v.get('value', v) if isinstance(v, dict) else v}" for k, v in glossary.items()
            )
            # Glossary values can originate from request task_metadata, so they are
            # untrusted. Fence them as term definitions rather than labelling them
            # "authoritative" — otherwise an injected value steers the system prompt.
            glossary_block = "\n\n" + fence_untrusted("glossary (pinned term definitions)", terms)
        custom_prompt = self._template.spec.constraints.get("system_prompt", "")
        base = (
            f"You are agent {self._template.metadata.id} v{self._template.metadata.version}. "
            f"{self._template.metadata.description}"
        )
        return (
            f"{base}\n\n{custom_prompt}\n\n"
            "Respond with a single JSON object satisfying the schema below. "
            "Do not wrap the JSON in code fences. Do not add commentary.\n\n"
            f"Required output fields:\n{chr(10).join(required)}"
            f"{glossary_block}"
        )


def _objective_from_state(state: dict[str, Any]) -> str:
    metadata = state.get("task_metadata") or {}
    objective = metadata.get("objective")
    if not isinstance(objective, str) or not objective.strip():
        raise AgentNodeError("OrchestratorState.task_metadata.objective is required")
    return objective


def _read_path(state: dict[str, Any], path: str) -> Any:
    """Dotted-path read into the state. Missing keys return None (not an error)."""
    parts = [p for p in path.split(".") if p]
    cursor: Any = state
    for part in parts:
        if isinstance(cursor, dict) and part in cursor:
            cursor = cursor[part]
        else:
            return None
    return cursor


_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


def _parse_json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*|\s*```$", "", stripped, flags=re.DOTALL).strip()
    try:
        loaded = json.loads(stripped)
    except json.JSONDecodeError:
        match = _JSON_OBJECT_RE.search(stripped)
        if not match:
            raise AgentNodeError("agent output was not valid JSON")  # noqa: B904
        try:
            loaded = json.loads(match.group(0))
        except json.JSONDecodeError as exc:
            raise AgentNodeError(f"agent output JSON parse failed: {exc}") from exc
    if not isinstance(loaded, dict):
        raise AgentNodeError("agent output must be a JSON object at the top level")
    return loaded
