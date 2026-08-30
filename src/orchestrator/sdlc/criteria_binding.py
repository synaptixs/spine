"""Bind each acceptance criterion to a symbol the graph actually holds.

**The defect this closes.** `spec["acceptance_criteria"]` originates in intake — a model reading
a document, not the code — and is read straight through by `design.py`, `codegen.py` and
`grounding.py`. Tests are generated against criteria written by something that had not examined
the repository. `validity` catches false *counts*; nothing bound a criterion to a symbol or a
`file:line`. Defect 4 of ``docs/specs/graphir-sdlc-workflow.md``.

**Why this is not a new rule.** A criterion is a claim about code, which is exactly what
`pkg/docs.py` already reconciles for documentation: it extracts code-intent mentions
deterministically, binds them to PKG anchors, and reports the unbound ones as drift — *"the docs
lie about the code"*. A criterion nobody can locate is the same failure wearing a different hat,
so this module reuses that extractor and that binder rather than defining a second notion of
"names a symbol". Two definitions would be two things that can disagree, and the first
disagreement would be reported as a defect in the pipeline.

**What parks a run, precisely.** Only a criterion that *makes a claim and gets it wrong*, and
only on a ticket whose subject is code that already exists:

| Criterion | Status | Parks? |
|---|---|---|
| names a symbol/file the graph holds | `bound` | no |
| names a symbol/file with **code intent** that resolves to nothing | `unbound` | **on a bug** |
| prose, or a mention that cannot carry code intent | `no-claim` | no |

The middle row is a false premise on a bug and the *deliverable* on an enhancement — the same
binding, two meanings — so `validity.assess` refuses the first and reports the second. Refusing
both would mean the better-written criterion is the one that parks a feature: "the compiler
returns >= 70 rules" is prose and proceeds, while "`rule_compiler` returns >= 70 rules" names
its subject, is more testable, and would be the one to stop the run.

The third row is what keeps this from parking everything. `_can_drift` in `pkg/docs.py` is the
arbiter: CamelCase prose ("GitHub", "Python"), ALL-CAPS tokens (env vars, config keys), plain
single backticked words (tool and branch names), and paths pointing outside the repo bind when
they resolve but never count against the author. A criterion reading *"the report should be
readable"* names nothing checkable and must not stop a build; one reading *"`OrderTotals` is
recalculated"* against a repo with no such symbol is a false premise, and building to it produces
code that passes its own tests and is still wrong — the same argument `assess()` already makes
for a false count.

Deterministic: same `(commit, spec)` in, same bindings out. No model, no network.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from orchestrator.pkg import FactStore
from orchestrator.pkg.docs import DocPage, DocReconciler, MentionKind, extract_mentions

__all__ = [
    "Anchor",
    "CriteriaBinding",
    "CriterionBinding",
    "bind_criteria",
]

BindingStatus = Literal["bound", "unbound", "no-claim"]

# More than a handful of anchors stops identifying the criterion and starts listing the
# repository. The first few, in graph order, are the ones a reader acts on.
_MAX_ANCHORS = 5


@dataclass(frozen=True)
class Anchor:
    """Where a criterion's claim landed in the code."""

    text: str  # the mention as the criterion wrote it
    symbol: str  # resolved node name, or the file path for a file anchor
    node_id: str  # "" for a file anchor
    where: str  # "file:line", or the file for a file anchor
    in_evidence: bool  # this anchor is a file the ticket's Evidence says it lands on


@dataclass(frozen=True)
class CriterionBinding:
    """One acceptance criterion, judged against the graph."""

    text: str
    status: BindingStatus
    anchors: tuple[Anchor, ...] = ()
    claims: tuple[str, ...] = ()  # code-intent mentions that resolved to nothing
    reason: str = ""

    @property
    def parks(self) -> bool:
        """Only an unbound *claim* can stop a run. Prose never can.

        **Can**, not does: `validity.assess` makes that call, and it depends on the issue
        type. A bug's subject is code that already exists, so an unbound claim is a false
        premise and parks. An enhancement's unbound claim names the deliverable, and is
        reported without stopping the run. This flag is the gate's input, not its verdict.
        """
        return self.status == "unbound"


@dataclass(frozen=True)
class CriteriaBinding:
    """Every criterion in a spec, with the ones that cannot be located."""

    rows: tuple[CriterionBinding, ...] = ()

    @property
    def bound(self) -> tuple[CriterionBinding, ...]:
        return tuple(r for r in self.rows if r.status == "bound")

    @property
    def unbound(self) -> tuple[CriterionBinding, ...]:
        return tuple(r for r in self.rows if r.status == "unbound")

    @property
    def no_claim(self) -> tuple[CriterionBinding, ...]:
        return tuple(r for r in self.rows if r.status == "no-claim")

    @property
    def parks(self) -> bool:
        return any(r.parks for r in self.rows)

    def to_dict(self) -> dict[str, Any]:
        return {
            "criteria": len(self.rows),
            "bound": len(self.bound),
            "unbound": len(self.unbound),
            "no_claim": len(self.no_claim),
            "parks": self.parks,
            "rows": [
                {
                    "text": r.text,
                    "status": r.status,
                    "reason": r.reason,
                    "claims": list(r.claims),
                    "anchors": [
                        {
                            "text": a.text,
                            "symbol": a.symbol,
                            "node_id": a.node_id,
                            "where": a.where,
                            "in_evidence": a.in_evidence,
                        }
                        for a in r.anchors
                    ],
                }
                for r in self.rows
            ],
        }

    def render(self) -> str:
        """Markdown for the run's artifacts. Every criterion appears, bound or not.

        Listing only the failures would leave a reader unable to tell "all bound" from "none
        checked" — the distinction this project keeps losing to a summary line.
        """
        if not self.rows:
            return "# Acceptance criteria\n\n_The ticket states no acceptance criteria._\n"
        out = [
            "# Acceptance criteria",
            "",
            f"_{len(self.bound)} bound · {len(self.unbound)} unbound · "
            f"{len(self.no_claim)} not a code claim. Deterministic: the graph answers._",
            "",
        ]
        for row in self.rows:
            mark = {"bound": "✅", "unbound": "❌", "no-claim": "➖"}[row.status]
            out.append(f"{mark} {row.text}")
            for anchor in row.anchors:
                loc = f" — `{anchor.where}`" if anchor.where else ""
                lands = " _(the ticket lands here)_" if anchor.in_evidence else ""
                out.append(f"   - `{anchor.symbol}`{loc}{lands}")
            if row.status == "unbound":
                named = ", ".join(f"`{claim}`" for claim in row.claims)
                out.append(f"   - **names {named}, which the code does not define**")
            elif row.status == "no-claim" and row.reason:
                out.append(f"   - _{row.reason}_")
        out.append("")
        return "\n".join(out)


def _criteria_text(spec: dict[str, Any]) -> list[str]:
    """The criteria as written. Same field `design`, `codegen` and `grounding` all read."""
    raw = spec.get("acceptance_criteria") or []
    if isinstance(raw, str):
        raw = [raw]
    return [str(item).strip() for item in raw if str(item).strip()]


def bind_criteria(
    spec: dict[str, Any],
    *,
    store: FactStore,
    evidence_files: tuple[str, ...] | list[str] = (),
    root: Path | str | None = None,
) -> CriteriaBinding:
    """Bind every acceptance criterion in ``spec`` against the graph.

    ``evidence_files`` are the files the ticket's Evidence says it lands on; an anchor inside
    one is flagged, because a criterion that binds *where the ticket actually is* is stronger
    evidence than one binding a same-named symbol on the other side of the repository.
    """
    criteria = _criteria_text(spec)
    if not criteria:
        return CriteriaBinding()

    reconciler = DocReconciler.from_nodes(store.nodes, repo_root=root)
    landing = {str(f) for f in evidence_files}
    rows: list[CriterionBinding] = []

    for text in criteria:
        page = DocPage(title="criterion", text=text)
        mentions = extract_mentions(page)
        anchors: list[Anchor] = []
        unresolved_claims: list[str] = []
        had_claim = False

        for mention in mentions:
            binding = reconciler.bind(mention)
            # `_can_drift` is the precision-first arbiter; private on purpose, and reaching for
            # it here is deliberate. Reimplementing the rule would be the second definition this
            # module exists to avoid.
            claimable = reconciler._can_drift(mention)  # noqa: SLF001
            had_claim = had_claim or claimable
            if not binding.bound:
                if claimable:
                    unresolved_claims.append(mention.text)
                continue
            for node_id in binding.anchor_ids[:_MAX_ANCHORS]:
                node = store.node(node_id)
                where = str(node.provenance) if node is not None and node.provenance else ""
                anchors.append(
                    Anchor(
                        text=mention.text,
                        symbol=node.name if node is not None else node_id,
                        node_id=node_id,
                        where=where,
                        in_evidence=where.split(":", 1)[0] in landing,
                    )
                )
            # Sorted, and that is load-bearing: `DocReconciler` collects provenance files in a
            # `set`, so its file anchors come out in hash order and differ between processes.
            # Unsorted, two identical runs would produce different bindings — the `state` bug
            # of 3.19.0 in a new place.
            for path in sorted(binding.anchor_files)[:_MAX_ANCHORS]:
                anchors.append(
                    Anchor(
                        text=mention.text,
                        symbol=path,
                        node_id="",
                        where=path,
                        in_evidence=path in landing,
                    )
                )

        if anchors:
            rows.append(CriterionBinding(text=text, status="bound", anchors=tuple(anchors[:_MAX_ANCHORS])))
        elif had_claim:
            rows.append(
                CriterionBinding(
                    text=text,
                    status="unbound",
                    claims=tuple(unresolved_claims),
                    reason="names code the graph does not hold",
                )
            )
        else:
            rows.append(
                CriterionBinding(
                    text=text,
                    status="no-claim",
                    reason=(
                        "states no symbol, file or path this graph can check — prose is not a false premise"
                        if mentions
                        else "no code-intent mention"
                    ),
                )
            )
    return CriteriaBinding(rows=tuple(rows))


def unbindable_kinds() -> tuple[str, ...]:
    """Mention kinds that never park a run. Exported so a test can assert the list, rather
    than the list living only inside a conditional."""
    return (MentionKind.CAMEL.value,)
