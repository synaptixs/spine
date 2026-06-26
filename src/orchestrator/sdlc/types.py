"""Block C: dataclasses crossing the SDLC workflow boundary.

Temporal serializes workflow/activity inputs and outputs, so these are kept
as plain dataclasses (JSON-friendly: str / int / bool / dict / list only).
The parent ``SDLCWorkflow`` consumes ``SDLCWorkflowInput`` and returns
``SDLCWorkflowResult``; each fanned-out child consumes ``FeatureWorkflowInput``
and returns ``FeatureWorkflowResult``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class SDLCWorkflowInput:
    """Caller-facing input to the parent SDLC workflow.

    ``auto_approve_intent`` is a test hook only: when set, the unit harness
    can skip driving gate 1 by signal. Production callers leave it false and
    drive both gates via the REST approval API.
    """

    sdlc_id: str
    source_uri: str
    actor: str = "system"
    # Bet 2c-ii: the owning tenant. Carried onto every approval + audit row this
    # run produces, so they're scoped to the submitting tenant. "default" keeps
    # single-tenant installs unchanged.
    tenant_id: str = "default"
    trace_id: str | None = None
    auto_approve_intent: bool = False
    labels: list[str] = field(default_factory=list)
    dry_run_jira: bool = True
    # Caps each feature's implement→test refinement loop (carried to children).
    # 5, not 3: preflight reports all of ruff/format/mypy at once, but a fix
    # can introduce a fresh lint nit, so a sound feature may need a few passes
    # to fully converge on the CI bar (run #28 ran out at 3 on a trailing
    # N801). The per-run budget cap (G9) still bounds total spend.
    max_refine_iterations: int = 5
    # Review-feedback rounds: on a review BLOCKER the child addresses the
    # reviewer's requested changes and resubmits (re-driving tests+preflight,
    # then re-reviewing) up to this many times before giving up with
    # changes_requested. 0 = old behavior (a blocker ends the feature).
    max_review_iterations: int = 2
    # Hard cap on features per run (0 = unlimited). Live runs set 1 so intake
    # variance can't fan out past the intent the operator actually approved.
    max_features: int = 0
    # Children run in batches of this size — a full fan-out of LLM codegen
    # bursts past provider rate tiers (run #6's lesson).
    max_parallel_features: int = 2


@dataclass
class IssuePlan:
    """One unit of fan-out: an issue key plus the spec the child implements."""

    issue_key: str
    spec: dict[str, Any]


@dataclass
class SDLCWorkflowResult:
    """Terminal result of the parent workflow.

    ``terminated`` records an early exit (a gate denial); the remaining stage
    fields stay empty in that case so callers can distinguish a clean run from
    a denied one without inspecting the audit log.
    """

    sdlc_id: str
    issue_keys: list[str] = field(default_factory=list)
    feature_results: list[dict[str, Any]] = field(default_factory=list)
    gate_decisions: list[dict[str, Any]] = field(default_factory=list)
    stage_outcomes: dict[str, Any] = field(default_factory=dict)
    terminated: bool = False
    termination_reason: str | None = None


@dataclass
class FeatureWorkflowInput:
    """Input to a fanned-out child feature workflow (one per issue)."""

    sdlc_id: str
    issue_key: str
    spec: dict[str, Any]
    # Bet 2c-ii: inherited from the parent run — scopes the child's in-loop
    # approval rows (2c-i) to the same tenant.
    tenant_id: str = "default"
    trace_id: str | None = None
    # Caps the implement→test refinement loop (test runs); 1 = single-shot.
    # Default 5 — see SDLCWorkflowInput.max_refine_iterations (run #28).
    max_refine_iterations: int = 5
    # Review-feedback rounds — see SDLCWorkflowInput.max_review_iterations.
    max_review_iterations: int = 2
    # Capability plan (Phase 5c): the run's selected skills + MCP servers, used
    # to condition the agentic codegen loop. Run-level (same for every feature).
    skills: list[str] = field(default_factory=list)
    mcp_servers: list[str] = field(default_factory=list)


# Child verdicts. ``passed`` is the only one that opens a PR and lets the parent
# proceed to merge; the others escalate the feature without a PR.
FEATURE_PASSED = "passed"
FEATURE_CHANGES_REQUESTED = "changes_requested"  # review BLOCKER
FEATURE_FAILED = "failed"  # tests still red after refinement


@dataclass
class FeatureWorkflowResult:
    """Terminal result of a child feature workflow.

    ``verdict`` is one of ``passed`` / ``changes_requested`` / ``failed``;
    ``iterations`` records how many test runs the refinement loop took (1 when
    the first run is green).
    """

    issue_key: str
    files_written: list[str] = field(default_factory=list)
    pr_url: str | None = None
    verdict: str = FEATURE_PASSED
    iterations: int = 0
    detail: str = ""
    # Calibrated escalation (G10): green but risky — surfaced to the merge gate.
    escalated: bool = False
    escalation_reasons: list[str] = field(default_factory=list)
    # Governance episodes from the agentic implement (Phase 2b) — fed to the
    # post-merge memory-consolidation hook.
    policy_blocks: list[dict[str, str]] = field(default_factory=list)
