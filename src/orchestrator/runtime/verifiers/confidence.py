"""ConfidenceVerifier: enforce a calibrated-confidence floor.

Per the spec (Sprint 10.2):
- pass: ``confidence >= threshold``
- warn: ``confidence`` within 10% (relative) below the threshold
- fail: more than 10% below the threshold (or absent / out of range)
"""

from __future__ import annotations

import time
from typing import Any

from orchestrator.runtime.verifiers.base import (
    VerifierContext,
    VerifierFailure,
    VerifierOutcome,
    VerifierResult,
)


class ConfidenceVerifier:
    verifier_id: str = "confidence"

    def __init__(self, threshold: float = 0.7, *, warn_band: float = 0.10) -> None:
        if not 0.0 <= threshold <= 1.0:
            raise ValueError(f"ConfidenceVerifier threshold must be in [0, 1]; got {threshold}")
        if not 0.0 < warn_band <= 1.0:
            raise ValueError(f"ConfidenceVerifier warn_band must be in (0, 1]; got {warn_band}")
        self._threshold = threshold
        self._warn_band = warn_band

    async def verify(self, output: dict[str, Any], ctx: VerifierContext) -> VerifierResult:
        _ = ctx
        start = time.perf_counter()
        actual = output.get("confidence")
        failures: list[VerifierFailure] = []
        outcome = VerifierOutcome.PASS

        if not isinstance(actual, (int, float)):
            outcome = VerifierOutcome.FAIL
            failures.append(
                VerifierFailure(
                    verifier_id=self.verifier_id,
                    rule="missing_or_non_numeric",
                    field="confidence",
                    message=f"expected number in [0, 1]; got {type(actual).__name__}",
                )
            )
        elif not 0.0 <= float(actual) <= 1.0:
            outcome = VerifierOutcome.FAIL
            failures.append(
                VerifierFailure(
                    verifier_id=self.verifier_id,
                    rule="out_of_range",
                    field="confidence",
                    message=f"confidence={actual} not in [0, 1]",
                )
            )
        elif float(actual) < self._threshold:
            actual_f = float(actual)
            warn_floor = self._threshold * (1.0 - self._warn_band)
            if actual_f >= warn_floor:
                outcome = VerifierOutcome.WARN
                severity = VerifierOutcome.WARN
            else:
                outcome = VerifierOutcome.FAIL
                severity = VerifierOutcome.FAIL
            failures.append(
                VerifierFailure(
                    verifier_id=self.verifier_id,
                    rule="below_threshold",
                    field="confidence",
                    message=(
                        f"confidence={actual_f:.3f} below threshold={self._threshold:.3f} "
                        f"(warn floor={warn_floor:.3f})"
                    ),
                    severity=severity,
                )
            )

        return VerifierResult(
            verifier_id=self.verifier_id,
            outcome=outcome,
            failures=tuple(failures),
            elapsed_ms=(time.perf_counter() - start) * 1000.0,
        )
