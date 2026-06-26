"""Persona-skill measurement P3 — apply the bar, promote winners, record losers.

Reads the P2 A/B result JSONs (``docs/evals/*-skill-ab-*.json`` from
``scripts/skill_ab.py``), applies the pre-registered promotion bar, and:

  * writes an honest decisions log to ``docs/evals/PROMOTIONS.md`` (winners AND
    losers, with their numbers — held skills stay candidates, not dropped);
  * prints the exact ``Capability(...)`` snippet for each promoted skill;
  * with ``--apply``, injects those into the catalog's ``_PROMOTED`` overlay
    (``src/orchestrator/catalog/catalog.py``) so the planner can select them —
    idempotently (re-running adds nothing already there).

A promotion is an evidence-backed, reviewable diff: each promoted capability
carries its measured score in ``payload["eval"]``. Read-only without ``--apply``.

Usage:
    uv run python scripts/skill_promote.py                       # decide + write log (dry)
    uv run python scripts/skill_promote.py --apply               # also edit the catalog overlay
    uv run python scripts/skill_promote.py docs/evals/2026-06-24-skill-ab-test-strategy-claude.json
"""

from __future__ import annotations

import argparse
import datetime
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))

from orchestrator.evals.promotion import (  # noqa: E402
    PromotionDecision,
    apply_to_catalog_source,
    capability_source,
    decision_from_ab,
    promoted_ids_in_source,
    render_decisions_log,
)

_CATALOG = REPO / "src" / "orchestrator" / "catalog" / "catalog.py"
_DEFAULT_GLOB = "*-skill-ab-*.json"


def _rel(path: Path) -> str:
    """``path`` relative to the repo when possible, else its absolute form."""
    try:
        return str(path.relative_to(REPO))
    except ValueError:
        return str(path)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Promote skills that cleared the A/B bar (P3).")
    p.add_argument("results", nargs="*", help="A/B result JSON files (default: docs/evals/*-skill-ab-*.json)")
    p.add_argument("--evals-dir", default=str(REPO / "docs" / "evals"), help="where to find/write results")
    p.add_argument("--margin", type=float, default=None, help="override the promotion bar (default: per-run)")
    p.add_argument("--apply", action="store_true", help="edit the catalog _PROMOTED overlay in place")
    return p.parse_args(argv)


def _find_results(args: argparse.Namespace) -> list[Path]:
    if args.results:
        return [Path(r) for r in args.results]
    return sorted(Path(args.evals_dir).glob(_DEFAULT_GLOB))


def _latest_per_skill(paths: list[Path], *, margin: float | None) -> list[PromotionDecision]:
    """One decision per skill — the lexically last file wins (date-stamped names)."""
    by_skill: dict[str, PromotionDecision] = {}
    for path in sorted(paths):
        data = json.loads(path.read_text(encoding="utf-8"))
        decision = decision_from_ab(data, margin=margin)
        by_skill[decision.skill] = decision
    return list(by_skill.values())


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    paths = _find_results(args)
    if not paths:
        print(f"no A/B results found (looked for {_DEFAULT_GLOB} in {args.evals_dir}).")
        print("run scripts/skill_ab.py --live first.")
        return 1

    decisions = _latest_per_skill(paths, margin=args.margin)
    print(f"read {len(paths)} result file(s) → {len(decisions)} skill(s)\n")
    for d in decisions:
        print(f"  {d.summary()}")

    stamp = datetime.date.today().isoformat()
    log_path = Path(args.evals_dir) / "PROMOTIONS.md"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(render_decisions_log(decisions, stamp=stamp), encoding="utf-8")
    print(f"\nwrote {_rel(log_path)}")

    promoted = [d for d in decisions if d.promote]
    if not promoted:
        print("\nno skills cleared the bar — nothing to promote (held skills stay candidates).")
        return 0

    print("\n=== catalog _PROMOTED entries for cleared skills ===")
    for d in promoted:
        print(capability_source(d))

    if not args.apply:
        print("\n(dry — pass --apply to inject these into src/orchestrator/catalog/catalog.py)")
        return 0

    source = _CATALOG.read_text(encoding="utf-8")
    already = set(promoted_ids_in_source(source))
    updated = apply_to_catalog_source(source, decisions)
    if updated == source:
        print(f"\ncatalog already up to date (present: {sorted(already) or '—'}).")
        return 0
    _CATALOG.write_text(updated, encoding="utf-8")
    added = [d.skill for d in promoted if d.skill not in already]
    print(f"\napplied → {_rel(_CATALOG)} (added: {', '.join(added)})")
    print("review the diff, then run the quality gate (mypy/ruff/pytest).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
