"""Block C: SDLC stage activities.

Every side effect the SDLC workflows need funnels through one of these
activities so the workflows stay deterministic and replay-safe. Each stage
delegates to the adapter seam on ``SDLCDeps`` — real implementations are
selected by env in ``worker.build_deps``; the defaults are safe stubs that
return success-shaped dicts.

Activities are bound methods of ``SDLCActivities`` so each closes over the
worker's ``SDLCDeps`` instead of reaching for globals.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from temporalio import activity

from orchestrator.approval import (
    ApprovalRequest,
    ApprovalRequestRepo,
    Approver,
    RiskClassification,
)
from orchestrator.core.llm import BudgetExceededError
from orchestrator.intake.factory import IntakeNotConfiguredError
from orchestrator.intake.service import SourceUriError, parse_source_uri
from orchestrator.registry.repositories import AuditLogRepo
from orchestrator.sdlc.deps import SDLCDeps

logger = logging.getLogger("orchestrator.sdlc.activities")


class SDLCStageError(RuntimeError):
    """A stage activity failed in a way the workflow should not retry past."""


class SDLCActivities:
    """Container for SDLC activity implementations bound to worker-side deps."""

    def __init__(self, deps: SDLCDeps) -> None:
        self._deps = deps

    # ---- audit -----------------------------------------------------------

    @activity.defn(name="sdlc_record_audit")
    async def record_audit(self, request: dict[str, Any]) -> None:
        """Write one audit row. The workflow calls this after each stage so the
        run leaves an append-only trail (same pattern as the orchestrator
        workflow's ``record_audit``)."""
        async with self._deps.session_factory() as session:
            await AuditLogRepo(session).write(
                actor=request.get("actor") or self._deps.actor,
                action=request["action"],
                resource_type=request.get("resource_type") or "sdlc",
                resource_id=request["resource_id"],
                after=request.get("after"),
                trace_id=request.get("trace_id"),
                tenant_id=str(request.get("tenant_id") or "default"),
            )
            await session.commit()

    @activity.defn(name="sdlc_raise_approval_request")
    async def raise_approval_request(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Persist a real ``ApprovalRequest`` row for a gate.

        This is what makes the SDLC gates decidable from the REST
        ``/v1/approvals/*`` API: the row shows up in ``GET /v1/approvals`` and a
        ``POST .../approve|reject|modify_input`` updates it and signals the
        waiting workflow (``task-{task_id}``). Mirrors the orchestrator's
        ``raise_approval_request`` — idempotent on ``approval_id`` so Temporal
        retries don't double-insert.
        """
        approval_id = str(payload["approval_id"])
        task_id = str(payload["task_id"])
        before_node = str(payload["before_node_id"])
        async with self._deps.session_factory() as session:
            repo = ApprovalRequestRepo(session)
            existing = await repo.get(approval_id)
            if existing is not None:
                return existing.model_dump(mode="json")

            risk_raw = str(payload.get("risk_classification") or "medium")
            try:
                risk = RiskClassification(risk_raw)
            except ValueError:
                risk = RiskClassification.MEDIUM

            # Bet 2c-ii: roles the decider must hold (default "any" = any
            # authenticated caller in the tenant); the owning tenant scopes the row.
            roles = [str(r) for r in (payload.get("approver_roles") or [])]
            approvers = (
                [Approver(role=r, min_required=1) for r in roles]
                if roles
                else [Approver(role="any", min_required=1)]
            )
            tenant_id = str(payload.get("tenant_id") or "default")
            request = ApprovalRequest(
                id=approval_id,
                task_id=task_id,
                tenant_id=tenant_id,
                before_node_id=before_node,
                title=str(payload.get("title") or f"Approval required: {before_node}"),
                description=str(payload.get("description") or f"Approve gate {before_node}"),
                action_summary=str(payload.get("action_summary") or f"Proceed past {before_node}"),
                risk_classification=risk,
                affected_resources=list(payload.get("affected_resources") or []),
                approvers=approvers,
                trace_id=payload.get("trace_id"),
            )
            saved = await repo.create(request)

            await AuditLogRepo(session).write(
                actor=self._deps.actor,
                action="sdlc_approval_raised",
                resource_type="sdlc_approval",
                resource_id=saved.id,
                tenant_id=tenant_id,
                after={
                    "task_id": saved.task_id,
                    "before_node_id": saved.before_node_id,
                    "risk": saved.risk_classification.value,
                    "approver_roles": [a.role for a in saved.approvers],
                },
                trace_id=saved.trace_id,
            )
            await session.commit()

        # Best-effort Slack alert so approvers don't have to poll /v1/approvals
        # (G13). Fired only on a fresh row (the idempotent-retry path returned
        # above), after commit, and outside the DB session. It never raises and
        # never affects the returned row — a notification is not worth failing a
        # gate over.
        await self._notify_slack(saved)
        return saved.model_dump(mode="json")

    async def _notify_slack(self, saved: ApprovalRequest) -> None:
        """Post an approval-raised alert to Slack, best-effort. Never raises.

        Reuses the standalone ``orchestrator.notify.slack`` notifier (its HTTP
        call is synchronous, so it runs in a worker thread to keep the activity
        loop free). A no-op when ``SLACK_WEBHOOK_URL`` is unset.
        """
        webhook_url = os.getenv("SLACK_WEBHOOK_URL")
        if not webhook_url:
            return
        try:
            from orchestrator.notify.slack import (
                ApprovalRequest as SlackApprovalRequest,
            )
            from orchestrator.notify.slack import (
                SlackWebhookConfig,
                SlackWebhookNotifier,
            )

            notifier = SlackWebhookNotifier(SlackWebhookConfig(webhook_url=webhook_url))
            await asyncio.to_thread(
                notifier.notify_approval_raised,
                SlackApprovalRequest(
                    approval_id=saved.id,
                    title=saved.title,
                    risk_classification=saved.risk_classification.value,
                ),
            )
        except Exception as exc:  # noqa: BLE001 — delivery must never break a gate
            logger.warning("sdlc.notify.slack_failed", extra={"error": str(exc)[:200]})

    # ---- parent stages ---------------------------------------------------

    @activity.defn(name="sdlc_intake_analyze")
    async def intake_analyze(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Run the real backlog pipeline (source → intents → specs).

        Returns a JSON-friendly summary plus the specs (each as a dict) so the
        workflow can fan them out. Tests inject ``deps.service_builder`` to
        keep this offline; production dispatches on the source kind
        (``confluence`` / ``notion``) via ``build_service_for``.
        """
        source_uri = str(payload["source_uri"])
        _, root_id = parse_source_uri(source_uri)
        dry_run = bool(payload.get("dry_run_jira", True))

        builder = self._deps.service_builder
        try:
            if builder is not None:
                service = builder(dry_run=dry_run)
            else:
                from orchestrator.intake.factory import build_service_for

                service = build_service_for(source_uri, dry_run=dry_run)
        except (SourceUriError, IntakeNotConfiguredError) as exc:
            raise SDLCStageError(str(exc)) from exc
        plan = await service.analyze(root_id)

        # Open questions the extractor flagged as needing a human answer —
        # surfaced at gate 1 so the approver can resolve ambiguity before
        # codegen instead of the model guessing (deduped, order-preserved).
        open_questions: list[str] = []
        for intent in plan.intents:
            for q in intent.open_questions:
                if q and q not in open_questions:
                    open_questions.append(q)

        return {
            "intent_count": len(plan.intents),
            "gap_count": len(plan.gaps),
            "blocked": plan.blocked,
            "truncated": plan.truncated,
            "specs": [_dump(spec) for spec in plan.specs],
            "open_questions": open_questions,
        }

    @activity.defn(name="sdlc_profile_and_plan")
    async def profile_and_plan(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Profile the target repo and assemble a capability plan (Phase 1+2).

        Best-effort and deterministic: profiles the base checkout when one is
        reachable, otherwise falls back to an intent-only profile (no repo
        signals). The plan is selected from the governed catalog — never
        improvised — and surfaced at the intent gate.
        """
        from orchestrator.catalog import ProjectProfile, plan_capabilities, task_type_from_intent

        intent_text = str(payload.get("intent_text") or "")
        try:
            base = await self._deps.workspace.ensure_base_repo()
            profile = ProjectProfile.from_repo(base, intent_title=intent_text)
        except Exception as exc:  # noqa: BLE001 — profiling is best-effort; degrade to intent-only
            logger.warning("sdlc.profile.fallback", extra={"error": str(exc)[:200]})
            profile = ProjectProfile(
                languages=frozenset(),
                framework=None,
                has_db=False,
                has_migrations=False,
                test_runner=None,
                task_type=task_type_from_intent(intent_text),
            )
        plan = plan_capabilities(profile)
        return {"profile": profile.to_dict(), "plan": plan.to_dict()}

    @activity.defn(name="sdlc_create_jira_issues")
    async def create_jira_issues(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Pair each spec with an issue key for the fan-out.

        Skeleton default is dry-run: synthetic keys (``SDLC-1``, ``SDLC-2``,
        …) so the fan-out has stable keys without writing to a real tracker.
        Live create stays behind the same gate/flag as Block B (Block D wires
        it through ``BacklogService.create_issues``).
        """
        specs: list[dict[str, Any]] = list(payload.get("specs") or [])
        prefix = str(payload.get("key_prefix") or "SDLC")
        issue_plans = [{"issue_key": f"{prefix}-{i + 1}", "spec": spec} for i, spec in enumerate(specs)]
        return {"issue_plans": issue_plans}

    @activity.defn(name="sdlc_integration_test")
    async def integration_test(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Run cross-issue integration checks via the CI adapter."""
        issue_keys = [str(k) for k in (payload.get("issue_keys") or [])]
        pr_urls = [str(u) for u in (payload.get("pr_urls") or []) if u]
        result = await self._deps.ci.run_checks(issue_keys=issue_keys, pr_urls=pr_urls)
        return {
            "verdict": "pass" if result.passed else "fail",
            "summary": result.summary,
            "issue_keys": list(result.issue_keys),
        }

    @activity.defn(name="sdlc_merge_prs")
    async def merge_prs(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Merge the green, gate-approved feature PRs (merge-on-green).

        All-or-nothing verdict: one PR failing to merge fails the stage —
        a half-merged change set is worse than a stopped pipeline.
        """
        pr_urls = [str(u) for u in (payload.get("pr_urls") or []) if u]
        outcomes: list[dict[str, Any]] = []
        all_merged = bool(pr_urls)
        for url in pr_urls:
            result = await self._deps.pr.merge_pr(pr_url=url)
            outcomes.append({"url": result.url, "merged": result.merged, "detail": result.detail})
            all_merged = all_merged and result.merged
        return {"verdict": "pass" if all_merged else "fail", "merges": outcomes}

    @activity.defn(name="sdlc_consolidate_memory")
    async def consolidate_memory(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Post-merge: distill the run's governance episodes into semantic memory
        (Phase 2b, cross-run memory). No-op — never raises — unless
        ``ORCHESTRATOR_SEMANTIC_MEMORY`` is on, an LLM is wired, and a repo key is
        resolvable (``SDLC_REPO_URL``). The bundle is the union of every merged
        feature's ``policy_blocks``; the whole SDLC run is one ``run_id``."""
        import os

        enabled = (os.getenv("ORCHESTRATOR_SEMANTIC_MEMORY") or "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        repo_key = payload.get("repo_key") or os.getenv("SDLC_REPO_URL")
        if not enabled or self._deps.llm is None or not repo_key:
            return {"skipped": True}

        from orchestrator.knowledge.consolidate import consolidate_run
        from orchestrator.sdlc.codegen import resolve_codegen_model

        model = resolve_codegen_model()
        if not model:
            return {"skipped": True, "reason": "no model"}
        # Always run — even with no episodes the decay sweep (Phase 3) should age
        # out stale memories on every merge.
        bundle = {"policy_blocks": list(payload.get("policy_blocks") or [])}
        async with self._deps.session_factory() as session:
            return await consolidate_run(
                bundle=bundle,
                repo_key=str(repo_key),
                session=session,
                llm=self._deps.llm,
                model=model,
                run_id=str(payload.get("sdlc_id") or ""),
                tenant_id=str(payload.get("tenant_id") or "default"),
                trace_id=payload.get("trace_id"),
            )

    @activity.defn(name="sdlc_register_units")
    async def register_units(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Post-merge: register shipped units with infodrift (Spine Seam 2).

        No-op — never raises — unless ``SPINE_INFODRIFT_URL`` and a deploy topology
        (``SPINE_DEPLOY_TOPOLOGY`` JSON: ``{component: [[region, interface], ...]}``)
        are configured. The topology keys are the components this repo ships; each
        placement becomes one ``unit_shipped`` registration baselined to
        ``SPINE_SHIP_VERSION`` (default "1")."""
        import json
        import os

        url = (os.getenv("SPINE_INFODRIFT_URL") or "").strip()
        topo_raw = (os.getenv("SPINE_DEPLOY_TOPOLOGY") or "").strip()
        if not url or not topo_raw:
            return {"skipped": True}
        try:
            table = {str(k): [tuple(p) for p in v] for k, v in json.loads(topo_raw).items()}
        except (ValueError, TypeError):
            return {"skipped": True, "reason": "bad SPINE_DEPLOY_TOPOLOGY"}

        from orchestrator.spine import (
            InfodriftHttpClient,
            ShipmentRegistrar,
            ShippedUnit,
            StaticDeployTopology,
        )

        version = os.getenv("SPINE_SHIP_VERSION") or "1"
        registrar = ShipmentRegistrar(InfodriftHttpClient(url), StaticDeployTopology(table))
        registered: list[dict[str, Any]] = []
        for component in table:
            unit = ShippedUnit(
                component=component,
                version=version,
                repo_key=str(payload.get("repo_key") or os.getenv("SDLC_REPO_URL") or ""),
                trace_id=str(payload.get("trace_id") or ""),
            )
            for result in registrar.register(unit):
                registered.append({"entity_key": result.entity_key, "ok": result.ok})
        return {"skipped": False, "registered": registered}

    # ---- child (feature) stages -----------------------------------------

    @activity.defn(name="sdlc_create_workspace")
    async def create_workspace(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Create a per-issue git worktree, returning its path."""
        sdlc_id = str(payload["sdlc_id"])
        issue_key = str(payload["issue_key"])
        path = await self._deps.workspace.create(sdlc_id, issue_key)
        return {"path": str(path)}

    @contextlib.asynccontextmanager
    async def _budget_scope(self, payload: dict[str, Any], stage: str) -> AsyncIterator[None]:
        """Attribute this stage's LLM spend to the run's budget (G9).

        Stages that may call an LLM open this around their adapter call so the
        shared ``RunBudget`` charges the right run — and refuses the call once
        that run is at its cap. No-op when no budget is wired (stub adapters).

        A tripped budget leaves a queryable ``sdlc_budget_exhausted`` audit
        row (stage, spend, cap) before the error propagates — without it the
        only record of the dollars is the error string buried inside the
        ``features_failed`` detail.
        """
        budget = self._deps.budget
        sdlc_id = str(payload.get("sdlc_id") or "")
        if budget is None or not sdlc_id:
            yield
            return
        with budget.activate(sdlc_id):
            try:
                yield
            except BudgetExceededError:
                # Best-effort: the audit write must never mask the budget error.
                with contextlib.suppress(Exception):
                    await self.record_audit(
                        {
                            "action": "sdlc_budget_exhausted",
                            "resource_id": sdlc_id,
                            "after": {
                                "stage": stage,
                                "spent_usd": round(budget.spent(sdlc_id), 4),
                                "max_cost_usd": budget.max_cost_usd,
                            },
                            "trace_id": sdlc_id,
                        }
                    )
                raise

    @activity.defn(name="sdlc_code_plan")
    async def code_plan(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Plan the change via the codegen adapter (stub = fixed steps)."""
        async with self._budget_scope(payload, "code_plan"):
            plan = await self._deps.codegen.plan(
                spec=dict(payload.get("spec") or {}), path=str(payload["path"])
            )
        return {"steps": list(plan.steps)}

    @activity.defn(name="sdlc_implement")
    async def implement(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Implement the change into the worktree via the codegen adapter.

        Returns a discriminated result: ``{"status": "complete", ...}`` when the
        change is done, or ``{"status": "needs_approval", ...}`` when the agentic
        loop hit a ``require_approval`` policy gate mid-run (Bet 2c-i). The pause
        is a *successful* activity result — never a raised failure — so Temporal
        does not retry it; the workflow raises the approval and resumes.
        """
        async with self._budget_scope(payload, "implement"):
            outcome = await self._deps.codegen.implement_governed(
                spec=dict(payload.get("spec") or {}),
                path=str(payload["path"]),
                issue_key=str(payload["issue_key"]),
                skills=[str(s) for s in (payload.get("skills") or [])],
                mcp_servers=[str(s) for s in (payload.get("mcp_servers") or [])],
            )
        return self._implement_result(outcome)

    @activity.defn(name="sdlc_implement_resume")
    async def implement_resume(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Resume a paused agentic implement with the human's decision (Bet 2c-i).

        Same discriminated result as ``sdlc_implement`` — a single resume can hit
        another ``require_approval`` gate, so the workflow loops until complete."""
        async with self._budget_scope(payload, "implement"):
            outcome = await self._deps.codegen.resume_implement(
                path=str(payload["path"]),
                checkpoint=dict(payload["checkpoint"]),
                decision=dict(payload["decision"]),
                prior_written=[str(f) for f in (payload.get("prior_written") or [])],
                prior_summary=str(payload.get("prior_summary") or ""),
                mcp_servers=[str(s) for s in (payload.get("mcp_servers") or [])],
            )
        return self._implement_result(outcome)

    @staticmethod
    def _implement_result(outcome: Any) -> dict[str, Any]:
        if outcome.needs_approval:
            return {
                "status": "needs_approval",
                "checkpoint": outcome.checkpoint,
                "pending": outcome.pending,
                "prior_written": list(outcome.written),
                "prior_summary": outcome.summary,
            }
        change = outcome.change
        return {
            "status": "complete",
            "files": list(change.files),
            "summary": change.summary,
            "policy_blocks": [dict(b) for b in getattr(outcome, "policy_blocks", [])],
        }

    @activity.defn(name="sdlc_test_author")
    async def test_author(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Author tests for the change via the codegen adapter."""
        async with self._budget_scope(payload, "test_author"):
            change = await self._deps.codegen.author_tests(
                spec=dict(payload.get("spec") or {}),
                path=str(payload["path"]),
                issue_key=str(payload["issue_key"]),
            )
        return {"files": list(change.files), "summary": change.summary}

    @activity.defn(name="sdlc_refine")
    async def refine(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Refine the implementation given the failing-test output.

        Called by the child's refinement loop when ``run_tests`` fails and
        iterations remain. The stub rewrites the same module; Block D feeds the
        failures back to the LLM for a corrected patch.
        """
        async with self._budget_scope(payload, "refine"):
            change = await self._deps.codegen.refine(
                spec=dict(payload.get("spec") or {}),
                path=str(payload["path"]),
                issue_key=str(payload["issue_key"]),
                failures=str(payload.get("failures") or ""),
            )
        return {"files": list(change.files), "summary": change.summary}

    @activity.defn(name="sdlc_run_tests")
    async def run_tests(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Run the worktree's tests via the test runner.

        The production worker uses ``SubprocessTestRunner`` (real ``pytest``);
        tests inject ``StubTestRunner`` to script pass/fail without a subprocess.
        """
        result = await self._deps.tests.run(path=str(payload["path"]))
        return {
            "passed": result.passed,
            "returncode": result.returncode,
            "output": result.output,
        }

    @activity.defn(name="sdlc_preflight")
    async def preflight(self, payload: dict[str, Any]) -> dict[str, Any]:
        """CI-parity quality gate (ruff + format + mypy) in the worktree."""
        result = await self._deps.preflight.run(path=str(payload["path"]))
        return {"passed": result.passed, "output": result.output}

    @activity.defn(name="sdlc_review")
    async def review(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Review the change via the review adapter (stub = COMMENT, no BLOCKER)."""
        spec = payload.get("spec") if isinstance(payload.get("spec"), dict) else None
        async with self._budget_scope(payload, "review"):
            result = await self._deps.review.review(
                path=str(payload["path"]), issue_key=str(payload["issue_key"]), spec=spec
            )
        return {
            "verdict": result.verdict,
            "blockers": list(result.blockers),
            "summary": result.summary,
            "has_blocker": result.has_blocker,
            "uncertain": list(result.uncertain),
        }

    @activity.defn(name="sdlc_escalation_check")
    async def escalation_check(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Calibrated escalation (G10): flag green-but-risky features for humans.

        Never blocks — the decision annotates the feature so the merge-gate
        approver reviews with attention proportional to risk.
        """
        from orchestrator.sdlc.escalation import EscalationSignals, blast_radius

        raw_review = payload.get("review")
        review: dict[str, Any] = raw_review if isinstance(raw_review, dict) else {}
        radius, symbol = blast_radius(str(payload.get("path") or "."))
        signals = EscalationSignals(
            uncertain_criteria=[str(u) for u in (review.get("uncertain") or [])],
            iterations=int(payload.get("iterations") or 0),
            blast_radius=radius,
            radius_symbol=symbol,
        )
        decision = self._deps.escalation.decide(signals)
        return {
            "escalate": decision.escalate,
            "reasons": list(decision.reasons),
            "blast_radius": radius,
            "radius_symbol": symbol,
        }

    @activity.defn(name="sdlc_open_pr")
    async def open_pr(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Open a PR for the change via the PR adapter (stub = synthetic URL)."""
        issue_key = str(payload["issue_key"])
        result = await self._deps.pr.open_pr(
            issue_key=issue_key,
            path=str(payload["path"]),
            branch=str(payload.get("branch") or ""),
            title=str(payload.get("title") or f"{issue_key}: automated change"),
            body=str(payload.get("body") or ""),
        )
        return {"pr_url": result.url, "number": result.number}

    @activity.defn(name="sdlc_cleanup_workspace")
    async def cleanup_workspace(self, payload: dict[str, Any]) -> None:
        """Tear down the per-issue worktree."""
        await self._deps.workspace.cleanup(Path(str(payload["path"])))


def _dump(spec: Any) -> dict[str, Any]:
    """Serialize a FeatureSpec (pydantic) — or pass through a plain dict."""
    if hasattr(spec, "model_dump"):
        return dict(spec.model_dump(mode="json"))
    return dict(spec)


__all__ = ["SDLCActivities", "SDLCStageError"]
