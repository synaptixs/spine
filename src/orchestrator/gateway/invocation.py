"""Invocation primitives shared by handlers, the gateway, and clients."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class InvocationOutcome(str, Enum):
    SUCCESS = "success"
    ERROR = "error"


@dataclass(frozen=True)
class InvocationContext:
    """Per-call context passed to a ToolHandler.

    Handlers receive this so they can attribute telemetry, propagate the
    trace id, and write structured logs without reaching for globals.
    """

    tool_id: str
    tool_version: str
    trace_id: str
    actor: str
    task_id: str | None = None
    agent_template_id: str | None = None
    credentials: dict[str, str] = field(default_factory=dict)


@dataclass
class InvocationResult:
    """Captured outcome of a single tool call."""

    outcome: InvocationOutcome
    output: dict[str, Any] | None = None
    elapsed_ms: float = 0.0
    cost_usd: float = 0.0
    error_type: str | None = None
    error_message: str | None = None
