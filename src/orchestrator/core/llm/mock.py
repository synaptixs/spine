"""MockLLMClient: replay pre-recorded responses keyed by prompt fingerprint.

Used in unit tests and CI golden tests to keep them hermetic and
deterministic. Fixtures live under ``tests/fixtures/llm/`` as one JSON file
per fingerprint::

    {
      "text": "...",
      "prompt_tokens": 123,
      "completion_tokens": 45,
      "cost_usd": 0.001,
      "model": "claude-opus-4-7"
    }

Record new fixtures by calling ``record_fixture(...)`` against a real run.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel

from orchestrator.core.llm.client import (
    CompletionResult,
    LLMError,
    Message,
    ToolSpec,
    prompt_fingerprint,
)


class MissingFixtureError(LLMError):
    """No recorded fixture matches the requested prompt."""


def fixture_path_for(fingerprint: str, *, root: Path | None = None) -> Path:
    base = root or _default_fixture_root()
    return base / f"{fingerprint}.json"


def record_fixture(
    *,
    messages: list[Message],
    model: str,
    result: CompletionResult,
    root: Path | None = None,
) -> Path:
    """Persist a real-run result so a later test can replay it."""
    base = root or _default_fixture_root()
    base.mkdir(parents=True, exist_ok=True)
    fingerprint = prompt_fingerprint(messages, model=model)
    path = base / f"{fingerprint}.json"
    payload = {
        "text": result.text,
        "model": result.model,
        "prompt_tokens": result.prompt_tokens,
        "completion_tokens": result.completion_tokens,
        "cost_usd": result.cost_usd,
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


class MockLLMClient:
    """LLM client that returns pre-recorded responses or inline-registered ones."""

    def __init__(
        self,
        *,
        fixture_root: Path | None = None,
        script: list[CompletionResult] | None = None,
    ) -> None:
        self._root = fixture_root or _default_fixture_root()
        self._inline: dict[str, CompletionResult] = {}
        # An ordered queue of results returned regardless of prompt — for
        # multi-turn agentic-loop tests, where each step's prompt differs so
        # fingerprint keying is impractical. Popped front-to-back per call.
        self._script: list[CompletionResult] = list(script or [])
        self.calls: list[tuple[str, list[Message]]] = []

    def register(self, *, messages: list[Message], model: str, result: CompletionResult) -> None:
        """Bind a response in-memory; useful for unit tests that don't want files."""
        self._inline[prompt_fingerprint(messages, model=model)] = result

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
    ) -> CompletionResult:
        _ = (response_format, json_object, temperature, max_tokens, tools)
        self.calls.append((model, list(messages)))
        if self._script:
            return self._script.pop(0)
        fingerprint = prompt_fingerprint(messages, model=model)

        if fingerprint in self._inline:
            return self._inline[fingerprint]

        path = self._root / f"{fingerprint}.json"
        if not path.exists():
            raise MissingFixtureError(
                f"No fixture for prompt fingerprint {fingerprint[:12]}... at {path}. "
                "Record one with orchestrator.core.llm.record_fixture()."
            )

        payload = json.loads(path.read_text(encoding="utf-8"))
        return CompletionResult(
            text=payload["text"],
            model=payload.get("model", model),
            prompt_tokens=int(payload.get("prompt_tokens", 0)),
            completion_tokens=int(payload.get("completion_tokens", 0)),
            cost_usd=float(payload.get("cost_usd", 0.0)),
            latency_ms=0.0,
        )


def _default_fixture_root() -> Path:
    """Resolve ``tests/fixtures/llm`` relative to the repo root."""
    here = Path(__file__).resolve()
    for parent in [here, *here.parents]:
        candidate = parent / "tests" / "fixtures" / "llm"
        if candidate.exists():
            return candidate
    # Fall back: assume tests live two levels up from src/orchestrator/core/llm
    return here.parents[4] / "tests" / "fixtures" / "llm"
