"""What a feature run cost, rendered for the ticket it was working.

A live run spends real tokens and real money and, until now, left no trace of either on the
issue. The pipeline already measures it: every LLM call funnels through
``RecordingLLMClient``, which keeps a ``TokenLedger`` of per-stage calls, prompt/completion
tokens, cost and the models seen. This module turns that ledger into a Jira worklog — a
duration Jira will accept, and a markdown body the ADF renderer turns into a real table.

Kept out of ``feature_runner`` so the shape of the report is testable without running a
pipeline, and so a change to the wording never touches the pipeline's control flow.

**Telemetry never fails the work.** The caller posts this inside a guard: a tracker that
rejects a worklog must not turn a green run red. What it costs is worth knowing; it is not
worth the run.
"""

from __future__ import annotations

from orchestrator.core.llm.recording import TokenLedger


def jira_duration(seconds: float) -> str:
    """Seconds → a duration Jira accepts (``"2h 5m"``).

    Rounded up to a whole minute, never zero: Jira rejects a zero-length worklog, and a
    30-second run did happen. Hours appear only when there is at least one.
    """
    minutes = max(1, int(seconds // 60) + (1 if seconds % 60 else 0))
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes}m" if hours else f"{minutes}m"


def render_worklog(ledger: TokenLedger, *, seconds: float, verdict: str) -> str:
    """The worklog body: what ran, on which models, for how many tokens.

    Markdown, because ``intake.adf`` renders headings, tables and lists into the ADF Jira
    draws natively — the same renderer issue bodies use.
    """
    total = ledger.total()
    models = ", ".join(f"`{m}`" for m in total.models) or "_none — no LLM call was made_"
    lines = [
        "## Spine run telemetry",
        "",
        f"**Verdict:** {verdict}",
        f"**Models:** {models}",
        f"**Wall clock:** {jira_duration(seconds)}",
        "",
    ]
    stages = ledger.ordered()
    if not stages:
        lines.append("No LLM calls were recorded for this run.")
        return "\n".join(lines)

    lines += [
        "| Stage | Calls | Prompt | Completion | Total | Cost (USD) |",
        "|---|---|---|---|---|---|",
    ]
    for usage in stages:
        lines.append(
            f"| {usage.stage} | {usage.calls} | {usage.prompt_tokens:,} | "
            f"{usage.completion_tokens:,} | {usage.total_tokens:,} | ${usage.cost_usd:.4f} |"
        )
    lines.append(
        f"| **TOTAL** | **{total.calls}** | **{total.prompt_tokens:,}** | "
        f"**{total.completion_tokens:,}** | **{total.total_tokens:,}** | **${total.cost_usd:.4f}** |"
    )
    lines += [
        "",
        "Logged automatically by `orchestrator sdlc feature --live`. Token counts come from "
        "the provider's own usage figures, not an estimate.",
    ]
    return "\n".join(lines)


__all__ = ["jira_duration", "render_worklog"]
