"""Read an MCP tool's declared argument types — to show them, and to honour them.

An MCP server describes each tool's arguments as a JSON Schema
(``MCPTool.input_schema``). The registry's ``ToolContract`` keeps only a
normalised field list, so the raw declared type (unions, ``anyOf``, ``$ref``)
is lost by the time ``orchestrator mcp contracts`` renders it. These helpers
read the schema directly and derive a short scannable label such as ``string``
or ``string|null``.

Two callers, from the same reading:

- **Display** (``argument_type_label``) — ``mcp contracts`` shows the declared
  shape so a caller sees it before a call fails.
- **Call** (``encode_for_schema``) — a server that types a structured argument
  as a JSON *string* gets one, so callers pass the natural object and no one
  double-encodes.

Neither mutates stored state; the labels are derived on every read.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
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


def needs_schema_lookup(arguments: Mapping[str, Any]) -> bool:
    """Whether any argument is structured, so coercion could apply.

    Fetching a tool's schema costs a round-trip (:class:`SessionMCPClient` opens a
    fresh session per operation). Only a ``dict``/``list`` value can ever need
    encoding, so a call passing scalars alone skips discovery entirely.
    """
    return any(isinstance(v, dict | list) for v in arguments.values())


def encode_for_schema(schema: dict[str, Any] | None, arguments: Mapping[str, Any]) -> dict[str, Any]:
    """JSON-encode arguments the server declares as ``string`` but were passed structured.

    Some servers type a structured argument as a *string* of JSON rather than an
    object — ``mcp-atlassian`` does this for ``jira_create_issue.additional_fields``.
    Without this, every caller has to ``json.dumps`` before calling, so a governed
    create double-encodes: once for the field, once for the transport. Callers that
    already pass a string are untouched, so both spellings work.

    Deliberately narrow. It encodes only when the declared type is exactly
    ``string`` (or ``string|null``) *and* the value is a ``dict``/``list``. A
    declared ``object``, an undeclared argument, or an unreadable schema is passed
    through as-is — guessing at a type the server did not state is how you corrupt
    a payload that was already correct.
    """
    if not isinstance(schema, dict):
        return dict(arguments)
    out = dict(arguments)
    for name, value in arguments.items():
        if not isinstance(value, dict | list):
            continue
        parts = {p for p in argument_type_label(schema, name).split("|") if p != "null"}
        if parts == {"string"}:
            out[name] = json.dumps(value)
    return out


__all__ = [
    "UNKNOWN_TYPE",
    "argument_type_label",
    "argument_type_labels",
    "encode_for_schema",
    "format_argument",
    "needs_schema_lookup",
]
