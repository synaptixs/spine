"""Block C: the SDLC parent + feature child workflows.

``SDLCWorkflow`` marches a source page A→Z to merged, CI-green feature PRs
with two human approval gates (intent + merge — the bookends); each created
issue fans out to a ``FeatureImplementationWorkflow`` child that owns the
per-issue worktree + codegen loop. Deployment is out of scope — the pipeline
ends at the merge and hands off to existing CD.

Workflow code must be deterministic, so every side effect funnels through an
activity (in ``orchestrator.sdlc.activities``). The gate mechanism mirrors
``OrchestratorWorkflow`` exactly: an append-only ``_decisions`` queue fed by
``approve`` / ``deny`` / ``modify_input`` signals, consumed by index after each
``raise_approval_request`` so a REST decision that races the wait point still
survives.
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import timedelta
from typing import Any

from temporalio import exceptions, workflow

with workflow.unsafe.imports_passed_through():
    from temporalio.common import RetryPolicy

    from orchestrator.sdlc.types import (
        FEATURE_CHANGES_REQUESTED,
        FEATURE_FAILED,
        FEATURE_PASSED,
        FeatureWorkflowInput,
        FeatureWorkflowResult,
        SDLCWorkflowInput,
        SDLCWorkflowResult,
    )

_AUDIT_RETRY = RetryPolicy(maximum_attempts=5, initial_interval=timedelta(milliseconds=200))
_STAGE_RETRY = RetryPolicy(maximum_attempts=3, initial_interval=timedelta(seconds=1))

_AUDIT_TIMEOUT = timedelta(seconds=10)
_APPROVAL_RAISE_TIMEOUT = timedelta(seconds=30)
_STAGE_TIMEOUT = timedelta(minutes=2)
# LLM codegen stages (plan/implement/test_author/refine/review) caps the
# TOTAL across all Temporal retries, and one attempt can itself make two LLM
# calls (the in-adapter anchor-repair). A 16k-token generation runs ~1-2 min,
# so 2 min schedule-to-close starved test_author (run #19). Be generous.
_CODEGEN_TIMEOUT = timedelta(minutes=15)
_INTAKE_TIMEOUT = timedelta(minutes=5)
_WORKSPACE_TIMEOUT = timedelta(minutes=2)
# Comprehension (M1) extracts the PKG + memory bank + current-state; generous for
# a large repo (SHA-cached, so re-runs on the same commit are fast).
_COMPREHEND_TIMEOUT = timedelta(minutes=10)
# Human approval can take a while but abandoned runs should self-clean.
_DEFAULT_APPROVAL_WAIT = timedelta(hours=24)


@workflow.defn(name="FeatureImplementationWorkflow")
class FeatureImplementationWorkflow:
    """One issue: workspace → plan → implement → (refine⇄test) → review⇄fix → PR.

    The pipeline **honors verdicts** rather than always succeeding:

      - the implement→test refinement loop retries up to
        ``max_refine_iterations`` times; if the tests are still red it
        escalates with verdict ``failed`` and opens **no** PR;
      - on a review BLOCKER the child **responds to the feedback like an
        engineer**: it feeds the reviewer's requested changes back through
        codegen, re-drives the change to green (tests + preflight), and
        re-reviews, up to ``max_review_iterations`` rounds. Only a blocker
        still unresolved after that budget yields ``changes_requested`` (no
        PR); a review fix that breaks tests yields ``failed``;
      - only an all-green, review-clean feature opens a PR and returns
        ``passed``.

    The worktree is always torn down in ``finally`` regardless of verdict.

    **In-loop approval (Bet 2c-i):** when the agentic implement loop hits a
    ``require_approval`` policy gate, ``sdlc_implement`` returns mid-run with
    ``status=needs_approval``; this child raises a real ``ApprovalRequest``
    (decided via the same REST ``/v1/approvals/*`` API) and resumes the loop with
    the human's decision. For REST signals to route here, the child is started
    with id ``task-feat-{sdlc_id}-{issue_key}`` (the API signals ``task-{task_id}``
    where ``task_id = feat-{sdlc_id}-{issue_key}``).
    """

    def __init__(self) -> None:
        # Same append-only decision queue + cancel flag as the parent, here for
        # the in-loop approval gates raised during agentic implement.
        self._decisions: list[dict[str, Any]] = []
        self._decisions_consumed: int = 0
        self._cancelled: bool = False

    @workflow.signal(name="cancel")
    def on_cancel(self) -> None:
        self._cancelled = True

    @workflow.signal(name="approve")
    def on_approve(self) -> None:
        self._decisions.append({"action": "approve", "patch": None})

    @workflow.signal(name="deny")
    def on_deny(self) -> None:
        self._decisions.append({"action": "deny", "patch": None})

    @workflow.signal(name="modify_input")
    def on_modify_input(self, patch: dict[str, Any]) -> None:
        self._decisions.append({"action": "modify_input", "patch": dict(patch)})

    @workflow.run
    async def run(self, payload: FeatureWorkflowInput) -> FeatureWorkflowResult:
        # M2: fold this issue's approved design into the spec so every codegen
        # stage (plan / implement / refine) builds to the grounded approach.
        payload.spec = _spec_with_design(payload.spec, payload.design)
        ws = await workflow.execute_activity(
            "sdlc_create_workspace",
            {
                "sdlc_id": payload.sdlc_id,
                "issue_key": payload.issue_key,
                "comprehension": payload.comprehension,
            },
            schedule_to_close_timeout=_WORKSPACE_TIMEOUT,
            retry_policy=_STAGE_RETRY,
        )
        path = ws["path"]
        await self._audit(payload, "feature_workspace_created", {"path": path})

        # Once the worktree exists it MUST be torn down, even if a later stage
        # fails — otherwise a failed feature orphans a git worktree on disk.
        # The finally compensates; cleanup is best-effort so a teardown error
        # never masks the original failure.
        files_written: list[str] = []
        iterations = 0
        try:
            await workflow.execute_activity(
                "sdlc_code_plan",
                {"sdlc_id": payload.sdlc_id, "spec": payload.spec, "path": path},
                schedule_to_close_timeout=_CODEGEN_TIMEOUT,
                retry_policy=_STAGE_RETRY,
            )

            impl = await self._implement(payload, path)
            test = await workflow.execute_activity(
                "sdlc_test_author",
                {"sdlc_id": payload.sdlc_id, "path": path, "issue_key": payload.issue_key},
                schedule_to_close_timeout=_CODEGEN_TIMEOUT,
                retry_policy=_STAGE_RETRY,
            )
            files_written = list(impl.get("files", [])) + list(test.get("files", []))
            impl_policy_blocks = [dict(b) for b in impl.get("policy_blocks", [])]

            # ---- get the change green (implement→test→preflight refine loop) --
            passed, used, last_output = await self._drive_to_green(payload, path)
            iterations += used
            if not passed:
                await self._audit(
                    payload,
                    "feature_tests_failed",
                    {"iterations": iterations, "output_tail": last_output[-1500:]},
                )
                return FeatureWorkflowResult(
                    issue_key=payload.issue_key,
                    files_written=files_written,
                    verdict=FEATURE_FAILED,
                    iterations=iterations,
                    detail="tests still failing after refinement",
                )

            # ---- review loop: respond to reviewer feedback like an engineer ---
            # A BLOCKER no longer ends the feature; the agent addresses the
            # reviewer's requested changes and resubmits, up to
            # ``max_review_iterations`` rounds. Each round's fix must still pass
            # tests + preflight (a review fix can break them), so we re-drive to
            # green before re-reviewing. Only an unresolved blocker after the
            # budget is exhausted yields ``changes_requested`` (no PR).
            review = await self._review(payload, path)
            review_round = 0
            max_review = max(0, payload.max_review_iterations)
            while review.get("has_blocker"):
                blockers = [str(b) for b in review.get("blockers", [])]
                if review_round >= max_review:
                    await self._audit(
                        payload,
                        "feature_review_blocked",
                        {"blockers": blockers, "rounds": review_round},
                    )
                    return FeatureWorkflowResult(
                        issue_key=payload.issue_key,
                        files_written=files_written,
                        verdict=FEATURE_CHANGES_REQUESTED,
                        iterations=iterations,
                        detail="review blocker unresolved after feedback rounds",
                    )
                review_round += 1
                await self._audit(
                    payload,
                    "feature_review_refined",
                    {"round": review_round, "blockers": blockers},
                )
                # Feed the reviewer's requested changes back through codegen.
                await workflow.execute_activity(
                    "sdlc_refine",
                    {
                        "sdlc_id": payload.sdlc_id,
                        "path": path,
                        "issue_key": payload.issue_key,
                        "spec": payload.spec,
                        "failures": (
                            "The code reviewer BLOCKED the change and requested these "
                            "changes — address every one:\n- " + "\n- ".join(blockers)
                        ),
                    },
                    schedule_to_close_timeout=_CODEGEN_TIMEOUT,
                    retry_policy=_STAGE_RETRY,
                )
                # A review fix must not regress tests or the CI bar.
                passed, used, last_output = await self._drive_to_green(payload, path)
                iterations += used
                if not passed:
                    await self._audit(
                        payload,
                        "feature_tests_failed",
                        {
                            "iterations": iterations,
                            "after_review_round": review_round,
                            "output_tail": last_output[-1500:],
                        },
                    )
                    return FeatureWorkflowResult(
                        issue_key=payload.issue_key,
                        files_written=files_written,
                        verdict=FEATURE_FAILED,
                        iterations=iterations,
                        detail="review fix broke tests/preflight",
                    )
                review = await self._review(payload, path)

            # ---- calibrated escalation (G10): annotate, never block --------
            escalation = await workflow.execute_activity(
                "sdlc_escalation_check",
                {"path": path, "issue_key": payload.issue_key, "review": review, "iterations": iterations},
                schedule_to_close_timeout=_STAGE_TIMEOUT,
                retry_policy=_STAGE_RETRY,
            )
            if escalation.get("escalate"):
                await self._audit(
                    payload,
                    "feature_escalated",
                    {"reasons": list(escalation.get("reasons", []))},
                )

            pr = await workflow.execute_activity(
                "sdlc_open_pr",
                {
                    "issue_key": payload.issue_key,
                    "path": path,
                    "branch": f"feat/{payload.sdlc_id}/{payload.issue_key}",
                },
                schedule_to_close_timeout=_STAGE_TIMEOUT,
                retry_policy=_STAGE_RETRY,
            )
            await self._audit(payload, "feature_pr_opened", {"pr_url": pr["pr_url"]})
        except exceptions.ActivityError as exc:
            # A stage exhausting its retries (e.g. codegen that can't produce
            # writable files) is a FAILED feature, not a crashed pipeline —
            # the parent must fan in, audit, and stop at features_failed.
            cause = getattr(exc, "cause", None)
            detail = str(cause or exc)[:300]
            await self._audit(payload, "feature_stage_failed", {"detail": detail})
            return FeatureWorkflowResult(
                issue_key=payload.issue_key,
                files_written=files_written,
                verdict=FEATURE_FAILED,
                iterations=iterations,
                detail=f"stage error: {detail}",
            )
        finally:
            # suppress(Exception) keeps a cleanup failure from masking the
            # stage error; it deliberately lets CancelledError (BaseException)
            # propagate so Temporal cancellation still works.
            with contextlib.suppress(Exception):
                await workflow.execute_activity(
                    "sdlc_cleanup_workspace",
                    {"path": path},
                    schedule_to_close_timeout=_WORKSPACE_TIMEOUT,
                    retry_policy=_STAGE_RETRY,
                )

        return FeatureWorkflowResult(
            issue_key=payload.issue_key,
            files_written=files_written,
            pr_url=pr["pr_url"],
            verdict=FEATURE_PASSED,
            iterations=iterations,
            escalated=bool(escalation.get("escalate")),
            escalation_reasons=[str(r) for r in escalation.get("reasons", [])],
            policy_blocks=impl_policy_blocks,
        )

    async def _implement(self, payload: FeatureWorkflowInput, path: str) -> dict[str, Any]:
        """Run implement, pausing for a human whenever the agentic loop hits a
        ``require_approval`` policy gate (Bet 2c-i), and resuming with the
        decision until the change completes.

        With no policy (or no agentic loop) the first call returns
        ``status=complete`` and this is just the old single activity call.
        """
        impl: dict[str, Any] = await workflow.execute_activity(
            "sdlc_implement",
            {
                "sdlc_id": payload.sdlc_id,
                "path": path,
                "issue_key": payload.issue_key,
                "spec": payload.spec,
                "skills": payload.skills,
                "mcp_servers": payload.mcp_servers,
            },
            schedule_to_close_timeout=_CODEGEN_TIMEOUT,
            retry_policy=_STAGE_RETRY,
        )
        pauses = 0
        while impl.get("status") == "needs_approval":
            decision = await self._approve_in_loop(payload, dict(impl.get("pending") or {}), pauses)
            pauses += 1
            impl = await workflow.execute_activity(
                "sdlc_implement_resume",
                {
                    "path": path,
                    "checkpoint": impl["checkpoint"],
                    "decision": decision,
                    "prior_written": impl.get("prior_written", []),
                    "prior_summary": impl.get("prior_summary", ""),
                    "mcp_servers": payload.mcp_servers,
                },
                schedule_to_close_timeout=_CODEGEN_TIMEOUT,
                retry_policy=_STAGE_RETRY,
            )
        return impl

    async def _approve_in_loop(
        self, payload: FeatureWorkflowInput, pending: dict[str, Any], index: int
    ) -> dict[str, Any]:
        """Raise a real approval for one gated in-loop tool call and wait for the
        decision. Returns a loop ``HumanDecision`` dict (approve / modify_input /
        reject). On timeout: **reject-and-continue** — a single gated action
        expiring must not strand the whole feature."""
        feat_id = f"feat-{payload.sdlc_id}-{payload.issue_key}"
        approval_id = f"{feat_id}-impl-{index}"
        tool = str(pending.get("tool", "?"))
        reason = str(pending.get("reason", ""))
        await workflow.execute_activity(
            "sdlc_raise_approval_request",
            {
                "approval_id": approval_id,
                "task_id": feat_id,  # REST signals task-{task_id} == this child's id
                "tenant_id": payload.tenant_id,
                "before_node_id": f"implement:{tool}",
                "title": f"approve in-loop tool call: {tool}",
                "description": f"The agent wants to call `{tool}` mid-implement (policy: {reason}).",
                "action_summary": f"call {tool}",
                "risk_classification": "high",
                "trace_id": payload.trace_id,
            },
            schedule_to_close_timeout=_APPROVAL_RAISE_TIMEOUT,
            retry_policy=_AUDIT_RETRY,
        )
        await self._audit(payload, "feature_impl_approval_raised", {"tool": tool, "approval_id": approval_id})

        waiting = self._decisions_consumed

        def _ready(n: int = waiting) -> bool:
            return len(self._decisions) > n or self._cancelled

        try:
            await workflow.wait_condition(_ready, timeout=_DEFAULT_APPROVAL_WAIT)
        except TimeoutError:
            await self._audit(payload, "feature_impl_approval_timeout", {"tool": tool})
            return {"action": "reject", "rationale": "approval timed out"}
        if self._cancelled:
            return {"action": "reject", "rationale": "run cancelled"}
        decision = self._decisions[self._decisions_consumed]
        self._decisions_consumed += 1
        await self._audit(
            payload, "feature_impl_approval_decided", {"tool": tool, "action": decision.get("action")}
        )
        return _to_loop_decision(decision)

    async def _drive_to_green(self, payload: FeatureWorkflowInput, path: str) -> tuple[bool, int, str]:
        """Run the test→preflight→refine loop on the current code until green.

        Returns ``(passed, iterations_used, last_output)``. Each call gets a
        fresh budget of ``max_refine_iterations`` test runs — the initial
        implementation and each later review fix each deserve their own
        convergence budget; the per-run LLM budget cap (G9) bounds the total.
        A test failure OR a preflight (ruff/format/mypy) failure feeds the same
        refine call, so the model fixes lint/type locally instead of burning a
        live CI round-trip.
        """
        max_iters = max(1, payload.max_refine_iterations)
        used = 0
        last_output = ""
        while used < max_iters:
            run = await workflow.execute_activity(
                "sdlc_run_tests",
                {"path": path},
                schedule_to_close_timeout=_STAGE_TIMEOUT,
                retry_policy=_STAGE_RETRY,
            )
            used += 1
            last_output = str(run.get("output", ""))
            if run.get("passed"):
                pre = await workflow.execute_activity(
                    "sdlc_preflight",
                    {"path": path},
                    schedule_to_close_timeout=_STAGE_TIMEOUT,
                    retry_policy=_STAGE_RETRY,
                )
                if pre.get("passed"):
                    return True, used, last_output
                last_output = str(pre.get("output", ""))
                await self._audit(
                    payload,
                    "feature_preflight_failed",
                    {"iteration": used, "output_tail": last_output[-1500:]},
                )
            if used >= max_iters:
                break
            await self._audit(payload, "feature_refined", {"iteration": used})
            await workflow.execute_activity(
                "sdlc_refine",
                {
                    "sdlc_id": payload.sdlc_id,
                    "path": path,
                    "issue_key": payload.issue_key,
                    "spec": payload.spec,
                    "failures": last_output,
                },
                schedule_to_close_timeout=_CODEGEN_TIMEOUT,
                retry_policy=_STAGE_RETRY,
            )
        return False, used, last_output

    async def _review(self, payload: FeatureWorkflowInput, path: str) -> dict[str, Any]:
        result: dict[str, Any] = await workflow.execute_activity(
            "sdlc_review",
            {
                "sdlc_id": payload.sdlc_id,
                "path": path,
                "issue_key": payload.issue_key,
                "spec": payload.spec,
            },
            schedule_to_close_timeout=_CODEGEN_TIMEOUT,
            retry_policy=_STAGE_RETRY,
        )
        return result

    async def _audit(self, payload: FeatureWorkflowInput, action: str, after: dict[str, Any]) -> None:
        await workflow.execute_activity(
            "sdlc_record_audit",
            {
                "action": action,
                "resource_type": "sdlc_feature",
                "resource_id": f"{payload.sdlc_id}/{payload.issue_key}",
                "after": after,
                "trace_id": payload.trace_id,
                "tenant_id": payload.tenant_id,
            },
            schedule_to_close_timeout=_AUDIT_TIMEOUT,
            retry_policy=_AUDIT_RETRY,
        )


@workflow.defn(name="SDLCWorkflow")
class SDLCWorkflow:
    """Parent: intake → gate1 → issues → fan-out → integration CI → gate2 → merge.

    **Routing requirement:** the gates persist real ``ApprovalRequest`` rows
    (task_id = ``sdlc_id``) that the REST ``/v1/approvals/*`` API decides. That
    API signals the workflow at ``task-{task_id}``, so this workflow MUST be
    started with id ``task-{sdlc_id}`` for REST decisions to route back. (The
    signal-driven unit tests don't depend on the id.)
    """

    def __init__(self) -> None:
        self._decisions: list[dict[str, Any]] = []
        # Cancel is a one-shot flag — any waiting gate wakes and terminates.
        self._cancelled: bool = False

    @workflow.signal(name="cancel")
    def on_cancel(self) -> None:
        """Cooperative cancel — a waiting gate bails and the run terminates."""
        self._cancelled = True

    @workflow.signal(name="approve")
    def on_approve(self) -> None:
        self._decisions.append({"action": "approve", "patch": None})

    @workflow.signal(name="deny")
    def on_deny(self) -> None:
        self._decisions.append({"action": "deny", "patch": None})

    @workflow.signal(name="modify_input")
    def on_modify_input(self, patch: dict[str, Any]) -> None:
        self._decisions.append({"action": "modify_input", "patch": dict(patch)})

    @workflow.query(name="is_cancelled")
    def is_cancelled(self) -> bool:
        return self._cancelled

    @workflow.query(name="status")
    def status(self) -> dict[str, Any]:
        return {"cancelled": self._cancelled, "decisions": list(self._decisions)}

    @workflow.run
    async def run(self, payload: SDLCWorkflowInput) -> SDLCWorkflowResult:
        result = SDLCWorkflowResult(sdlc_id=payload.sdlc_id)
        decisions_consumed = 0

        # ---- 1. intake_analyze --------------------------------------------
        intake = await workflow.execute_activity(
            "sdlc_intake_analyze",
            {
                "source_uri": payload.source_uri,
                "dry_run_jira": payload.dry_run_jira,
                "trace_id": payload.trace_id,
            },
            schedule_to_close_timeout=_INTAKE_TIMEOUT,
            retry_policy=_STAGE_RETRY,
        )
        result.stage_outcomes["intake"] = {
            "intent_count": intake["intent_count"],
            "blocked": intake["blocked"],
        }
        await self._audit(payload, "sdlc_intake_analyzed", result.stage_outcomes["intake"])

        # ---- 1b. profile the target + assemble a capability plan (Phase 3) --
        # Deterministic profile → plan from the governed catalog, surfaced at
        # the gate so the human approves the toolkit alongside the intents.
        intent_text = " ".join(str(s.get("title") or "") for s in intake["specs"])
        plan_out = await workflow.execute_activity(
            "sdlc_profile_and_plan",
            {"intent_text": intent_text, "trace_id": payload.trace_id},
            schedule_to_close_timeout=_INTAKE_TIMEOUT,
            retry_policy=_STAGE_RETRY,
        )
        capability_plan: dict[str, Any] = plan_out["plan"]
        result.stage_outcomes["capability_plan"] = capability_plan
        await self._audit(
            payload, "sdlc_capability_plan", {"profile": plan_out["profile"], "plan": capability_plan}
        )

        # ---- 1c. comprehend the repo → architectural artifacts (M1) --------
        # Deterministic, best-effort: extract the knowledge graph + memory bank +
        # current-state on the base checkout, persist them as run artifacts, and
        # summarise "what Spine understood" at the intent gate below.
        comprehension = await workflow.execute_activity(
            "sdlc_comprehend_repo",
            {"sdlc_id": payload.sdlc_id, "trace_id": payload.trace_id},
            schedule_to_close_timeout=_COMPREHEND_TIMEOUT,
            retry_policy=_STAGE_RETRY,
        )
        result.stage_outcomes["comprehension"] = comprehension
        await self._audit(payload, "sdlc_repo_comprehended", comprehension)

        # ---- 2. GATE 1: approve intents (and answer any open questions) ----
        # Surface the extractor's open questions to the approver so ambiguity
        # is resolved here — by a human — rather than guessed at by codegen.
        open_questions = [str(q) for q in (intake.get("open_questions") or [])]
        gate1, decisions_consumed = await self._gate(
            payload,
            decisions_consumed,
            gate_index=0,
            before_node="intents",
            title="approve intents",
            risk="medium",
            description=_intents_gate_description(
                intake["intent_count"], open_questions, capability_plan, comprehension
            ),
        )
        result.gate_decisions.append(gate1)
        if gate1["action"] in ("deny", "cancel"):
            reason = "cancelled" if gate1["action"] == "cancel" else "intents_denied"
            await self._audit(payload, f"sdlc_{reason}", {"gate": "intents"})
            result.terminated = True
            result.termination_reason = reason
            return result

        # A modify_input patch at gate 1 may drop/edit specs before fan-out...
        specs: list[dict[str, Any]] = list(intake["specs"])
        patch1 = gate1.get("patch") or {}
        if isinstance(patch1.get("specs"), list):
            specs = list(patch1["specs"])
        # ...and/or answer the open questions: clarifications fold into every
        # spec's technical_notes so codegen builds against the human's answers.
        clarifications = _coerce_clarifications(patch1.get("clarifications"))
        if clarifications:
            specs = [_apply_clarifications(s, clarifications) for s in specs]
            await self._audit(payload, "sdlc_clarifications_applied", {"clarifications": clarifications})

        # ...and/or edit the capability plan. The approved plan's workflow params
        # override the run's defaults (e.g. a migration fans out wider / reviews
        # harder); selected MCP servers are recorded for governance.
        patch_plan = patch1.get("capability_plan")
        if isinstance(patch_plan, dict):
            capability_plan = patch_plan
            await self._audit(payload, "sdlc_capability_plan_edited", {"plan": capability_plan})
        wf_params = capability_plan.get("workflow_params") or {}
        eff_max_parallel = int(wf_params.get("max_parallel_features", payload.max_parallel_features))
        eff_max_review = int(wf_params.get("max_review_iterations", payload.max_review_iterations))
        if capability_plan.get("mcp_servers"):
            await self._audit(payload, "sdlc_mcp_selected", {"servers": capability_plan["mcp_servers"]})

        # ---- 3. create_jira_issues (dry-run default) ----------------------
        issues = await workflow.execute_activity(
            "sdlc_create_jira_issues",
            {"specs": specs, "dry_run": payload.dry_run_jira},
            schedule_to_close_timeout=_STAGE_TIMEOUT,
            retry_policy=_STAGE_RETRY,
        )
        issue_plans: list[dict[str, Any]] = list(issues["issue_plans"])
        if payload.max_features > 0 and len(issue_plans) > payload.max_features:
            dropped = [p["issue_key"] for p in issue_plans[payload.max_features :]]
            issue_plans = issue_plans[: payload.max_features]
            await self._audit(payload, "sdlc_features_capped", {"kept": len(issue_plans), "dropped": dropped})
        result.issue_keys = [p["issue_key"] for p in issue_plans]
        await self._audit(payload, "sdlc_issues_created", {"issue_keys": result.issue_keys})

        # ---- 3b. DESIGN wave (M2) — one grounded design per issue -----------
        # Fan out design activities (each self-gates on SDLC_DESIGN, best-effort)
        # so every design is anchored to the comprehension knowledge graph BEFORE
        # any code is written. Optionally gate the whole set (Gate 1.5).
        designs_by_issue: dict[str, dict[str, Any]] = {}
        if issue_plans:
            design_results = await asyncio.gather(
                *[
                    workflow.execute_activity(
                        "sdlc_design_feature",
                        {
                            "sdlc_id": payload.sdlc_id,
                            "issue_key": plan["issue_key"],
                            "spec": plan["spec"],
                            "comprehension": comprehension if isinstance(comprehension, dict) else {},
                            "trace_id": payload.trace_id,
                        },
                        schedule_to_close_timeout=_CODEGEN_TIMEOUT,
                        retry_policy=_STAGE_RETRY,
                    )
                    for plan in issue_plans
                ]
            )
            designs_by_issue = {d["issue_key"]: d for d in design_results if d.get("issue_key")}
            produced = [d for d in design_results if not d.get("skipped")]
            if produced:
                # One audit per design carrying its artifact manifest, so the
                # run-artifacts API (keyed on ``feature_designed`` + ``artifacts``)
                # surfaces design.json/design.md for download in the console —
                # the same way ``sdlc_repo_comprehended`` surfaces M1's artifacts.
                for d in produced:
                    if isinstance(d.get("artifacts"), dict) and d["artifacts"]:
                        await self._audit(
                            payload,
                            "feature_designed",
                            {
                                "issue_key": d.get("issue_key"),
                                "summary": d.get("summary"),
                                "files_to_touch": d.get("files_to_touch") or [],
                                "artifacts": d["artifacts"],
                            },
                        )
                await self._audit(payload, "sdlc_designs_ready", {"count": len(produced)})
                if payload.design_gate:
                    gate15, decisions_consumed = await self._gate(
                        payload,
                        decisions_consumed,
                        gate_index=2,
                        before_node="designs",
                        title="approve designs",
                        risk="medium",
                        description=_designs_gate_description(produced),
                    )
                    result.gate_decisions.append(gate15)
                    if gate15["action"] in ("deny", "cancel"):
                        reason = "cancelled" if gate15["action"] == "cancel" else "designs_denied"
                        await self._audit(payload, f"sdlc_{reason}", {"gate": "designs"})
                        result.terminated = True
                        result.termination_reason = reason
                        return result

        # ---- 4. fan-out: one child workflow per issue, run concurrently ----
        # Each child gets a deterministic id (feat-{sdlc_id}-{issue_key}) for
        # idempotency on replay. asyncio.gather starts them concurrently and
        # fans in once every child resolves, before integration test (stage 5).
        # Batched fan-out: full concurrency across many LLM-codegen children
        # bursts past provider rate tiers, so run at most
        # ``max_parallel_features`` at a time (1 = strictly sequential).
        batch_size = max(1, eff_max_parallel)
        feature_results: list[FeatureWorkflowResult] = []
        for start in range(0, len(issue_plans), batch_size):
            batch = [
                workflow.execute_child_workflow(
                    FeatureImplementationWorkflow.run,
                    FeatureWorkflowInput(
                        sdlc_id=payload.sdlc_id,
                        issue_key=plan["issue_key"],
                        spec=plan["spec"],
                        tenant_id=payload.tenant_id,
                        trace_id=payload.trace_id,
                        max_refine_iterations=payload.max_refine_iterations,
                        max_review_iterations=eff_max_review,
                        skills=list(capability_plan.get("skills") or []),
                        mcp_servers=list(capability_plan.get("mcp_servers") or []),
                        comprehension=comprehension if isinstance(comprehension, dict) else {},
                        design=designs_by_issue.get(plan["issue_key"], {}),
                    ),
                    # ``task-`` prefix so the child can receive REST approval
                    # signals for in-loop gates (the API signals task-{task_id}).
                    id=f"task-feat-{payload.sdlc_id}-{plan['issue_key']}",
                )
                for plan in issue_plans[start : start + batch_size]
            ]
            feature_results.extend(await asyncio.gather(*batch))
        result.feature_results = [
            {
                "issue_key": fr.issue_key,
                "pr_url": fr.pr_url,
                "verdict": fr.verdict,
                "iterations": fr.iterations,
                "escalated": fr.escalated,
                "escalation_reasons": list(fr.escalation_reasons),
            }
            for fr in feature_results
        ]

        # A feature that didn't reach `passed` (tests still red, or a review
        # blocker) means the change set isn't shippable — stop before merge.
        failed = [fr for fr in feature_results if fr.verdict != FEATURE_PASSED]
        if failed:
            await self._audit(
                payload,
                "sdlc_features_failed",
                {"issues": [{"issue_key": fr.issue_key, "verdict": fr.verdict} for fr in failed]},
            )
            result.terminated = True
            result.termination_reason = "features_failed"
            return result

        # ---- 5. integration_test ------------------------------------------
        # The feature PRs carry the CI signal: a real adapter awaits their
        # check runs; the stub ignores them.
        integ = await workflow.execute_activity(
            "sdlc_integration_test",
            {
                "issue_keys": result.issue_keys,
                "pr_urls": [fr.pr_url for fr in feature_results if fr.pr_url],
            },
            schedule_to_close_timeout=_STAGE_TIMEOUT,
            retry_policy=_STAGE_RETRY,
        )
        result.stage_outcomes["integration_test"] = integ
        await self._audit(payload, "sdlc_integration_tested", integ)
        if integ.get("verdict") != "pass":
            await self._audit(payload, "sdlc_integration_failed", integ)
            result.terminated = True
            result.termination_reason = "integration_failed"
            return result

        # Surface calibrated escalations to the approver: green-but-risky
        # features reviewed with attention proportional to risk, not rubber-stamped.
        escalated = [fr for fr in feature_results if fr.escalated]
        if escalated:
            await self._audit(
                payload,
                "sdlc_escalations_for_gate",
                {
                    "issues": [
                        {"issue_key": fr.issue_key, "reasons": list(fr.escalation_reasons)}
                        for fr in escalated
                    ]
                },
            )

        # ---- 6. GATE 2: approve merge ---------------------------------------
        # Same approval id (``sdlc-{id}-1``) the REST API has always routed;
        # only the label changed when the deploy stages were removed.
        gate2, decisions_consumed = await self._gate(
            payload,
            decisions_consumed,
            gate_index=1,
            before_node="merge",
            title="approve merge",
            risk="high",
        )
        result.gate_decisions.append(gate2)
        if gate2["action"] in ("deny", "cancel"):
            reason = "cancelled" if gate2["action"] == "cancel" else "merge_denied"
            await self._audit(payload, f"sdlc_{reason}", {"gate": "merge"})
            result.terminated = True
            result.termination_reason = reason
            return result

        # ---- 7. merge_prs (merge-on-green) ----------------------------------
        # CI is green (stage 5) and the gate is approved — merge the feature
        # PRs. All-or-nothing: one failed merge stops the pipeline. The merge
        # is the end of the line; deployment hands off to existing CD.
        merge = await workflow.execute_activity(
            "sdlc_merge_prs",
            {"pr_urls": [fr.pr_url for fr in feature_results if fr.pr_url]},
            schedule_to_close_timeout=_STAGE_TIMEOUT,
            retry_policy=_STAGE_RETRY,
        )
        result.stage_outcomes["merge"] = merge
        await self._audit(payload, "sdlc_prs_merged", merge)
        if merge.get("verdict") != "pass":
            await self._audit(payload, "sdlc_merge_failed", merge)
            result.terminated = True
            result.termination_reason = "merge_failed"
            return result

        # ---- 8. consolidate semantic memory (post-merge, Phase 2b) ----------
        # Distill this run's governance episodes into cross-run memory. The
        # activity no-ops unless ORCHESTRATOR_SEMANTIC_MEMORY is on, so this is a
        # cheap call otherwise; it must never fail the (already-merged) run.
        merged_blocks = [b for fr in feature_results for b in fr.policy_blocks]
        consolidation = await workflow.execute_activity(
            "sdlc_consolidate_memory",
            {
                "sdlc_id": payload.sdlc_id,
                "tenant_id": payload.tenant_id,
                "trace_id": payload.trace_id,
                "policy_blocks": merged_blocks,
            },
            schedule_to_close_timeout=_STAGE_TIMEOUT,
            retry_policy=_STAGE_RETRY,
        )
        result.stage_outcomes["consolidate_memory"] = consolidation
        await self._audit(payload, "sdlc_memory_consolidated", consolidation)

        # ---- 9. register shipped units with infodrift (Spine Seam 2) --------
        # No-op unless SPINE_INFODRIFT_URL + a deploy topology are configured.
        registration = await workflow.execute_activity(
            "sdlc_register_units",
            {
                "sdlc_id": payload.sdlc_id,
                "tenant_id": payload.tenant_id,
                "trace_id": payload.trace_id,
            },
            schedule_to_close_timeout=_STAGE_TIMEOUT,
            retry_policy=_STAGE_RETRY,
        )
        result.stage_outcomes["register_units"] = registration
        await self._audit(payload, "sdlc_units_registered", registration)

        return result

    async def _gate(
        self,
        payload: SDLCWorkflowInput,
        decisions_consumed: int,
        *,
        gate_index: int,
        before_node: str,
        title: str,
        risk: str,
        description: str | None = None,
    ) -> tuple[dict[str, Any], int]:
        """Raise a real approval request and wait for the next queued decision.

        Persists an ``ApprovalRequest`` row (decidable via the REST
        ``/v1/approvals/*`` API, which signals this workflow back) then waits on
        an ``approve`` / ``deny`` / ``modify_input`` signal — consumed by index.
        Returns ``(decision, new_decisions_consumed)``. ``auto_approve_intent``
        short-circuits gate 0 for the offline test harness.
        """
        if gate_index == 0 and payload.auto_approve_intent:
            return {"action": "approve", "patch": None}, decisions_consumed

        # ``approval_id`` mirrors the orchestrator's deterministic scheme so a
        # retry of this activity is idempotent on the row.
        approval_id = f"sdlc-{payload.sdlc_id}-{gate_index}"
        await workflow.execute_activity(
            "sdlc_raise_approval_request",
            {
                "approval_id": approval_id,
                "task_id": payload.sdlc_id,
                "tenant_id": payload.tenant_id,
                "before_node_id": before_node,
                "title": title,
                "description": description or f"Gate {gate_index}: {title}",
                "action_summary": title,
                "risk_classification": risk,
                "trace_id": payload.trace_id,
            },
            schedule_to_close_timeout=_APPROVAL_RAISE_TIMEOUT,
            retry_policy=_AUDIT_RETRY,
        )

        waiting = decisions_consumed

        def _ready(n: int = waiting) -> bool:
            # Wake on the next queued decision OR a cancel signal.
            return len(self._decisions) > n or self._cancelled

        await workflow.wait_condition(_ready, timeout=_DEFAULT_APPROVAL_WAIT)
        if self._cancelled:
            # Cancel takes precedence; the decision queue is left untouched.
            return {"action": "cancel", "patch": None}, decisions_consumed
        decision = self._decisions[decisions_consumed]
        return decision, decisions_consumed + 1

    async def _audit(self, payload: SDLCWorkflowInput, action: str, after: dict[str, Any]) -> None:
        await workflow.execute_activity(
            "sdlc_record_audit",
            {
                "action": action,
                "resource_type": "sdlc",
                "resource_id": payload.sdlc_id,
                "after": after,
                "trace_id": payload.trace_id,
                "tenant_id": payload.tenant_id,
            },
            schedule_to_close_timeout=_AUDIT_TIMEOUT,
            retry_policy=_AUDIT_RETRY,
        )


def _to_loop_decision(decision: dict[str, Any]) -> dict[str, Any]:
    """Map a gate decision (``approve`` / ``deny`` / ``modify_input`` + patch) to
    the loop's ``HumanDecision`` shape. A ``modify_input`` patch becomes the
    gated tool call's replacement arguments; ``deny`` becomes a reject."""
    action = decision.get("action")
    if action == "approve":
        return {"action": "approve"}
    if action == "modify_input":
        return {"action": "modify_input", "modified_input": dict(decision.get("patch") or {})}
    return {"action": "reject", "rationale": "human denied the tool call"}


def _designs_gate_description(designs: list[dict[str, Any]]) -> str:
    """Gate 1.5 prompt: the per-issue designs to approve before implementation."""
    lines = []
    for d in designs:
        files = d.get("files_to_touch") or []
        files_note = f" — touches {', '.join(str(f) for f in files[:4])}" if files else ""
        lines.append(f"  - {d.get('issue_key')}: {str(d.get('summary') or '')[:160]}{files_note}")
    body = "\n".join(lines)
    return (
        f"Gate 1.5: approve {len(designs)} feature design(s) before implementation. Each is grounded "
        f"in the repo's knowledge graph (see the run's design artifacts):\n{body}"
    )


def _spec_with_design(spec: dict[str, Any], design: dict[str, Any]) -> dict[str, Any]:
    """Fold an approved design into the spec's technical notes so codegen builds
    to the grounded approach (no codegen-adapter change needed)."""
    d = (design or {}).get("design") or {}
    if not d:
        return spec
    parts = [f"APPROVED DESIGN — approach: {d.get('approach', '')}"]
    if d.get("files_to_touch"):
        parts.append("Files to touch: " + ", ".join(str(f) for f in d["files_to_touch"]))
    if d.get("interfaces"):
        parts.append("Interfaces: " + "; ".join(str(i) for i in d["interfaces"]))
    if d.get("data_changes"):
        parts.append("Data changes: " + "; ".join(str(x) for x in d["data_changes"]))
    if d.get("test_strategy"):
        parts.append("Test strategy: " + str(d["test_strategy"]))
    design_block = "\n".join(parts)
    merged = dict(spec)
    existing = str(merged.get("technical_notes") or "")
    merged["technical_notes"] = f"{existing}\n\n{design_block}".strip() if existing else design_block
    return merged


def _comprehension_summary_line(comprehension: dict[str, Any] | None) -> str | None:
    """A one-line "what Spine understood" summary for the intent gate (M1)."""
    if not comprehension:
        return None
    if comprehension.get("skipped"):
        return f"Repo comprehension skipped ({comprehension.get('reason', 'n/a')})."
    counts = comprehension.get("counts") or {}
    if comprehension.get("greenfield"):
        return "Greenfield repo — no existing code to map."
    nodes = counts.get("nodes")
    edges = counts.get("edges")
    if nodes is None:
        return None
    files = len(comprehension.get("memory_bank_files") or [])
    return (
        f"Understood the repo: {nodes} code entities, {edges} relationships; "
        f"knowledge graph + {files} memory-bank docs + current-state saved (see the run's artifacts)."
    )


def _intents_gate_description(
    intent_count: int,
    open_questions: list[str],
    capability_plan: dict[str, Any] | None = None,
    comprehension: dict[str, Any] | None = None,
) -> str:
    """Gate-1 prompt: intents, open questions, and the assembled capability plan.

    The questions are shown to the approver so a ``modify_input`` decision can
    answer them with a ``clarifications`` patch; the capability plan is shown so
    the approver also signs off on the toolkit (and may override it with a
    ``capability_plan`` patch) before codegen runs.
    """
    base = f"Gate 0: approve {intent_count} intent(s)"
    parts = [base]
    if open_questions:
        lines = "\n".join(f"  - {q}" for q in open_questions)
        parts.append(
            "Open questions to resolve — answer with a modify_input "
            "`clarifications` patch (string or list), or approve to proceed as "
            f"specified:\n{lines}"
        )
    comprehension_line = _comprehension_summary_line(comprehension)
    if comprehension_line:
        parts.append(comprehension_line)
    plan_lines = _plan_summary_lines(capability_plan)
    if plan_lines:
        body = "\n".join(f"  - {line}" for line in plan_lines)
        parts.append(f"Capability plan (toolkit for this run):\n{body}")
    return ". ".join(parts) if len(parts) > 1 else base


def _plan_summary_lines(capability_plan: dict[str, Any] | None) -> list[str]:
    """One line per selected capability (mirrors CapabilityPlan.summary_lines)."""
    if not capability_plan:
        return []
    items = capability_plan.get("items") or []
    if not items:
        return ["base pipeline — no extra capabilities selected"]
    return [f"{i['capability_id']} [{i['kind']}] — {i['rationale']}" for i in items]


def _coerce_clarifications(raw: Any) -> list[str]:
    """Normalize a ``clarifications`` patch (str | list | None) to list[str]."""
    if isinstance(raw, str):
        return [raw.strip()] if raw.strip() else []
    if isinstance(raw, list):
        return [str(c).strip() for c in raw if str(c).strip()]
    return []


def _apply_clarifications(spec: dict[str, Any], clarifications: list[str]) -> dict[str, Any]:
    """Fold approver clarifications into a spec's technical_notes (a copy).

    Codegen reads ``technical_notes``, so appending the human's answers there
    is how they reach the implementation without reshaping the spec.
    """
    updated = dict(spec)
    note = "Approver clarifications (resolve the spec's open questions):\n" + "\n".join(
        f"- {c}" for c in clarifications
    )
    existing = str(updated.get("technical_notes") or "").strip()
    updated["technical_notes"] = f"{existing}\n\n{note}".strip() if existing else note
    return updated


__all__ = ["FeatureImplementationWorkflow", "SDLCWorkflow"]
