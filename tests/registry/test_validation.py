from __future__ import annotations

from typing import Any

from orchestrator.registry.validation import (
    ValidationFailure,
    ValidationReport,
    validate_agent_template_payload,
    validate_tool_contract_payload,
)


def _agent_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "metadata": {
            "id": "research.summarizer",
            "version": "0.1.0",
            "description": "Summarizes research findings.",
        },
        "spec": {
            "outputs": [
                {"name": "confidence", "type": "float"},
                {"name": "caveats", "type": "list[str]"},
            ],
            "model": "claude-opus-4-7",
        },
    }
    payload.update(overrides)
    return payload


def _tool_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "metadata": {
            "id": "tool.web_search",
            "version": "0.1.0",
            "description": "Search the web.",
        },
        "spec": {
            "purpose": "Search the web.",
            "side_effects": "read",
            "idempotent": True,
        },
    }
    payload.update(overrides)
    return payload


def test_valid_agent_payload_returns_model_and_empty_report() -> None:
    template, report = validate_agent_template_payload(_agent_payload())
    assert template is not None
    assert report.ok


def test_invalid_agent_payload_returns_structured_failures() -> None:
    payload = _agent_payload()
    payload["metadata"]["id"] = "BAD-ID"
    template, report = validate_agent_template_payload(payload)
    assert template is None
    assert not report.ok
    assert any("metadata.id" in f.field for f in report.failures)


def test_missing_mandatory_outputs_surfaces_in_report() -> None:
    payload = _agent_payload()
    payload["spec"]["outputs"] = [{"name": "findings", "type": "str"}]
    _, report = validate_agent_template_payload(payload)
    assert not report.ok
    assert any("confidence" in f.message and "caveats" in f.message for f in report.failures)


def test_valid_tool_payload() -> None:
    contract, report = validate_tool_contract_payload(_tool_payload())
    assert contract is not None
    assert report.ok


def test_destructive_tool_without_always_approval_rejected() -> None:
    payload = _tool_payload(
        spec={
            "purpose": "Delete a record.",
            "inputs": [{"name": "idempotency_key", "type": "str"}],
            "side_effects": "destructive",
            "idempotent": False,
            "requires_approval": "conditional",
        }
    )
    _, report = validate_tool_contract_payload(payload)
    assert not report.ok
    assert any("requires_approval" in f.message for f in report.failures)


def test_validation_failure_is_serialisable() -> None:
    failure = ValidationFailure(field="metadata.id", message="bad", rule="value_error")
    assert failure.model_dump()["field"] == "metadata.id"


def test_empty_report_ok_property() -> None:
    assert ValidationReport().ok
    assert not ValidationReport(failures=[ValidationFailure(field="x", message="y", rule="z")]).ok
