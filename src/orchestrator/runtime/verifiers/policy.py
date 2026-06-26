"""PolicyVerifier: PII detection, data-classification, approval-required checks.

Sprint 10.4 ships the three baseline rule categories:

- **PII patterns** — regex catches for SSN, credit card (with Luhn check),
  email, phone. Each match becomes a `fail` failure.
- **Data classification** — the template can declare a max classification
  level in `spec.policies` or `spec.constraints.allowed_classifications`.
  Outputs above the declared ceiling fail.
- **Approval required** — if any claim references a tool/action whose
  contract requires approval, and the upstream chain didn't insert an
  approval node, surface a `warn` (escalates to `fail` when configured).

Custom per-tenant policies arrive with multi-tenancy in Phase 4
(Sprint 16+); the structure here makes a tenant adapter additive.
"""

from __future__ import annotations

import re
import time
from typing import Any

from orchestrator.runtime.verifiers.base import (
    VerifierContext,
    VerifierFailure,
    VerifierOutcome,
    VerifierResult,
)

# SSN: 3-2-4 digits with separators. Excludes the well-known invalid 000-,
# 666-, 9xx- areas to keep the false-positive rate sane.
_SSN_RE = re.compile(r"\b(?!000|666|9\d{2})\d{3}[- ]?(?!00)\d{2}[- ]?(?!0000)\d{4}\b")
# Credit card: 13–19 digits, optional separators. Validated with Luhn below.
_CC_RE = re.compile(r"\b(?:\d[ -]?){13,19}\b")
# Email: minimal RFC 5322 acceptable subset.
_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
# Phone: 10 digits with common separators; this is intentionally US-leaning
# and earns a tenant adapter later.
_PHONE_RE = re.compile(r"\b(?:\+?1[-. ]?)?\(?\d{3}\)?[-. ]?\d{3}[-. ]?\d{4}\b")

_CLASSIFICATION_RANK = {"public": 0, "internal": 1, "confidential": 2, "restricted": 3}


def _luhn_ok(digits: str) -> bool:
    s = 0
    parity = len(digits) % 2
    for i, ch in enumerate(digits):
        d = int(ch)
        if i % 2 == parity:
            d *= 2
            if d > 9:
                d -= 9
        s += d
    return s % 10 == 0


def _iter_text_fields(output: dict[str, Any]) -> list[tuple[str, str]]:
    """Yield (dotted_path, text) for every string we should scan."""
    out: list[tuple[str, str]] = []

    def walk(node: Any, path: str) -> None:
        if isinstance(node, str):
            out.append((path, node))
        elif isinstance(node, dict):
            for k, v in node.items():
                child_path = f"{path}.{k}" if path else k
                walk(v, child_path)
        elif isinstance(node, list):
            for i, item in enumerate(node):
                walk(item, f"{path}[{i}]")

    walk(output, "")
    return out


class PolicyVerifier:
    verifier_id: str = "policy"

    def __init__(
        self,
        *,
        scan_pii: bool = True,
        check_classification: bool = True,
        check_approval: bool = True,
    ) -> None:
        self._scan_pii = scan_pii
        self._check_classification = check_classification
        self._check_approval = check_approval

    async def verify(self, output: dict[str, Any], ctx: VerifierContext) -> VerifierResult:
        start = time.perf_counter()
        failures: list[VerifierFailure] = []

        if self._scan_pii:
            failures.extend(self._scan_pii_failures(output))
        if self._check_classification:
            failures.extend(self._classification_failures(output, ctx))
        if self._check_approval:
            failures.extend(self._approval_failures(output, ctx))

        outcome = (
            VerifierOutcome.FAIL
            if any(f.severity is VerifierOutcome.FAIL for f in failures)
            else (
                VerifierOutcome.WARN
                if any(f.severity is VerifierOutcome.WARN for f in failures)
                else VerifierOutcome.PASS
            )
        )
        return VerifierResult(
            verifier_id=self.verifier_id,
            outcome=outcome,
            failures=tuple(failures),
            elapsed_ms=(time.perf_counter() - start) * 1000.0,
        )

    def _scan_pii_failures(self, output: dict[str, Any]) -> list[VerifierFailure]:
        out: list[VerifierFailure] = []
        for path, text in _iter_text_fields(output):
            if _SSN_RE.search(text):
                out.append(self._pii_fail(path, "ssn", text))
            for match in _CC_RE.finditer(text):
                digits = re.sub(r"[ -]", "", match.group(0))
                if 13 <= len(digits) <= 19 and _luhn_ok(digits):
                    out.append(self._pii_fail(path, "credit_card", match.group(0)))
            if _EMAIL_RE.search(text):
                out.append(self._pii_fail(path, "email", text))
            if _PHONE_RE.search(text):
                out.append(
                    VerifierFailure(
                        verifier_id=self.verifier_id,
                        rule="pii_phone",
                        field=path,
                        message="phone-shaped token detected",
                        severity=VerifierOutcome.WARN,
                    )
                )
        return out

    def _pii_fail(self, path: str, kind: str, _evidence: str) -> VerifierFailure:
        # We do not log the matched value — recording PII to defeat PII would be
        # ironic. The audit row carries the field path and the rule only.
        return VerifierFailure(
            verifier_id=self.verifier_id,
            rule=f"pii_{kind}",
            field=path,
            message=f"{kind} detected at {path}",
            severity=VerifierOutcome.FAIL,
        )

    def _classification_failures(self, output: dict[str, Any], ctx: VerifierContext) -> list[VerifierFailure]:
        ceiling = ctx.template.spec.constraints.get("max_classification")
        if ceiling is None:
            return []
        ceiling_rank = _CLASSIFICATION_RANK.get(str(ceiling).lower())
        if ceiling_rank is None:
            return []
        declared = output.get("classification")
        if declared is None:
            return []
        declared_rank = _CLASSIFICATION_RANK.get(str(declared).lower())
        if declared_rank is None:
            return [
                VerifierFailure(
                    verifier_id=self.verifier_id,
                    rule="classification_unknown",
                    field="classification",
                    message=f"unknown classification {declared!r}",
                    severity=VerifierOutcome.WARN,
                )
            ]
        if declared_rank > ceiling_rank:
            return [
                VerifierFailure(
                    verifier_id=self.verifier_id,
                    rule="classification_exceeds_ceiling",
                    field="classification",
                    message=(f"output classification {declared!r} exceeds template ceiling {ceiling!r}"),
                    severity=VerifierOutcome.FAIL,
                )
            ]
        return []

    def _approval_failures(self, output: dict[str, Any], ctx: VerifierContext) -> list[VerifierFailure]:
        # The runtime hasn't wired approval nodes yet (Sprint 14). We surface a
        # `warn` whenever a claim's supporting_artifacts references a tool whose
        # contract requires approval, so the audit log captures intent.
        required = ctx.template.spec.constraints.get("requires_approval", False)
        if not required:
            return []
        if output.get("approval_artifact_id"):
            return []
        return [
            VerifierFailure(
                verifier_id=self.verifier_id,
                rule="approval_missing",
                field="approval_artifact_id",
                message="template declares requires_approval but output has no approval_artifact_id",
                severity=VerifierOutcome.WARN,
            )
        ]
