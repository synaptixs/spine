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
    # B77. The caller wants this schema enforced by the provider's native structured output where the model
    # supports it, instead of by a forced tool call. A request, not a requirement: the client decides (it
    # reads the model's capability) and falls back to the forced tool. Never sent to a provider: `to_dict`.
    native_schema: bool = False

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
    # False when the reply carried no prompt-token count, so ``prompt_tokens`` and
    # ``completion_tokens`` are 0 because they are *unknown*, not because nothing was used (B81). A real
    # call always has at least one prompt token; litellm pads a missing or null ``usage`` with zeros
    # before this code sees it, so the count itself is the only signal there is.
    usage_reported: bool = True
    # False when the price of ``model`` could not be looked up, so ``cost_usd`` is 0.0 because it is
    # *unknown*, not because the call was free. A price of exactly zero (a local model) is known.
    cost_known: bool = True


class LLMClient(Protocol):
    """Minimum surface every concrete client must implement.

    ``tool_choice`` names a tool in ``tools`` the model *must* call. That turns a
    tool into a schema the provider enforces — the only constraint on output shape
    that works on every provider, since ``json_object`` is an OpenAI/Ollama concept
    that Anthropic has no equivalent for and silently ignores.
    """

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
        tool_choice: str | None = None,
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
