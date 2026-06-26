"""The set of published personas (unified UI P1 — personas read).

A single place to enumerate the persona ``AgentTemplate``s the orchestrator
offers, so the API (``GET /v1/personas``), the delegation inbox's persona picker,
and a future personas browser all read from one list. As more personas land
(QA, PM, …) they join here.
"""

from __future__ import annotations

from orchestrator.personas.software_engineer import SOFTWARE_ENGINEER
from orchestrator.registry.agent_template import AgentTemplate

ALL_PERSONAS: tuple[AgentTemplate, ...] = (SOFTWARE_ENGINEER,)


def get_persona(persona_id: str) -> AgentTemplate | None:
    """The persona with ``metadata.id == persona_id``, or ``None``."""
    return next((p for p in ALL_PERSONAS if p.metadata.id == persona_id), None)


__all__ = ["ALL_PERSONAS", "get_persona"]
