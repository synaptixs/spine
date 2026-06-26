"""Spine — the semantic backbone joining domain, code, deployment, and drift.

Phase 0 (``docs/specs/tri-repo-integration.md``): prove the join before anything
builds on it. This package ships the two foundational contracts and the mapper
that earns the join:

- ``EntityKey`` — the universal identity ``Component_vX::Region::Interface`` that
  the same artifact carries through every stage (ontomesh entity → PKG code node →
  infodrift deployment unit → drift signal).
- ``OntologyClass`` / ``OntologyRef`` — the ontology side and the reference a code
  node carries (``ontology_iri`` + confidence + rationale).
- ``CodeOntologyMapper`` + ``MappingLedger`` — propose code↔ontology mappings
  heuristically, then **human-confirm**; only confirmed mappings become
  authoritative. Every proposal and decision is auditable.
- ``evaluate_precision`` — measure mapping precision against a gold standard, so
  the join is proven, not assumed.

Nothing here depends on ontomesh or infodrift being wired: ontology classes are
inputs. That keeps Phase 0 standalone and testable.
"""

from __future__ import annotations

from orchestrator.spine.benchmark import PrecisionReport, evaluate_precision
from orchestrator.spine.drift import DriftFinding, DriftReport
from orchestrator.spine.entity_key import EntityKey, EntityKeyError
from orchestrator.spine.execute import (
    RemediationOutcome,
    RemediationRunner,
    execute_remediations,
    infer_entity_iris,
)
from orchestrator.spine.grounder import (
    CompositeGrounder,
    OntomeshGrounder,
    compose_factory_with_ontomesh,
    compose_with_ontomesh,
    ontomesh_grounder_from_env,
)
from orchestrator.spine.grounding import Citation, GroundingBlock
from orchestrator.spine.lineage import LineageIndex, LineageRecord, correlation_handles
from orchestrator.spine.mapper import (
    CodeOntologyMapper,
    MappingCandidate,
    MappingLedger,
)
from orchestrator.spine.ontology import OntologyClass, OntologyRef
from orchestrator.spine.ontomesh_client import (
    OntomeshError,
    OntomeshHttpClient,
    OntomeshSearch,
    ReasonedAnswer,
)
from orchestrator.spine.remediation import (
    RemediationTask,
    code_for_iri_from_ledger,
    plan_remediations,
)
from orchestrator.spine.shipment import (
    DeployTopology,
    InfodriftError,
    InfodriftHttpClient,
    InfodriftRegistry,
    RegistrationRequest,
    RegistrationResult,
    ShipmentRegistrar,
    ShippedUnit,
    StaticDeployTopology,
    component_for_nodes,
)
from orchestrator.spine.store import MappingStore

__all__ = [
    "Citation",
    "CodeOntologyMapper",
    "CompositeGrounder",
    "DeployTopology",
    "DriftFinding",
    "DriftReport",
    "EntityKey",
    "EntityKeyError",
    "GroundingBlock",
    "InfodriftError",
    "InfodriftHttpClient",
    "InfodriftRegistry",
    "LineageIndex",
    "LineageRecord",
    "MappingCandidate",
    "MappingStore",
    "MappingLedger",
    "OntologyClass",
    "OntologyRef",
    "OntomeshError",
    "OntomeshGrounder",
    "OntomeshHttpClient",
    "OntomeshSearch",
    "PrecisionReport",
    "ReasonedAnswer",
    "RegistrationRequest",
    "RegistrationResult",
    "RemediationOutcome",
    "RemediationRunner",
    "RemediationTask",
    "ShipmentRegistrar",
    "ShippedUnit",
    "StaticDeployTopology",
    "code_for_iri_from_ledger",
    "component_for_nodes",
    "compose_factory_with_ontomesh",
    "compose_with_ontomesh",
    "correlation_handles",
    "evaluate_precision",
    "execute_remediations",
    "infer_entity_iris",
    "ontomesh_grounder_from_env",
    "plan_remediations",
]
