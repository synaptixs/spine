"""Top-k localization — given a real issue, does Spine land where the fix went?

The claim this scores is the one the product is sold on: *"`investigate` finds where a ticket
lands."* Until now it was unmeasured, and `ticket-to-landing-sites.md` says so — a `0` there
would not have distinguished *bad* from *never measured*.

**How a run works.** The repository is materialised at its pinned commit — the **pre-fix**
state — its graph is built, and ``build_investigation`` is given the issue's own words. The
landing sites it returns are reduced to files, and a label scores a hit at *k* when any of its
recorded fix paths appears in the first *k*.

**Files, not symbols.** ``investigate`` returns symbols and a symbol's file is unambiguous, so
a path is what a reader can check and what the tool can be held to. Symbol-level accuracy is a
stricter question the same labels can answer later; scoring it now would confuse *"looked in
the wrong place"* with *"named the enclosing function rather than the method"*.

**Deterministic and no-LLM**, like everything on the scoreboard. Same commit, same labels, same
number.
"""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

from orchestrator.evals.labels import GoldSet, Label

#: The cut-offs reported. 1 is the honest headline — did it land first — and 10 is
#: `build_investigation`'s own default ceiling, so a larger k would measure nothing new.
DEFAULT_KS: tuple[int, ...] = (1, 3, 5, 10)


@dataclass(frozen=True)
class LocalizationResult:
    """One labelled issue, scored."""

    issue: str
    repo: str
    rank: int | None  # 1-based position of the first correct file; None = not found
    returned: int  # how many landing sites came back, so a miss can be read


@dataclass(frozen=True)
class LocalizationReport:
    """Top-k hit rates over the gold set."""

    results: tuple[LocalizationResult, ...]
    ks: tuple[int, ...] = DEFAULT_KS

    @property
    def measured(self) -> bool:
        """False when nothing was labelled — "not yet", never "bad"."""
        return bool(self.results)

    def hits_at(self, k: int) -> int:
        return sum(1 for r in self.results if r.rank is not None and r.rank <= k)

    def rate_at(self, k: int) -> Fraction | None:
        """Exact, from the integer counts. Floats are for display only."""
        return Fraction(self.hits_at(k), len(self.results)) if self.results else None

    def as_dict(self) -> dict[str, object]:
        return {
            "labelled": len(self.results),
            "top_k": {str(k): {"hits": self.hits_at(k), "of": len(self.results)} for k in self.ks},
            # A miss where nothing came back at all is a different failure from a miss among
            # ten candidates, and averaging them would hide it.
            "empty_results": sum(1 for r in self.results if r.returned == 0),
        }


def score_label(label: Label, root: Path | str, *, max_symbols: int = max(DEFAULT_KS)) -> LocalizationResult:
    """Run ``investigate`` for one issue against the pinned tree and rank the answer."""
    from orchestrator.pkg.persistence import load_or_extract
    from orchestrator.pkg.store import FactStore
    from orchestrator.sdlc.investigate import build_investigation

    root_path = Path(root)
    store = FactStore(load_or_extract(root_path))
    investigation = build_investigation(
        label.title, label.body, store=store, root=root_path, max_symbols=max_symbols
    )
    wanted = label.paths
    rank: int | None = None
    for position, landing in enumerate(investigation.landing, start=1):
        # `where` is "file:line"; rsplit so a Windows-style path with a colon cannot confuse it.
        if landing.where.rsplit(":", 1)[0] in wanted:
            rank = position
            break
    return LocalizationResult(
        issue=label.issue, repo=label.repo, rank=rank, returned=len(investigation.landing)
    )


def score_localization(gold: GoldSet, roots: dict[str, Path]) -> LocalizationReport:
    """Score every label whose repository is **on disk right now**.

    A label whose repository is absent from ``roots``, or whose root no longer exists, is
    **skipped, not missed**. A corpus that could not be fetched must not read as a tool that
    could not find anything.

    The existence check is not defensive padding — it is here because the omission shipped and
    scored. The first run of this scorer reported **0.00 at every k across 24 labels**, a clean
    and entirely publishable-looking number, because the caller held the checkouts in a
    ``TemporaryDirectory`` that had already exited: every path was gone, every extraction
    returned an empty graph, and every label scored as "no landing site". A total plumbing
    failure is indistinguishable from a catastrophic result unless something refuses to score
    what it cannot read.
    """
    scored = [label for label in gold.labels if label.repo in roots and Path(roots[label.repo]).is_dir()]
    return LocalizationReport(results=tuple(score_label(label, roots[label.repo]) for label in scored))


__all__ = [
    "DEFAULT_KS",
    "LocalizationReport",
    "LocalizationResult",
    "score_label",
    "score_localization",
]
