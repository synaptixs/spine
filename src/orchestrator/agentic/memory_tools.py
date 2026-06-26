"""In-loop semantic-memory tool — ``recall_memory`` (cross-run memory, Phase 1).

Lets the agent pull facts learned from *past* runs mid-task: conventions a human
corrected, pitfalls that tripped earlier runs, decisions that worked. The read
path of docs/specs/cross-run-semantic-memory.md — the experience-true companion
to the code-true ``memory-bank/`` grounding.

Governed like any other tool: it's a ``Tool`` in the loop, so ``Policy`` gates it
and the call shows up in the trace. Retrieved memories are marked used (``hits`` +
``last_used_at``) so the feedback loop can later reinforce/decay them.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from orchestrator.agentic.loop import Tool
from orchestrator.core.llm import ToolSpec
from orchestrator.registry.repositories import MemoryRepo

_VALID_KINDS = ("convention", "pitfall", "decision", "fix-pattern")


def build_memory_tools(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    repo_key: str,
    tenant_id: str = "default",
    limit: int = 5,
) -> list[Tool]:
    """A ``recall_memory`` tool bound to one repo's memories. Empty-safe: returns
    a friendly observation when nothing matches, never raises into the loop."""

    async def _recall(args: dict[str, object]) -> str:
        query = str(args.get("query", "")).strip()
        if not query:
            return "error: 'query' is required"
        kind = args.get("kind")
        kind_str = str(kind).strip() if kind else None
        if kind_str and kind_str not in _VALID_KINDS:
            return f"error: kind must be one of {', '.join(_VALID_KINDS)}"

        async with session_factory() as session:
            repo = MemoryRepo(session)
            rows = await repo.search(
                query=query, repo_key=repo_key, tenant_id=tenant_id, kind=kind_str, limit=limit
            )
            for row in rows:
                await repo.record_hit(row.pk)
            await session.commit()

        if not rows:
            return "no relevant memories"
        lines: list[str] = []
        for row in rows:
            run_ids = (row.evidence or {}).get("run_ids") or []
            cite = f"; runs: {', '.join(map(str, run_ids))}" if run_ids else ""
            lines.append(f"[{row.kind}] {row.statement} (confidence {row.confidence:.2f}{cite})")
        return "\n".join(lines)

    return [
        Tool(
            ToolSpec(
                "recall_memory",
                "Recall facts learned from past runs on this project — conventions, pitfalls, "
                "decisions, fix-patterns — before deciding how to implement something. Optional "
                f"'kind' filters to one of: {', '.join(_VALID_KINDS)}.",
                {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "kind": {"type": "string", "enum": list(_VALID_KINDS)},
                    },
                    "required": ["query"],
                },
            ),
            _recall,
        )
    ]


__all__ = ["build_memory_tools"]
