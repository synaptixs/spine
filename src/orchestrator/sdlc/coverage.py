"""Blast-radius regression coverage (C8): what a change should re-test.

Given a symbol you're about to change (or a fault site from RCA), this walks the
call graph to find everything that transitively depends on it — the blast radius
— and splits it into: **tests that already exercise it** and **production code in
the radius that lacks a covering test**. The gaps are exactly where a regression
test should go before the change ships.

Deterministic, no LLM. It's only as good as the call graph, which now exists for
Python / C / C++ / C# / Java / TypeScript (C5); on a language without CALLS the
radius is empty and the report says so rather than implying full coverage.

"Covered" here means *reachable from a test through the call graph* — a test
transitively calls the symbol. It doesn't prove the test asserts the right thing,
only that the symbol is exercised; treat gaps as "definitely untested", coverage
as "exercised, worth confirming".
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from orchestrator.pkg import FactStore
from orchestrator.pkg.facts import EdgeKind, Node, NodeKind

_TEST_PATH_RE = re.compile(
    r"(^|/)tests?/|(^|/)__tests__/|(^|/)test_|_test\.|\.test\.|\.spec\.|Test\.\w+$", re.IGNORECASE
)


def is_test_node(node: Node) -> bool:
    """Heuristic: does this symbol live in a test (by path or name)?"""
    path = (node.provenance.file if node.provenance else "").replace("\\", "/")
    if _TEST_PATH_RE.search(path):
        return True
    name = node.name.lower()
    return name.startswith("test") or name.endswith(("test", "tests", "spec"))


@dataclass(frozen=True)
class CoverageItem:
    name: str
    where: str  # "file:line"
    covered: bool  # reachable from a test through the call graph


@dataclass
class RegressionPlan:
    target: str = ""  # "name at file:line"
    target_covered: bool = False
    covering_tests: list[str] = field(default_factory=list)  # tests that exercise the target
    impacted: list[CoverageItem] = field(default_factory=list)  # production blast radius + coverage
    truncated: bool = False  # blast radius clipped to max_impacted
    grounded: bool = False
    call_graph_available: bool = False


def _is_reached_by_test(store: FactStore, node_id: str, *, max_depth: int = 3) -> bool:
    return any(
        is_test_node(n) for n, _ in store.impact_across(node_id, kinds=(EdgeKind.CALLS,), max_depth=max_depth)
    )


def build_regression_plan(store: FactStore, target_id: str, *, max_impacted: int = 20) -> RegressionPlan:
    """Compute the regression coverage plan for changing ``target_id``."""
    node = store.node(target_id)
    call_graph = bool(store.edges_of_kind(EdgeKind.CALLS))
    grounded = store.summary().get("grounded_nodes", 0) > 0
    if node is None:
        return RegressionPlan(grounded=grounded, call_graph_available=call_graph)

    where = str(node.provenance) if node.provenance else ""
    impact = store.impact_across(target_id, kinds=(EdgeKind.CALLS,), max_depth=4)
    covering = [f"{n.name} @ {n.provenance}" for n, _ in impact if is_test_node(n)]

    production = [n for n, _ in impact if not is_test_node(n)]
    clipped = production[:max_impacted]
    items = [
        CoverageItem(
            name=n.name,
            where=str(n.provenance) if n.provenance else "",
            covered=_is_reached_by_test(store, n.id),
        )
        for n in clipped
    ]
    return RegressionPlan(
        target=f"{node.name} at {where}" if where else node.name,
        target_covered=bool(covering),
        covering_tests=covering,
        impacted=items,
        truncated=len(production) > len(clipped),
        grounded=grounded,
        call_graph_available=call_graph,
    )


def resolve_target(store: FactStore, name: str) -> str | None:
    """Best grounded FUNCTION/TYPE node id for a symbol name (for the CLI)."""
    hits = store.find(name)
    for kind in (NodeKind.FUNCTION, NodeKind.TYPE):
        for n in hits:
            if n.grounded and n.kind is kind:
                return n.id
    return hits[0].id if hits else None


def render_regression_plan_md(plan: RegressionPlan) -> str:
    out: list[str] = ["# Regression coverage\n"]
    if not plan.target:
        out.append("_Target symbol not found in the knowledge graph._\n")
        return "\n".join(out)

    out.append(f"**Change target:** `{plan.target}`\n")
    if not plan.call_graph_available:
        out.append(
            "_No call graph for this language — the blast radius can't be computed; "
            "review dependents manually._\n"
        )
        return "\n".join(out)

    status = "✓ exercised by tests" if plan.target_covered else "⚠ no test exercises it directly"
    out.append(f"**Target coverage:** {status}")
    if plan.covering_tests:
        out.extend(f"- {t}" for t in plan.covering_tests[:10])
    out.append("")

    gaps = [i for i in plan.impacted if not i.covered]
    covered = [i for i in plan.impacted if i.covered]

    out.append("## Regression gaps — add tests here")
    if gaps:
        out.append(
            "_In the blast radius and NOT reached by any test — a change here could break these silently:_\n"
        )
        out.extend(f"- `{i.name}` — {i.where}" for i in gaps)
    else:
        out.append("_Every impacted symbol is reached by some test._")
    out.append("")

    if covered:
        out.append("## Already covered (in the blast radius)")
        out.extend(f"- `{i.name}` — {i.where}" for i in covered[:15])
        out.append("")
    if plan.truncated:
        out.append("_Blast radius clipped — showing the top impacted symbols only._\n")

    out.append("## Next step")
    out.append(
        "Before changing the target, add/verify regression tests for the gaps above, then "
        "re-run the suite over the impacted set."
    )
    return "\n".join(out) + "\n"


__all__ = [
    "CoverageItem",
    "RegressionPlan",
    "build_regression_plan",
    "is_test_node",
    "render_regression_plan_md",
    "resolve_target",
]
