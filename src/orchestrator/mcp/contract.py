"""Derive a registry ``ToolContract`` from a discovered MCP tool.

Turns an onboarded MCP tool into a first-class, governed gateway tool: a
``ToolContract`` carrying its inputs (from the tool's JSON Schema), its
side-effect class (from the server's read-only hint), and an approval policy.
This is what lets MCP tools ride the gateway's existing rate-limit + audit +
approval machinery instead of being an ungoverned side channel.
"""

from __future__ import annotations

import re

from orchestrator.mcp.models import MCPTool
from orchestrator.registry._common import Metadata
from orchestrator.registry.agent_template import FieldSchema
from orchestrator.registry.tool_contract import (
    ApprovalPolicy,
    SideEffect,
    ToolContract,
    ToolSpec,
)

CONTRACT_VERSION = "0.1.0"

# JSON Schema type → the FieldSchema type strings the registry uses.
_JSON_TYPES = {"string", "number", "integer", "boolean", "object", "array", "null"}


def _slug(value: str) -> str:
    """Coerce a server/tool name into one valid ResourceId segment.

    Registry ids are ``^[a-z][a-z0-9_]*`` per segment, so lowercase, replace
    anything else with ``_``, and prefix a letter if it would start with a digit.
    """
    s = re.sub(r"[^a-z0-9_]", "_", value.lower()).strip("_") or "x"
    return s if s[0].isalpha() else f"t_{s}"


def contract_id_for(tool: MCPTool) -> str:
    """``mcp.<server>.<tool>`` — the registry id this tool is contracted under."""
    return f"mcp.{_slug(tool.server)}.{_slug(tool.name)}"


def _inputs_from_schema(schema: dict[str, object]) -> list[FieldSchema]:
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return []
    required = schema.get("required")
    required_names = set(required) if isinstance(required, list) else set()
    fields: list[FieldSchema] = []
    for name, raw in properties.items():
        spec = raw if isinstance(raw, dict) else {}
        json_type = spec.get("type")
        field_type = json_type if json_type in _JSON_TYPES else "string"
        fields.append(
            FieldSchema(
                name=str(name),
                type=str(field_type),
                description=str(spec.get("description") or ""),
                required=name in required_names,
            )
        )
    return fields


def mcp_tool_to_contract(tool: MCPTool, *, version: str = CONTRACT_VERSION) -> ToolContract:
    """Build a published-shape ``ToolContract`` for an MCP tool.

    A tool the server flags ``readOnlyHint=true`` is a READ (idempotent, no
    approval). Anything else is treated as a WRITE: non-idempotent (so it gets
    an ``idempotency_key`` input, which the contract requires) and gated behind
    conditional approval — conservative, since MCP doesn't tell us more.
    """
    read_only = tool.read_only is True
    inputs = _inputs_from_schema(tool.input_schema)
    if not read_only and not any(f.name == "idempotency_key" for f in inputs):
        inputs.append(
            FieldSchema(
                name="idempotency_key",
                type="string",
                description="Caller-supplied key to make this mutating call idempotent.",
                required=False,
            )
        )
    return ToolContract(
        metadata=Metadata(
            id=contract_id_for(tool),
            version=version,
            description=(tool.description or f"MCP tool {tool.qualified_name}")[:1024],
            tags=["mcp", _slug(tool.server)],
        ),
        spec=ToolSpec(
            purpose=(tool.description or f"MCP tool {tool.qualified_name}")[:1024],
            inputs=inputs,
            outputs=[
                FieldSchema(name="text", type="string", description="Tool result text.", required=False)
            ],
            side_effects=SideEffect.READ if read_only else SideEffect.WRITE,
            idempotent=read_only,
            requires_approval=ApprovalPolicy.NEVER if read_only else ApprovalPolicy.CONDITIONAL,
        ),
    )


__all__ = ["CONTRACT_VERSION", "contract_id_for", "mcp_tool_to_contract"]
