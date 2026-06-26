"""GlossaryVerifier: rejects outputs that contradict pinned glossary terms.

Sprint 11.5. Glossary entries arrive on the runtime state via
``OrchestratorState.task_glossary`` (write-once channel), produced by the
planner's user_specified → org_default → planner_inferred merge. The
chain node copies the slice onto ``VerifierContext.task_glossary`` before
running verifiers so this module stays stateless.

Contract:

- For every pinned term that appears in the agent's textual output, the
  output must not introduce a *different* canonical value for that term.
- Detection is heuristic: inline definitions matched against patterns
  ``<term> means <value>``, ``<term> is defined as <value>``,
  ``<term> = <value>``, ``<term>: <value>`` (short, no list bullets).
- Detected alternatives are normalised (whitespace + lowercase + trailing
  punctuation stripped) and compared to the pinned canonical value. If
  the pinned value isn't a substring of what the output said, fail.

This avoids the spec's flagship case — the LLM redefining ``churn`` as
revenue churn when the org pinned it as logo churn — without doing full
semantic entailment.
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
from orchestrator.runtime.verifiers.policy import _iter_text_fields

# Phrases that strongly suggest the writer is *defining* a term inline.
_DEFINITION_PATTERNS = [
    re.compile(
        r"\b{term}\b\s+(?:means|is\s+defined\s+as|is\s+just|refers\s+to|=)\s+(?P<value>[^\.\n]{{2,160}})",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b{term}\b\s*:\s*(?P<value>[^\.\n]{{2,80}})",
        re.IGNORECASE,
    ),
]


def _normalise(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower().rstrip(".,;:")


class GlossaryVerifier:
    verifier_id: str = "glossary"

    async def verify(self, output: dict[str, Any], ctx: VerifierContext) -> VerifierResult:
        start = time.perf_counter()
        failures: list[VerifierFailure] = []
        glossary = _coerce_glossary(ctx.task_glossary)

        if glossary:
            for path, text in _iter_text_fields(output):
                for term, entry in glossary.items():
                    pinned = _normalise(entry["value"])
                    if not pinned:
                        continue
                    for found in _extract_definitions(text, term):
                        if _normalise(found) == pinned:
                            continue
                        if _contains(text=found, target=entry["value"]):
                            continue
                        failures.append(
                            VerifierFailure(
                                verifier_id=self.verifier_id,
                                rule="glossary_contradiction",
                                field=path,
                                message=(
                                    f"output at {path} defines {term!r} as "
                                    f"{found!r}; pinned glossary says "
                                    f"{entry['value']!r} (source={entry['source']})"
                                ),
                            )
                        )

        outcome = VerifierOutcome.FAIL if failures else VerifierOutcome.PASS
        return VerifierResult(
            verifier_id=self.verifier_id,
            outcome=outcome,
            failures=tuple(failures),
            elapsed_ms=(time.perf_counter() - start) * 1000.0,
        )


def _coerce_glossary(raw: dict[str, Any]) -> dict[str, dict[str, str]]:
    """Accept the runtime's loose glossary shape; emit a flat dict for scanning."""
    out: dict[str, dict[str, str]] = {}
    for term, entry in raw.items():
        if isinstance(entry, dict):
            value = str(entry.get("value", ""))
            source = str(entry.get("source", "unknown"))
        else:
            value = str(entry)
            source = "user_specified"
        if value:
            out[term] = {"value": value, "source": source}
    return out


def _extract_definitions(text: str, term: str) -> list[str]:
    """Find inline definitions of ``term`` in ``text``."""
    out: list[str] = []
    for pattern in _DEFINITION_PATTERNS:
        regex = re.compile(pattern.pattern.format(term=re.escape(term)), re.IGNORECASE)
        for match in regex.finditer(text):
            value = match.group("value").strip()
            if value:
                out.append(value)
    return out


def _contains(*, text: str, target: str) -> bool:
    """Does ``target`` (normalised) appear inside ``text`` (normalised)?"""
    return _normalise(target) in _normalise(text)
