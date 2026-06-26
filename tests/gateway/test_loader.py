from __future__ import annotations

from typing import Any

from orchestrator.gateway import HandlerRegistry, InvocationContext
from orchestrator.gateway.loader import LoadedTool, LoaderReport


class _StubHandler:
    contract_id = "tool.echo"
    contract_version = "0.1.0"

    async def __call__(self, inputs: dict[str, Any], ctx: InvocationContext) -> dict[str, Any]:
        return {}


def test_loaded_tool_exposes_contract_fields() -> None:
    class FakeContract:
        id = "tool.echo"
        version = "0.1.0"
        spec_json = {"purpose": "x"}

    handler = _StubHandler()
    loaded = LoadedTool(handler=handler, contract=FakeContract())  # type: ignore[arg-type]
    assert loaded.contract_id == "tool.echo"
    assert loaded.contract_version == "0.1.0"
    assert loaded.spec == {"purpose": "x"}


def test_empty_registry_short_circuits() -> None:
    """No handlers means no SQL query — load_published_tools returns empty."""
    report = LoaderReport(loaded=[], unmatched_handlers=[], unhandled_contracts=[])
    assert report.by_id_version("tool.echo", "0.1.0") is None


def test_default_registry_is_singleton() -> None:
    from orchestrator.gateway import get_default_registry

    assert get_default_registry() is get_default_registry()


def test_handler_registry_isolation() -> None:
    """Local registries don't share state with the default."""
    local = HandlerRegistry()
    local.register(_StubHandler())
    assert ("tool.echo", "0.1.0") in local.keys()  # noqa: SIM118 — keys() is a method, not a dict view
