"""Run export + replay (Bet 2b) — the compliance receipt.

``build_run_bundle`` turns a ``LoopResult`` into a self-contained, JSON-
serializable record: every step, every tool call + observation, every policy
block, cost, and any caller-supplied metadata (persona, capability plan, gate
decisions). It answers "what did the agent do, and what was refused?" —
the artifact an auditor or compliance reviewer reads.

``replay_llm_from_trace`` rebuilds a ``MockLLMClient`` from a recorded trace so a
run replays deterministically offline (no live model) — proving reproducibility.
"""

from __future__ import annotations

from typing import Any

from orchestrator.agentic.loop import LoopResult
from orchestrator.core.llm import CompletionResult, MockLLMClient, ToolCall


def build_run_bundle(
    result: LoopResult,
    *,
    persona: str = "",
    cost_usd: float | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """A self-contained, serializable record of one agentic run."""
    return {
        "persona": persona,
        "stopped_reason": result.stopped_reason,
        "steps": result.steps,
        "tool_calls": list(result.tool_calls_made),
        "cost_usd": round(cost_usd, 4) if cost_usd is not None else None,
        "policy_blocks": [dict(b) for b in result.policy_blocks],
        "trace": [
            {
                "step": s.step,
                "text": s.text,
                "calls": [
                    {
                        "name": c.get("name"),
                        "args": c.get("args"),
                        "blocked": bool(c.get("blocked")),
                        "observation": str(c.get("observation", ""))[:2000],
                    }
                    for c in s.calls
                ],
            }
            for s in result.trace
        ],
        "metadata": dict(metadata or {}),
    }


def render_bundle_markdown(bundle: dict[str, Any], *, title: str = "Run bundle") -> str:
    lines = [
        f"# {title}",
        "",
        f"- **persona:** {bundle.get('persona') or '—'} · **stopped:** {bundle['stopped_reason']} · "
        f"**steps:** {bundle['steps']}",
        f"- **tool calls:** {len(bundle['tool_calls'])} · **policy blocks:** {len(bundle['policy_blocks'])}"
        + (f" · **cost:** ${bundle['cost_usd']:.4f}" if bundle.get("cost_usd") is not None else ""),
        "",
    ]
    if bundle["policy_blocks"]:
        lines.append("## Policy blocks")
        for b in bundle["policy_blocks"]:
            lines.append(f"- `{b['tool']}` → {b['action']}: {b['reason']}")
        lines.append("")
    lines.append("## Trace")
    for s in bundle["trace"]:
        for c in s["calls"]:
            flag = " (blocked)" if c["blocked"] else ""
            lines.append(f"- step {s['step']}: `{c['name']}`{flag}")
    return "\n".join(lines).rstrip() + "\n"


def replay_llm_from_trace(bundle: dict[str, Any], *, final_text: str = "replayed") -> MockLLMClient:
    """Rebuild a scripted ``MockLLMClient`` from a bundle's trace so the run
    replays deterministically offline. Each traced step → the assistant turn
    that produced it (tool calls reconstructed); a trailing text turn finishes.
    """
    script: list[CompletionResult] = []
    for s in bundle["trace"]:
        calls = tuple(
            ToolCall(id=f"r{s['step']}-{i}", name=str(c["name"]), arguments=dict(c.get("args") or {}))
            for i, c in enumerate(s["calls"])
        )
        script.append(_result(text=str(s.get("text") or ""), tool_calls=calls))
    script.append(_result(text=final_text))
    return MockLLMClient(script=script)


def _result(*, text: str, tool_calls: tuple[ToolCall, ...] = ()) -> CompletionResult:
    return CompletionResult(
        text=text,
        model="replay",
        prompt_tokens=0,
        completion_tokens=0,
        cost_usd=0.0,
        latency_ms=0.0,
        tool_calls=tool_calls,
    )


__all__ = ["build_run_bundle", "render_bundle_markdown", "replay_llm_from_trace"]
