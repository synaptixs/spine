"""Agentic codegen loop (Phase 5).

A bounded, budgeted, governed think → act → observe loop and the tools the agent
calls mid-task. 5a ships the loop machinery + read-only tools (PKG queries, file
read); write/test tools, governed MCP tools, and the codegen wiring follow in
later sub-phases.
"""

from orchestrator.agentic.export import build_run_bundle, render_bundle_markdown, replay_llm_from_trace
from orchestrator.agentic.loop import (
    AgentLoop,
    HumanDecision,
    LoopCheckpoint,
    LoopResult,
    PendingApproval,
    StepRecord,
    Tool,
)
from orchestrator.agentic.policy import Decision, Policy, PolicyAction, ToolRule
from orchestrator.agentic.tools import build_readonly_tools

__all__ = [
    "AgentLoop",
    "Decision",
    "HumanDecision",
    "LoopCheckpoint",
    "LoopResult",
    "PendingApproval",
    "Policy",
    "PolicyAction",
    "StepRecord",
    "Tool",
    "ToolRule",
    "build_readonly_tools",
    "build_run_bundle",
    "render_bundle_markdown",
    "replay_llm_from_trace",
]
