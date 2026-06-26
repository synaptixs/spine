"""IR validator: structural and reference checks for a GraphIR.

Sprint 6 ships the structural rules deferred from Sprint 1.9's Pydantic model:
DAG-ness, reachability from the entry node, pattern coherence (single_agent is
the only pattern the runtime executes today), budget sanity, and — when a
registry session is supplied — that every agent node's ``(template_id, version)``
resolves to a published row.

Per-edge confidence/evidence/policy verifier insertion lives in Sprint 10.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from orchestrator.ir.graph import GraphIR, Node, NodeType, WorkflowPattern
from orchestrator.registry._common import LifecycleState
from orchestrator.registry.db.models import AgentTemplateRow
from orchestrator.registry.repositories import VersionedRepo


@dataclass(frozen=True)
class IRValidationFailure:
    rule: str
    message: str
    field: str | None = None


class IRValidationReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    failures: list[dict[str, str | None]] = []

    @property
    def ok(self) -> bool:
        return not self.failures


def _to_report(failures: list[IRValidationFailure]) -> IRValidationReport:
    return IRValidationReport(
        failures=[{"rule": f.rule, "message": f.message, "field": f.field} for f in failures]
    )


class IRValidator:
    """Runs structural and reference checks against a GraphIR.

    Cross-registry checks require an ``AsyncSession`` so the validator can
    look up published templates. Skip the session for pure structural checks.
    """

    SUPPORTED_PATTERNS: frozenset[WorkflowPattern] = frozenset(
        {
            WorkflowPattern.SINGLE_AGENT,
            WorkflowPattern.SEQUENTIAL,
            WorkflowPattern.MANAGER_SPECIALISTS,
        }
    )

    async def validate(self, ir: GraphIR, *, session: AsyncSession | None = None) -> IRValidationReport:
        failures: list[IRValidationFailure] = []
        failures.extend(self._check_pattern_supported(ir))
        failures.extend(self._check_dag(ir))
        failures.extend(self._check_reachability(ir))
        failures.extend(self._check_pattern_coherence(ir))
        failures.extend(self._check_budget_sanity(ir))
        if session is not None:
            failures.extend(await self._check_references(ir, session))
        return _to_report(failures)

    def _check_pattern_supported(self, ir: GraphIR) -> list[IRValidationFailure]:
        if ir.spec.workflow_pattern not in self.SUPPORTED_PATTERNS:
            return [
                IRValidationFailure(
                    rule="pattern_unsupported",
                    field="spec.workflow_pattern",
                    message=(
                        f"workflow_pattern={ir.spec.workflow_pattern.value!r} not yet executable; "
                        f"runtime supports {{{', '.join(p.value for p in self.SUPPORTED_PATTERNS)}}}."
                    ),
                )
            ]
        return []

    def _check_dag(self, ir: GraphIR) -> list[IRValidationFailure]:
        adjacency: dict[str, set[str]] = {n.id: set() for n in ir.spec.nodes}
        for edge in ir.spec.edges:
            adjacency[edge.source].add(edge.target)

        # DFS colours: 0=unvisited, 1=on stack, 2=done.
        colour = dict.fromkeys(adjacency, 0)
        cycle: list[str] | None = None

        def dfs(node: str, stack: list[str]) -> bool:
            nonlocal cycle
            colour[node] = 1
            stack.append(node)
            for nbr in adjacency[node]:
                if colour[nbr] == 1:
                    idx = stack.index(nbr)
                    cycle = [*stack[idx:], nbr]
                    return True
                if colour[nbr] == 0 and dfs(nbr, stack):
                    return True
            stack.pop()
            colour[node] = 2
            return False

        for nid in adjacency:
            if colour[nid] == 0 and dfs(nid, []):
                break

        if cycle is not None:
            return [
                IRValidationFailure(
                    rule="cycle",
                    field="spec.edges",
                    message=f"cycle detected: {' -> '.join(cycle)}",
                )
            ]
        return []

    def _check_reachability(self, ir: GraphIR) -> list[IRValidationFailure]:
        if not ir.spec.nodes:
            return []
        adjacency: dict[str, set[str]] = {n.id: set() for n in ir.spec.nodes}
        incoming: dict[str, int] = {n.id: 0 for n in ir.spec.nodes}
        for edge in ir.spec.edges:
            adjacency[edge.source].add(edge.target)
            incoming[edge.target] += 1

        entries = [nid for nid, deg in incoming.items() if deg == 0]
        if not entries:
            return [
                IRValidationFailure(
                    rule="no_entry",
                    field="spec.nodes",
                    message="no node has zero in-degree; the graph cannot start",
                )
            ]

        reachable: set[str] = set()
        queue: deque[str] = deque(entries)
        while queue:
            cur = queue.popleft()
            if cur in reachable:
                continue
            reachable.add(cur)
            queue.extend(adjacency[cur])

        unreachable = sorted({n.id for n in ir.spec.nodes} - reachable)
        if unreachable:
            return [
                IRValidationFailure(
                    rule="unreachable",
                    field="spec.nodes",
                    message=f"unreachable from entry: {unreachable}",
                )
            ]
        return []

    def _check_pattern_coherence(self, ir: GraphIR) -> list[IRValidationFailure]:
        if ir.spec.workflow_pattern is WorkflowPattern.SINGLE_AGENT:
            agent_nodes = [n for n in ir.spec.nodes if n.type is NodeType.AGENT]
            if len(agent_nodes) != 1:
                return [
                    IRValidationFailure(
                        rule="single_agent_shape",
                        field="spec.nodes",
                        message=(
                            f"single_agent pattern requires exactly one agent node; got {len(agent_nodes)}"
                        ),
                    )
                ]
            return []
        if ir.spec.workflow_pattern is WorkflowPattern.SEQUENTIAL:
            return self._check_sequential_shape(ir)
        if ir.spec.workflow_pattern is WorkflowPattern.MANAGER_SPECIALISTS:
            return self._check_manager_specialists_shape(ir)
        return []

    def _check_manager_specialists_shape(self, ir: GraphIR) -> list[IRValidationFailure]:
        """Manager-specialists graphs need one node tagged role=manager and >=2 role=specialist."""
        agent_nodes = [n for n in ir.spec.nodes if n.type is NodeType.AGENT]
        managers = [n for n in agent_nodes if n.config.get("role") == "manager"]
        specialists = [n for n in agent_nodes if n.config.get("role") == "specialist"]
        if len(managers) != 1:
            return [
                IRValidationFailure(
                    rule="manager_specialists_shape",
                    field="spec.nodes",
                    message=(
                        "manager_specialists pattern requires exactly one node with "
                        f"config.role='manager'; got {len(managers)}"
                    ),
                )
            ]
        if len(specialists) < 2:
            return [
                IRValidationFailure(
                    rule="manager_specialists_shape",
                    field="spec.nodes",
                    message=(
                        "manager_specialists pattern requires at least two specialists; "
                        f"got {len(specialists)}"
                    ),
                )
            ]
        return []

    def _check_sequential_shape(self, ir: GraphIR) -> list[IRValidationFailure]:
        """Sequential graphs must form a linear chain of >=2 agent nodes."""
        agent_nodes = [n for n in ir.spec.nodes if n.type is NodeType.AGENT]
        if len(agent_nodes) < 2:
            return [
                IRValidationFailure(
                    rule="sequential_shape",
                    field="spec.nodes",
                    message=(f"sequential pattern requires at least two agent nodes; got {len(agent_nodes)}"),
                )
            ]
        # Build adjacency restricted to agent-only edges.
        agent_ids = {n.id for n in agent_nodes}
        agent_edges = [e for e in ir.spec.edges if e.source in agent_ids and e.target in agent_ids]
        out_degree: dict[str, int] = dict.fromkeys(agent_ids, 0)
        in_degree: dict[str, int] = dict.fromkeys(agent_ids, 0)
        for edge in agent_edges:
            out_degree[edge.source] += 1
            in_degree[edge.target] += 1
        bad = [node_id for node_id in agent_ids if out_degree[node_id] > 1 or in_degree[node_id] > 1]
        if bad:
            return [
                IRValidationFailure(
                    rule="sequential_shape",
                    field="spec.nodes",
                    message=f"sequential pattern requires a linear chain; branching nodes: {sorted(bad)}",
                )
            ]
        # Exactly one head (no incoming) and one tail (no outgoing).
        heads = [n for n in agent_ids if in_degree[n] == 0]
        tails = [n for n in agent_ids if out_degree[n] == 0]
        if len(heads) != 1 or len(tails) != 1:
            return [
                IRValidationFailure(
                    rule="sequential_shape",
                    field="spec.nodes",
                    message=(
                        f"sequential pattern requires one head and one tail; "
                        f"heads={sorted(heads)} tails={sorted(tails)}"
                    ),
                )
            ]
        return []

    def _check_budget_sanity(self, ir: GraphIR) -> list[IRValidationFailure]:
        # Pydantic enforces non-negative bounds; this exists for explicit auditability
        # and to surface a single rule name in the report if those bounds ever loosen.
        budget = ir.spec.budget
        out: list[IRValidationFailure] = []
        if budget.max_tokens is not None and budget.max_tokens <= 0:
            out.append(
                IRValidationFailure(rule="budget", field="spec.budget.max_tokens", message="must be > 0")
            )
        if budget.max_wall_clock_seconds is not None and budget.max_wall_clock_seconds <= 0:
            out.append(
                IRValidationFailure(
                    rule="budget",
                    field="spec.budget.max_wall_clock_seconds",
                    message="must be > 0",
                )
            )
        return out

    async def _check_references(self, ir: GraphIR, session: AsyncSession) -> list[IRValidationFailure]:
        repo = VersionedRepo(session, AgentTemplateRow)
        failures: list[IRValidationFailure] = []
        for node in ir.spec.nodes:
            if node.type is not NodeType.AGENT or not node.template_id:
                continue
            row = await _resolve_template(repo, node)
            if row is None:
                failures.append(
                    IRValidationFailure(
                        rule="reference_unresolved",
                        field=f"spec.nodes[{node.id}].template_id",
                        message=(
                            f"no published version of {node.template_id}"
                            f"{('@' + node.template_version) if node.template_version else ''}"
                        ),
                    )
                )
        return failures


async def _resolve_template(repo: VersionedRepo[AgentTemplateRow], node: Node) -> AgentTemplateRow | None:
    if node.template_id is None:
        return None
    if node.template_version is None:
        return await repo.get_latest_published(node.template_id)
    row = await repo.get_by_id_version(node.template_id, node.template_version)
    if row is None or row.status != LifecycleState.PUBLISHED.value:
        return None
    return row
