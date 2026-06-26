# Core data models

The Pydantic models in `src/orchestrator/` are the authoritative spec. This document
explains the *intent* behind each model and the invariants worth knowing without
reading the code. When this document and the code disagree, the code wins —
file an issue.

Four models are defined in Sprint 1:

| Model | Module | Purpose |
|---|---|---|
| `AgentTemplate` | `orchestrator.registry.agent_template` | A versioned, registry-published spec for an agent. |
| `ToolContract` | `orchestrator.registry.tool_contract` | A versioned spec for an external capability. |
| `Claim` + `Evidence` | `orchestrator.core.claim` | A verifier-checkable analytical statement and its supporting artifacts. |
| `GraphIR` | `orchestrator.ir.graph` | The typed intermediate representation of a workflow. |

Shared building blocks (`Metadata`, `Status`, `ResourceId`, `SemVer`,
`LifecycleState`) live in `orchestrator.registry._common`.

---

## AgentTemplate

A `AgentTemplate` is the unit a planner selects from and the runtime instantiates.
Templates are **immutable once published**: any change to behaviour requires a
new semver version. This is what makes audits and replay tractable.

Structure:

- **`metadata`** — `id`, `version` (semver), `description`, `tags`, `authors`,
  `created_at`, `updated_at`. `id` must match `^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$`
  (e.g. `research.summarizer`).
- **`spec`** — what the agent does:
  - `inputs`, `outputs` — typed field schemas.
  - `allowed_tools` — tool contract IDs the agent may call.
  - `allowed_state_channels` — LangGraph state channels the agent may read/write.
  - `policies` — policy IDs the agent must obey.
  - `known_limitations` — surfaced to the planner during candidate selection.
  - `model` — model identifier (e.g. `claude-opus-4-7`).
  - `constraints` — free-form, e.g. `temperature`, `max_tokens`.
  - `evals` — registered eval references that gate publication.
- **`status`** — `state` (`draft` | `published` | `deprecated`) plus optional
  `replacement` ID.

**Mandatory output fields.** Every `AgentTemplate.spec.outputs` list must
include `confidence` and `caveats`. This is a load-bearing invariant of the
system: verifiers downstream rely on every node emitting a calibrated
confidence and an explicit list of caveats. The model enforces this at
construction time.

---

## ToolContract

A `ToolContract` describes one external capability — a web search API, a
warehouse query, a code sandbox. The MCP gateway loads contracts at startup
and exposes each as an MCP tool descriptor.

Structure:

- **`metadata`** — same shape as `AgentTemplate.metadata`.
- **`spec`** — what the tool does and how it must be handled:
  - `purpose` — one-sentence description.
  - `inputs`, `outputs` — typed schemas.
  - `side_effects` — `read` | `write` | `destructive`.
  - `idempotent` — whether repeat invocations with the same input are safe.
  - `contains_pii` — whether inputs or outputs may carry PII.
  - `data_freshness` — optional human note ("daily", "real-time").
  - `requires_approval` — `never` | `conditional` | `always`.
  - `rate_limits` — per-minute / per-day / burst caps.
  - `authentication` — `none` | `api_key` | `oauth2` | `mtls`.
  - `endpoint` — URL or service identifier (optional).
  - `observability` — `audit` and `trace` toggles.
- **`status`** — same as `AgentTemplate`.

**Three cross-field invariants** are enforced at construction:

1. **Audit is mandatory.** `observability.audit` cannot be `false`. Every tool
   call lands in the audit log, no exceptions.
2. **Non-idempotent tools must accept `idempotency_key`.** Retries during
   replan or worker failure must not double-charge, double-write, or
   double-send. Tools that aren't naturally idempotent take the key in `inputs`.
3. **Destructive tools must set `requires_approval='always'`.** No path to
   irreversible action without a human in the loop.

---

## Claim and Evidence

A `Claim` is one structured statement an agent makes — a metric value, a
qualitative finding, a comparison, a projection. Every claim carries at
least one `Evidence` reference pointing at the artifact (warehouse query
result, fetched document, executed notebook) that supports it.

This decomposition is what lets `EvidenceVerifier` spot-check outputs
deterministically: pick a claim, fetch its artifact, compare the claimed
metric to the artifact's value within tolerance.

Structure:

- `Claim.id` — `^c_[a-z0-9_]{1,64}$`.
- `Claim.statement` — natural-language version.
- `Claim.claim_type` — `metric` | `qualitative` | `comparison` | `projection`.
- `Claim.supporting_artifacts` — non-empty list of `Evidence`.
- `Claim.metric_values` — optional numeric values keyed by metric name.
- `Claim.confidence` — float in `[0, 1]`.
- `Claim.caveats` — free-form caveats from the producing agent.
- `Evidence.artifact_id` — required.
- `Evidence.locator` — optional pointer inside the artifact (cell, row, page).
- `Evidence.note` — optional human note.

`supporting_artifacts` is required and non-empty. A claim with no evidence
isn't a claim — it's a guess, and the system refuses to construct one.

---

## GraphIR

The `GraphIR` is the planner's output and the runtime's input. It is a
**typed, validated** description of the workflow: nodes, edges, approvals,
budgets, and the task glossary that pins shared definitions for the run.

Structure:

- **`metadata`** — same shape.
- **`spec`**:
  - `objective` — the original user request.
  - `workflow_pattern` — `single_agent` | `sequential` |
    `manager_specialists` | `router` | `mixture`.
  - `task_glossary` — `term -> {value, source}`. Loaded as a read-only
    state channel at task start.
  - `nodes` — list of `Node`. Each has an `id` (`^n_*`), a `type`
    (`agent` | `verifier` | `approval` | `loop_guard` | `reflection` |
    `a2a_call`), an optional `template_id`/`template_version`, and a
    free-form `config` dict.
  - `edges` — list of `Edge` with `source`, `target`, optional `condition`.
  - `approval_points` — explicit human-gate insertions.
  - `budget` — token, cost, wall-clock, replan caps.
  - `constraints` — free-form.
- **`status`** — same.

**Structural invariants enforced by the model itself**:

- Node IDs are unique.
- Every edge references known nodes.
- Approval points reference known nodes.
- No self-loop edges.
- Node list is non-empty.

**Deeper validation lives elsewhere.** Reachability, DAG-ness, budget
sanity, schema compatibility between connected nodes, policy compliance,
reference resolution against the registry — all belong in the IR validator
service (Sprint 6, Task 6.2). Keeping those out of the Pydantic model lets
the model stay a pure data definition and lets the validator return a
structured `ValidationReport` rather than a single exception.
