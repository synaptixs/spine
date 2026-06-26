"""Per-edge verifier chain.

A ``Verifier`` is anything that takes an agent's output plus a small
``VerifierContext`` and returns a typed ``VerifierResult``. Verifiers
compose into chains via ``VerifierChain`` that the graph builder slots
between agent nodes; failures route through ``FailureAction`` handlers
(Sprint 8 enum) via ``orchestrator.runtime.failure_dispatch``.

Concrete verifiers:

- SchemaVerifier   — ``orchestrator.runtime.verifier`` (Sprint 5).
- ConfidenceVerifier (10.2)
- EvidenceVerifier  (10.3)
- PolicyVerifier    (10.4)
- GlossaryVerifier  (11.5)
"""

from orchestrator.runtime.verifiers.base import (
    Verifier,
    VerifierContext,
    VerifierFailure,
    VerifierOutcome,
    VerifierResult,
    combine_results,
)
from orchestrator.runtime.verifiers.chain import ChainResult, VerifierChain
from orchestrator.runtime.verifiers.confidence import ConfidenceVerifier
from orchestrator.runtime.verifiers.evidence import EvidenceVerifier
from orchestrator.runtime.verifiers.glossary import GlossaryVerifier
from orchestrator.runtime.verifiers.policy import PolicyVerifier

__all__ = [
    "ChainResult",
    "ConfidenceVerifier",
    "EvidenceVerifier",
    "GlossaryVerifier",
    "PolicyVerifier",
    "Verifier",
    "VerifierChain",
    "VerifierContext",
    "VerifierFailure",
    "VerifierOutcome",
    "VerifierResult",
    "combine_results",
]
