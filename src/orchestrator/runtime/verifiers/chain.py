"""VerifierChain: sequentially apply a list of verifiers to one node output.

Sprint 10.5. The chain short-circuits on the first FAIL (downstream
verifiers don't run when an earlier one has already terminated the
sequence) and aggregates the per-verifier results via
``combine_results``. The aggregate's outcome is the worst seen across
verifiers that actually ran; per-verifier results are kept so the audit
log can render them as separate rows.

Sprint 10.7's on-failure dispatch lives in ``runtime.failure_dispatch``
so the chain itself stays pure (no LangGraph dependency).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from orchestrator.runtime.verifiers.base import (
    Verifier,
    VerifierContext,
    VerifierFailure,
    VerifierOutcome,
    VerifierResult,
    combine_results,
)


@dataclass(frozen=True)
class ChainResult:
    """A chain's aggregate result plus the per-verifier breakdown.

    ``per_verifier`` is keyed by verifier_id so callers can render an audit
    row per verifier. ``short_circuited_after`` is set when an earlier
    verifier failed and downstream ones did not run.
    """

    chain_id: str
    aggregate: VerifierResult
    per_verifier: dict[str, VerifierResult] = field(default_factory=dict)
    short_circuited_after: str | None = None

    @property
    def outcome(self) -> VerifierOutcome:
        return self.aggregate.outcome

    @property
    def failures(self) -> tuple[VerifierFailure, ...]:
        return self.aggregate.failures

    def to_state_value(self) -> dict[str, Any]:
        return {
            "chain_id": self.chain_id,
            "outcome": self.aggregate.outcome.value,
            "elapsed_ms": round(self.aggregate.elapsed_ms, 3),
            "cost_usd": self.aggregate.cost_usd,
            "short_circuited_after": self.short_circuited_after,
            "per_verifier": {k: v.to_state_value() for k, v in self.per_verifier.items()},
            "failures": self.aggregate.to_state_value()["failures"],
        }


class VerifierChain:
    """Sequential composition of verifiers with short-circuit on FAIL."""

    def __init__(self, verifiers: list[Verifier], *, chain_id: str = "chain") -> None:
        if not verifiers:
            raise ValueError("VerifierChain requires at least one verifier")
        ids = [v.verifier_id for v in verifiers]
        if len(ids) != len(set(ids)):
            raise ValueError(f"VerifierChain: duplicate verifier ids in {ids}")
        self._verifiers = list(verifiers)
        self._chain_id = chain_id

    @property
    def chain_id(self) -> str:
        return self._chain_id

    @property
    def verifier_ids(self) -> list[str]:
        return [v.verifier_id for v in self._verifiers]

    async def run(self, output: dict[str, Any], ctx: VerifierContext) -> ChainResult:
        start = time.perf_counter()
        per_verifier: dict[str, VerifierResult] = {}
        short_circuit_after: str | None = None
        for verifier in self._verifiers:
            result = await verifier.verify(output, ctx)
            per_verifier[verifier.verifier_id] = result
            if result.outcome is VerifierOutcome.FAIL:
                short_circuit_after = verifier.verifier_id
                break

        aggregate = combine_results(list(per_verifier.values()), chain_id=self._chain_id)
        # Override the aggregate's elapsed_ms with the actual wall clock so the
        # chain reports total time including any short-circuit savings.
        aggregate = VerifierResult(
            verifier_id=aggregate.verifier_id,
            outcome=aggregate.outcome,
            failures=aggregate.failures,
            elapsed_ms=(time.perf_counter() - start) * 1000.0,
            cost_usd=aggregate.cost_usd,
        )
        return ChainResult(
            chain_id=self._chain_id,
            aggregate=aggregate,
            per_verifier=per_verifier,
            short_circuited_after=short_circuit_after,
        )
