"""EvidenceVerifier: deterministic claim-vs-artifact spot-check.

Sprint 10.3. For every analytical output that carries claims, the verifier:

1. Confirms each claim has at least one ``supporting_artifacts`` entry.
2. Picks **one** claim per node deterministically (hash of claim_id ⊕
   node_id) and reads its supporting artifact from the store.
3. If the claim declared ``metric_values``, compares each metric to the
   artifact's value within ``tolerance`` (default 1%). Mismatches fail.
4. If no metric values exist on the claim, the spot-check passes as
   long as the artifact resolves — the claim is grounded.

This is what makes the audit story stand up: an analytical answer is no
longer "the LLM said so" — at least one cited number was independently
checked against the artifact that produced it.
"""

from __future__ import annotations

import hashlib
import time
from typing import Any

from orchestrator.runtime.verifiers.base import (
    VerifierContext,
    VerifierFailure,
    VerifierOutcome,
    VerifierResult,
)

DEFAULT_TOLERANCE = 0.01  # 1% relative


class EvidenceVerifier:
    verifier_id: str = "evidence"

    def __init__(self, *, tolerance: float = DEFAULT_TOLERANCE) -> None:
        if not 0.0 <= tolerance <= 1.0:
            raise ValueError(f"EvidenceVerifier tolerance must be in [0, 1]; got {tolerance}")
        self._tolerance = tolerance

    async def verify(self, output: dict[str, Any], ctx: VerifierContext) -> VerifierResult:
        start = time.perf_counter()
        failures: list[VerifierFailure] = []
        claims = list(output.get("claims") or [])

        if claims:
            failures.extend(self._check_supporting_artifacts_present(claims))
            spotcheck = await self._spot_check_one_claim(claims, ctx)
            failures.extend(spotcheck)

        if any(f.severity is VerifierOutcome.FAIL for f in failures):
            outcome = VerifierOutcome.FAIL
        elif any(f.severity is VerifierOutcome.WARN for f in failures):
            outcome = VerifierOutcome.WARN
        else:
            outcome = VerifierOutcome.PASS

        return VerifierResult(
            verifier_id=self.verifier_id,
            outcome=outcome,
            failures=tuple(failures),
            elapsed_ms=(time.perf_counter() - start) * 1000.0,
        )

    # --- structural checks ------------------------------------------------

    def _check_supporting_artifacts_present(self, claims: list[dict[str, Any]]) -> list[VerifierFailure]:
        out: list[VerifierFailure] = []
        for idx, claim in enumerate(claims):
            if not isinstance(claim, dict):
                out.append(
                    VerifierFailure(
                        verifier_id=self.verifier_id,
                        rule="claim_malformed",
                        field=f"claims[{idx}]",
                        message="expected an object",
                    )
                )
                continue
            arts = claim.get("supporting_artifacts") or []
            if not isinstance(arts, list) or not arts:
                out.append(
                    VerifierFailure(
                        verifier_id=self.verifier_id,
                        rule="missing_supporting_artifacts",
                        field=f"claims[{idx}].supporting_artifacts",
                        message=(f"claim {claim.get('id', '<unknown>')!r} has no supporting_artifacts"),
                    )
                )
        return out

    # --- spot-check -------------------------------------------------------

    def _pick_claim(self, claims: list[dict[str, Any]], node_id: str) -> dict[str, Any] | None:
        """Deterministic selection: hash of claim_id ⊕ node_id picks the smallest."""
        candidates = [c for c in claims if isinstance(c, dict) and c.get("supporting_artifacts")]
        if not candidates:
            return None

        def key(claim: dict[str, Any]) -> bytes:
            seed = f"{claim.get('id', '')}::{node_id}".encode()
            return hashlib.sha256(seed).digest()

        return min(candidates, key=key)

    async def _spot_check_one_claim(
        self, claims: list[dict[str, Any]], ctx: VerifierContext
    ) -> list[VerifierFailure]:
        claim = self._pick_claim(claims, ctx.node_id)
        if claim is None:
            return []  # already reported by missing_supporting_artifacts

        if ctx.artifact_store is None:
            return [
                VerifierFailure(
                    verifier_id=self.verifier_id,
                    rule="artifact_store_unavailable",
                    field=f"claims[{claim.get('id', '?')}].supporting_artifacts",
                    message="EvidenceVerifier configured but no artifact_store on context",
                    severity=VerifierOutcome.WARN,
                )
            ]

        first = claim["supporting_artifacts"][0]
        artifact_id = first.get("artifact_id") if isinstance(first, dict) else None
        if not artifact_id:
            return [
                VerifierFailure(
                    verifier_id=self.verifier_id,
                    rule="artifact_id_missing",
                    field=f"claims[{claim.get('id', '?')}].supporting_artifacts[0].artifact_id",
                    message="first supporting artifact has no artifact_id",
                )
            ]

        try:
            artifact = await ctx.artifact_store.get_json(artifact_id)
        except LookupError:
            return [
                VerifierFailure(
                    verifier_id=self.verifier_id,
                    rule="artifact_not_found",
                    field=f"claims[{claim.get('id', '?')}].supporting_artifacts[0].artifact_id",
                    message=f"artifact {artifact_id!r} not found in store",
                )
            ]
        except (ValueError, OSError) as exc:
            return [
                VerifierFailure(
                    verifier_id=self.verifier_id,
                    rule="artifact_unreadable",
                    field=f"claims[{claim.get('id', '?')}].supporting_artifacts[0].artifact_id",
                    message=f"artifact {artifact_id!r} unreadable: {exc}",
                )
            ]

        claimed_metrics = claim.get("metric_values") or {}
        if not isinstance(claimed_metrics, dict) or not claimed_metrics:
            return []  # qualitative claim — grounded by virtue of artifact existing

        return self._compare_metrics(claim, claimed_metrics, artifact)

    def _compare_metrics(
        self,
        claim: dict[str, Any],
        claimed_metrics: dict[str, Any],
        artifact: dict[str, Any],
    ) -> list[VerifierFailure]:
        out: list[VerifierFailure] = []
        artifact_metrics: dict[str, Any] = {}
        # Accept either {"metrics": {...}} or top-level metric keys.
        if isinstance(artifact.get("metrics"), dict):
            artifact_metrics.update(artifact["metrics"])
        for key, value in artifact.items():
            if isinstance(value, (int, float)) and key not in artifact_metrics:
                artifact_metrics[key] = value

        claim_id = claim.get("id", "<unknown>")
        for metric_name, claimed_raw in claimed_metrics.items():
            if not isinstance(claimed_raw, (int, float)):
                continue
            if metric_name not in artifact_metrics:
                out.append(
                    VerifierFailure(
                        verifier_id=self.verifier_id,
                        rule="metric_missing_in_artifact",
                        field=f"claims[{claim_id}].metric_values.{metric_name}",
                        message=f"artifact does not contain metric {metric_name!r}",
                    )
                )
                continue
            actual_raw = artifact_metrics[metric_name]
            if not isinstance(actual_raw, (int, float)):
                out.append(
                    VerifierFailure(
                        verifier_id=self.verifier_id,
                        rule="metric_type_mismatch",
                        field=f"claims[{claim_id}].metric_values.{metric_name}",
                        message=(
                            f"artifact metric {metric_name!r} is {type(actual_raw).__name__}, not numeric"
                        ),
                    )
                )
                continue
            if not _within_tolerance(float(claimed_raw), float(actual_raw), self._tolerance):
                out.append(
                    VerifierFailure(
                        verifier_id=self.verifier_id,
                        rule="metric_mismatch",
                        field=f"claims[{claim_id}].metric_values.{metric_name}",
                        message=(
                            f"claimed {claimed_raw} vs artifact {actual_raw} "
                            f"(tolerance ±{self._tolerance:.1%})"
                        ),
                    )
                )
        return out


def _within_tolerance(claimed: float, actual: float, tolerance: float) -> bool:
    if actual == 0.0:
        return abs(claimed) <= tolerance
    return abs(claimed - actual) / max(abs(actual), 1e-12) <= tolerance
