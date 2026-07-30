# Bet 2 — the trust spine (design, under review)

> The governance moat: make the agentic layer one a team that **can't** run an
> ungoverned agent (regulated, enterprise, anything touching prod) is willing to
> turn on. The differentiator is not smarter codegen — it's *control*: every
> tool call policy-checked, every run replayable and exportable, destructive
> actions gated, and (eventually) tenant- and role-scoped.

## Why this stands out

The field ships ungoverned autonomy — "let the agent run." This project already
has the bones almost nobody else does: durable execution (Temporal), a
persistent approval plane (`ApprovalRequest` + `/v1/approvals/*` + workflow
signals), an audit log (`AuditLogRepo`), and per-tool write-gating
(`ToolContract` side-effects, the MCP loop tools). Bet 2 turns those bones into
a spine the agentic loop runs *inside*.

## Building blocks that already exist

- **Approval plane** — `ApprovalRequest`/`ApprovalRequestRepo`, gate ids
  `sdlc-{id}-{n}`, REST decide endpoints, workflow signal back. Today: two
  bookend gates (intent, merge).
- **Audit** — `AuditLogRepo` rows (actor/action/resource/before/after/trace).
- **Tool governance** — `ToolContract` side-effects (READ/WRITE/DESTRUCTIVE) +
  `ApprovalPolicy`; the loop's `write_files`/MCP tools already write-gate.
- **The loop** — `AgentLoop` dispatches tools; `RecordingLLMClient` records LLM
  calls; `RunBudget` caps spend.

## Phased plan

### 2a — Policy-as-code + governed tool execution in the loop  *(highest value, achievable now)*
A declarative **policy** evaluated *before every tool call* in the loop:
- **allow / deny / require-approval** per tool (and per path for file tools, per
  server for MCP);
- **budget** ceiling (already enforced; surfaced in the policy);
- default-deny for destructive tools unless explicitly allowed.

Shape (illustrative):
```yaml
tools:
  read_file: allow
  write_files: { allow: true, paths: ["src/**", "tests/**"], else: require_approval }
  "mcp:db:*": { allow: false }   # no DB writes from the loop
budget_usd: 25
```
In the loop, a **policy decision** wraps `_dispatch`:
- `deny` → the call never runs; a refusal observation goes back to the model (it
  adapts), and an audit row records the blocked attempt.
- `require_approval` → in 2a, treated as deny-with-reason + audit (a true human
  pause is 2c); the run surfaces "N actions needed approval" at the merge gate.
- `allow` → runs as today, audited.

`PolicyVerifier`-style evaluation already exists in the verifier chain — reuse
the spirit (declarative, auditable predicates), not a new `eval()`.

**Exit:** a policy file gates the loop's tool calls; denials are observations +
audit rows; tested deterministically (no LLM).

### 2b — Run replay + audit export  *(the receipts)*
- **Record the whole run**: extend the loop to emit a structured trace (each
  step: model turn, tool calls, observations, policy decisions) alongside the
  existing LLM recording.
- **Export a run bundle**: one JSON (gates + decisions + every tool call +
  policy verdicts + cost + the capability plan) — the artifact an auditor or
  compliance reviewer reads to answer "what did it do and who approved it."
- **Replay**: re-run a recorded trace deterministically (LLM from
  `RecordingLLMClient`, tools from recorded observations) — for debugging and
  for proving a run is reproducible.

**Exit:** `orchestrator run export <sdlc_id>` (or a plugin tool) emits the
bundle; a recorded run replays without live LLM/tools.

### 2c — In-loop human approval (durable) + RBAC / multi-tenancy (G11)  *(the hard part)*
- **Mid-loop human pause.** The wrinkle: the loop runs *inside* the
  `sdlc_implement` **activity**, but human waits belong in the **workflow**
  (Temporal activities should be bounded). Approach: when a `require_approval`
  tool is hit, the loop **returns a "needs-approval" checkpoint** (state +
  pending action); the workflow raises an `ApprovalRequest`, waits on the
  signal, then **re-invokes the loop** with the decision folded in (loop
  resumption). Needs loop-state checkpointing — design carefully.
- **RBAC / multi-tenancy.** `tenant_id` scoping on runs/approvals + enforced
  `approver_roles` (today `approver_roles` is a free-form field; G11 = real
  enforcement). Schema + auth work.

**Exit:** a destructive in-loop action pauses for a real human decision; runs +
approvals are tenant-scoped and role-gated.

## Recommended sequencing

**2a → 2b → 2c.** 2a is the highest differentiation per unit of work and builds
straight on the policy/audit seams; 2b makes governance *visible* (the export is
the sales artifact); 2c is the largest and carries the Temporal + schema risk,
so it goes last with the most design care.

## Honest risks / notes

- **In-loop human pause is genuinely hard** under Temporal's activity model —
  hence deferred to 2c with an explicit resumption design, not hand-waved.
- **Policy must not become a second `eval()`** — keep it declarative predicates
  (allow/deny/scope), like the existing verifier chain.
- **Don't over-build RBAC before a buyer needs it** — 2c is gated on a real
  multi-tenant use case; 2a+2b deliver most of the "governed" story for a
  single-tenant team today.

## Decisions (locked)

1. **Scope = all of 2a → 2b → 2c.** The full governance story, including the
   hard mid-loop human pause and RBAC/multi-tenancy. Built in that order.
2. **`require_approval` in 2a = deny-with-reason.** It acts as deny + a clear
   observation + an audit row; the run surfaces "N actions needed approval" for
   the gate approver. **2c upgrades it** to a real mid-loop human pause (the
   loop-checkpoint/workflow-resumption design above).
3. *(default)* **Policy = a YAML file** (like `mcpServers` / gap-rules), loaded
   per run — consistent with the project's other no-code config; not a second
   `eval()`.
4. *(default)* **Export = both** a CLI command and a plugin tool (the bundle is
   useful to an operator *and* to a hosted client).
