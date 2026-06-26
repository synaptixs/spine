"""Client for ontomesh reasoning-search — the domain-knowledge source (Phase 1).

Thin, sync client over ontomesh's ``POST /api/search`` (flag-gated `ONTOFORGE_SEARCH`
on ontomesh's side). Returns a ``ReasonedAnswer`` mirroring ontomesh's contract:
a cited, confidence-scored answer over the connected domain ontology.

The ``OntomeshSearch`` Protocol is the seam — production wires ``OntomeshHttpClient``;
tests pass a fake. Either way the grounder (``grounder.py``) is unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from orchestrator.spine.grounding import Citation


class OntomeshError(RuntimeError):
    """ontomesh search was unreachable or returned an error."""


@dataclass(frozen=True)
class ReasonedAnswer:
    """ontomesh's response contract (the subset Spine consumes).

    ``status`` is one of ``ok`` / ``blocked`` / ``ungrounded`` / ``empty`` —
    only ``ok`` carries usable grounding.
    """

    answer: str = ""
    citations: tuple[Citation, ...] = field(default_factory=tuple)
    confidence: float = 0.0
    executed_query: str = ""
    status: str = "ok"

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> ReasonedAnswer:
        cites = tuple(
            Citation(
                iri=str(c.get("iri", "")),
                label=str(c.get("label", "")),
                source_table=str(c.get("source_table", "")),
                inferred=bool(c.get("inferred", False)),
            )
            for c in (payload.get("citations") or [])
            if c.get("iri")
        )
        return cls(
            answer=str(payload.get("answer", "")),
            citations=cites,
            confidence=float(payload.get("confidence", 0.0) or 0.0),
            executed_query=str(payload.get("executed_query", "")),
            status=str(payload.get("status", "ok")),
        )


@runtime_checkable
class OntomeshSearch(Protocol):
    """Anything that answers a domain question with a cited ``ReasonedAnswer``."""

    def search(self, question: str) -> ReasonedAnswer: ...


class OntomeshHttpClient:
    """Calls ontomesh ``POST /api/search`` with ``{question, flavor}``.

    ``flavor`` selects the ontology/sensitivity flavor (ontomesh requires it).
    Transport/HTTP errors raise ``OntomeshError`` — the grounder catches and
    degrades to no grounding, never breaking a build.
    """

    def __init__(
        self,
        base_url: str,
        *,
        flavor: str,
        timeout: float = 10.0,
        api_key: str | None = None,
    ) -> None:
        self._url = base_url.rstrip("/") + "/api/search"
        self._flavor = flavor
        self._timeout = timeout
        self._headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}

    def search(self, question: str) -> ReasonedAnswer:
        import httpx

        try:
            resp = httpx.post(
                self._url,
                json={"question": question, "flavor": self._flavor},
                headers=self._headers,
                timeout=self._timeout,
            )
        except httpx.HTTPError as exc:  # network / timeout
            raise OntomeshError(f"ontomesh search unreachable: {exc}") from exc
        if resp.status_code != 200:
            raise OntomeshError(f"ontomesh search HTTP {resp.status_code}: {resp.text[:200]}")
        try:
            payload = resp.json()
        except ValueError as exc:
            raise OntomeshError("ontomesh search returned non-JSON") from exc
        return ReasonedAnswer.from_payload(payload)


__all__ = ["OntomeshError", "OntomeshHttpClient", "OntomeshSearch", "ReasonedAnswer"]
