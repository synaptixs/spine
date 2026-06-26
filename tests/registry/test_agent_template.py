from __future__ import annotations

import pytest
from pydantic import ValidationError

from orchestrator.registry._common import LifecycleState, Metadata
from orchestrator.registry.agent_template import AgentSpec, AgentTemplate, FieldSchema


def _minimal_outputs() -> list[FieldSchema]:
    return [
        FieldSchema(name="confidence", type="float"),
        FieldSchema(name="caveats", type="list[str]"),
        FieldSchema(name="findings", type="str"),
    ]


def _valid_template(**overrides: object) -> AgentTemplate:
    payload: dict[str, object] = {
        "metadata": Metadata(
            id="research.summarizer",
            version="0.1.0",
            description="Summarizes research findings.",
        ),
        "spec": AgentSpec(outputs=_minimal_outputs(), model="claude-opus-4-7"),
    }
    payload.update(overrides)
    return AgentTemplate(**payload)  # type: ignore[arg-type]


def test_valid_template_round_trips() -> None:
    template = _valid_template()
    assert template.metadata.id == "research.summarizer"
    assert template.status.state == LifecycleState.DRAFT


def test_invalid_id_rejected() -> None:
    with pytest.raises(ValidationError):
        Metadata(id="Bad-ID", version="0.1.0", description="x")


def test_invalid_semver_rejected() -> None:
    with pytest.raises(ValidationError):
        Metadata(id="ok.id", version="1.0", description="x")


def test_missing_mandatory_outputs_rejected() -> None:
    outputs = [FieldSchema(name="findings", type="str")]
    with pytest.raises(ValidationError) as exc:
        AgentSpec(outputs=outputs, model="claude-opus-4-7")
    assert "confidence" in str(exc.value)
    assert "caveats" in str(exc.value)


def test_extra_fields_rejected() -> None:
    with pytest.raises(ValidationError):
        AgentTemplate(
            metadata=Metadata(id="ok.id", version="0.1.0", description="x"),
            spec=AgentSpec(outputs=_minimal_outputs(), model="m"),
            extra_field="nope",  # type: ignore[call-arg]
        )
