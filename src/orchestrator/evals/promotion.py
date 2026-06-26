"""Promotion decisions for the persona-skill measurement (P3).

The last mile: turn a P2 A/B result into a go/no-go and the artifacts that record
it. A skill that clears the pre-registered bar earns a catalog ``Capability`` (so
the planner can select it) carrying its measured score as evidence; a skill that
doesn't **stays a candidate with its honest numbers written down** — not silently
dropped. "A skill that doesn't move a metric doesn't ship."

Pure and testable: this decides and *renders* (the capability, its source snippet,
the decisions log). Reading the JSON, writing ``docs/evals/``, and editing the
catalog overlay live in ``scripts/skill_promote.py``.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from orchestrator.catalog.models import Capability, CapabilityKind, CapabilitySelector
from orchestrator.evals.skill_ab import PROMOTION_MARGIN

# Where a promoted candidate plugs into the planner. The three SWE candidates all
# condition feature codegen, like their conventions/grounding kin already in _SEED.
_SKILL_SELECTORS: dict[str, CapabilitySelector] = {
    "test-strategy": CapabilitySelector(task_types=frozenset({"feature"})),
    "security-aware-coding": CapabilitySelector(task_types=frozenset({"feature"})),
    "convention-digest": CapabilitySelector(task_types=frozenset({"feature"})),
}
_SKILL_SUMMARIES: dict[str, str] = {
    "test-strategy": "Cover acceptance criteria, error paths and boundaries in tests",
    "security-aware-coding": "Validate untrusted input; avoid injection and secret leaks",
    "convention-digest": "Match repo conventions; reuse existing helpers",
}


@dataclass(frozen=True)
class PromotionDecision:
    """The go/no-go for one skill, with the numbers it rests on."""

    skill: str
    promote: bool
    baseline_rate: float
    treatment_rate: float
    margin: float
    model: str
    provider: str
    runs_per_arm: int

    @property
    def delta(self) -> float:
        return self.treatment_rate - self.baseline_rate

    @property
    def eval_id(self) -> str:
        return f"skill-ab:{self.skill}"

    @property
    def min_score(self) -> float:
        """The bar this skill cleared — baseline + margin, the gate to keep meeting."""
        return round(self.baseline_rate + self.margin, 3)

    def summary(self) -> str:
        decision = "PROMOTE" if self.promote else "HOLD"
        return (
            f"{self.skill}: baseline {self.baseline_rate:.0%} → treatment "
            f"{self.treatment_rate:.0%} (Δ {self.delta:+.1%}; bar +{self.margin:.0%}) "
            f"on {self.provider}/{self.model}, {self.runs_per_arm} runs/arm → {decision}"
        )


def decision_from_ab(data: Mapping[str, Any], *, margin: float | None = None) -> PromotionDecision:
    """Build a ``PromotionDecision`` from a ``scripts/skill_ab.py`` result JSON.

    ``margin`` re-applies the bar at promotion time (defaults to the one recorded
    in the run, else ``PROMOTION_MARGIN``) so the threshold is explicit here, not
    inherited unseen from whatever produced the file.
    """
    verdict = data.get("verdict", {})
    base = float(verdict["baseline_rate"])
    treat = float(verdict["treatment_rate"])
    bar = margin if margin is not None else float(verdict.get("margin", PROMOTION_MARGIN))
    runs_per_arm = _runs_per_arm(data.get("baseline", {}))
    return PromotionDecision(
        skill=str(data["skill"]),
        # round the delta so float noise (0.5 - 0.4 = 0.0999…) can't flip a verdict.
        promote=round(treat - base, 9) >= bar,
        baseline_rate=base,
        treatment_rate=treat,
        margin=bar,
        model=str(data.get("model", "")),
        provider=str(data.get("provider", "")),
        runs_per_arm=runs_per_arm,
    )


def _runs_per_arm(arm: Mapping[str, Any]) -> int:
    metrics = arm.get("metrics", {}) if isinstance(arm, Mapping) else {}
    return int(metrics.get("runs", 0) or 0)


def promoted_capability(decision: PromotionDecision) -> Capability:
    """The catalog ``Capability`` for a promoted skill, carrying its score as evidence.

    The measured numbers ride in ``payload["eval"]`` — the catalog entry *is* the
    evidence record. Raises if the decision is a HOLD (don't mint a capability for
    a skill that didn't clear) or the skill has no registered selector.
    """
    if not decision.promote:
        raise ValueError(f"{decision.skill} did not clear the bar — nothing to promote")
    selector = _SKILL_SELECTORS.get(decision.skill)
    if selector is None:
        raise ValueError(f"no catalog selector registered for skill {decision.skill!r}")
    return Capability(
        decision.skill,
        CapabilityKind.SKILL,
        _SKILL_SUMMARIES.get(decision.skill, decision.skill),
        selector,
        payload={
            "eval": {
                "id": decision.eval_id,
                "min_score": decision.min_score,
                "achieved": round(decision.treatment_rate, 3),
                "baseline": round(decision.baseline_rate, 3),
                "model": decision.model,
                "provider": decision.provider,
            }
        },
    )


def _selector_src(selector: CapabilitySelector) -> str:
    parts: list[str] = []
    if selector.languages is not None:
        langs = ", ".join(repr(s) for s in sorted(selector.languages))
        parts.append(f"languages=frozenset({{{langs}}})")
    if selector.task_types is not None:
        tts = ", ".join(repr(s) for s in sorted(selector.task_types))
        parts.append(f"task_types=frozenset({{{tts}}})")
    if selector.requires_db:
        parts.append("requires_db=True")
    return f"CapabilitySelector({', '.join(parts)})"


def capability_source(decision: PromotionDecision) -> str:
    """The exact ``Capability(...)`` source to add to the catalog's ``_PROMOTED`` overlay."""
    cap = promoted_capability(decision)
    return (
        f"    Capability(\n"
        f"        {cap.id!r},\n"
        f"        CapabilityKind.SKILL,\n"
        f"        {cap.summary!r},\n"
        f"        {_selector_src(cap.selector)},\n"
        f"        payload={cap.payload!r},\n"
        f"    ),"
    )


_PROMOTED_MARKER = "_PROMOTED: tuple[Capability, ...] = "
_CAP_ID_RE = re.compile(r"Capability\(\s*[\"']([\w-]+)[\"']")


def _promoted_span(source: str) -> tuple[int, int]:
    """``(open_paren_index, close_paren_index)`` of the ``_PROMOTED = (...)`` tuple.

    Scans for the balanced close so ``Capability(...)``'s own parens don't fool a
    naive match. Raises if the overlay marker is absent or unterminated."""
    start = source.find(_PROMOTED_MARKER)
    if start == -1:
        raise ValueError("catalog source has no _PROMOTED overlay marker")
    open_idx = source.index("(", start + len(_PROMOTED_MARKER))
    depth = 0
    for j in range(open_idx, len(source)):
        if source[j] == "(":
            depth += 1
        elif source[j] == ")":
            depth -= 1
            if depth == 0:
                return open_idx, j
    raise ValueError("unterminated _PROMOTED tuple")


def promoted_ids_in_source(source: str) -> list[str]:
    """The skill ids already present in the catalog's ``_PROMOTED`` overlay."""
    open_idx, close_idx = _promoted_span(source)
    return _CAP_ID_RE.findall(source[open_idx : close_idx + 1])


def apply_to_catalog_source(source: str, decisions: Iterable[PromotionDecision]) -> str:
    """Inject promoted skills into the catalog ``_PROMOTED`` overlay (idempotent).

    Only PROMOTE decisions are added, and only if not already present, so re-running
    is a no-op. Returns the source unchanged when there's nothing new to add. Pure
    text transform — the caller writes the file.
    """
    open_idx, close_idx = _promoted_span(source)
    existing_inner = source[open_idx + 1 : close_idx]
    existing_ids = set(_CAP_ID_RE.findall(existing_inner))
    new_snippets = [capability_source(d) for d in decisions if d.promote and d.skill not in existing_ids]
    if not new_snippets:
        return source
    pieces: list[str] = []
    if existing_inner.strip():
        pieces.append(existing_inner.strip("\n").rstrip())
    pieces.extend(s.rstrip() for s in new_snippets)
    new_block = "(\n" + "\n".join(pieces) + "\n)"
    return source[:open_idx] + new_block + source[close_idx + 1 :]


def render_decisions_log(decisions: Iterable[PromotionDecision], *, stamp: str) -> str:
    """An honest decisions log for ``docs/evals/`` — winners AND losers, with numbers."""
    rows = list(decisions)
    lines = [
        "# Persona-skill promotion decisions",
        "",
        f"_Updated {stamp}. The pre-registered bar is a held-out-acceptance margin over "
        "baseline; a skill promotes only when it clears it. Skills that don't are kept "
        "as candidates with their numbers, not dropped._",
        "",
        "| skill | provider/model | baseline | treatment | Δ | bar | runs/arm | decision |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for d in rows:
        lines.append(
            f"| {d.skill} | {d.provider}/{d.model} | {d.baseline_rate:.0%} | "
            f"{d.treatment_rate:.0%} | {d.delta:+.1%} | +{d.margin:.0%} | {d.runs_per_arm} | "
            f"{'**PROMOTE**' if d.promote else 'HOLD'} |"
        )
    promoted = [d.skill for d in rows if d.promote]
    held = [d.skill for d in rows if not d.promote]
    lines += [
        "",
        f"**Promoted:** {', '.join(promoted) or '—'}",
        f"**Held (still candidates):** {', '.join(held) or '—'}",
    ]
    return "\n".join(lines) + "\n"


__all__ = [
    "PromotionDecision",
    "apply_to_catalog_source",
    "capability_source",
    "decision_from_ab",
    "promoted_capability",
    "promoted_ids_in_source",
    "render_decisions_log",
]
