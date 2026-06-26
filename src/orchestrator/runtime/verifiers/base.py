"""Shared protocol and result types for per-edge verifiers.

A Verifier is intentionally simple: take the upstream node's output dict,
take a small ``VerifierContext`` (the agent template, the artifact store
when needed, and so on), return a ``VerifierResult``. Composition is done
by ``combine_results`` (worst outcome wins; failures are concatenated).

The result shape extends Sprint 5's terminal ``VerifierResult`` (which
lives under ``orchestrator.runtime.verifier``) but lives here so the
per-edge chain can grow independently. The two are interchangeable on
the wire — both serialise to ``{outcome, failures}`` dicts via
``to_state_value``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol

from orchestrator.registry.agent_template import AgentTemplate
from orchestrator.runtime.artifacts import ArtifactStore


class VerifierOutcome(str, Enum):
    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"


_OUTCOME_RANK = {VerifierOutcome.PASS: 0, VerifierOutcome.WARN: 1, VerifierOutcome.FAIL: 2}


@dataclass(frozen=True)
class VerifierFailure:
    verifier_id: str
    rule: str
    field: str
    message: str
    severity: VerifierOutcome = VerifierOutcome.FAIL


@dataclass(frozen=True)
class VerifierResult:
    """Outcome of one verifier (or one combined chain)."""

    verifier_id: str
    outcome: VerifierOutcome
    failures: tuple[VerifierFailure, ...] = field(default_factory=tuple)
    elapsed_ms: float = 0.0
    cost_usd: float = 0.0

    def to_state_value(self) -> dict[str, Any]:
        return {
            "verifier_id": self.verifier_id,
            "outcome": self.outcome.value,
            "failures": [
                {
                    "verifier_id": f.verifier_id,
                    "rule": f.rule,
                    "field": f.field,
                    "message": f.message,
                    "severity": f.severity.value,
                }
                for f in self.failures
            ],
            "elapsed_ms": round(self.elapsed_ms, 3),
            "cost_usd": self.cost_usd,
        }


@dataclass(frozen=True)
class VerifierContext:
    """Per-call context handed to every verifier.

    ``task_glossary`` carries the pinned glossary slice the runtime read
    off the OrchestratorState's write-once channel at chain dispatch
    time. Verifiers that need it (e.g. GlossaryVerifier) consult it
    directly; others ignore it.
    """

    template: AgentTemplate
    node_id: str
    task_id: str
    trace_id: str
    artifact_store: ArtifactStore | None = None
    task_glossary: dict[str, Any] = field(default_factory=dict)


class Verifier(Protocol):
    """The minimal contract every verifier in the chain satisfies."""

    verifier_id: str

    async def verify(self, output: dict[str, Any], ctx: VerifierContext) -> VerifierResult: ...


def combine_results(results: list[VerifierResult], *, chain_id: str = "chain") -> VerifierResult:
    """Aggregate a list of per-verifier results into one chain result.

    Worst outcome wins; failures are concatenated in input order so the
    report reads from upstream to downstream. ``elapsed_ms`` sums; cost
    sums.
    """
    if not results:
        return VerifierResult(verifier_id=chain_id, outcome=VerifierOutcome.PASS)
    worst = max(results, key=lambda r: _OUTCOME_RANK[r.outcome]).outcome
    failures: list[VerifierFailure] = []
    elapsed = 0.0
    cost = 0.0
    for r in results:
        failures.extend(r.failures)
        elapsed += r.elapsed_ms
        cost += r.cost_usd
    return VerifierResult(
        verifier_id=chain_id,
        outcome=worst,
        failures=tuple(failures),
        elapsed_ms=elapsed,
        cost_usd=cost,
    )
