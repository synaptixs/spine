"""Common interface and types for LLM clients."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class LLMError(RuntimeError):
    """Base class for LLM client failures."""


class StructuredOutputError(LLMError):
    """Model returned content that did not parse against the requested schema."""


@dataclass(frozen=True)
class ToolSpec:
    """A tool offered to the model: name, description, JSON-Schema parameters."""

    name: str
    description: str
    parameters: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {"name": self.name, "description": self.description, "parameters": self.parameters},
        }


@dataclass(frozen=True)
class ToolCall:
    """A tool invocation the model requested."""

    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class Message:
    role: str
    content: str
    # Set on an assistant turn that requested tools; and on a ``tool`` turn
    # carrying a tool's result (paired by ``tool_call_id``). Both default empty
    # so plain single-shot callers are unchanged.
    tool_calls: tuple[ToolCall, ...] = ()
    tool_call_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"role": self.role, "content": self.content}
        if self.tool_calls:
            out["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)},
                }
                for tc in self.tool_calls
            ]
        if self.tool_call_id is not None:
            out["tool_call_id"] = self.tool_call_id
        return out


@dataclass(frozen=True)
class CompletionResult:
    """What a single ``complete()`` call produced."""

    text: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float
    latency_ms: float
    raw: dict[str, Any] | None = None
    # Tool calls the model requested this turn (empty for a plain text answer).
    tool_calls: tuple[ToolCall, ...] = ()


class LLMClient(Protocol):
    """Minimum surface every concrete client must implement."""

    async def complete(
        self,
        messages: list[Message],
        *,
        model: str,
        response_format: type[BaseModel] | None = None,
        json_object: bool = False,
        temperature: float | None = None,
        max_tokens: int | None = None,
        tools: list[ToolSpec] | None = None,
    ) -> CompletionResult: ...


def prompt_fingerprint(messages: list[Message], *, model: str) -> str:
    """Deterministic hash over (model, messages) used to key cached fixtures.

    Stable across runs because we serialize through canonical JSON. Used by
    ``MockLLMClient`` to look up a recorded response.
    """
    payload = {
        "model": model,
        "messages": [m.to_dict() for m in messages],
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
