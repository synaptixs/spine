"""The output drift guard.

Every tool's declared output type (``plugin/outputs.py``) is what a host reads before it
calls; a key a tool returns that the type does not declare is a field the host never learns
about — and pydantic would drop it from the structured result silently if the types were
strict. So every tool is wrapped for the whole test session: after each call, any returned
key the type does not declare fails the test that made the call, naming the tool and the
key path. The existing tests then exercise the declared shapes for free.

The wrapping happens at conftest import, before the test modules bind the names with
``from orchestrator.plugin.server import blast_radius``.
"""

from __future__ import annotations

import functools
import inspect
from typing import Any

import orchestrator.plugin.server as server
from orchestrator.plugin.outputs import OUTPUTS, undeclared_keys


def _check(name: str, result: Any) -> Any:
    if isinstance(result, dict):
        extra = undeclared_keys(result, OUTPUTS[name])
        assert not extra, (
            f"{name} returned keys its output type does not declare: {extra} — add them to plugin/outputs.py"
        )
    return result


def _guarded(fn: Any) -> Any:
    name = fn.__name__
    if inspect.iscoroutinefunction(fn):

        @functools.wraps(fn)
        async def async_wrapper(*a: Any, **k: Any) -> Any:
            return _check(name, await fn(*a, **k))

        return async_wrapper

    @functools.wraps(fn)
    def wrapper(*a: Any, **k: Any) -> Any:
        return _check(name, fn(*a, **k))

    return wrapper


_wrapped = tuple(_guarded(_fn) for _fn in server._TOOLS)
for _fn in _wrapped:
    setattr(server, _fn.__name__, _fn)
# The tuple too, so `tool in _TOOLS` identity checks hold and registration wraps the guarded
# functions (which carry the originals' names, signatures and annotations).
server._TOOLS = _wrapped
