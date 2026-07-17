"""Persistent intake cache: extract a source's backlog once, then reuse it.

Intent extraction + spec writing are LLM calls over the whole source document.
Run non-deterministically (and re-run on every ``sdlc feature`` invocation) they
return a *different* subset of intents each time, so a pinned ``--intent <id>``
becomes a moving target — the id you implemented yesterday may not be extracted
today. This module caches the analyzed ``BacklogPlan`` keyed by the source URI:
the first run extracts and persists; later runs reuse it (no Confluence fetch, no
LLM) until ``--refresh``. Combined with temperature-0 extraction, ``--intent`` is
finally stable.

The cache lives in a user dir (``~/.cache/orchestrator/intake`` by default, or
``$ORCHESTRATOR_INTAKE_CACHE_DIR``) so it persists across runs from any working
directory.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import logging
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from orchestrator.intake.gaps import GapFinding, GapSeverity
from orchestrator.intake.intents import Intent
from orchestrator.intake.service import BacklogPlan, BacklogService, parse_source_uri
from orchestrator.intake.source import SourceDocument
from orchestrator.intake.specs import FeatureSpec

logger = logging.getLogger("orchestrator.intake.cache")

# Bump when the serialized shape changes so stale files are ignored, not crashed on.
_CACHE_VERSION = 1


def default_cache_dir() -> Path:
    env = os.getenv("ORCHESTRATOR_INTAKE_CACHE_DIR")
    return Path(env) if env else Path.home() / ".cache" / "orchestrator" / "intake"


def cache_path(source_uri: str, cache_dir: Path | None = None) -> Path:
    root = cache_dir or default_cache_dir()
    key = hashlib.sha1(source_uri.encode("utf-8"), usedforsecurity=False).hexdigest()[:16]
    return root / f"{key}.json"


def _plan_to_dict(plan: BacklogPlan) -> dict[str, Any]:
    return {
        "version": _CACHE_VERSION,
        "documents": [dataclasses.asdict(d) for d in plan.documents],
        "intents": [i.model_dump() for i in plan.intents],
        "gaps": [
            {
                "rule_id": g.rule_id,
                "intent_id": g.intent_id,
                "severity": g.severity.value,
                "message": g.message,
            }
            for g in plan.gaps
        ],
        "specs": [s.model_dump() for s in plan.specs],
        "blocked": plan.blocked,
        "truncated": plan.truncated,
    }


def _plan_from_dict(data: dict[str, Any]) -> BacklogPlan:
    docs: list[dict[str, Any]] = data.get("documents") or []
    gaps: list[dict[str, Any]] = data.get("gaps") or []
    intents: list[dict[str, Any]] = data.get("intents") or []
    specs: list[dict[str, Any]] = data.get("specs") or []
    return BacklogPlan(
        documents=[SourceDocument(**{**d, "labels": tuple(d.get("labels") or ())}) for d in docs],
        intents=[Intent.model_validate(i) for i in intents],
        gaps=[
            GapFinding(
                rule_id=g["rule_id"],
                intent_id=g["intent_id"],
                severity=GapSeverity(g["severity"]),
                message=g["message"],
            )
            for g in gaps
        ],
        specs=[FeatureSpec.model_validate(s) for s in specs],
        blocked=bool(data.get("blocked", False)),
        truncated=bool(data.get("truncated", False)),
    )


def _read_raw(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def load_progress(source_uri: str, cache_dir: Path | None = None) -> dict[str, dict[str, Any]]:
    """Per-intent progress map (``{intent_id: {status, issue_key, pr_url}}``) from the cache."""
    return _read_raw(cache_path(source_uri, cache_dir)).get("progress") or {}


def set_progress(
    source_uri: str,
    intent_id: str,
    *,
    status: str,
    issue_key: str | None = None,
    pr_url: str | None = None,
    cache_dir: Path | None = None,
) -> None:
    """Update one intent's progress in the cache file. No-op if no cache exists yet."""
    path = cache_path(source_uri, cache_dir)
    raw = _read_raw(path)
    if not raw:
        return
    progress: dict[str, dict[str, Any]] = raw.get("progress") or {}
    entry = progress.get(intent_id, {})
    entry["status"] = status
    if issue_key:
        entry["issue_key"] = issue_key
    if pr_url:
        entry["pr_url"] = pr_url
    progress[intent_id] = entry
    raw["progress"] = progress
    path.write_text(json.dumps(raw, indent=2), encoding="utf-8")


def complete_by_pr(pr_url: str, cache_dir: Path | None = None) -> tuple[str, BacklogPlan] | None:
    """Find the cached intent whose progress records ``pr_url`` and mark it done.

    ``sdlc complete`` knows only the PR; this matches it back to the backlog by
    the ``pr_url`` recorded when the feature run opened it. Returns
    ``(source_uri, plan)`` for re-rendering the ledger, or ``None`` if unmatched.
    """
    root = cache_dir or default_cache_dir()
    if not root.is_dir():
        return None
    for file in sorted(root.glob("*.json")):
        raw = _read_raw(file)
        progress: dict[str, dict[str, Any]] = raw.get("progress") or {}
        for entry in progress.values():
            if entry.get("pr_url") == pr_url:
                entry["status"] = "done"
                raw["progress"] = progress
                file.write_text(json.dumps(raw, indent=2), encoding="utf-8")
                source = str(raw.get("source") or "")
                return source, _plan_from_dict(raw)
    return None


def load_cached_plan(source_uri: str, cache_dir: Path | None = None) -> BacklogPlan | None:
    path = cache_path(source_uri, cache_dir)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("version") != _CACHE_VERSION:
            return None
        return _plan_from_dict(data)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        logger.warning("intake.cache.load_failed path=%s err=%s", path, exc)
        return None


def save_plan(source_uri: str, plan: BacklogPlan, cache_dir: Path | None = None) -> Path:
    path = cache_path(source_uri, cache_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = _plan_to_dict(plan)
    payload["source"] = source_uri
    # Carry progress forward across a --refresh re-extract: keep entries for
    # intents that still exist (deterministic ids map cleanly), drop the rest.
    old_progress = _read_raw(path).get("progress") or {}
    live_ids = {i.id for i in plan.intents}
    payload["progress"] = {k: v for k, v in old_progress.items() if k in live_ids}
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


async def analyze_cached(
    service: BacklogService,
    source_uri: str,
    *,
    cache_dir: Path | None = None,
    refresh: bool = False,
    log: Callable[[str], None] | None = None,
) -> BacklogPlan:
    """``service.analyze`` with a persistent cache keyed by ``source_uri``.

    On a cache hit (and not ``refresh``) returns the stored plan without touching
    the source or the LLM — so the intent set is identical run to run. On a miss
    or ``refresh`` it extracts, persists, and returns the fresh plan.
    """
    emit = log or (lambda _m: None)
    _, root_id = parse_source_uri(source_uri)
    if not refresh:
        cached = load_cached_plan(source_uri, cache_dir)
        if cached is not None:
            emit(
                f"[intake] reusing cached backlog: {len(cached.intents)} intents for {source_uri} "
                f"(--refresh to re-extract) — {cache_path(source_uri, cache_dir)}"
            )
            return cached
    plan = await service.analyze(root_id)
    path = save_plan(source_uri, plan, cache_dir)
    emit(f"[intake] extracted + cached {len(plan.intents)} intents for {source_uri} — {path}")
    return plan


__all__ = [
    "analyze_cached",
    "cache_path",
    "complete_by_pr",
    "default_cache_dir",
    "load_cached_plan",
    "load_progress",
    "save_plan",
    "set_progress",
]
