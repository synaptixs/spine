"""Human-readable type labels for MCP tool arguments.

An MCP server describes each tool's arguments as a JSON Schema
(``MCPTool.input_schema``). The registry's ``ToolContract`` keeps only a
normalised field list, so the raw declared type (unions, ``anyOf``, ``$ref``)
is lost by the time ``orchestrator mcp contracts`` renders it. These helpers
read the schema at *display time* and derive a short scannable label such as
``string`` or ``string|null`` — nothing here mutates stored state or affects
how ``mcp call`` serialises arguments.
"""

from __future__ import annotations

from typing import Any

# Shown when the schema declares no usable top-level ``type`` (``anyOf``,
# ``$ref``, ``const``, a missing property, or no schema at all).
UNKNOWN_TYPE = "any"


def _label_from_type(raw: Any) -> str:
    """Render a JSON Schema ``type`` value as one label (``["a","b"]`` → ``a|b``)."""
    if isinstance(raw, str):
        return raw.strip() or UNKNOWN_TYPE
    if isinstance(raw, list):
        parts = [
            str(item).strip() for item in raw if isinstance(item, str | int | float) and str(item).strip()
        ]
        return "|".join(parts) if parts else UNKNOWN_TYPE
    return UNKNOWN_TYPE


def argument_type_label(schema: dict[str, Any] | None, name: str) -> str:
    """Label for argument ``name`` in ``schema``, or ``any`` when undeclared.

    Tolerant by design: a server that nests its properties behind ``$defs`` /
    ``$ref``, or omits ``input_schema`` entirely, falls back to ``any`` rather
    than raising — the contracts view must never fail on an odd schema.
    """
    if not isinstance(schema, dict):
        return UNKNOWN_TYPE
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return UNKNOWN_TYPE
    spec = properties.get(name)
    if not isinstance(spec, dict):
        return UNKNOWN_TYPE
    return _label_from_type(spec.get("type"))


def argument_type_labels(schema: dict[str, Any] | None, names: list[str]) -> dict[str, str]:
    """``{name: label}`` for each requested argument, in the order given."""
    return {name: argument_type_label(schema, name) for name in names}


def format_argument(name: str, label: str) -> str:
    """Render one argument for display: ``name (type)``."""
    return f"{name} ({label})"


__all__ = [
    "UNKNOWN_TYPE",
    "argument_type_label",
    "argument_type_labels",
    "format_argument",
]
