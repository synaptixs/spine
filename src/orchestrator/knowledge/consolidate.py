"""Consolidation — turn a finished run's episodes into semantic memory (Phase 2).

The write path of docs/specs/cross-run-semantic-memory.md. Given a run bundle
(``agentic/export.build_run_bundle``), it selects the salient governance episodes,
asks a reflector LLM to distill each into one reusable sentence, dedups against
existing memories, and either reinforces a near-duplicate or inserts a new one.

Phase 2 is deliberately narrow — it consolidates the highest-signal, lowest-volume
episodes only: ``policy_blocks``. A human **reject** is the strongest signal (a
person corrected the agent) → a ``convention``; a policy **deny** / required
approval → a ``pitfall``. Replan and test-fix episodes (decision / fix-pattern)
need richer trace plumbing and are deferred. Dedup uses token-Jaccard here (no
embeddings yet); pgvector ANN is the Phase 3 swap. Decay is Phase 3.

Locked principle (derived-not-authored): every memory cites its source run in
``evidence.run_ids``. The reflector is instructed to ground its sentence in the
episode, and a SKIP reply drops episodes with no durable lesson.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from orchestrator.core.llm import LLMClient, Message
from orchestrator.registry.repositories import MemoryRepo, _tokenize

logger = logging.getLogger("orchestrator.knowledge.consolidate")

# A new run re-deriving an existing memory nudges its confidence by this much.
_REINFORCE_DELTA = 0.1
# Token-Jaccard at/above this counts as the same memory (dedup-hit).
_DEDUP_JACCARD = 0.6
# A freshly-learned memory starts here (mid-confidence; reinforced or decayed later).
_SEED_CONFIDENCE = 0.5
# Decay (Phase 3): each consolidation ages out memories untouched for this long,
# dropping their confidence; below the floor they're pruned. Tying decay to the
# consolidation (per-merge) cadence makes "unused for a while" the prune signal.
_DECAY_WINDOW = timedelta(days=30)
_DECAY_DELTA = 0.05
_DECAY_FLOOR = 0.15

_REFLECT_SYSTEM = (
    "You distill one reusable engineering lesson from a single governance event in a past "
    "coding run. Reply with ONE imperative sentence a future run should follow, grounded in "
    "the event. If there is no durable, generalizable lesson, reply exactly: SKIP."
)


def _select_episodes(bundle: dict[str, Any]) -> list[dict[str, str]]:
    """Salient episodes from a run bundle.

    Phase 2: governance blocks — a human **reject** → ``convention`` (strongest
    signal), a policy **deny**/required approval → ``pitfall``. Phase 3 widens to
    **tool errors** in the trace (the agent hit a real failure) → ``pitfall``.
    Collapses identical (kind, tool, reason) episodes so one repeated event yields
    one candidate, not many.
    """
    seen: set[tuple[str, str, str]] = set()
    episodes: list[dict[str, str]] = []

    def _emit(kind: str, tool: str, reason: str) -> None:
        key = (kind, tool, reason)
        if key in seen:
            return
        seen.add(key)
        episodes.append({"kind": kind, "tool": tool, "reason": reason})

    for block in bundle.get("policy_blocks") or []:
        action = str(block.get("action") or "")
        if action == "rejected":
            kind = "convention"  # a human corrected the agent — strongest signal
        elif action in ("deny", "require_approval"):
            kind = "pitfall"  # a guard refused it — learn to avoid
        else:
            continue
        _emit(kind, str(block.get("tool") or ""), str(block.get("reason") or ""))

    # Phase 3 — tool failures the agent actually hit (not policy-blocked).
    for step in bundle.get("trace") or []:
        for call in step.get("calls") or []:
            if call.get("blocked"):
                continue  # already captured as a governance block above
            observation = str(call.get("observation") or "")
            if observation.startswith("error:"):
                _emit("pitfall", str(call.get("name") or ""), observation[:120])

    return episodes


async def _reflect(llm: LLMClient, model: str, episode: dict[str, str]) -> str | None:
    """One reflector call → a reusable sentence, or None when there's no lesson."""
    prompt = (
        "Event: a tool call was blocked during a coding run.\n"
        f"Tool: {episode['tool']}\n"
        f"Reason: {episode['reason']}\n\n"
        "Write the one-sentence lesson, or SKIP."
    )
    result = await llm.complete([Message("system", _REFLECT_SYSTEM), Message("user", prompt)], model=model)
    text = (result.text or "").strip()
    if not text or text.upper().startswith("SKIP"):
        return None
    return text[:300]


def _is_duplicate(statement: str, existing_statement: str) -> bool:
    a, b = _tokenize(statement), _tokenize(existing_statement)
    if not a or not b:
        return False
    return len(a & b) / len(a | b) >= _DEDUP_JACCARD


async def consolidate_run(
    *,
    bundle: dict[str, Any],
    repo_key: str,
    session: AsyncSession,
    llm: LLMClient,
    model: str,
    run_id: str | None = None,
    tenant_id: str = "default",
    trace_id: str | None = None,
    now: datetime | None = None,
) -> dict[str, int]:
    """Consolidate one run bundle into semantic memory.

    Returns counts ``{episodes, inserted, reinforced, skipped, decayed, deleted}``.
    Commits on success. Never raises into the caller's run — consolidation is a
    background benefit, so episode-level failures are logged and skipped. ``now``
    overrides the decay clock (for deterministic tests).
    """
    episodes = _select_episodes(bundle)
    repo = MemoryRepo(session)
    inserted = reinforced = skipped = 0

    for episode in episodes:
        try:
            statement = await _reflect(llm, model, episode)
        except Exception as exc:  # noqa: BLE001 — one bad episode must not abort the rest
            logger.warning("consolidate.reflect_failed", extra={"error": str(exc)[:200]})
            statement = None
        if not statement:
            skipped += 1
            continue

        existing = await repo.search(
            query=statement, repo_key=repo_key, tenant_id=tenant_id, kind=episode["kind"], limit=1
        )
        dup = existing[0] if existing and _is_duplicate(statement, existing[0].statement) else None
        if dup is not None:
            await repo.reinforce(dup.pk, run_id=run_id, delta=_REINFORCE_DELTA)
            reinforced += 1
        else:
            await repo.add(
                repo_key=repo_key,
                kind=episode["kind"],
                statement=statement,
                evidence={"run_ids": [run_id] if run_id else [], "tool": episode["tool"]},
                confidence=_SEED_CONFIDENCE,
                trace_id=trace_id,
                tenant_id=tenant_id,
            )
            inserted += 1

    await session.commit()

    # Decay (Phase 3): age out memories untouched for the window; prune below floor.
    # Inserted/reinforced/recalled rows have fresh timestamps and are spared.
    cutoff = (now or datetime.now(UTC)) - _DECAY_WINDOW
    decay = await repo.decay(
        repo_key=repo_key,
        tenant_id=tenant_id,
        cutoff=cutoff,
        delta=_DECAY_DELTA,
        floor=_DECAY_FLOOR,
    )
    await session.commit()

    summary = {
        "episodes": len(episodes),
        "inserted": inserted,
        "reinforced": reinforced,
        "skipped": skipped,
        **decay,
    }
    logger.info("consolidate.done", extra={"repo_key": repo_key, **summary})
    return summary


__all__ = ["consolidate_run"]
