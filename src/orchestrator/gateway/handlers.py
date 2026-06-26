"""Tool handler protocol and the in-process handler registry.

A ``ToolHandler`` is an async callable that takes typed inputs plus an
``InvocationContext`` and returns a dict of outputs. Handlers register
themselves against ``(contract_id, contract_version)`` keys so the
gateway can match each handler to a published ToolContract at startup.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Protocol, runtime_checkable

from orchestrator.gateway.invocation import InvocationContext

HandlerCallable = Callable[[dict[str, Any], InvocationContext], Awaitable[dict[str, Any]]]


@runtime_checkable
class ToolHandler(Protocol):
    """A registered, transport-agnostic tool implementation."""

    contract_id: str
    contract_version: str

    async def __call__(self, inputs: dict[str, Any], ctx: InvocationContext) -> dict[str, Any]: ...


class HandlerRegistry:
    """Process-local store of registered tool handlers."""

    def __init__(self) -> None:
        self._handlers: dict[tuple[str, str], ToolHandler] = {}

    def register(self, handler: ToolHandler) -> None:
        key = (handler.contract_id, handler.contract_version)
        if key in self._handlers:
            raise ValueError(f"handler already registered for {key[0]}@{key[1]}")
        self._handlers[key] = handler

    def get(self, contract_id: str, contract_version: str) -> ToolHandler | None:
        return self._handlers.get((contract_id, contract_version))

    def keys(self) -> list[tuple[str, str]]:
        return list(self._handlers)

    def clear(self) -> None:
        self._handlers.clear()


_default = HandlerRegistry()


def get_default_registry() -> HandlerRegistry:
    return _default


def register_tool(*, contract_id: str, contract_version: str) -> Callable[[HandlerCallable], HandlerCallable]:
    """Decorator that wraps an async function into a ToolHandler and registers it."""

    def decorator(fn: HandlerCallable) -> HandlerCallable:
        class _Wrapped:
            def __init__(self) -> None:
                self.contract_id = contract_id
                self.contract_version = contract_version

            async def __call__(self, inputs: dict[str, Any], ctx: InvocationContext) -> dict[str, Any]:
                return await fn(inputs, ctx)

        _default.register(_Wrapped())
        return fn

    return decorator
