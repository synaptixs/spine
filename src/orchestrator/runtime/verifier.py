"""Terminal SchemaVerifier: validates a node's output against the template's schema.

Per-edge confidence/evidence/policy verifiers land in Sprint 10. Sprint 5
only needs the schema check — every node-emitted output must have the fields
the agent template declares, including the mandatory `confidence` (in [0, 1])
and `caveats` (list).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from orchestrator.registry.agent_template import AgentSpec, AgentTemplate, FieldSchema
from orchestrator.runtime.post_conditions import (
    MinConfidenceRule,
    PostCondition,
    evaluate_post_conditions,
)


class VerifierOutcome(str, Enum):
    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"


@dataclass(frozen=True)
class VerifierFailure:
    field: str
    rule: str
    message: str


@dataclass(frozen=True)
class VerifierResult:
    outcome: VerifierOutcome
    failures: tuple[VerifierFailure, ...] = field(default_factory=tuple)

    def to_state_value(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome.value,
            "failures": [{"field": f.field, "rule": f.rule, "message": f.message} for f in self.failures],
        }


_PRIMITIVE_TYPES: dict[str, tuple[type, ...]] = {
    "str": (str,),
    "string": (str,),
    "int": (int,),
    "integer": (int,),
    "float": (int, float),
    "number": (int, float),
    "bool": (bool,),
    "boolean": (bool,),
    "list": (list,),
    "dict": (dict,),
    "object": (dict,),
}


def _type_matches(declared: str, value: Any) -> bool:
    """Best-effort type check that tolerates generics like ``list[str]``."""
    base = declared.split("[", 1)[0].strip().lower()
    types = _PRIMITIVE_TYPES.get(base)
    if types is None:
        return True  # unknown / custom types are accepted; spec downstream will tighten
    return isinstance(value, types)


class SchemaVerifier:
    """Pure-Python output validator. Decoupled from LangGraph for unit testing."""

    def __init__(self, spec: AgentSpec) -> None:
        self._spec = spec

    @classmethod
    def from_template(cls, template: AgentTemplate) -> SchemaVerifier:
        return cls(template.spec)

    def verify(self, output: dict[str, Any]) -> VerifierResult:
        failures: list[VerifierFailure] = []
        declared: dict[str, FieldSchema] = {f.name: f for f in self._spec.outputs}

        for name, decl in declared.items():
            if name not in output:
                if decl.required:
                    failures.append(
                        VerifierFailure(field=name, rule="missing", message=f"required field {name!r} absent")
                    )
                continue
            if not _type_matches(decl.type, output[name]):
                failures.append(
                    VerifierFailure(
                        field=name,
                        rule="type_mismatch",
                        message=f"expected {decl.type!r}, got {type(output[name]).__name__}",
                    )
                )

        if "confidence" in output:
            failures.extend(_check_confidence(output["confidence"]))
        if "caveats" in output:
            failures.extend(_check_caveats(output["caveats"]))

        if any(f.rule == "missing" or f.rule == "type_mismatch" for f in failures):
            return VerifierResult(outcome=VerifierOutcome.FAIL, failures=tuple(failures))
        if failures:
            return VerifierResult(outcome=VerifierOutcome.WARN, failures=tuple(failures))
        return VerifierResult(outcome=VerifierOutcome.PASS)


def _check_confidence(value: Any) -> list[VerifierFailure]:
    if not isinstance(value, (int, float)):
        return [
            VerifierFailure(
                field="confidence",
                rule="type_mismatch",
                message=f"expected number in [0,1], got {type(value).__name__}",
            )
        ]
    if not 0.0 <= float(value) <= 1.0:
        return [
            VerifierFailure(
                field="confidence",
                rule="out_of_range",
                message=f"confidence={value} not in [0, 1]",
            )
        ]
    return []


def _check_caveats(value: Any) -> list[VerifierFailure]:
    if not isinstance(value, list):
        return [
            VerifierFailure(
                field="caveats",
                rule="type_mismatch",
                message=f"expected list, got {type(value).__name__}",
            )
        ]
    return []


class SchemaVerifierNode:
    """Adapter that runs ``SchemaVerifier`` (and any post-conditions) against
    a named upstream node's output."""

    def __init__(
        self,
        template: AgentTemplate,
        *,
        target_node: str = "agent",
        verifier_id: str = "verify",
        post_conditions: list[PostCondition] | None = None,
        min_confidence: MinConfidenceRule | None = None,
    ) -> None:
        self._verifier = SchemaVerifier.from_template(template)
        self._target_node = target_node
        self._verifier_id = verifier_id
        self._post_conditions = list(post_conditions or [])
        self._min_confidence = min_confidence

    @property
    def verifier_id(self) -> str:
        return self._verifier_id

    async def __call__(self, state: dict[str, Any]) -> dict[str, Any]:
        upstream = (state.get("node_outputs") or {}).get(self._target_node, {})
        schema_result = self._verifier.verify(upstream)

        schema_value = schema_result.to_state_value()
        if self._post_conditions or self._min_confidence is not None:
            post_report = evaluate_post_conditions(
                upstream,
                self._post_conditions,
                min_confidence=self._min_confidence,
            )
            schema_value["post_conditions"] = post_report.to_state_value()
            # Schema failures still win over post-condition warnings; only
            # downgrade pass -> warn / fail when post-conditions are stricter.
            if not post_report.ok and schema_value["outcome"] == "pass":
                schema_value["outcome"] = "fail" if post_report.should_terminate else "warn"

        return {
            "node_outputs": {self._verifier_id: schema_value},
            "current_node_id": self._verifier_id,
        }
