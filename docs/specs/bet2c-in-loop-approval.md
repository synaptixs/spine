# Bet 2c-i — In-loop human approval (durable mid-loop pause)

> Status: **implemented.** Upgrades the 2a `require_approval` decision from
> *deny-with-reason* to a **real human pause** mid-loop, resumed durably through
> Temporal. The design below is as-built; the "As built" notes at the end record
> where the implementation firmed up choices the design left open.

## The goal

In 2a, a `require_approval` policy decision is treated as deny: the loop records
a block, feeds a refusal observation to the model, and moves on. 2c-i makes it a
true checkpoint: when the agentic loop wants to run a `require_approval` tool, the
run **pauses**, a real `ApprovalRequest` is raised, a human decides via the
existing `/v1/approvals/*` API, and the loop **resumes from exactly where it
stopped** with the decision folded in — approve (run the pending call), reject
(feed a denial observation and continue), or modify (run with patched arguments).

## The Temporal wrinkle (why this is the hard slice)

The loop runs inside the **`sdlc_implement` activity**. Temporal activities must
be bounded — they cannot block for an open-ended human wait (heartbeat timeouts,
worker restarts, no durable wait primitive inside an activity). Human waits belong
in the **workflow**, which is durable and can `wait_condition` on a signal for
days.

Second structural fact, confirmed in the code: `sdlc_implement` is dispatched by
the **child** `FeatureImplementationWorkflow` (`workflows.py:102`), id
`feat-{sdlc_id}-{issue_key}` — **not** the parent `SDLCWorkflow`. The parent owns
the approve/deny/modify_input signal handlers and `_gate` (`workflows.py:375-385`,
`621-673`); the child has none of that today. So 2c-i must give the *child* its
own gate + signal plumbing, routed to the child's workflow id.

## Design: checkpoint-return + workflow-driven resume

The loop never blocks for a human. Instead it **returns a checkpoint**; the
workflow does the durable wait and re-invokes the activity. State travels through
Temporal workflow history (the activity result → the resume activity input), so
**no external checkpoint store is needed** — the durability we already pay for
covers it.

### 1. Loop layer (`agentic/loop.py`)

- Distinguish `REQUIRE_APPROVAL` from `DENY` in the policy gate
  (`loop.py:138-157`). `DENY` keeps 2a behavior. `REQUIRE_APPROVAL` stops the
  loop and returns a checkpoint.
- New `LoopResult.stopped_reason = "needs_approval"`, plus a `pending` field
  describing the gated call and a `checkpoint` capturing resumable state.

```python
@dataclass
class PendingApproval:
    tool: str
    arguments: dict[str, Any]
    reason: str          # the policy reason
    call_id: str         # the model's tool_call id, to answer on resume
    step: int

@dataclass
class LoopCheckpoint:
    messages: list[Message]      # full conversation incl. the assistant turn that
                                 # requested the gated call
    made: list[str]
    blocks: list[dict[str, str]]
    trace: list[StepRecord]
    step: int
    nudges: int
    pending: PendingApproval
```

- New entry point `resume(checkpoint, decision)`:
  - **approve** → dispatch `pending` (or with `decision.modified_input` as
    patched arguments), append the observation as the tool result for
    `pending.call_id`, then continue the `for step` loop from `checkpoint.step`.
  - **reject** → append a refusal observation (`"human rejected: <rationale>"`)
    for `pending.call_id`, record the block, continue.
  - Resume rebuilds `messages` from the checkpoint and re-enters the same loop
    body — no duplication; factor the per-step body so `run` and `resume` share it.
- The checkpoint must be JSON-serializable (it crosses the activity boundary as a
  Temporal payload). `Message` already serializes for the LLM seam; verify
  tool-call turns round-trip. Bound: max 16 steps → payload stays well under
  Temporal's per-payload limit, but **log the checkpoint size** and treat
  "checkpoint too large" as a config error, not a silent failure.

### 2. Activity layer (`sdlc/activities.py`, `sdlc/codegen.py`)

- `_agentic_implement` (`codegen.py:404-448`) currently builds `AgentLoop`
  **without a policy** (`codegen.py:438`). Thread a policy through
  `codegen.implement(...)` (loaded from the run config, like `mcpServers`).
- The `sdlc_implement` activity returns a discriminated result:
  - `{"status": "complete", "files": [...], "summary": ...}` — as today.
  - `{"status": "needs_approval", "checkpoint": {...}, "pending": {...}}`.
- A second activity `sdlc_implement_resume(checkpoint, decision)` calls
  `loop.resume(...)` and returns the same discriminated shape (a single resume
  can hit *another* approval — the workflow loops).
- **Do not raise across the activity boundary** for the pause — a raise is a
  Temporal failure and would retry. The pause is a normal, successful activity
  result.

### 3. Workflow layer (`sdlc/workflows.py`, `FeatureImplementationWorkflow`)

- Add `approve` / `deny` / `modify_input` signal handlers + a decision queue to
  the **child** workflow (mirror the parent, `workflows.py:375-385`). Signals
  route to the child's own id `feat-{sdlc_id}-{issue_key}`.
- Wrap the `sdlc_implement` call (`workflows.py:102`) in a resume loop:

```python
impl = await execute_activity("sdlc_implement", {...})
while impl["status"] == "needs_approval":
    approval_id = f"feat-{sdlc_id}-{issue_key}-impl-{n}"   # n increments per pause
    await execute_activity("sdlc_raise_approval_request", {
        "approval_id": approval_id,
        "task_id": f"feat-{sdlc_id}-{issue_key}",           # routes the signal back
        "before_node_id": f"implement:{impl['pending']['tool']}",
        "title": f"approve tool: {impl['pending']['tool']}",
        "action_summary": impl["pending"]["reason"],
        "risk_classification": "high",
        ...
    })
    decision = await self._await_decision(...)              # wait_condition on signal
    impl = await execute_activity("sdlc_implement_resume",
        {"checkpoint": impl["checkpoint"], "decision": decision, ...})
```

- Reuse the existing `sdlc_raise_approval_request` activity (`activities.py:68`)
  and the REST decide endpoints unchanged — the approval plane is already generic
  over `task_id` + `approval_id`. The only new ids are the per-pause approval ids.
- `before_node_id` carries `implement:<tool>` so the export bundle (2b) and audit
  log show *which* in-loop action was gated and who decided it.

### 4. Idempotency / determinism

- `approval_id` is deterministic (`feat-{sdlc_id}-{issue_key}-impl-{n}`), so a
  workflow replay re-derives the same id and `sdlc_raise_approval_request` stays
  idempotent (`repository.create` is insert-if-absent).
- The resume activity is pure given (checkpoint, decision); the checkpoint pins
  the conversation, so retries are deterministic.
- Changing workflow/activity signatures orphans in-flight runs
  (Nondeterminism on replay) — ship behind the agentic flag and note in release.

## Scope boundaries

- **In:** the pause/resume mechanism end-to-end, deterministic tests (mock LLM
  scripted to call a `require_approval` tool → pause → resume on each of
  approve/reject/modify), the audit + export trail for in-loop approvals.
- **Out (2c-ii):** `tenant_id` scoping and enforced `approver_roles`. 2c-i uses
  the existing `Approver(role="any")` default.
- **Out:** approval of MCP/write tools by *default* — the policy file decides
  what is `require_approval`; 2c-i ships the machinery, not a default policy.

## Test plan (no live LLM)

1. `MockLLMClient(script=[call require_approval tool, then submit])`, policy marks
   that tool `require_approval`. Assert `run()` returns `needs_approval` with a
   well-formed checkpoint + pending.
2. `resume(checkpoint, approve)` → the tool runs, loop reaches `submitted`.
3. `resume(checkpoint, reject)` → refusal observation present, block recorded,
   loop continues to its next decision.
4. `resume(checkpoint, modify_input)` → dispatched with patched args.
5. Checkpoint JSON round-trips (serialize → deserialize → resume).
6. Workflow-level: a signal-driven test (no Temporal server, the existing test
   style) drives child gate → resume loop across two pauses.

## As built (notes)

- **Signal routing.** REST `/v1/approvals/*` signals `task-{task_id}`. The parent
  runs as `task-{sdlc_id}`; the **child** now starts as
  `task-feat-{sdlc_id}-{issue_key}` (was `feat-...`) and the in-loop approval's
  `task_id` is `feat-{sdlc_id}-{issue_key}`, so a decision routes straight to the
  paused child. Changing the child id is a replay-determinism break for in-flight
  runs — safe because the feature is flag-gated and in-flight runs predate it.
- **Discriminated activity result.** `sdlc_implement` returns
  `{"status": "complete"|"needs_approval", ...}`; a new `sdlc_implement_resume`
  activity (registered in `sdlc_activity_methods`) takes the checkpoint + decision
  and returns the same shape, so one implement can pause more than once. The
  child's `_implement` helper loops over `needs_approval`, raising one approval
  per pause (id `feat-{sdlc_id}-{issue_key}-impl-{n}`).
- **`ImplementOutcome`** (in `codegen.py`) is the adapter-level discriminated
  result; `implement_governed` / `resume_implement` are on the `CodegenAdapter`
  Protocol (the stub completes immediately; resume is unreachable for it).
- **Non-workflow callers** (CLI `implement()`) have no durable pause harness, so
  they keep 2a semantics: a `require_approval` is **auto-denied** in-loop (the
  model adapts) rather than pausing. Only `implement_governed` surfaces the pause.
- **Policy source.** `SDLC_AGENTIC_POLICY=<file>` loads the policy in
  `_build_codegen`; unset = no governance (no pauses), preserving prior behavior.
- **Tests:** `tests/agentic/test_approval.py` (loop pause/resume on
  approve/reject/modify, terminal-tool approval, sibling-call ordering, JSON
  round-trip) + two `tests/sdlc/test_workflows.py` cases (two-pause resume; the
  timeout → reject-and-continue path, via the time-skipping env).

## Decisions (resolved)

1. **Risk classification** — derive from the gated tool's side-effect metadata
   where available, defaulting to `high`. Honest and reuses existing contract
   data.
2. **Timeout behavior** — **reject-and-continue.** On `_DEFAULT_APPROVAL_WAIT`
   expiry the gate resolves as a rejection: a denial observation is fed back, the
   block is recorded, the loop continues. A single gated action expiring does not
   kill the whole feature.
3. **Checkpoint size** — **full `messages` payload.** Simple and correct, bounded
   by `max_steps`; log the size and treat an over-limit checkpoint as a config
   error. Revisit (trace-only reconstruction) only if payload size bites.
