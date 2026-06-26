"""Bounded, budgeted, governed think → act → observe loop.

The agent calls tools mid-task instead of getting one static prompt and one
shot. Safe by construction: a hard step cap, an optional ``RunBudget`` checked
before each step, a no-progress detector, and an optional ``Policy`` that
allow/deny/require-approval-gates every tool call. A ``deny`` becomes an
observation the model adapts to; a ``require_approval`` (Bet 2c) **pauses** the
run — the loop returns a ``needs_approval`` checkpoint, a human decides out of
band, and ``resume`` continues from exactly where it stopped. Tool errors are
fed back as observations, never raised as loop failures.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from orchestrator.agentic.policy import Policy, PolicyAction
from orchestrator.core.llm import LLMClient, Message, ToolCall, ToolSpec
from orchestrator.core.llm.budget import RunBudget
from orchestrator.obs import tracing

logger = logging.getLogger("orchestrator.agentic.loop")


@dataclass(frozen=True)
class Tool:
    """A callable offered to the model: its spec + an async handler.

    ``run`` takes the parsed arguments and returns an observation string that is
    fed back to the model as the tool result. A ``terminal`` tool (e.g.
    ``submit_changes``) ends the loop once it runs — its observation becomes the
    loop's final text.
    """

    spec: ToolSpec
    run: Callable[[dict[str, object]], Awaitable[str]]
    terminal: bool = False


@dataclass
class StepRecord:
    """One model turn in the run trace (Bet 2b) — for export + replay."""

    step: int
    text: str  # the model's assistant text this turn
    calls: list[dict[str, Any]] = field(default_factory=list)  # [{name, args, observation, blocked}]


@dataclass
class PendingApproval:
    """The tool call a ``require_approval`` policy decision paused on (Bet 2c).

    Carries everything a human (or the workflow gate) needs to decide and
    everything ``resume`` needs to act on the decision.
    """

    tool: str
    arguments: dict[str, Any]
    reason: str  # the policy reason for requiring approval
    call_id: str  # the model's tool_call id — the resume answers on this id
    step: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool": self.tool,
            "arguments": self.arguments,
            "reason": self.reason,
            "call_id": self.call_id,
            "step": self.step,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> PendingApproval:
        return cls(
            tool=str(d["tool"]),
            arguments=dict(d.get("arguments") or {}),
            reason=str(d.get("reason", "")),
            call_id=str(d["call_id"]),
            step=int(d.get("step", 0)),
        )


@dataclass
class LoopCheckpoint:
    """Resumable loop state captured at a ``require_approval`` pause (Bet 2c).

    JSON-serializable so it can cross the Temporal activity → workflow → activity
    boundary as a payload (state rides through workflow history; no external
    store). Bounded by ``max_steps`` so it stays small.
    """

    messages: list[Message]
    made: list[str]
    blocks: list[dict[str, str]]
    trace: list[StepRecord]
    recent: list[list[list[str]]]  # no-progress signatures: [[ [name, args_json], ... ], ...]
    nudges: int
    step: int
    step_text: str  # the assistant text of the in-flight step
    step_calls: list[dict[str, Any]]  # calls already processed this step (for the trace)
    pending: PendingApproval
    remaining_calls: list[dict[str, Any]]  # calls in this step after the gated one: [{id, name, arguments}]

    def to_dict(self) -> dict[str, Any]:
        return {
            "messages": [_message_to_json(m) for m in self.messages],
            "made": list(self.made),
            "blocks": [dict(b) for b in self.blocks],
            "trace": [{"step": s.step, "text": s.text, "calls": s.calls} for s in self.trace],
            "recent": self.recent,
            "nudges": self.nudges,
            "step": self.step,
            "step_text": self.step_text,
            "step_calls": self.step_calls,
            "pending": self.pending.to_dict(),
            "remaining_calls": self.remaining_calls,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> LoopCheckpoint:
        return cls(
            messages=[_message_from_json(m) for m in d.get("messages", [])],
            made=[str(x) for x in d.get("made", [])],
            blocks=[dict(b) for b in d.get("blocks", [])],
            trace=[
                StepRecord(int(s["step"]), str(s.get("text", "")), list(s.get("calls", [])))
                for s in d.get("trace", [])
            ],
            recent=[list(sig) for sig in d.get("recent", [])],
            nudges=int(d.get("nudges", 0)),
            step=int(d.get("step", 0)),
            step_text=str(d.get("step_text", "")),
            step_calls=list(d.get("step_calls", [])),
            pending=PendingApproval.from_dict(d["pending"]),
            remaining_calls=list(d.get("remaining_calls", [])),
        )


@dataclass(frozen=True)
class HumanDecision:
    """A human's verdict on a paused tool call (Bet 2c) — the input to ``resume``.

    ``action`` is ``"approve"`` | ``"reject"`` | ``"modify_input"``. ``approve``
    runs the gated call as-is; ``modify_input`` runs it with ``modified_input``
    as the arguments; ``reject`` (and a timed-out gate, by policy) feeds a denial
    observation back and the loop continues.
    """

    action: str
    rationale: str | None = None
    modified_input: dict[str, Any] | None = None

    @property
    def approved(self) -> bool:
        return self.action in ("approve", "modify_input")


@dataclass
class LoopResult:
    """The outcome of a loop run."""

    final_text: str
    steps: int
    # "final" | "submitted" | "max_steps" | "no_progress" | "needs_approval"
    stopped_reason: str
    tool_calls_made: list[str]
    # Policy blocks: tool calls the policy refused (Bet 2a) — {tool, action, reason}.
    policy_blocks: list[dict[str, str]] = field(default_factory=list)
    # Per-step trace (Bet 2b): the run's receipt, for export + deterministic replay.
    trace: list[StepRecord] = field(default_factory=list)
    # Set only when stopped_reason == "needs_approval" (Bet 2c).
    pending: PendingApproval | None = None
    checkpoint: LoopCheckpoint | None = None


@dataclass
class _State:
    """Mutable loop state threaded through the driver — shared by run + resume."""

    messages: list[Message]
    made: list[str]
    blocks: list[dict[str, str]]
    trace: list[StepRecord]
    recent: list[tuple[tuple[str, str], ...]]
    nudges: int
    step: int = 0
    # Set while a model turn's tool calls are being processed.
    step_text: str = ""
    step_calls: list[dict[str, Any]] = field(default_factory=list)
    pending_calls: list[ToolCall] | None = None


class AgentLoop:
    """Drive an LLM through tool calls until it produces a final answer."""

    def __init__(
        self,
        llm: LLMClient,
        *,
        model: str,
        tools: list[Tool],
        max_steps: int = 12,
        budget: RunBudget | None = None,
        max_tokens: int | None = None,
        no_progress_repeats: int = 3,
        require_terminal: bool = False,
        max_nudges: int = 2,
        policy: Policy | None = None,
    ) -> None:
        self._llm = llm
        self._model = model
        self._policy = policy
        self._tools = {t.spec.name: t for t in tools}
        self._specs = [t.spec for t in tools]
        self._max_steps = max(1, max_steps)
        self._budget = budget
        self._max_tokens = max_tokens
        self._no_progress = max(2, no_progress_repeats)
        # When set, a prose answer (no tool call) is not accepted as the end —
        # the model is nudged to finish via a terminal tool. Models often
        # *describe* their answer instead of emitting the structured terminal
        # call (the auditor's live lesson); the nudge recovers the structure.
        self._require_terminal = require_terminal and any(t.terminal for t in tools)
        self._max_nudges = max(0, max_nudges)
        self._terminal_names = [t.spec.name for t in tools if t.terminal]

    async def run(self, system: str, task: str) -> LoopResult:
        st = _State(
            messages=[Message("system", system), Message("user", task)],
            made=[],
            blocks=[],
            trace=[],
            recent=[],
            nudges=0,
        )
        return await self._drive(st)

    async def resume(self, checkpoint: LoopCheckpoint, decision: HumanDecision) -> LoopResult:
        """Continue a paused run after a human decided the gated tool call.

        The gated call's name is already in ``made`` (recorded before the pause),
        so we resolve it here without re-recording, then hand the rest of the
        in-flight step's calls back to the driver.
        """
        st = _State(
            messages=list(checkpoint.messages),
            made=list(checkpoint.made),
            blocks=[dict(b) for b in checkpoint.blocks],
            trace=list(checkpoint.trace),
            recent=[tuple((c[0], c[1]) for c in sig) for sig in checkpoint.recent],
            nudges=checkpoint.nudges,
            step=checkpoint.step,
            step_text=checkpoint.step_text,
            step_calls=list(checkpoint.step_calls),
        )
        pending = checkpoint.pending
        if decision.approved:
            args = decision.modified_input if decision.action == "modify_input" else pending.arguments
            call = ToolCall(id=pending.call_id, name=pending.tool, arguments=dict(args or {}))
            observation = await self._dispatch(call)
            st.messages.append(Message(role="tool", content=observation, tool_call_id=pending.call_id))
            st.step_calls.append(
                {"name": pending.tool, "args": call.arguments, "observation": observation, "blocked": False}
            )
            tool = self._tools.get(pending.tool)
            if tool is not None and tool.terminal:
                st.trace.append(StepRecord(st.step, st.step_text, st.step_calls))
                return LoopResult(observation, st.step, "submitted", st.made, st.blocks, st.trace)
        else:
            rationale = decision.rationale or "no rationale given"
            observation = f"blocked by human (rejected): {rationale}"
            st.blocks.append({"tool": pending.tool, "action": "rejected", "reason": rationale})
            logger.info("agentic.human_reject", extra={"tool": pending.tool})
            st.messages.append(Message(role="tool", content=observation, tool_call_id=pending.call_id))
            st.step_calls.append(
                {"name": pending.tool, "args": pending.arguments, "observation": observation, "blocked": True}
            )
        # Finish the rest of this step's calls (if any), then continue normally.
        st.pending_calls = [
            ToolCall(id=str(c["id"]), name=str(c["name"]), arguments=dict(c.get("arguments") or {}))
            for c in checkpoint.remaining_calls
        ]
        return await self._drive(st)

    async def _drive(self, st: _State) -> LoopResult:
        """The loop. Handles both a fresh start and a mid-step resume (when
        ``st.pending_calls`` is already populated)."""
        while True:
            # Resuming mid-step: finish the in-flight step's remaining calls.
            if st.pending_calls is not None:
                with tracing.span("agent.step", **{"agent.step": st.step, "agent.resumed": True}):
                    outcome = await self._process_calls(st)
                    if outcome is not None:
                        return outcome  # terminal, or another approval pause
                    self._finalize_step(st)
                    if outcome := self._no_progress_check(st):
                        return outcome
                    st.pending_calls = None

            if st.step >= self._max_steps:
                return LoopResult("", self._max_steps, "max_steps", st.made, st.blocks, st.trace)
            st.step += 1
            # One span per model turn; the LLM call (llm.complete) and any tool
            # calls (tool.<name>) nest under it for a per-step timeline (Phase 2,
            # docs/specs/live-observability-otel.md). No-op unless OTEL is on.
            with tracing.span("agent.step", **{"agent.step": st.step}) as sp:
                if self._budget is not None:
                    self._budget.check()  # raises BudgetExceededError when over cap
                result = await self._llm.complete(
                    st.messages, model=self._model, tools=self._specs, max_tokens=self._max_tokens
                )
                if not result.tool_calls:
                    if self._require_terminal and st.nudges < self._max_nudges:
                        st.nudges += 1
                        st.messages.append(Message(role="assistant", content=result.text or ""))
                        st.messages.append(
                            Message(
                                role="user",
                                content=(
                                    "Do not answer in prose. Finish by calling "
                                    f"{' or '.join(self._terminal_names)} with structured arguments."
                                ),
                            )
                        )
                        sp.set_attribute("agent.nudged", True)
                        continue
                    st.trace.append(StepRecord(st.step, result.text))
                    sp.set_attribute("agent.stopped", "final")
                    return LoopResult(result.text, st.step, "final", st.made, st.blocks, st.trace)

                # Record the assistant's tool-call turn, then process each tool.
                st.messages.append(
                    Message(role="assistant", content=result.text or "", tool_calls=result.tool_calls)
                )
                st.step_text = result.text or ""
                st.step_calls = []
                st.pending_calls = list(result.tool_calls)
                sp.set_attribute("agent.tool_calls", len(result.tool_calls))
                outcome = await self._process_calls(st)
                if outcome is not None:
                    sp.set_attribute("agent.stopped", outcome.stopped_reason)
                    return outcome
                self._finalize_step(st)
                if outcome := self._no_progress_check(st):
                    sp.set_attribute("agent.stopped", "no_progress")
                    return outcome
                st.pending_calls = None

    async def _process_calls(self, st: _State) -> LoopResult | None:
        """Process ``st.pending_calls`` in order, draining the list. Returns a
        ``LoopResult`` to stop the loop (terminal tool or a ``require_approval``
        pause), or ``None`` when the step's calls are all handled."""
        while st.pending_calls:
            call = st.pending_calls[0]
            st.made.append(call.name)
            if self._policy is not None:
                decision = self._policy.decide(call.name, call.arguments)
                if decision.action is PolicyAction.REQUIRE_APPROVAL:
                    # Pause: checkpoint and hand control to the human/workflow.
                    pending = PendingApproval(
                        tool=call.name,
                        arguments=call.arguments,
                        reason=decision.reason,
                        call_id=call.id,
                        step=st.step,
                    )
                    logger.info("agentic.needs_approval", extra={"tool": call.name})
                    tracing.add_event("needs_approval", **{"tool": call.name})
                    checkpoint = self._checkpoint(st, pending, st.pending_calls[1:])
                    return LoopResult(
                        "",
                        st.step,
                        "needs_approval",
                        st.made,
                        st.blocks,
                        st.trace,
                        pending=pending,
                        checkpoint=checkpoint,
                    )
                if decision.action is not PolicyAction.ALLOW:  # DENY (Bet 2a)
                    st.blocks.append(
                        {"tool": call.name, "action": decision.action.value, "reason": decision.reason}
                    )
                    logger.info(
                        "agentic.policy_block", extra={"tool": call.name, "action": decision.action.value}
                    )
                    tracing.add_event("policy_block", **{"tool": call.name, "action": decision.action.value})
                    observation = f"blocked by policy ({decision.action.value}): {decision.reason}"
                    st.messages.append(Message(role="tool", content=observation, tool_call_id=call.id))
                    st.step_calls.append(
                        {
                            "name": call.name,
                            "args": call.arguments,
                            "observation": observation,
                            "blocked": True,
                        }
                    )
                    st.pending_calls.pop(0)
                    continue
            observation = await self._dispatch(call)
            st.messages.append(Message(role="tool", content=observation, tool_call_id=call.id))
            st.step_calls.append(
                {"name": call.name, "args": call.arguments, "observation": observation, "blocked": False}
            )
            st.pending_calls.pop(0)
            tool = self._tools.get(call.name)
            if tool is not None and tool.terminal:
                st.trace.append(StepRecord(st.step, st.step_text, st.step_calls))
                return LoopResult(observation, st.step, "submitted", st.made, st.blocks, st.trace)
        return None

    def _finalize_step(self, st: _State) -> None:
        st.trace.append(StepRecord(st.step, st.step_text, st.step_calls))

    def _no_progress_check(self, st: _State) -> LoopResult | None:
        signature = tuple((c["name"], json.dumps(c.get("args", {}), sort_keys=True)) for c in st.step_calls)
        st.recent.append(signature)
        if len(st.recent) >= self._no_progress and len(set(st.recent[-self._no_progress :])) == 1:
            return LoopResult("", st.step, "no_progress", st.made, st.blocks, st.trace)
        return None

    def _checkpoint(self, st: _State, pending: PendingApproval, remaining: list[ToolCall]) -> LoopCheckpoint:
        cp = LoopCheckpoint(
            messages=list(st.messages),
            made=list(st.made),
            blocks=[dict(b) for b in st.blocks],
            trace=list(st.trace),
            recent=[[[name, args] for (name, args) in sig] for sig in st.recent],
            nudges=st.nudges,
            step=st.step,
            step_text=st.step_text,
            step_calls=list(st.step_calls),
            pending=pending,
            remaining_calls=[{"id": c.id, "name": c.name, "arguments": c.arguments} for c in remaining],
        )
        size = len(json.dumps(cp.to_dict()))
        logger.info("agentic.checkpoint", extra={"bytes": size, "step": st.step})
        return cp

    async def _dispatch(self, call: ToolCall) -> str:
        tool = self._tools.get(call.name)
        with tracing.span(
            f"tool.{call.name}",
            **{"tool.name": call.name, "tool.terminal": bool(tool is not None and tool.terminal)},
        ) as sp:
            if tool is None:
                sp.set_attribute("tool.error", "unknown_tool")
                return f"error: unknown tool {call.name!r}"
            try:
                observation = await tool.run(call.arguments)
                sp.set_attribute("tool.observation_len", len(observation))
                return observation
            except Exception as exc:  # noqa: BLE001 — a tool error is an observation, not a loop crash
                logger.warning("agentic.tool_error", extra={"tool": call.name, "error": str(exc)[:200]})
                sp.set_attribute("tool.error", type(exc).__name__)
                return f"error: {type(exc).__name__}: {exc}"


def _message_to_json(m: Message) -> dict[str, Any]:
    """Serialize a ``Message`` to a plain dict that round-trips to our dataclass
    (distinct from ``Message.to_dict``, which emits the OpenAI wire shape)."""
    out: dict[str, Any] = {"role": m.role, "content": m.content}
    if m.tool_calls:
        out["tool_calls"] = [{"id": tc.id, "name": tc.name, "arguments": tc.arguments} for tc in m.tool_calls]
    if m.tool_call_id is not None:
        out["tool_call_id"] = m.tool_call_id
    return out


def _message_from_json(d: dict[str, Any]) -> Message:
    tool_calls = tuple(
        ToolCall(id=str(t["id"]), name=str(t["name"]), arguments=dict(t.get("arguments") or {}))
        for t in d.get("tool_calls", [])
    )
    return Message(
        role=str(d["role"]),
        content=str(d.get("content", "")),
        tool_calls=tool_calls,
        tool_call_id=d.get("tool_call_id"),
    )


__all__ = [
    "AgentLoop",
    "HumanDecision",
    "LoopCheckpoint",
    "LoopResult",
    "PendingApproval",
    "StepRecord",
    "Tool",
]
