"""`_to_tool` reads the SDK's own field names — so a rename must fail a test, not a run.

Every other test here builds an ``MCPTool`` (our dataclass) directly, which never exercises
the translation from the SDK's ``Tool``. So when v2 renamed ``inputSchema`` to
``input_schema`` and ``readOnlyHint`` to ``read_only_hint``, the defaulted ``getattr`` calls
returned ``None`` instead of raising: every tool silently lost its argument types, which
took the ``mcp contracts`` type labels with it and stopped ``MCPRegistry.call`` coercing a
structured argument a server declares as a string. Nothing went red.

These build a real ``mcp.types.Tool``, so the next rename is a failing test.
"""

from __future__ import annotations

import pytest

from orchestrator.mcp.client import SessionMCPClient
from orchestrator.mcp.config import MCPServerConfig

mcp_types = pytest.importorskip("mcp.types", reason="needs the 'mcp' extra")

_SCHEMA = {
    "type": "object",
    "properties": {
        "project_key": {"type": "string"},
        "additional_fields": {"type": "string"},
    },
}


def _client() -> SessionMCPClient:
    return SessionMCPClient(MCPServerConfig(name="atlassian", command="x"))


def _sdk_tool(**kw: object) -> object:
    return mcp_types.Tool(name="jira_create_issue", inputSchema=_SCHEMA, **kw)


def test_the_input_schema_survives_the_translation() -> None:
    """The whole chain downstream — type labels and argument coercion — needs this."""
    tool = _client()._to_tool(_sdk_tool())

    assert tool.input_schema == _SCHEMA
    assert tool.name == "jira_create_issue"


def test_a_declared_string_argument_is_still_visible_as_one() -> None:
    """What `mcp contracts` renders and what `MCPRegistry.call` coerces on."""
    from orchestrator.mcp.schema_types import argument_type_label

    tool = _client()._to_tool(_sdk_tool())

    assert argument_type_label(tool.input_schema, "additional_fields") == "string"


def test_the_read_only_hint_survives_the_translation() -> None:
    annotated = _sdk_tool(annotations=mcp_types.ToolAnnotations(readOnlyHint=True))

    assert _client()._to_tool(annotated).read_only is True


def test_a_tool_without_annotations_reports_unknown_not_false() -> None:
    """`None` means "the server did not say"; `False` would claim it declared a write."""
    assert _client()._to_tool(_sdk_tool()).read_only is None
