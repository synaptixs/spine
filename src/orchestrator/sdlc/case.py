"""The Case — one engineering objective's state, per node rather than per stage.

`RunContext` already carries a run's identity, branch, worktree, PR and verdict, and
checkpoints after every stage. What it could not do is say *what each step computed*: a stage
recorded a status line and a markdown artifact, so a resume restarted a stage rather than
continuing from it, and nothing could be replayed and compared.

The Case adds exactly that — a typed row per graph node with the **digest of what it produced**
— and nothing else. `RunContext` remains where a run lives; this is a view of the same run
keyed by node. Phase 2a of ``docs/specs/graphir-sdlc-workflow.md``.

**Digests cover content, never timing.** `seconds` and `cost_usd` are recorded because a run
that spent money should be able to say where, but they are excluded from every digest: two
identical runs must agree, and no two runs take the same time. This is the same line the
determinism boundary draws — a clock may be *reported*, never *computed with*.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from orchestrator.core.digest import digest_of

__all__ = ["Case", "NodeResult", "load_case"]

NodeStatus = Literal["ok", "skipped", "failed"]


@dataclass(frozen=True)
class NodeResult:
    """What one graph node produced."""

    node: str  # IR node id, e.g. "n_investigate"
    kind: str  # "tool" | "agent"
    status: NodeStatus
    digest: str = ""  # of the node's output; "" when it produced no comparable value
    tool: str = ""  # registered tool name, "" for an agent node
    detail: str = ""
    seconds: float = 0.0  # reported, never digested
    cost_usd: float = 0.0  # reported, never digested

    def to_dict(self) -> dict[str, Any]:
        return {
            "node": self.node,
            "kind": self.kind,
            "status": self.status,
            "digest": self.digest,
            "tool": self.tool,
            "detail": self.detail,
            "seconds": round(self.seconds, 3),
            "cost_usd": round(self.cost_usd, 6),
        }


@dataclass
class Case:
    """One ticket's run, as the graph executed it."""

    run_id: str
    issue_key: str = ""
    issue_type: str = ""
    title: str = ""
    profile: str = ""
    mode: str = "graph"  # "graph" | "imperative" — which path produced this case
    evidence: dict[str, Any] = field(default_factory=dict)
    criteria: dict[str, Any] = field(default_factory=dict)
    nodes: list[NodeResult] = field(default_factory=list)

    def record(
        self,
        node: str,
        *,
        kind: str,
        status: NodeStatus,
        digest: str = "",
        tool: str = "",
        detail: str = "",
        seconds: float = 0.0,
        cost_usd: float = 0.0,
    ) -> NodeResult:
        """Append a node's result. Re-recording a node replaces it, so a retried node leaves
        one row rather than a history that reads as two executions."""
        result = NodeResult(
            node=node,
            kind=kind,
            status=status,
            digest=digest,
            tool=tool,
            detail=detail,
            seconds=seconds,
            cost_usd=cost_usd,
        )
        self.nodes = [n for n in self.nodes if n.node != node]
        self.nodes.append(result)
        return result

    def result(self, node: str) -> NodeResult | None:
        return next((n for n in self.nodes if n.node == node), None)

    @property
    def spent_usd(self) -> float:
        return round(sum(n.cost_usd for n in self.nodes), 6)

    @property
    def seconds(self) -> float:
        """Wall-clock the recorded nodes account for.

        The sum of the rows, not of the run: `autorun` does work between nodes — the PKG
        extraction, the plan gate, writing artifacts — that no node owns. Reporting this as the
        run's duration would quietly credit the graph with time it never spent.
        """
        return round(sum(n.seconds for n in self.nodes), 3)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "issue_key": self.issue_key,
            "issue_type": self.issue_type,
            "title": self.title,
            "profile": self.profile,
            "mode": self.mode,
            "evidence": self.evidence,
            "criteria": self.criteria,
            "nodes": [n.to_dict() for n in self.nodes],
            "digest": self.digest(),
        }

    def digest(self) -> str:
        """Content digest of the run: evidence, criteria, and each node's output digest.

        Excludes timing, cost and `run_id` — everything that legitimately differs between two
        runs of the same commit. What remains is the thing a replay must reproduce.
        """
        return digest_of(
            {
                "evidence": self.evidence,
                "criteria": self.criteria,
                "nodes": [
                    {"node": n.node, "kind": n.kind, "status": n.status, "digest": n.digest}
                    for n in sorted(self.nodes, key=lambda n: n.node)
                ],
            }
        )

    def write(self, path: Path | str) -> str:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(self.to_dict(), indent=2, sort_keys=True), encoding="utf-8")
        return str(target)

    def render(self) -> str:
        """The graph as it actually executed — including what was skipped, and why.

        A summary that lists only the nodes that ran cannot be told apart from one where the
        rest were never reached, which is the ambiguity `bound honestly` exists to prevent.
        """
        out = [f"# Run {self.run_id}", ""]
        if self.title:
            out.append(f"**{self.title}**  ")
        out.append(
            f"profile `{self.profile or '—'}` · mode `{self.mode}` · "
            f"{len(self.nodes)} node(s) · {self.seconds:.2f}s in-node · "
            f"digest `{self.digest()[:12]}`"
        )
        out.append("")
        out.append("| Node | Kind | Status | Detail | Digest | Seconds | USD |")
        out.append("|---|---|---|---|---|---|---|")
        for n in self.nodes:
            mark = {"ok": "✅", "skipped": "➖", "failed": "❌"}[n.status]
            out.append(
                f"| `{n.node}` | {n.kind} | {mark} {n.status} | {n.detail or '—'} | "
                f"`{n.digest[:12] or '—'}` | {n.seconds:.2f} | {n.cost_usd:.4f} |"
            )
        criteria = self.criteria or {}
        if criteria:
            out += [
                "",
                "## Acceptance criteria",
                "",
                f"{criteria.get('bound', 0)} bound · {criteria.get('unbound', 0)} unbound · "
                f"{criteria.get('no_claim', 0)} not a code claim",
            ]
            for row in criteria.get("rows") or []:
                if row.get("status") == "unbound":
                    named = ", ".join(f"`{c}`" for c in row.get("claims") or [])
                    out.append(f"- ❌ {row.get('text', '')} — names {named}, absent from the graph")
        out.append("")
        return "\n".join(out)


def load_case(path: Path | str) -> Case:
    """Read a persisted Case. Used by `sdlc explain`."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    case = Case(
        run_id=str(data.get("run_id", "")),
        issue_key=str(data.get("issue_key", "")),
        issue_type=str(data.get("issue_type", "")),
        title=str(data.get("title", "")),
        profile=str(data.get("profile", "")),
        mode=str(data.get("mode", "graph")),
        evidence=dict(data.get("evidence") or {}),
        criteria=dict(data.get("criteria") or {}),
    )
    for row in data.get("nodes") or []:
        case.record(
            str(row.get("node", "")),
            kind=str(row.get("kind", "")),
            status=row.get("status", "ok"),
            digest=str(row.get("digest", "")),
            tool=str(row.get("tool", "")),
            detail=str(row.get("detail", "")),
            seconds=float(row.get("seconds", 0.0)),
            cost_usd=float(row.get("cost_usd", 0.0)),
        )
    return case
