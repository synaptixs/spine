"""Planner v0: pick a single agent template for an objective and emit a GraphIR.

Walking-skeleton planner. Constrained to ``workflow_pattern=single_agent``.
Behaviour:

- 0 published candidates → ``PlannerError``.
- 1 published candidate → use it (no LLM call needed).
- 2+ candidates → one LLM call returns a chosen ``(template_id, version)``.

The output is a typed ``GraphIR`` ready for the IR validator. Sequential,
manager-with-specialists, router, and mixture patterns land in later sprints
along with their planner logic.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from orchestrator.core.llm import LLMClient, Message
from orchestrator.ir.graph import (
    Budget,
    GlossaryTerm,
    GraphIR,
    GraphSpec,
    Node,
    NodeType,
    WorkflowPattern,
)
from orchestrator.registry._common import LifecycleState, Metadata
from orchestrator.registry.db.models import AgentTemplateRow


class PlannerError(RuntimeError):
    """Raised when planning cannot produce a valid GraphIR."""


class _Choice(BaseModel):
    template_id: str
    template_version: str
    justification: str | None = None


class PlannerV0:
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
    ) -> GraphIR:
        candidates = await self._list_candidates(session, tag=tag_filter)
        if not candidates:
            raise PlannerError("no published agent templates available for planning")

        if len(candidates) == 1:
            chosen = candidates[0]
            justification = "single published candidate"
        else:
            choice = await self._select_with_llm(objective, candidates)
            match = next(
                (
                    c
                    for c in candidates
                    if c.id == choice.template_id and c.version == choice.template_version
                ),
                None,
            )
            if match is None:
                raise PlannerError(
                    f"planner returned unknown template {choice.template_id}@{choice.template_version}"
                )
            chosen = match
            justification = choice.justification or "LLM-selected candidate"

        return _build_single_agent_ir(
            objective=objective,
            row=chosen,
            glossary=glossary or {},
            justification=justification,
        )

    async def _list_candidates(self, session: AsyncSession, *, tag: str | None) -> list[AgentTemplateRow]:
        stmt = select(AgentTemplateRow).where(AgentTemplateRow.status == LifecycleState.PUBLISHED.value)
        if tag is not None:
            stmt = stmt.where(AgentTemplateRow.tags.contains([tag]))
        result = await session.execute(stmt)
        return list(result.scalars().all())

    async def _select_with_llm(self, objective: str, candidates: list[AgentTemplateRow]) -> _Choice:
        catalogue = "\n".join(
            f"- {c.id}@{c.version}: {c.description}"
            f"\n    known_limitations: {c.spec_json.get('known_limitations', [])}"
            f"\n    tags: {list(c.tags)}"
            for c in candidates
        )
        messages = [
            Message(
                role="system",
                content=(
                    "You are a planner. Given a user objective and a catalogue of agent "
                    "templates, pick the single template most fit for purpose. Respond "
                    "with one JSON object: "
                    '{"template_id": str, "template_version": str, "justification": str}. '
                    "No code fences. No commentary."
                ),
            ),
            Message(
                role="user",
                content=f"Objective: {objective}\n\nCandidates:\n{catalogue}",
            ),
        ]
        result = await self._llm.complete(messages, model=self._default_model, temperature=0.0)
        try:
            payload = json.loads(result.text.strip())
        except json.JSONDecodeError as exc:
            raise PlannerError(f"planner LLM did not return JSON: {exc}") from exc
        return _Choice.model_validate(payload)


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


def _coerce_glossary_term(value: Any) -> GlossaryTerm:
    """Glossary entries accept a bare string or a {value, source} dict; normalise."""
    if isinstance(value, GlossaryTerm):
        return value
    if isinstance(value, dict):
        return GlossaryTerm(
            value=str(value.get("value", "")),
            source=str(value.get("source", "planner")),
        )
    return GlossaryTerm(value=str(value), source="user_specified")
