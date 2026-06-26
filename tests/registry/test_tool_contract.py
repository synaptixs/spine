from __future__ import annotations

import pytest
from pydantic import ValidationError

from orchestrator.registry._common import Metadata
from orchestrator.registry.agent_template import FieldSchema
from orchestrator.registry.tool_contract import (
    ApprovalPolicy,
    Observability,
    SideEffect,
    ToolContract,
    ToolSpec,
)


def _meta() -> Metadata:
    return Metadata(id="tool.web_search", version="0.1.0", description="x")


def test_read_idempotent_tool_valid() -> None:
    contract = ToolContract(
        metadata=_meta(),
        spec=ToolSpec(
            purpose="Search the web.",
            side_effects=SideEffect.READ,
            idempotent=True,
        ),
    )
    assert contract.spec.requires_approval is ApprovalPolicy.NEVER


def test_non_idempotent_without_idempotency_key_rejected() -> None:
    with pytest.raises(ValidationError, match="idempotency_key"):
        ToolSpec(
            purpose="Charge a card.",
            side_effects=SideEffect.WRITE,
            idempotent=False,
        )


def test_non_idempotent_with_idempotency_key_accepted() -> None:
    spec = ToolSpec(
        purpose="Charge a card.",
        inputs=[FieldSchema(name="idempotency_key", type="str")],
        side_effects=SideEffect.WRITE,
        idempotent=False,
    )
    assert spec.idempotent is False


def test_destructive_requires_always_approval() -> None:
    with pytest.raises(ValidationError, match="requires_approval='always'"):
        ToolSpec(
            purpose="Delete a record.",
            inputs=[FieldSchema(name="idempotency_key", type="str")],
            side_effects=SideEffect.DESTRUCTIVE,
            idempotent=False,
            requires_approval=ApprovalPolicy.CONDITIONAL,
        )


def test_destructive_with_always_approval_accepted() -> None:
    spec = ToolSpec(
        purpose="Delete a record.",
        inputs=[FieldSchema(name="idempotency_key", type="str")],
        side_effects=SideEffect.DESTRUCTIVE,
        idempotent=False,
        requires_approval=ApprovalPolicy.ALWAYS,
    )
    assert spec.side_effects is SideEffect.DESTRUCTIVE


def test_audit_cannot_be_disabled() -> None:
    with pytest.raises(ValidationError, match="audit"):
        ToolSpec(
            purpose="x",
            side_effects=SideEffect.READ,
            idempotent=True,
            observability=Observability(audit=False),
        )
