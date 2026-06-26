"""Echo handler: returns its inputs back.

Trivial reference implementation used by the gateway integration tests and
as a sanity check when bringing the gateway up in a new environment.
"""

from __future__ import annotations

from typing import Any

from orchestrator.gateway.invocation import InvocationContext


class EchoHandler:
    contract_id: str = "tool.echo"
    contract_version: str = "0.1.0"

    async def __call__(self, inputs: dict[str, Any], ctx: InvocationContext) -> dict[str, Any]:
        return {"echoed": inputs, "trace_id": ctx.trace_id}


ECHO_CONTRACT_PAYLOAD: dict[str, Any] = {
    "metadata": {
        "id": "tool.echo",
        "version": "0.1.0",
        "description": "Returns the input payload unchanged.",
        "tags": ["dev"],
    },
    "spec": {
        "purpose": "Returns the input payload unchanged. Used for testing and bring-up.",
        "side_effects": "read",
        "idempotent": True,
        "rate_limits": {"requests_per_minute": 120, "burst": 20},
        "authentication": {"type": "none"},
        "observability": {"audit": True, "trace": True},
    },
}
