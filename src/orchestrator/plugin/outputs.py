"""What each tool returns, as a type a host can read before it calls.

Every tool returns a plain ``dict``; that does not change here. What changes is that the
server advertises an **output schema** per tool, derived from these ``TypedDict``s, so a host
knows the fields — ``found``, ``matches[].where``, ``uncovered_elsewhere`` — without parsing
markdown or guessing. The SDK builds the schema from the type and validates a result
against it.

**Two properties the types are built around.**

- *No key is required.* Every tool has an error path (``{"error": …, "hint": …}``) and most
  have several shapes (found / not found; single-repo / multi-repo). ``total=False`` lets one
  type describe them all, and the shared :class:`Failure` keys sit on every type.
- *An undeclared key would be dropped silently.* Pydantic validates a ``TypedDict`` by
  keeping the declared keys and discarding the rest — the text copy of the result keeps
  them, the structured copy does not. So the types carry ``extra="allow"`` (nothing is
  lost at runtime even if a key is missing here), and ``tests/plugin/conftest.py`` wraps
  every tool during the test run and fails on any returned key the type does not declare —
  the drift guard. Adding a key to a tool means adding it here, on one screen.

Open shapes — the twelve-section build document, a run's Temporal status, the engine's
design dict — stay ``dict[str, Any]``: typing them would mean re-declaring another
module's contract here and letting the two drift.
"""

from __future__ import annotations

from typing import Any, TypedDict

from pydantic import ConfigDict, with_config

#: Every type carries this so an undeclared-but-present key survives into the structured
#: result; the drift guard, not the runtime, is what keeps the declarations honest.
_OPEN = ConfigDict(extra="allow")


@with_config(_OPEN)
class Failure(TypedDict, total=False):
    """The keys any tool may return instead of a result."""

    error: str
    hint: str
    registry: str  # registry_* tools: which base URL did not answer
    code: int  # sdlc_complete / sdlc_remediate: the engine's exit code
    step: str  # sdlc_address_review: which checkout step failed
    needs: str  # scope guard: the scope the token lacked
    has: list[str]  # scope guard: the scopes the token has
    valid_fields: list[str]  # sdlc_plan / design_change: the spec's valid fields


@with_config(_OPEN)
class Standing(TypedDict, total=False):
    repos: list[str]
    reproducible: bool
    untrusted: list[str]


@with_config(_OPEN)
class ReposNote(TypedDict, total=False):
    config: str
    declares: list[str]
    note: str


# ---- doctor ---------------------------------------------------------------------------


@with_config(_OPEN)
class Check(TypedDict, total=False):
    name: str
    passed: bool
    detail: str


@with_config(_OPEN)
class ServerIdentity(TypedDict, total=False):
    package: str
    version: str | None
    python: str
    interpreter: str
    mcp_sdk: str | None
    extras: dict[str, bool]


@with_config(_OPEN)
class DoctorOut(Failure, total=False):
    all_passed: bool
    server: ServerIdentity
    checks: list[Check]


# ---- intake -----------------------------------------------------------------------------


@with_config(_OPEN)
class IntentSummary(TypedDict, total=False):
    id: str
    title: str


@with_config(_OPEN)
class IngestPreviewOut(Failure, total=False):
    documents: int
    intent_count: int
    intents: list[IntentSummary]
    gap_count: int
    blocked: bool


# ---- codegen --------------------------------------------------------------------------


@with_config(_OPEN)
class SdlcFeatureOut(Failure, total=False):
    passed: bool
    intent_id: str
    issue_key: str
    branch: str
    files: list[str]
    iterations: int
    grounding_chars: int
    live: bool
    pr_url: str | None


@with_config(_OPEN)
class PkgGroundingOut(Failure, total=False):
    chars: int
    context: str
    summary: dict[str, Any]
    title: str


@with_config(_OPEN)
class ReadMemoryBankOut(Failure, total=False):
    exists: bool
    dir: str
    sections: list[str]
    index: str
    section: str
    content: str | None


# ---- comprehension --------------------------------------------------------------------


@with_config(_OPEN)
class Hotspot(TypedDict, total=False):
    function: str
    call_sites: int


@with_config(_OPEN)
class UntestedArea(TypedDict, total=False):
    area: str
    types: int


@with_config(_OPEN)
class Coverage(TypedDict, total=False):
    tested_areas: int
    total_areas: int
    largest_untested: list[UntestedArea]


@with_config(_OPEN)
class Recommendation(TypedDict, total=False):
    priority: str
    action: str


@with_config(_OPEN)
class MapRepoOut(Failure, total=False):
    languages: list[str]
    counts: dict[str, int]
    areas: int
    files: int
    has_call_graph: bool
    call_hotspots: list[Hotspot]
    coverage: Coverage
    recommendations: list[Recommendation]
    markdown: str
    multi_repo_available: ReposNote


@with_config(_OPEN)
class CallSite(TypedDict, total=False):
    id: str
    at: str


@with_config(_OPEN)
class Touched(TypedDict, total=False):
    id: str
    where: str | None


@with_config(_OPEN)
class CrossRepoReach(TypedDict, total=False):
    id: str
    repo: str
    hops: int
    where: str | None


@with_config(_OPEN)
class BlastMatch(TypedDict, total=False):
    id: str
    kind: str
    where: str | None
    caller_count: int
    callers: list[CallSite]
    touch_count: int
    touches: list[Touched]
    cross_repo_count: int
    cross_repo: list[CrossRepoReach]


@with_config(_OPEN)
class BlastRadiusOut(Failure, total=False):
    symbol: str
    found: bool
    matches: list[BlastMatch]
    markdown: str
    standing: Standing
    multi_repo_available: ReposNote


@with_config(_OPEN)
class SymbolMatch(TypedDict, total=False):
    id: str
    kind: str
    name: str
    language: str
    where: str | None
    called_by: list[str]
    calls: list[str]
    contains: list[str]
    repo: str
    cross_repo_count: int
    cross_repo: list[CrossRepoReach]


@with_config(_OPEN)
class ExplainSymbolOut(Failure, total=False):
    symbol: str
    found: bool
    matches: list[SymbolMatch]
    standing: Standing
    multi_repo_available: ReposNote


@with_config(_OPEN)
class Landing(TypedDict, total=False):
    name: str
    kind: str
    where: str | None
    module: str
    callers: int
    repo: str
    cross_repo: list[CrossRepoReach]


@with_config(_OPEN)
class InvestigateOut(Failure, total=False):
    title: str
    landing: list[Landing]
    areas: list[str]
    has_knowledge: bool
    knowledge: dict[str, Any]
    markdown: str
    standing: Standing
    multi_repo_available: ReposNote


@with_config(_OPEN)
class PkgJoinsOut(Failure, total=False):
    mode: str
    config: str
    candidates: list[dict[str, Any]]
    declared: list[dict[str, Any]]
    already_declared: list[dict[str, Any]]
    joined: int
    unjoined: list[dict[str, Any]]
    per_join: list[dict[str, Any]]
    examined: int
    recall: float | None
    note: str
    markdown: str
    standing: Standing


@with_config(_OPEN)
class Fault(TypedDict, total=False):
    func: str
    where: str
    id: str


@with_config(_OPEN)
class TraceFrame(TypedDict, total=False):
    func: str
    trace_at: str
    resolved: bool
    id: str
    where: str
    repo: str
    candidates: list[str]


@with_config(_OPEN)
class AmbiguousFrame(TypedDict, total=False):
    trace_at: str
    resolved: str
    also: list[str]


@with_config(_OPEN)
class LocalizeOut(Failure, total=False):
    exception: str
    grounded: bool
    fault: Fault | None
    frames: list[TraceFrame]
    callers: list[str]
    ambiguous_frames: list[AmbiguousFrame]
    markdown: str
    standing: Standing
    multi_repo_available: ReposNote


@with_config(_OPEN)
class Uncovered(TypedDict, total=False):
    name: str
    where: str
    repo: str


@with_config(_OPEN)
class RegressionGapsOut(Failure, total=False):
    target: str
    found: bool
    target_covered: bool
    call_graph_available: bool
    impacted_count: int
    uncovered: list[Uncovered]
    covering_tests: list[str]
    truncated: bool
    target_repo: str
    uncovered_elsewhere: list[Uncovered]
    markdown: str
    standing: Standing
    multi_repo_available: ReposNote


@with_config(_OPEN)
class HypothesisOut(TypedDict, total=False):
    claim: str
    evidence: list[str]
    confidence: str


@with_config(_OPEN)
class RootCauseOut(Failure, total=False):
    problem: str
    exception: str
    fault_site: str
    hypotheses: list[HypothesisOut]
    regression_surface: list[str]
    fix_approach: str
    used_llm: bool
    markdown: str
    multi_repo_available: ReposNote


@with_config(_OPEN)
class DocMatch(TypedDict, total=False):
    id: str
    kind: str
    where: str | None
    docs: list[str]


@with_config(_OPEN)
class Drift(TypedDict, total=False):
    claim: str
    doc: str


@with_config(_OPEN)
class DocsForOut(Failure, total=False):
    repo: str
    symbol: str | None
    found: bool
    matches: list[DocMatch]
    docs: int
    note: str
    documented_symbols: int
    coverable_symbols: int
    coverage_pct: int
    drift_total: int
    drift_top: list[Drift]
    reproducible: bool
    repos: dict[str, Any]  # the per-repository fan-out: each value is a DocsForOut
    standing: Standing
    markdown: str
    multi_repo_available: ReposNote


# ---- plan and decide ------------------------------------------------------------------


@with_config(_OPEN)
class SdlcPlanOut(Failure, total=False):
    intent_id: str
    document: dict[str, Any]
    path: str
    superseded: str


@with_config(_OPEN)
class SdlcApproveOut(Failure, total=False):
    intent_id: str
    decision: str
    decided_by: str
    decided_at: str
    path: str


# ---- the run ----------------------------------------------------------------------------


@with_config(_OPEN)
class SdlcStartRunOut(Failure, total=False):
    sdlc_id: str
    workflow_id: str
    task_queue: str
    gates: dict[str, str]
    status: str


@with_config(_OPEN)
class SdlcRunStatusOut(Failure, total=False):
    sdlc_id: str
    status: str
    awaiting_gate: str | None
    gate_title: str | None
    gate_description: str | None


@with_config(_OPEN)
class SdlcDecideGateOut(Failure, total=False):
    sdlc_id: str
    gate: str
    approval_id: str
    action: str
    state: str
    status: str


@with_config(_OPEN)
class SdlcRunResultOut(Failure, total=False):
    sdlc_id: str
    status: str
    result: dict[str, Any] | None


# ---- the back half ----------------------------------------------------------------------


@with_config(_OPEN)
class BankCheck(TypedDict, total=False):
    ok: bool
    absent: bool
    bank_dir: str
    missing: list[str]
    stale: list[str]
    orphaned: list[str]
    commit: str | None
    dirty: bool
    summary: str


@with_config(_OPEN)
class UnderstandRepoOut(BankCheck, Failure, total=False):
    dir: str
    greenfield: bool
    entry_pages: list[str]
    files_written: int
    profile: dict[str, Any]
    markdown: str


@with_config(_OPEN)
class ProfileRepoOut(Failure, total=False):
    languages: list[str]
    framework: str | None
    has_db: bool
    has_migrations: bool
    test_runner: str | None
    task_type: str
    markdown: str


@with_config(_OPEN)
class DesignChangeOut(Failure, total=False):
    title: str
    design: dict[str, Any]
    unverified_references: list[str]
    used_llm: bool
    markdown: str


@with_config(_OPEN)
class GateScoreOut(TypedDict, total=False):
    accuracy: float
    cases: int
    false_refusals: int
    missed_refusals: int


@with_config(_OPEN)
class RunMetricsOut(TypedDict, total=False):
    runs: int
    completed: int
    parked: int
    failed: int
    completion_rate: float
    intervention_rate: float
    mean_cost_usd: float


@with_config(_OPEN)
class SdlcBaselineOut(Failure, total=False):
    gate: GateScoreOut
    runs: RunMetricsOut
    markdown: str


@with_config(_OPEN)
class SdlcAddressReviewOut(Failure, total=False):
    pr: str
    branch: str
    comments: int
    addressed: bool
    green: bool
    refines: int
    detail: str


@with_config(_OPEN)
class SdlcCompleteOut(Failure, total=False):
    issue: str
    pr: str
    merged: bool
    status: str
    backlog_done: bool


@with_config(_OPEN)
class RemediationOutcome(TypedDict, total=False):
    entity: str
    title: str
    ok: bool
    detail: str
    result: str | None


@with_config(_OPEN)
class SdlcRemediateOut(Failure, total=False):
    live: bool
    tasks: int
    ok: int
    outcomes: list[RemediationOutcome]
    markdown: str


@with_config(_OPEN)
class Finding(TypedDict, total=False):
    title: str
    file: str
    line: int
    severity: str
    detail: str


@with_config(_OPEN)
class AuditRepoOut(Failure, total=False):
    summary: str
    findings: list[Finding]
    unresolved: list[Finding]
    steps: int
    stopped_reason: str
    markdown: str


# ---- operate ------------------------------------------------------------------------------


@with_config(_OPEN)
class RegistryRunsOut(Failure, total=False):
    count: int
    items: list[dict[str, Any]]  # the registry's RunSummary rows
    markdown: str


@with_config(_OPEN)
class RegistryApprovalsOut(Failure, total=False):
    count: int
    items: list[dict[str, Any]]  # the registry's ApprovalRequest rows
    markdown: str


@with_config(_OPEN)
class RegistryDecideOut(Failure, total=False):
    approval_id: str
    action: str
    approval: dict[str, Any]


@with_config(_OPEN)
class Truncation(TypedDict, total=False):
    audit: int
    tool_invocations: int


@with_config(_OPEN)
class RegistryTraceOut(Failure, total=False):
    sdlc_id: str
    task_id: str
    verifier_outcome: str | None
    workflow_pattern: str | None
    replan_count: int
    replan_budget: int
    audit: list[dict[str, Any]]
    tool_invocations: list[dict[str, Any]]
    truncated: Truncation
    markdown: str


#: The type each registered tool returns, by name. Total by construction: registration
#: refuses a tool it does not list, the way ``_TIER`` does for tiers.
OUTPUTS: dict[str, type] = {
    "doctor": DoctorOut,
    "ingest_preview": IngestPreviewOut,
    "sdlc_feature": SdlcFeatureOut,
    "pkg_grounding": PkgGroundingOut,
    "read_memory_bank": ReadMemoryBankOut,
    "map_repo": MapRepoOut,
    "blast_radius": BlastRadiusOut,
    "explain_symbol": ExplainSymbolOut,
    "investigate": InvestigateOut,
    "pkg_joins": PkgJoinsOut,
    "localize": LocalizeOut,
    "regression_gaps": RegressionGapsOut,
    "root_cause": RootCauseOut,
    "sdlc_plan": SdlcPlanOut,
    "sdlc_approve": SdlcApproveOut,
    "docs_for": DocsForOut,
    "sdlc_start_run": SdlcStartRunOut,
    "sdlc_run_status": SdlcRunStatusOut,
    "sdlc_decide_gate": SdlcDecideGateOut,
    "sdlc_run_result": SdlcRunResultOut,
    "understand_repo": UnderstandRepoOut,
    "profile_repo": ProfileRepoOut,
    "design_change": DesignChangeOut,
    "sdlc_baseline": SdlcBaselineOut,
    "sdlc_address_review": SdlcAddressReviewOut,
    "sdlc_complete": SdlcCompleteOut,
    "sdlc_remediate": SdlcRemediateOut,
    "audit_repo": AuditRepoOut,
    "registry_runs": RegistryRunsOut,
    "registry_approvals": RegistryApprovalsOut,
    "registry_decide": RegistryDecideOut,
    "registry_trace": RegistryTraceOut,
}


def undeclared_keys(value: Any, declared: type, path: str = "") -> list[str]:
    """Every key in ``value`` (a tool result) that ``declared`` (its TypedDict) does not
    name, recursing into declared nested TypedDicts and lists of them. The drift guard."""
    import typing

    if not isinstance(value, dict):
        return []
    try:
        hints = typing.get_type_hints(declared)
    except Exception:  # pragma: no cover - a hint we cannot resolve is not a drift
        return []
    out: list[str] = []
    for key, item in value.items():
        here = f"{path}.{key}" if path else key
        if key not in hints:
            out.append(here)
            continue
        out.extend(_undeclared_in(item, hints[key], here))
    return out


def _undeclared_in(item: Any, hint: Any, path: str) -> list[str]:
    import typing

    origin = typing.get_origin(hint)
    if origin is None and isinstance(hint, type) and hasattr(hint, "__required_keys__"):
        return undeclared_keys(item, hint, path)
    if origin in (list, tuple) and isinstance(item, list):
        (inner,) = typing.get_args(hint)[:1] or (Any,)
        return [k for i, el in enumerate(item) for k in _undeclared_in(el, inner, f"{path}[{i}]")]
    if origin is typing.Union or str(origin) == "types.UnionType":
        for arg in typing.get_args(hint):
            if isinstance(arg, type) and hasattr(arg, "__required_keys__"):
                return undeclared_keys(item, arg, path)
    return []


__all__ = ["OUTPUTS", "Failure", "Standing", "undeclared_keys"]
