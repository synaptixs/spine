"""LLM client: a thin wrapper over LiteLLM with structured output and a mock mode.

LiteLLM dispatches by model name, so a single client object can target
Anthropic, OpenAI, Bedrock, or any other supported provider without a
provider-specific code path. Agents declare the model in their template.

A ``MockLLMClient`` replays pre-recorded responses keyed by prompt hash so
the test suite stays deterministic in CI.
"""

from orchestrator.core.llm.budget import BudgetedLLMClient, BudgetExceededError, RunBudget
from orchestrator.core.llm.client import (
    CompletionResult,
    LLMClient,
    LLMError,
    Message,
    StructuredOutputError,
    ToolCall,
    ToolSpec,
)
from orchestrator.core.llm.litellm_client import LiteLLMClient
from orchestrator.core.llm.mock import MockLLMClient, fixture_path_for, record_fixture
from orchestrator.core.llm.recording import RecordingLLMClient, StageUsage, TokenLedger

__all__ = [
    "BudgetExceededError",
    "BudgetedLLMClient",
    "CompletionResult",
    "LLMClient",
    "LLMError",
    "LiteLLMClient",
    "Message",
    "MockLLMClient",
    "RecordingLLMClient",
    "RunBudget",
    "StageUsage",
    "StructuredOutputError",
    "ToolCall",
    "ToolSpec",
    "TokenLedger",
    "fixture_path_for",
    "record_fixture",
]
