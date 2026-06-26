"""Register shipped units with infodrift — Seam 2 (Phase 3).

When the orchestrator merges a unit, it tells infodrift what shipped so monitoring
baselines from the exact version produced — no manual onboarding. The orchestrator
emits a ``unit_shipped`` event (``RegistrationRequest``); it does **not** ship a
feature DataFrame — infodrift establishes the baseline from its own data pipeline
when it receives the event. That keeps the orchestrator decoupled from feature data.

The hard part (per the spec's risks) is deriving the ``entity_key``: Component comes
from the shipped code's ontology mapping, Version from the release, and
**Region/Interface from a deploy-config source** (``DeployTopology``). A component
that runs in several regions yields several entity keys — several registrations.

External system behind a Protocol (``InfodriftRegistry``): production wires the HTTP
client; tests pass a fake. Registration is best-effort — it must never fail an
already-merged run.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from orchestrator.spine.entity_key import EntityKey
from orchestrator.spine.mapper import MappingLedger


class InfodriftError(RuntimeError):
    """infodrift registration was unreachable or returned an error."""


@dataclass(frozen=True)
class ShippedUnit:
    """What just merged: the component (a domain entity) + the version + provenance."""

    component: str
    version: str
    ontology_iri: str = ""
    repo_key: str = ""
    trace_id: str = ""
    pr_url: str = ""


@dataclass(frozen=True)
class RegistrationRequest:
    """The ``unit_shipped`` event infodrift consumes to baseline a new unit."""

    entity_key: str
    version: str
    model_version: str
    baseline_id: str
    ontology_iri: str = ""
    trace_id: str = ""
    pr_url: str = ""

    def as_payload(self) -> dict[str, Any]:
        return {
            "entity_key": self.entity_key,
            "version": self.version,
            "model_version": self.model_version,
            "baseline_id": self.baseline_id,
            "ontology_iri": self.ontology_iri,
            "trace_id": self.trace_id,
            "pr_url": self.pr_url,
        }


@dataclass(frozen=True)
class RegistrationResult:
    """Outcome of one registration attempt."""

    entity_key: str
    ok: bool
    detail: str = ""


@runtime_checkable
class DeployTopology(Protocol):
    """Resolves where a component runs: ``(component, version) -> [(region, interface)]``."""

    def placements(self, component: str, version: str) -> list[tuple[str, str]]: ...


@dataclass
class StaticDeployTopology:
    """A fixed component → placements map (Phase 3 input; a CMDB/Helm source later)."""

    table: Mapping[str, list[tuple[str, str]]] = field(default_factory=dict)

    def placements(self, component: str, version: str) -> list[tuple[str, str]]:
        return list(self.table.get(component, []))


@runtime_checkable
class InfodriftRegistry(Protocol):
    """Anything that accepts a ``unit_shipped`` registration."""

    def register(self, request: RegistrationRequest) -> RegistrationResult: ...


class InfodriftHttpClient:
    """POSTs ``unit_shipped`` events to infodrift's ``/api/register`` (the thin
    service infodrift exposes for Seam 2). Transport/HTTP errors raise
    ``InfodriftError``; the registrar catches and degrades."""

    def __init__(self, base_url: str, *, timeout: float = 10.0, api_key: str | None = None) -> None:
        self._url = base_url.rstrip("/") + "/api/register"
        self._timeout = timeout
        self._headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}

    def register(self, request: RegistrationRequest) -> RegistrationResult:
        import httpx

        try:
            resp = httpx.post(
                self._url, json=request.as_payload(), headers=self._headers, timeout=self._timeout
            )
        except httpx.HTTPError as exc:
            raise InfodriftError(f"infodrift register unreachable: {exc}") from exc
        if resp.status_code not in (200, 201, 202):
            raise InfodriftError(f"infodrift register HTTP {resp.status_code}: {resp.text[:200]}")
        return RegistrationResult(entity_key=request.entity_key, ok=True, detail="registered")


def component_for_nodes(node_ids: list[str], ledger: MappingLedger) -> tuple[str, str] | None:
    """Pick the dominant ontology entity among changed nodes → ``(component, iri)``.

    Component is derived from the ontology class label (spaces stripped, so
    ``"Fraud Detector"`` → ``"FraudDetector"``). Returns ``None`` when none of the
    changed nodes carry a confirmed mapping (scope unresolved).
    """
    resolved = ledger.resolved()
    counts: dict[tuple[str, str], int] = {}
    for node_id in node_ids:
        ref = resolved.get(node_id)
        if ref is None:
            continue
        key = (ref.label.replace(" ", ""), ref.ontology_iri)
        counts[key] = counts.get(key, 0) + 1
    if not counts:
        return None
    return max(counts, key=lambda k: counts[k])


class ShipmentRegistrar:
    """Derive entity keys for a shipped unit and register each with infodrift."""

    def __init__(self, registry: InfodriftRegistry, topology: DeployTopology) -> None:
        self._registry = registry
        self._topology = topology

    def entity_keys(self, unit: ShippedUnit) -> list[EntityKey]:
        """The entity keys this unit produces — one per deploy placement."""
        return [
            EntityKey(component=unit.component, version=unit.version, region=region, interface=interface)
            for region, interface in self._topology.placements(unit.component, unit.version)
        ]

    def register(self, unit: ShippedUnit) -> list[RegistrationResult]:
        """Register every placement of ``unit``. Best-effort: one failure doesn't
        stop the others, and nothing raises into the (already-merged) caller."""
        results: list[RegistrationResult] = []
        for key in self.entity_keys(unit):
            request = RegistrationRequest(
                entity_key=key.format(),
                version=unit.version,
                model_version=unit.version,
                baseline_id=f"{key.format()}@ship",
                ontology_iri=unit.ontology_iri,
                trace_id=unit.trace_id,
                pr_url=unit.pr_url,
            )
            try:
                results.append(self._registry.register(request))
            except InfodriftError as exc:
                results.append(RegistrationResult(entity_key=key.format(), ok=False, detail=str(exc)))
        return results


__all__ = [
    "DeployTopology",
    "InfodriftError",
    "InfodriftHttpClient",
    "InfodriftRegistry",
    "RegistrationRequest",
    "RegistrationResult",
    "ShipmentRegistrar",
    "ShippedUnit",
    "StaticDeployTopology",
    "component_for_nodes",
]
