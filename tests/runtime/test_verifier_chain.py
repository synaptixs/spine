from __future__ import annotations

import time
from typing import Any

import pytest

from orchestrator.registry._common import Metadata
from orchestrator.registry.agent_template import AgentSpec, AgentTemplate, FieldSchema
from orchestrator.runtime.verifiers import (
    VerifierChain,
    VerifierContext,
    VerifierFailure,
    VerifierOutcome,
    VerifierResult,
)


class _FakeVerifier:
    def __init__(self, verifier_id: str, outcome: VerifierOutcome, *, calls: list[str]) -> None:
        self.verifier_id = verifier_id
        self._outcome = outcome
        self._calls = calls

    async def verify(self, output: dict[str, Any], ctx: VerifierContext) -> VerifierResult:
        self._calls.append(self.verifier_id)
        time.sleep(0)  # deterministic but real elapsed
        failures = (
            (
                VerifierFailure(
                    verifier_id=self.verifier_id,
                    rule="r",
                    field="f",
                    message="m",
                    severity=self._outcome,
                ),
            )
            if self._outcome is not VerifierOutcome.PASS
            else ()
        )
        return VerifierResult(
            verifier_id=self.verifier_id,
            outcome=self._outcome,
            failures=failures,
            elapsed_ms=1.0,
        )


def _ctx() -> VerifierContext:
    template = AgentTemplate(
        metadata=Metadata(id="agent.x", version="0.1.0", description="x"),
        spec=AgentSpec(
            outputs=[
                FieldSchema(name="confidence", type="float"),
                FieldSchema(name="caveats", type="list[str]"),
            ],
            model="claude-haiku-4-5-20251001",
        ),
    )
    return VerifierContext(template=template, node_id="n_x", task_id="t", trace_id="tr")


async def test_chain_runs_all_verifiers_when_all_pass() -> None:
    calls: list[str] = []
    chain = VerifierChain(
        [
            _FakeVerifier("a", VerifierOutcome.PASS, calls=calls),
            _FakeVerifier("b", VerifierOutcome.PASS, calls=calls),
            _FakeVerifier("c", VerifierOutcome.PASS, calls=calls),
        ]
    )
    result = await chain.run({}, _ctx())
    assert calls == ["a", "b", "c"]
    assert result.outcome is VerifierOutcome.PASS
    assert result.short_circuited_after is None
    assert set(result.per_verifier) == {"a", "b", "c"}


async def test_chain_short_circuits_on_fail() -> None:
    calls: list[str] = []
    chain = VerifierChain(
        [
            _FakeVerifier("a", VerifierOutcome.PASS, calls=calls),
            _FakeVerifier("b", VerifierOutcome.FAIL, calls=calls),
            _FakeVerifier("c", VerifierOutcome.PASS, calls=calls),
        ]
    )
    result = await chain.run({}, _ctx())
    assert calls == ["a", "b"]
    assert result.outcome is VerifierOutcome.FAIL
    assert result.short_circuited_after == "b"
    assert "c" not in result.per_verifier


async def test_chain_does_not_short_circuit_on_warn() -> None:
    calls: list[str] = []
    chain = VerifierChain(
        [
            _FakeVerifier("a", VerifierOutcome.WARN, calls=calls),
            _FakeVerifier("b", VerifierOutcome.PASS, calls=calls),
        ]
    )
    result = await chain.run({}, _ctx())
    assert calls == ["a", "b"]
    assert result.outcome is VerifierOutcome.WARN
    assert result.short_circuited_after is None


async def test_chain_to_state_value_serialises_per_verifier() -> None:
    calls: list[str] = []
    chain = VerifierChain(
        [
            _FakeVerifier("a", VerifierOutcome.PASS, calls=calls),
            _FakeVerifier("b", VerifierOutcome.WARN, calls=calls),
        ],
        chain_id="agent_edge",
    )
    result = await chain.run({}, _ctx())
    payload = result.to_state_value()
    assert payload["chain_id"] == "agent_edge"
    assert payload["outcome"] == "warn"
    assert set(payload["per_verifier"]) == {"a", "b"}


def test_chain_rejects_empty_list() -> None:
    with pytest.raises(ValueError, match="at least one verifier"):
        VerifierChain([])


def test_chain_rejects_duplicate_ids() -> None:
    calls: list[str] = []
    with pytest.raises(ValueError, match="duplicate verifier ids"):
        VerifierChain(
            [
                _FakeVerifier("a", VerifierOutcome.PASS, calls=calls),
                _FakeVerifier("a", VerifierOutcome.PASS, calls=calls),
            ]
        )
