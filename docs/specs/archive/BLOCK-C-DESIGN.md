# Block C — End-to-end SDLC skeleton (design)

**Status:** Proposed · **Branch:** `block-c-skeleton` · **Plan ref:**
[SDLC-ORCHESTRATOR-PLAN.md](SDLC-ORCHESTRATOR-PLAN.md) §6 Block C

## 1. Goal

Prove the **orchestration** end-to-end: a Confluence page marches A→Z to a
(stubbed) deployment, both human approval gates fire, and an audit row lands
at every stage. Every domain step that isn't orchestration is **stubbed** —
codegen writes one hardcoded file per issue, deploy/E2E are no-ops that
return success. The plan is explicit: *"everything stubbed but the
orchestration… Do not skip this phase."*

**Out of scope (Block D/E):** real LLM codegen, real CI, real Cloud Run
deploy, Playwright E2E, web UI. Real Cloud Run needs the user's GCP account,
so it stays behind a stub adapter + a runbook.

## 2. Reuse vs. build-new

**Reuse as-is** (mapped from the platform-primitives audit):

| Need | Existing primitive | File |
|---|---|---|
| Durable multi-stage workflow + signals + queries | `OrchestratorWorkflow` pattern | [temporal/workflow.py](../../src/orchestrator/temporal/workflow.py) |
| Approval gate (raise → wait on signal → consume by index) | `raise_approval_request` activity + `approve`/`deny`/`modify_input` signals + `wait_condition` | [temporal/activities.py:210](../../src/orchestrator/temporal/activities.py) |
| Approval persistence + REST decision API | `ApprovalRequest`/`ApprovalRequestRepo`, `/v1/approvals/*` | [approval/](../../src/orchestrator/approval), [registry/api/approvals.py](../../src/orchestrator/registry/api/approvals.py) |
| Audit row per decision/stage | `AuditLogRepo.write(...)` via a `record_audit` activity | [registry/repositories.py](../../src/orchestrator/registry/repositories.py) |
| Worker wiring (session factory, LLM, artifact store) | `ActivityDeps`, `run_worker`, `build_deps` | [temporal/worker.py](../../src/orchestrator/temporal/worker.py) |
| Confluence → intents → specs → Jira | `BacklogService.analyze` / `create_issues` | [intake/service.py](../../src/orchestrator/intake/service.py) |

**Build-new** (this block):

- `SDLCWorkflow` (parent) + `FeatureImplementationWorkflow` (child) workflows.
- `SDLCActivities` — the stage activities (mostly stubs).
- `WorkspaceManager` — per-task/per-issue git worktree lifecycle.
- `DeployAdapter` protocol + `StubDeployAdapter` (real Cloud Run = Block D).
- `intake.factory.build_confluence_service` — **copy over** from
  `infra-a-b-enablement` (not yet on `develop`) so the intake stage reuses it.
- Worker registration for the new workflows/activities (own task queue).

## 3. Module layout

```
src/orchestrator/sdlc/
  __init__.py
  types.py          # SDLCWorkflowInput/Result, FeatureWorkflowInput/Result, IssuePlan, verdict consts
  deps.py           # SDLCDeps — frozen deps bundle, all adapters injected (stub defaults)
  workspace.py      # WorkspaceManager (git worktree create/cleanup)
  deploy.py         # DeployAdapter protocol + StubDeployAdapter
  codegen.py        # CodegenAdapter (plan/implement/author_tests/refine) + StubCodegenAdapter
  testrunner.py     # TestRunner + SubprocessTestRunner (real pytest, default) + StubTestRunner
  review.py         # ReviewAdapter + StubReviewAdapter
  forge.py          # PRAdapter + StubPRAdapter
  ci.py             # CIAdapter + StubCIAdapter
  activities.py     # SDLCActivities(deps) — stage activities, all @activity.defn, delegate to adapters
  workflows.py      # SDLCWorkflow (parent) + FeatureImplementationWorkflow (child, refinement loop)
  worker.py         # run_sdlc_worker — registers the above on the sdlc task queue
```

Keep it a sibling of `intake/` and `temporal/`; the SDLC worker reuses
`ActivityDeps` from [temporal/deps.py](../../src/orchestrator/temporal/deps.py)
(extended with a `workspace_root: Path` and a `DeployAdapter`).

## 4. Parent: `SDLCWorkflow`

Input `SDLCWorkflowInput`: `{ sdlc_id, source_uri, actor, trace_id,
auto_approve_intent?: bool (test hook), labels? }`.

Stages (each side effect is an activity; the workflow stays deterministic):

```
1. intake_analyze(source_uri)            → {intents, gaps, specs, blocked}   [+audit]
2. ✋ GATE 1 "approve intents"           raise_approval_request → wait signal
        - deny → terminate (audit: intents_denied)
        - modify_input patch → carried into issue creation (e.g. drop/edit specs)
3. create_jira_issues(specs)             → [issue_key,...]  (dry-run default) [+audit]
4. fan-out: for each issue, execute_child_workflow(
        FeatureImplementationWorkflow, FeatureWorkflowInput) — gathered concurrently
5. integration_test(issue_keys)          → stub PASS  (IntegrationTestVerifier) [+audit]
6. deploy_to_staging()                    → stub revision url                   [+audit]
7. run_e2e()                              → stub PASS  (E2ETestVerifier)         [+audit]
8. ✋ GATE 2 "promote to prod"           raise_approval_request → wait signal
        - modify_input patch carries release_notes
        - deny → terminate (audit: prod_denied)
9. deploy_to_prod(release_notes)          → stub prod revision                   [+audit]
10. post_deploy_smoke()                   → stub PASS                            [+audit]
    return SDLCWorkflowResult {stage outcomes, issue_keys, revisions, gate decisions}
```

**Gate mechanism** mirrors `OrchestratorWorkflow` exactly: each gate calls
`raise_approval_request` to persist a real `ApprovalRequest` row (decidable via
the REST `/v1/approvals/*` API, which signals the workflow back), then waits on
an append-only `self._decisions` queue fed by `on_approve/on_deny/on_modify_input`
signals, consumed by index. Approval ids are `sdlc-{sdlc_id}-{0|1}`; task_id is
`sdlc_id`, so the workflow must run with id `task-{sdlc_id}` for REST decisions
to route. Two gates = consume indices 0 and 1.
`modify_input` at gate 2 carries `{release_notes: ...}`. A `cancel` signal
(one-shot `self._cancelled` flag) wakes whichever gate is waiting and
terminates the run with reason `cancelled` — same cooperative-cancel shape as
`OrchestratorWorkflow`.

**Fan-out:** Temporal `workflow.execute_child_workflow` per issue, started
**concurrently** via `asyncio.gather`. Child id = `feat-{sdlc_id}-{issue_key}`
for determinism / idempotency. Fan-in = all children resolved before stage 5.

## 5. Child: `FeatureImplementationWorkflow`

Input `FeatureWorkflowInput`: `{ sdlc_id, issue_key, spec: dict, trace_id,
max_refine_iterations: int = 3 }`.

```
1. create_workspace(sdlc_id, issue_key)  → worktree path  (WorkspaceManager) [+audit]
2. code_plan(spec, path)                  → adapter plan (stub = fixed steps)
3. implement(spec, path, issue_key)       → adapter writes module into worktree
4. test_author(spec, path, issue_key)     → adapter writes the test into worktree
5. refinement loop (≤ max_refine_iterations test runs):
     run_tests(path)                       → {passed, returncode, output}
     if passed: break
     if iterations exhausted: break
     audit feature_refined{iteration}
     refine(spec, path, issue_key, failures) → adapter rewrites from failures
   if tests still red → audit feature_tests_failed, verdict=failed, NO PR
6. review(path, issue_key)                → {verdict, blockers, has_blocker}
     if has_blocker → audit feature_review_blocked, verdict=changes_requested, NO PR
7. open_pr(issue_key, path, branch)       → PR url (stub = synthetic)         [+audit]
8. cleanup_workspace(path)   ← runs in a `finally` so a mid-pipeline failure
                               never orphans the worktree (best-effort: a
                               teardown error won't mask the stage error)
   return FeatureWorkflowResult {issue_key, files_written, pr_url, verdict, iterations}
```

**Verdict gating (not "always succeed").** The child returns one of three
verdicts — `passed` (all green, PR opened), `failed` (tests still red after the
refinement cap), or `changes_requested` (review BLOCKER). Only `passed` opens a
PR; the other two escalate without one. The parent treats any non-`passed`
child as a stop: it audits `sdlc_features_failed` and terminates with reason
`features_failed` **before** integration/deploy.

**Refinement loop (Block D's central architectural change, scaffolded now).**
`implement → run_tests → refine` repeats up to `max_refine_iterations` total
test runs, feeding the failing output back into `refine` each cycle. With the
default `StubTestRunner`/`StubCodegenAdapter` the first run is green so the loop
is single-shot; Block D's real test runner + LLM `refine` make it iterate for
real. `SubprocessTestRunner` (the production default) already runs the generated
tests for real via `pytest`.

The hardcoded-file codegen is the deliberate Block-C stub. Real
`code_planner`/`implementer`/`test_author`/`refine` (LLM) drop in behind the
`CodegenAdapter` Protocol (§5a) without touching the activities or the workflow.

## 5a. Adapter seams (the Block-D plug points)

Every domain step the workflow calls is a swappable adapter injected via the
frozen `SDLCDeps`, mirroring the existing `DeployAdapter`. Block D drops real
implementations in behind the same `@runtime_checkable` Protocols — no edits to
`activities.py` or `workflows.py`. Defaults stay stubbed (no new creds); the
one exception is the test runner, whose default is **real**.

| Seam | Protocol | Stub default | Block-D real impl |
|---|---|---|---|
| codegen | `CodegenAdapter` (`plan`/`implement`/`author_tests`/`refine`) | `StubCodegenAdapter` (one hardcoded module + test) | LLM codegen |
| tests | `TestRunner` (`run`) | **`SubprocessTestRunner` (real `pytest`)** — `StubTestRunner` in unit tests | real CI runner |
| review | `ReviewAdapter` (`review`) | `StubReviewAdapter` (COMMENT, no BLOCKER) | Block A `codereview` |
| PR | `PRAdapter` (`open_pr`) | `StubPRAdapter` (synthetic url) | GitHub PR |
| CI | `CIAdapter` (`run_checks`) | `StubCIAdapter` (always pass) | real integration CI |
| deploy | `DeployAdapter` (`deploy`) | `StubDeployAdapter` (synthetic revision) | Cloud Run |

The refinement-loop cap (`max_refine_iterations`) lives on the **workflow
input**, not on deps — it's orchestration logic, and workflow code can't read
worker-side deps deterministically. `SubprocessTestRunner` shells out via
`asyncio.create_subprocess_exec` with an explicit argv list (no shell), same
injection-safe shape as the workspace git calls, and runs inside an activity.

## 6. WorkspaceManager

```python
class WorkspaceManager:
    def __init__(self, root: Path, repo_url: str | None = None) -> None: ...
    async def create(self, sdlc_id: str, issue_key: str) -> Path:   # git worktree add
    async def cleanup(self, path: Path) -> None:                    # git worktree remove
```

- Per-issue worktree under `root/{sdlc_id}/{issue_key}`.
- For the skeleton: if no `repo_url`, `git init` a scratch repo so the stub
  file write + worktree mechanics are exercised without a real monorepo.
- All git via `asyncio.create_subprocess_exec` (no shell string interp —
  avoids injection). Runs inside an activity, never in workflow code.

## 7. Deploy adapter

```python
class DeployAdapter(Protocol):
    async def deploy(self, *, target: str, tag: str, release_notes: str = "") -> DeployResult: ...

class StubDeployAdapter:   # returns a synthetic revision url, no cloud calls
    ...
```

Real `CloudRunDeployAdapter` (`gcloud run deploy`) is Block D and needs the
user's GCP project — documented as a runbook, default stays stub.

## 8. Worker / registration

New `run_sdlc_worker` on its own task queue (`SDLC_TASK_QUEUE`, default
`sdlc-tasks`) to keep it isolated from the existing `orchestrator-tasks`
worker. Registers `SDLCWorkflow` + `FeatureImplementationWorkflow` and the
`SDLCActivities` methods. Reuses `connect_client` / `TemporalConfig`.

## 9. Testing

- **Unit (no docker):** `WorkflowEnvironment.from_local()` time-skipping
  server (the existing [tests/temporal/test_workflow.py](../../tests/temporal/test_workflow.py)
  pattern). Register `SDLCWorkflow` + child + **stub activities**; drive both
  gates by sending `approve` / `modify_input` signals; assert the pipeline
  reaches `deploy_to_prod`, fans out N children, and records the expected
  audit actions. A second test asserts `deny` at gate 1 terminates early.
- **WorkspaceManager unit test:** real `git` in a tmp dir — create worktree,
  write file, cleanup; assert isolation between two issue keys.
- **Activity tests:** `ActivityEnvironment` for `create_workspace` and the
  audit/deploy stubs.
- No new integration (Postgres) test required for the skeleton; approval rows
  are exercised by the existing approvals integration suite.

## 10. Increment breakdown (build order once approved)

1. `sdlc/types.py` + `sdlc/workspace.py` (+ worktree unit test).
2. `sdlc/deploy.py` stub adapter.
3. Copy `intake/factory.py` from `infra-a-b-enablement`.
4. `sdlc/activities.py` (intake, jira, integration/e2e/deploy stubs, audit,
   workspace, child-stage stubs).
5. `sdlc/workflows.py` parent + child.
6. `sdlc/worker.py` + registration.
7. Temporal test-env tests (happy path through both gates + deny path).
8. PROGRESS.md + a short "run the skeleton" runbook in LIVE-TESTING.md.

## 11. Open questions

1. **Real intake at the front, or stub it too for the first skeleton test?**
   Proposal: make `intake_analyze` call the real `BacklogService` but allow a
   stubbed source in tests (inject a fake `SourceAdapter`), so the unit test
   stays offline while the activity is production-shaped.
2. **Jira create in the skeleton:** default dry-run (synthetic keys) so the
   fan-out has issue keys without writing to a real tracker. Live create stays
   behind the same gate/flag as Block B.
3. **Separate task queue vs. reuse `orchestrator-tasks`?** Proposal: separate
   (`sdlc-tasks`) — cleaner isolation, independent scaling, no risk to the
   existing worker.
4. **`sdlc_id` source:** generate a ULID/uuid at submit time; used for child
   workflow ids and the audit `resource_id`.
