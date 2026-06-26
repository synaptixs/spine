"""Load AgentTemplate and ToolContract definitions from disk.

Used by the examples bundle and by tests that want to instantiate a real
template without hand-writing the Pydantic structure each time.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from orchestrator.registry.agent_template import AgentTemplate
from orchestrator.registry.tool_contract import ToolContract


def _load_dict(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    loaded = yaml.safe_load(text) if path.suffix.lower() in {".yaml", ".yml"} else json.loads(text)
    if not isinstance(loaded, dict):
        raise ValueError(f"{path}: expected a JSON object at the top level")
    return loaded


def load_agent_template(path: str | Path) -> AgentTemplate:
    return AgentTemplate.model_validate(_load_dict(Path(path)))


def load_tool_contract(path: str | Path) -> ToolContract:
    return ToolContract.model_validate(_load_dict(Path(path)))
