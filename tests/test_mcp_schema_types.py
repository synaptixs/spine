"""Tests for the human-readable argument type labels used by `mcp contracts`.

The labels are derived from ``MCPTool.input_schema`` at display time; these
tests pin the label rules (simple type, union, missing/odd schema) and the
``name (type)`` display format.
"""

from __future__ import annotations

from typing import Any

from orchestrator.mcp.schema_types import (
    UNKNOWN_TYPE,
    argument_type_label,
    argument_type_labels,
    format_argument,
)

_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "additional_fields": {"type": "string"},
        "limit": {"type": "integer"},
        "cursor": {"type": ["string", "null"]},
        "payload": {"anyOf": [{"type": "string"}, {"type": "object"}]},
        "linked": {"$ref": "#/$defs/Thing"},
    },
}


def test_simple_declared_type_is_surfaced() -> None:
    assert argument_type_label(_SCHEMA, "additional_fields") == "string"
    assert argument_type_label(_SCHEMA, "limit") == "integer"


def test_type_list_is_joined_with_a_pipe() -> None:
    assert argument_type_label(_SCHEMA, "cursor") == "string|null"


def test_anyof_and_ref_fall_back_to_any() -> None:
    assert argument_type_label(_SCHEMA, "payload") == UNKNOWN_TYPE == "any"
    assert argument_type_label(_SCHEMA, "linked") == "any"


def test_missing_property_and_missing_schema_fall_back_to_any() -> None:
    assert argument_type_label(_SCHEMA, "nope") == "any"
    assert argument_type_label(None, "anything") == "any"
    assert argument_type_label({}, "anything") == "any"
    assert argument_type_label({"properties": []}, "anything") == "any"
    assert argument_type_label({"properties": {"a": "string"}}, "a") == "any"


def test_blank_or_empty_type_values_fall_back_to_any() -> None:
    assert argument_type_label({"properties": {"a": {"type": "   "}}}, "a") == "any"
    assert argument_type_label({"properties": {"a": {"type": []}}}, "a") == "any"


def test_labels_preserve_requested_order() -> None:
    labels = argument_type_labels(_SCHEMA, ["limit", "additional_fields", "nope"])
    assert list(labels) == ["limit", "additional_fields", "nope"]
    assert labels == {"limit": "integer", "additional_fields": "string", "nope": "any"}


def test_format_argument_renders_name_and_type() -> None:
    assert format_argument("additional_fields", "string") == "additional_fields (string)"


def test_labelling_does_not_mutate_the_schema() -> None:
    schema: dict[str, Any] = {"properties": {"a": {"type": "string"}}}
    before = {"properties": {"a": {"type": "string"}}}
    argument_type_labels(schema, ["a", "missing"])
    assert schema == before


def test_user_guide_documents_argument_types_in_the_mcp_step() -> None:
    """Acceptance: the choice is documented where MCP server setup lives (step 9)."""
    from pathlib import Path

    guide = Path(__file__).resolve().parents[1] / "USER_GUIDE.md"
    text = guide.read_text(encoding="utf-8")
    assert "## Step 9 — Connect external tools (MCP)" in text
    start = text.index("## Step 9 — Connect external tools (MCP)")
    end = text.index("\n## ", start + 1)
    step9 = text[start:end]
    assert "mcp contracts" in step9
    lowered = step9.lower()
    assert "type" in lowered
    assert "(string)" in step9 or "string|null" in step9
