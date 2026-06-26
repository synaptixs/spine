from __future__ import annotations

from typing import Any

import pytest

from orchestrator.gateway import (
    HandlerRegistry,
    InvocationContext,
    ToolHandler,
)


class _EchoHandler:
    contract_id = "tool.echo"
    contract_version = "0.1.0"

    async def __call__(self, inputs: dict[str, Any], ctx: InvocationContext) -> dict[str, Any]:
        return {"echoed": inputs}


def test_handler_satisfies_protocol() -> None:
    assert isinstance(_EchoHandler(), ToolHandler)


def test_register_and_get_round_trip() -> None:
    reg = HandlerRegistry()
    handler = _EchoHandler()
    reg.register(handler)
    assert reg.get("tool.echo", "0.1.0") is handler
    assert reg.get("tool.echo", "9.9.9") is None
    assert ("tool.echo", "0.1.0") in reg.keys()  # noqa: SIM118 — keys() is a method, not a dict view


def test_duplicate_registration_rejected() -> None:
    reg = HandlerRegistry()
    reg.register(_EchoHandler())
    with pytest.raises(ValueError, match="already registered"):
        reg.register(_EchoHandler())


def test_clear_empties_registry() -> None:
    reg = HandlerRegistry()
    reg.register(_EchoHandler())
    reg.clear()
    assert reg.keys() == []
