"""All bundled agent templates must parse via load_agent_template."""

from __future__ import annotations

from pathlib import Path

import pytest

from orchestrator.registry.agent_template import AgentTemplate
from orchestrator.registry.loader import load_agent_template

EXAMPLES = Path(__file__).resolve().parents[2] / "examples" / "templates"

TEMPLATE_FILES = sorted(EXAMPLES.glob("*.yaml"))


@pytest.mark.parametrize("path", TEMPLATE_FILES, ids=lambda p: p.name)
def test_each_example_template_parses(path: Path) -> None:
    template = load_agent_template(path)
    assert isinstance(template, AgentTemplate)
    output_names = {f.name for f in template.spec.outputs}
    assert {"confidence", "caveats"} <= output_names, f"{path.name}: missing mandatory output fields"


def test_all_expected_templates_present() -> None:
    expected = {
        "data_analyst.yaml",
        "code_executor.yaml",
        "document_summarizer.yaml",
        "business_writer.yaml",
        "policy_checker.yaml",
        "research_agent.yaml",
    }
    actual = {p.name for p in TEMPLATE_FILES}
    assert expected <= actual, expected - actual
