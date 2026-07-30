# Blueprint: Tracking the SDLC — tokens, cost, and impact per unit of work

**Goal.** Make every unit of work Spine delivers — a **feature, a bug fix, or an
enhancement**, in a **greenfield or brownfield** repo — answerable to three questions:

1. **What did it cost?** tokens in/out, dollars, wall-clock, human-gate time.
2. **How efficiently was it produced?** first-pass yield, refine loops, rework, grounding leverage.
3. **Was it worth it?** cost vs value delivered (lines/tests/PR merged), and cost *trend* over time.

This is not a greenfield build — Spine already has the hard parts (a per-stage token ledger,
per-call OTel spans, per-run budget enforcement). The blueprint's job is to (a) **attribute**
that telemetry to a durable *work item*, (b) **persist** it so it's queryable over time, and
(c) **surface** it as decisions: budgets, dashboards, and a per-feature cost line.

---

## 1. Principles

- **One unit of work = one trackable, durable record.** Everything rolls up to a *work item*
  (the Jira/issue key, or a synthetic id in safe mode). Tokens are meaningless unattributed.
- **Measure at the chokepoint, attribute by context.** Every LLM call already funnels through
  `RecordingLLMClient.complete` ([recording.py](../../src/orchestrator/core/llm/recording.py)) —
  instrument once there, tag with the active *stage* + *work item* + *dimensions*, never per call site.
- **Three planes, one schema.** *Live* (OTel spans, for ops), *per-run* (the ledger, for the
  developer who just ran it), *historical* (a persisted store, for trends/budgets/ROI) — all use the
  same field names so a number means the same thing everywhere.
- **Deterministic cost is free; estimate the rest.** LLM dollars come straight from LiteLLM
  (`litellm.completion_cost`). PKG extraction, builds, and test runs are *compute* — track wall-clock
  and call counts; price them with a simple rate card if you need dollars.
- **Greenfield vs brownfield is a first-class dimension, not an afterthought** — their cost shapes
  differ (see §5), and the comparison is one of the most useful things this unlocks.

---

## 2. The unit of work and its dimensions

Every record is keyed by a **work item** and carries the dimensions you'll slice by:

| Field | Source | Why it matters |
|---|---|---|
| `work_item_id` | `issue_key` (Jira) / `intent_id` (safe mode) | the durable key everything rolls up to |
| `work_type` | **feature \| bug \| enhancement** (from intent classification) | "what does a bug *actually* cost vs a feature?" |
| `mode` | **greenfield \| brownfield** (from `layout.mode`: `new`/`auto` vs `existing`) | the headline comparison |
| `language` | `layout.language` | $/feature by language; where the model struggles |
| `repo` | `repo` / `SDLC_REPO_URL` | per-team / per-service cost attribution |
| `model(s)` | `StageUsage.models` | model mix; price/perf of model choices |
| `persona` / `skills` | capability plan | does a skill pay for itself? |
| `run_id` | autonomous run id (if part of a batch) | batch-level rollups |
| `outcome` | `passed`, `live`, `pr_url`, merged? | cost **per merged** unit, not per attempt |

> **Action:** `work_type` and `mode` are the two new tags that turn raw telemetry into the
> answer the user asked for. `mode` is already derivable from `TargetLayout`; `work_type`
> should be lifted from intent classification (intake already derives intents — classify each
> as feature/bug/enhancement and carry it on `Intent`).

---

## 3. What to measure — the metric model

For each work item, accumulate **per stage** and roll up to a **total** (the ledger already
does exactly this — `StageUsage` → `TokenLedger.total()`):

**LLM (deterministic, already captured):** `calls`, `prompt_tokens`, `completion_tokens`,
`total_tokens`, `cost_usd`, `latency_ms`, `models[]`.

**Process (add):**
- `iterations` — implement→test→refine loops (already on `FeatureRunResult`). **The single best
  efficiency signal.** `iterations == 1` = first-pass success.
- `grounding_chars` — size of PKG context fed to codegen (already on `FeatureRunResult`). The
  brownfield "leverage" input — correlate against iterations.
- `pkg_extract_ms`, `pkg_nodes`, `pkg_edges` — comprehension cost (brownfield only).
- `build_ms`, `test_ms`, `test_runs` — compute spend in the verify loop.
- `files_changed`, `lines_added/removed`, `blast_radius` — size/impact of the output
  (`blast_radius` already exists in [activities.py](../../src/orchestrator/sdlc/activities.py)).
- `human_gate_wait_ms` — wall-clock parked at the `intents` / `merge` gates (autonomous run).
- `outcome` — passed, PR opened, **merged** (the value denominator).

**Derived (compute on read, §9):** `$/work_item`, `$/merged_work_item`, `tokens/line`,
`first_pass_yield`, `refine_rate`, `grounding_leverage`, `human_gate_latency`, `$/work_type/mode`.

---

## 4. The SDLC funnel — stages to instrument

Spine already names these stages via `RecordingLLMClient.stage(...)`; the ledger keys on them.
Keep the stage taxonomy stable so cost-by-phase is comparable across runs:

```
intake          → ingest source, derive + classify intents (feature/bug/enh), gap-check
spec_writing    → intent → acceptance-criteria-bearing spec
grounding        → PKG extract (brownfield) + retrieve context     [compute + 0 LLM]
implement       → grounded codegen (the big LLM spend)
author_tests    → test generation
run_tests        → build + test                                     [compute, 0 LLM]
refine          → fix loop (repeats; count it)                      [LLM, the rework cost]
review          → auditor persona / PR review
gate:intents    → human decision                                    [wall-clock only]
gate:merge      → human decision                                    [wall-clock only]
deliver          → branch / push / PR / Jira update
```

Per-stage cost answers *"where does the money go?"* — typically `implement` + `refine`
dominate; a high `refine` share is the strongest "spec was underspecified / model is weak here"
signal.

---

## 5. Greenfield vs brownfield — why the split matters

| | Greenfield (`layout=new`) | Brownfield (`layout=existing`) |
|---|---|---|
| Dominant cost | `implement` (cold generation) | `grounding` (extract+retrieve) + `implement` conditioned on context |
| `grounding_chars` | ~0 | high — and **should reduce `refine` loops** |
| Extra compute | scaffold (cheap) | **PKG extraction** (one-time per commit, cached) |
| Failure mode | wrong assumptions → refine | mis-grounding → wrong reuse → refine |
| The key question | "what does net-new cost?" | "**does grounding pay for itself?**" — i.e. does more `grounding_chars` buy fewer `iterations`? |

The blueprint's flagship insight: **plot `grounding_chars` (x) against `iterations` and
`$/work_item` (y), split by `mode`.** If brownfield grounding is working, brownfield features
should converge in *fewer* iterations than the same-sized greenfield feature despite more input
tokens — quantifying the core thesis that "understanding the repo makes codegen cheaper and better."

---

## 6. Attribution model

```
LLM call ──(chokepoint: RecordingLLMClient.complete)──► tagged with current_stage
   │                                                     + work_item_id + dimensions
   ▼
StageUsage (per stage)  ──►  TokenLedger (per work item)  ──►  persisted CostRecord
   │                                                              │
   └── live: OTel span llm.complete {stage, model, tokens, cost} ─┘ (correlated by trace_id→run)
```

Two changes make attribution durable:
1. **Carry `work_item_id` + dimensions on the recording context** (today the span has `llm.stage`
   + `llm.model` only — add `work.id`, `work.type`, `work.mode`, `repo`, `language`). One edit in
   `RecordingLLMClient.complete` + a contextvar set at run start.
2. **Return + persist the ledger.** `FeatureRunResult` carries `iterations`/`grounding_chars` but
   **not the ledger** — add `ledger: TokenLedger` (or a flattened cost summary) to it, so the CLI,
   the MCP `sdlc_feature` result, and the persistence layer all see the cost of *this* work item.

---

## 7. What exists today vs the gaps

**Already shipped (reuse, don't rebuild):**
- Per-stage ledger with tokens/cost/latency/models — [recording.py](../../src/orchestrator/core/llm/recording.py).
- Per-call OTel spans (`llm.complete`) with token/cost attributes — exported when `OTEL_EXPORTER_OTLP_ENDPOINT` is set (the `otel` extra).
- Per-run budget cap + enforcement — `RunBudget` / `BudgetedLLMClient`, `SDLC_RUN_BUDGET_USD` (default $25) — [budget.py](../../src/orchestrator/core/llm/budget.py), wired in [worker.py](../../src/orchestrator/sdlc/worker.py).
- Ledger → HTML audit table — [intake/report.py](../../src/orchestrator/intake/report.py).
- Cost in the **evals** harness (mean/total $) — [evals/models.py](../../src/orchestrator/evals/models.py).
- `iterations`, `grounding_chars`, `blast_radius` signals.

**Gaps to close (this blueprint):**
1. **No work-item attribution** on spans/ledger (stage only) → can't group by feature/type/mode.
2. **Ledger is ephemeral** — not returned on `FeatureRunResult`, not persisted → no history, no trends, no $/feature.
3. **No `work_type`** (feature/bug/enhancement) classification carried through.
4. **Compute spend (PKG/build/test) untracked** as cost — only LLM dollars exist.
5. **No aggregation surface** — no `orchestrator cost` CLI, no cost dashboard, no PR cost comment.

---

## 8. Storage architecture

Three planes, one schema (field names from §3):

- **Live (ops):** OTel spans → your collector (Honeycomb/Grafana Tempo/Datadog). Already wired;
  just enrich span attributes (§6.1). Gives real-time traces + per-stage flamegraphs.
- **Per-run (developer):** the in-memory `TokenLedger`, returned on `FeatureRunResult` and printed
  by the CLI / returned by the MCP tool. Zero infra.
- **Historical (analytics/budgets/ROI):** a persisted **`work_item_cost`** table (one row per work
  item) + a **`stage_cost`** child table (one row per stage). Postgres in Mode B; a local SQLite/JSONL
  ledger file in Mode A (CLI) so cost history works without the full stack. Schema sketch:

```
work_item_cost(
  work_item_id PK, run_id, work_type, mode, language, repo, model_mix,
  total_prompt_tokens, total_completion_tokens, total_cost_usd, total_latency_ms,
  iterations, grounding_chars, pkg_nodes, build_ms, test_ms,
  files_changed, lines_added, lines_removed, blast_radius,
  human_gate_wait_ms, outcome, pr_url, merged_at, created_at )

stage_cost( work_item_id FK, stage, calls, prompt_tokens, completion_tokens,
            cost_usd, latency_ms, models )
```

This is the queryable backbone: every KPI in §9 is a `GROUP BY` over these two tables.

---

## 9. Impactful KPIs — the numbers that drive decisions

Group everything by `work_type` × `mode` (and optionally language/repo/model). The high-signal set:

| KPI | Formula | Decision it drives |
|---|---|---|
| **Cost per merged unit** | `Σ cost_usd / count(merged)` | the headline ROI number; budget setting |
| **First-pass yield** | `count(iterations==1) / count` | spec quality + model fit; where to invest in better prompts |
| **Refine tax** | `Σ refine.cost_usd / Σ total cost` | how much you pay for rework; underspecified specs |
| **Grounding leverage** (brownfield) | corr(`grounding_chars`, −`iterations`) | does comprehension pay for itself? (the thesis) |
| **Token efficiency** | `total_tokens / lines_added` | bloat / prompt waste; model choice |
| **$ / work_type** | `Σ cost by feature/bug/enh` | "bugs are cheap, enhancements are dear" → planning |
| **Greenfield vs brownfield Δ** | `$/unit(green)` vs `$/unit(brown)` | where Spine is most/least cost-effective |
| **Human-gate latency** | `mean(human_gate_wait_ms)` | the *non*-AI bottleneck; process, not model |
| **Budget adherence** | `count(BudgetExceeded) / runs` | are caps right? runaway detection |
| **Cost trend** | `$/unit` over time (weekly) | is the platform getting cheaper as models/grounding improve? |
| **Cost vs human baseline** | `$/unit` vs estimated eng-hours×rate | the adoption / business case |

Two flagship visuals: (1) **stacked cost-by-stage bar** per work item (where the money goes),
(2) **grounding_chars vs iterations scatter, colored by mode** (does brownfield grounding work).

---

## 10. Surfacing — where the numbers show up

- **Inline, every run (Mode A & the MCP tool):** add a one-line cost summary to the `sdlc_feature`
  result + CLI: `feature DRY-1 · brownfield · python · $0.42 · 18.3k tok · 1 iter · 6.1k grounding`.
  This is the single highest-adoption change — developers *see* the cost immediately.
- **PR comment (live):** the reviewer bot posts the cost card on the PR it opens (cost, tokens,
  iterations, stage breakdown) — cost becomes part of the review artifact.
- **`orchestrator cost` CLI:** `orchestrator cost --by work_type --mode brownfield --since 30d` →
  the §9 table from the persisted store. Works in Mode A off the local ledger file.
- **Dashboard (Mode B / OTel):** Grafana/Honeycomb boards for the live + historical planes — the
  two flagship visuals, plus trend + budget-burn.
- **Run summary (autonomous):** `sdlc_run_result` already returns per-run data; add the rolled-up
  batch cost + per-feature breakdown.

---

## 11. Implementation roadmap (incremental, each phase shippable)

**Phase 0 — exists.** Per-stage ledger, per-call OTel spans, per-run budget cap.

**Phase 1 — make cost visible per work item (small, high-impact).**
- Add `work_type` to intent classification; thread `work_item_id` + dimensions onto a contextvar at
  run start; enrich the `llm.complete` span attributes (§6.1).
- Return the ledger (or a flattened cost summary) on `FeatureRunResult`; print the one-line summary
  in the CLI and include it in the MCP `sdlc_feature` result.
- *Outcome:* every run tells you its cost, attributed and dimensioned. No new infra.

**Phase 2 — persist (history & trends).**
- Write a `work_item_cost` + `stage_cost` record at run end — Postgres (Mode B) or a local
  JSONL/SQLite ledger (Mode A). Backfill `iterations`/`grounding_chars`/`blast_radius`.
- *Outcome:* queryable history; the §9 KPIs become possible.

**Phase 3 — aggregate & surface.**
- `orchestrator cost` CLI (group-by/since), the PR cost comment, and the two flagship dashboards.
- *Outcome:* the team can answer "$/feature by mode" and "is grounding paying off" on demand.

**Phase 4 — govern & optimize.**
- Per-`work_type`/per-repo budgets + alerts; anomaly detection on `$/unit` and `refine tax`;
  the cost-vs-human-baseline ROI view; feed `grounding_leverage` back into model/grounding choices.
- *Outcome:* tracking closes the loop — it changes how features get built and budgeted.

**Compute-cost (PKG/build/test) tracking** can land alongside Phase 2 (wrap the extract/build/test
calls with timers, price with a rate card) — defer dollars until LLM cost is fully wired.

---

## 12. Complete automation — triggering the SDLC without a human kickoff

Tracking and automation are the same coin: **you can't safely automate what you don't measure.**
The cost/impact signals above are simultaneously the *reporting* output **and** the *control input*
that lets the loop run unattended. Spine already runs end-to-end from one call (`run_feature`) and
as a gated batch (`sdlc run` over Temporal with `run_control`); "complete automation" removes the
human *kickoff*, and — for low-risk work — the human *gates* too, driven by events and governed by
policy.

### 12.1 The trigger surface (events → work)

A thin **trigger layer** normalizes any event into `(source_uri, work_type, repo, dimensions)` and
enqueues an autonomous run. Everything collapses to the same core: **event → intent → autonomous
run**. Sources, in rough order of value:

| Trigger | Event | Spine hook to build on |
|---|---|---|
| **Issue-tracker label** | a Jira issue enters `Ready for Spine` / gets a `spine:auto` label | Jira webhook → issue→intent → `run_feature` |
| **GitHub issue / comment** | issue labeled `spine:auto`, or a `/spine implement` comment | **GitHub App webhook — already exists for PR review** — add an `issues`/`issue_comment` handler |
| **Confluence publish** | a spec page published/updated under a watched space | Confluence webhook → `confluence://<id>` → ingest → run |
| **CI failure → auto-fix** | a red build/test on `main` | CI webhook → synthesize a **bug** intent from the failure → fix PR |
| **Monitoring / alert** | a Sentry/PagerDuty error signature crosses a threshold | alert webhook → **bug** intent (repro from the stack) → fix PR |
| **Security / deps** | Dependabot / CVE advisory | webhook → **enhancement/bug** intent → upgrade PR |
| **Scheduled sweep** | nightly / cron | Temporal cron (or a schedule routine) → ingest the source → run all `Ready` intents |
| **Chat** | Slack `/spine <ticket>` | slash command → `run_feature` |

The intent classifier (Phase 1) tags `work_type`; the source + repo supply the remaining dimensions —
so automation and tracking share one normalization step.

### 12.2 Architecture (reuses what's shipped)

```
event ──► Trigger adapter ──► normalize → (source_uri, work_type, repo)
                                   │
                          Admission policy  (repo+label allowlist · rate limit ·
                                   │          budget headroom · dedup/idempotency)
                                   ▼
                          enqueue ──► Temporal autonomous run  (sdlc run / run_control)
                                   │
                       ┌───────────┴────────────┐
                  gate:intents              gate:merge
                       │  (auto OR human — §12.3)   │
                       ▼                            ▼
              grounded codegen → test/refine → PR ──► [auto-merge OR human]
```

The pieces already exist: the **GitHub App webhook** (PR reviewer) is the template for an
`issues`/`issue_comment` handler; **Temporal** runs the gated workflow; **`run_control`**
(`start_run`/`run_status`/`decide_gate`/`run_result`) is the programmatic seam — an *auto* decision
is just `decide_gate(...)` called by policy instead of a human; **source adapters** already turn
Confluence/Jira/Notion/file into intents.

### 12.3 Graduated autonomy — where the tracking pays off

Not all-or-nothing. Each gate becomes a **policy decision** whose inputs are exactly the §3/§9
metrics — reporting and control, unified:

```
auto_approve(work_item)  IF
   work_type ∈ allowlist                 (e.g. bug, small enhancement; not "feature" yet)
   AND tests_pass AND iterations ≤ 2      (first/second-pass success)
   AND (mode == greenfield OR grounding_chars > 0)   (brownfield actually understood the repo)
   AND blast_radius ≤ R                   (small, contained change)
   AND cost_usd ≤ budget_for(work_type)   (within the per-type cap)
   AND changed_paths ⊆ allowed_paths      (no migrations / auth / secrets / infra / CI)
ELSE escalate_to_human(reason)
```

Ratchet up one **tier** at a time, promoted only when the *tracked* numbers justify it:

- **Tier 0 — Observe.** Auto-run in **safe mode** only (branch + diff); a human opens every PR. Zero
  risk; this is what *builds* the cost/quality dataset that licenses the next tier.
- **Tier 1 — Auto-PR.** Auto-open the PR (today's `live=true`, behind policy); a human merges.
- **Tier 2 — Auto-merge** for green, small, in-budget, allow-listed work; humans review only escalations.
- **Tier 3 — Auto-deploy** behind feature flags + canary, with auto-rollback on the *same* monitoring
  signal that can trigger a fix — the loop fully closed.

First-pass-yield, refine-tax, and escaped-defect rate (all from §9) are the **promotion criteria**
between tiers — tracking literally decides how much autonomy is safe.

### 12.4 Safety rails (non-negotiable for unattended runs)

- **Budget** — `SDLC_RUN_BUDGET_USD` per run (exists) + per-`work_type` caps (§9) + a daily/global
  ceiling; halt on `BudgetExceededError`.
- **Concurrency** — `max_parallel` (exists) + a global in-flight cap per repo.
- **Idempotency / dedup** — one run per `(work_item_id, content_hash)`; a label/state machine so a
  re-fired webhook can't double-deliver.
- **Allowlist** — repos, branches, labels, and changed-path globs auto-mode may touch (deny
  migrations/auth/secrets/CI by default).
- **Kill switch** — a global `SPINE_AUTOPILOT=off` + per-repo disable; in-flight runs drain.
- **Audit + replay** — every auto-decision recorded through the trust-spine policy/export/replay, so
  an auto-merge is as accountable as a human one.
- **Anomaly halt** — the §9 anomaly detector (cost / refine spikes) *pauses autopilot*, not just alerts.

### 12.5 Automation roadmap (layers on §11)

- **A0 — exists:** one-call `run_feature`; gated Temporal batch with programmatic `run_control`.
- **A1 — Trigger adapter + GitHub issues/label webhook:** label `spine:auto` → safe-mode run → PR.
  Reuses the PR-reviewer webhook plumbing. **Tier 0/1.**
- **A2 — Admission policy + auto-gate:** the §12.3 predicate calling `decide_gate` automatically;
  allowlist + dedup + budgets. **Tier 2 for low-risk classes.**
- **A3 — More trigger sources:** CI-failure / monitoring / Dependabot → bug & upgrade intents;
  scheduled backlog sweep.
- **A4 — Tier 3:** flagged auto-deploy + auto-rollback wired to the monitoring trigger.

### 12.6 The end state

A teammate labels a Jira ticket `spine:auto`. Spine ingests it, classifies it a **bug**, grounds
against the brownfield repo, generates + tests a fix, and — because it's green, small, in-budget, and
on the allowlist — **auto-opens (Tier 2: auto-merges) a PR**, logging `bug · brownfield · $0.31 ·
1 iter` to the cost store. A human is paged only when policy escalates. Cost-per-merged-bug trends on
the dashboard; budgets enforce themselves; an anomaly pauses autopilot. **Events in, reviewed PRs
out — every one metered and governed.**

---

## 13. One-line takeaway

Spine already *meters* the SDLC (stage ledger + OTel + budgets). Turn metering into **management** by
attributing every token to a typed work item (feature/bug/enhancement × greenfield/brownfield),
persisting it, and surfacing `$/merged-unit` + `does-grounding-pay-off` — first as a one-line per-run
summary (Phase 1), then history, dashboards, and budgets. Those same metrics are the **control inputs
for automation**: events trigger runs, and tracked cost/quality thresholds decide — gate by gate, tier
by tier — how much of the loop runs unattended.
