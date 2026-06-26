"""OntomeshGrounder — domain-grounded build, with citations (Phase 1, Seam 1).

Implements the orchestrator's ``CodegenGrounder`` protocol
(``context_for_spec(spec) -> str``), so it drops in beside ``PKGCodegenGrounder``.
For a given spec it asks ontomesh for the relevant domain knowledge and returns a
**cited** grounding block. Best-effort: low confidence, an empty/blocked answer,
or any error → ``""`` (no grounding), so a domain-knowledge outage never breaks a
build — exactly how the orchestrator already treats grounding.

``CompositeGrounder`` runs several grounders and concatenates their blocks, so the
**code-true** PKG context and the **domain-true** ontology context compose into one
grounding payload.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

from orchestrator.spine.grounding import GroundingBlock
from orchestrator.spine.ontomesh_client import OntomeshError, OntomeshSearch

# Spec prose fields used to form the domain question (mirrors the PKG grounder).
_SPEC_FIELDS = ("title", "summary", "user_story", "technical_notes")


def _spec_question(spec: dict[str, Any]) -> str:
    parts = [str(spec.get(k) or "") for k in _SPEC_FIELDS]
    criteria = spec.get("acceptance_criteria")
    if isinstance(criteria, list):
        parts.extend(str(c) for c in criteria)
    return " ".join(p for p in parts if p).strip()


class OntomeshGrounder:
    """Cited domain grounding for a spec, sourced from ontomesh."""

    def __init__(
        self,
        client: OntomeshSearch,
        *,
        min_confidence: float = 0.0,
        budget: int = 2000,
    ) -> None:
        self._client = client
        self._min_confidence = min_confidence
        self._budget = budget

    def block_for_spec(self, spec: dict[str, Any]) -> GroundingBlock:
        """The structured grounding block (empty when nothing usable)."""
        question = _spec_question(spec)
        if not question:
            return GroundingBlock(text="")
        try:
            answer = self._client.search(question)
        except OntomeshError:
            return GroundingBlock(text="")  # degrade quietly — grounding is best-effort
        if answer.status != "ok" or answer.confidence < self._min_confidence:
            return GroundingBlock(text="")
        return GroundingBlock(
            text=answer.answer,
            citations=answer.citations,
            confidence=answer.confidence,
            source="ontomesh",
        )

    def context_for_spec(self, spec: dict[str, Any]) -> str:
        """``CodegenGrounder`` contract: the rendered block, or ''."""
        return self.block_for_spec(spec).render(budget=self._budget)


class CompositeGrounder:
    """Combine multiple ``CodegenGrounder``s — code-true + domain-true, composed."""

    def __init__(self, grounders: list[Any]) -> None:
        self._grounders = list(grounders)

    def context_for_spec(self, spec: dict[str, Any]) -> str:
        blocks: list[str] = []
        for grounder in self._grounders:
            try:
                block = grounder.context_for_spec(spec)
            except Exception:  # noqa: BLE001 — one grounder failing must not sink the rest
                continue
            if block and block.strip():
                blocks.append(block.strip())
        return "\n\n".join(blocks)


def ontomesh_grounder_from_env() -> OntomeshGrounder | None:
    """Build an ``OntomeshGrounder`` from env, or ``None`` when not configured.

    Gated by ``SPINE_ONTOMESH_URL`` + ``SPINE_ONTOMESH_FLAVOR`` (both required);
    optional ``SPINE_ONTOMESH_MIN_CONFIDENCE``. Off by default → Spine grounding is
    inert unless the operator points it at an ontomesh instance.
    """
    url = (os.getenv("SPINE_ONTOMESH_URL") or "").strip()
    flavor = (os.getenv("SPINE_ONTOMESH_FLAVOR") or "").strip()
    if not url or not flavor:
        return None
    from orchestrator.spine.ontomesh_client import OntomeshHttpClient

    try:
        min_conf = float(os.getenv("SPINE_ONTOMESH_MIN_CONFIDENCE") or "0.0")
    except ValueError:
        min_conf = 0.0
    return OntomeshGrounder(OntomeshHttpClient(url, flavor=flavor), min_confidence=min_conf)


def compose_with_ontomesh(base: Any) -> Any:
    """Wrap ``base`` (a CodegenGrounder) with ontomesh domain grounding when
    configured; otherwise return ``base`` unchanged."""
    extra = ontomesh_grounder_from_env()
    return CompositeGrounder([base, extra]) if extra is not None else base


def compose_factory_with_ontomesh(
    factory: Callable[[Any], Any],
) -> Callable[[Any], Any]:
    """Wrap a per-root grounder *factory* with ontomesh grounding when configured."""
    extra = ontomesh_grounder_from_env()
    if extra is None:
        return factory

    def composed(root: Any) -> Any:
        return CompositeGrounder([factory(root), extra])

    return composed


__all__ = [
    "CompositeGrounder",
    "OntomeshGrounder",
    "compose_factory_with_ontomesh",
    "compose_with_ontomesh",
    "ontomesh_grounder_from_env",
]
