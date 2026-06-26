"""Track 3.5 / G10: calibrated escalation — knowing when not to know.

Autonomy's hardest requirement isn't doing the work, it's recognising when a
change deserves a human even though every gate is technically green. The
``EscalationPolicy`` combines the run's *risk signals* into a deterministic
decision the pipeline records before the merge gate:

- **judge uncertainty** — the semantic reviewer couldn't confirm criteria;
- **refinement effort** — many test→patch cycles means the model struggled,
  and struggle correlates with subtle breakage;
- **blast radius** — the change touches symbols with many cross-file callers
  (computed from the PKG, the same graph the reviewer uses).

Escalation never blocks the pipeline — green is still green. It *annotates*:
the feature is flagged, audited, and surfaced to the gate-2 approver, who now
reviews with attention proportional to risk instead of rubber-stamping.

v0 thresholds are static; the seam exists for the registry's
confidence-calibration history to tune them per-agent later.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from orchestrator.pkg import FactStore, RepoCodeExtractor


@dataclass(frozen=True)
class EscalationSignals:
    """The per-feature risk signals the policy weighs."""

    uncertain_criteria: list[str] = field(default_factory=list)
    iterations: int = 0
    blast_radius: int = 0
    radius_symbol: str = ""


@dataclass(frozen=True)
class EscalationDecision:
    escalate: bool
    reasons: list[str] = field(default_factory=list)


class EscalationPolicy:
    """Deterministic v0 policy over the risk signals."""

    def __init__(
        self,
        *,
        max_uncertain: int = 0,
        max_iterations: int = 3,
        max_blast_radius: int = 10,
    ) -> None:
        self._max_uncertain = max_uncertain
        self._max_iterations = max_iterations
        self._max_radius = max_blast_radius

    def decide(self, signals: EscalationSignals) -> EscalationDecision:
        reasons: list[str] = []
        if len(signals.uncertain_criteria) > self._max_uncertain:
            sample = "; ".join(signals.uncertain_criteria[:3])
            reasons.append(
                f"semantic judge uncertain on {len(signals.uncertain_criteria)} criteria ({sample})"
            )
        if signals.iterations > self._max_iterations:
            reasons.append(
                f"refinement took {signals.iterations} test cycles (>{self._max_iterations}) — "
                "the model struggled with this change"
            )
        if signals.blast_radius > self._max_radius:
            reasons.append(
                f"high blast radius: `{signals.radius_symbol}` has {signals.blast_radius} "
                f"cross-file callers (>{self._max_radius})"
            )
        return EscalationDecision(escalate=bool(reasons), reasons=reasons)


def blast_radius(root: Path | str) -> tuple[int, str]:
    """Max cross-file caller count among symbols defined in this change's files.

    Extracts the worktree fresh (it's dirty by definition — the change isn't
    committed) and, for each grounded symbol in a changed ``.py`` file, counts
    callers whose provenance lies in a *different* file. Returns ``(0, "")``
    for non-git directories or changes touching nothing extractable.
    """
    root_path = Path(root)
    proc = subprocess.run(
        ["git", "-C", str(root_path), "status", "--porcelain"], capture_output=True, text=True, check=False
    )
    if proc.returncode != 0:
        return 0, ""
    changed = {
        line[3:].strip().strip('"')
        for line in proc.stdout.splitlines()
        if line[3:].strip().strip('"').endswith(".py")
    }
    if not changed:
        return 0, ""

    store = FactStore(RepoCodeExtractor().extract(root_path))
    worst, worst_symbol = 0, ""
    for node in store.nodes:
        prov = node.provenance
        if not node.grounded or prov is None or prov.file not in changed:
            continue
        cross = [
            c
            for c in store.callers_of(node.id)
            if c.caller.provenance is not None and c.caller.provenance.file != prov.file
        ]
        if len(cross) > worst:
            worst, worst_symbol = len(cross), node.id
    return worst, worst_symbol


__all__ = ["EscalationDecision", "EscalationPolicy", "EscalationSignals", "blast_radius"]
