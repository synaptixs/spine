from __future__ import annotations

from pathlib import Path

from orchestrator.registry.agent_template import AgentTemplate
from orchestrator.registry.loader import load_agent_template

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_research_agent_template_parses() -> None:
    path = REPO_ROOT / "examples" / "templates" / "research_agent.yaml"
    template = load_agent_template(path)
    assert isinstance(template, AgentTemplate)
    assert template.metadata.id == "agent.research"
    assert template.metadata.version == "0.1.0"
    output_names = {f.name for f in template.spec.outputs}
    assert {"confidence", "caveats", "findings", "claims"} <= output_names
    assert "tool.web_search" in template.spec.allowed_tools
    assert template.spec.model.startswith("claude")
