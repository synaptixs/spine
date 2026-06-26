# Design: live observability (OpenTelemetry over the existing trace_id)

**Status:** **Phases 1–3 implemented.** `orchestrator/obs/tracing.py` (the no-op-by-default
seam: `span` / `traced` / `bind_trace_id` / `add_event` / `temporal_interceptors`). Phase 1:
`RecordingLLMClient` emits an `llm.complete` span per call → every LLM leg (intake / codegen /
review / consolidation) traced through one chokepoint. Phase 2: the agentic loop
(`agentic/loop.py`) emits an `agent.step` span per model turn with `llm.complete` and
`tool.<name>` spans nested under it, plus `policy_block` / `needs_approval` span events. Phase
3: `tracing.temporal_interceptors()` (Temporal OTel `TracingInterceptor`) wired onto the
client (`temporal/config.py`) and worker (`temporal/worker.py`), and `execute_graph_pass`
binds the app `trace_id` + opens its own span — so one trace spans API → workflow → activities
→ agentic loop → LLM/tool calls, propagated over W3C trace context. Optional `otel` extra
(`opentelemetry-sdk` + OTLP/HTTP exporter); tests use an in-memory exporter. Activates only
when
`OTEL_EXPORTER_OTLP_ENDPOINT` is set (unset = silent no-op, zero overhead). Builds on
shipped seams: `trace_id` already generated in `registry/api/deps.py` (`request_trace_id`),
persisted + indexed on `AuditLogRow` / `TaskRow` (`registry/db/models.py`), and propagated
through Temporal (`temporal/config.py`); per-call token/cost/latency already computed in
`RecordingLLMClient` (`core/llm/recording.py`); per-step records already in
`LoopResult.trace` (`agentic/loop.py`).
**Goal:** a **live** view over runs — span trees for on-call debugging and latency
analysis — joined to the durable audit log by the **existing** `trace_id`. The audit log
stays the forensic record; OTel is the real-time lens over the same events.

## Reframe
The "0% observability" read is misleading. `trace_id` is **already threaded end to end** —
API request → workflow → activities, persisted on audit + task rows. You have the
correlation key. What's missing is (a) the **span tree** that turns flat correlated events
into a navigable timeline, and (b) a **live sink** to watch it as it happens. This spec adds
one tracing seam and instruments three layers over it — no new identifiers, no rewrite.

> Dual-sink, not replacement. The audit log answers "what happened, for compliance"
> (append-only, permanent). OTel answers "what's happening / what broke, live" (sampled,
> TTL'd). Both fire from the same code points and **join on `trace_id`**.

## Locked decisions
1. **No-op when unconfigured.** No `OTEL_EXPORTER_OTLP_ENDPOINT` → a no-op tracer. Default
   path pays nothing; safe to merge dark.
2. **Reuse `trace_id`, don't mint a new one.** The existing `trace_id` seeds the root span
   (as the trace attribute / baggage) so spans and `AuditLogRow`s join on one key.
3. **Keep the audit log.** OTel is additive. The forensic record + replay path
   (`build_run_bundle`, `replay_llm_from_trace`) are unchanged.
4. **Instrument at chokepoints, not call sites.** One insertion in `RecordingLLMClient`
   traces *every* LLM call across the whole pipeline. Favor seams already wrapping the work.

## Architecture (new `obs/tracing.py` + three instrumented layers)
`obs/tracing.py` — thin wrapper over `opentelemetry-sdk`:
- reads `OTEL_EXPORTER_OTLP_ENDPOINT`; absent → no-op tracer;
- seeds the root span from the existing `trace_id` (baggage), so spans correlate to audit
  rows;
- exposes one decorator `@traced(name)` and one context manager `span(name, **attrs)`.

### Dual-sink: same events, two destinations
| | Audit log (`AuditLogRow`) | OTel spans |
|---|---|---|
| Question | "what happened, for compliance" | "what's happening / what broke, live" |
| Durability | append-only Postgres, permanent | sampled, TTL'd in the collector |
| Join key | `trace_id` | same `trace_id` (baggage) |
| Audience | auditor, replay | on-call, latency debugging |

### Layer 1 — the agentic loop (highest value: the 3am case)
In `_drive` (`agentic/loop.py`), wrap each iteration. Every attribute below already exists
on `CompletionResult` and the `StepRecord.calls` dicts — emit them as span attributes
*in addition to* appending to `trace`:

```
span "agent.step" {step}
  span "llm.complete"  → model, prompt_tokens, completion_tokens, cost_usd, latency_ms
  for each tool call:
    span "tool.<name>" → args_digest, blocked, policy_action, observation_len
  event "no_progress" / "max_steps" / "needs_approval"   ← stop reasons as span events
```
The `StepRecord` trace and the span tree become the same data in two sinks: one durable
(export/replay), one live (OTel).

### Layer 2 — the LLM client (most coverage, least code)
Emit the `llm.complete` span inside `RecordingLLMClient.complete()` — it already intercepts
every call and computes tokens/cost/latency, attributed to the active `stage(...)`. One
insertion → **every** LLM call (intake, codegen, review, memory consolidation) traced for
free.

### Layer 3 — Temporal activities (the cross-process trace) ✅ live-verified
`tracing.temporal_interceptors()` returns Temporal's first-class **OTel `TracingInterceptor`**
bound to our tracer (empty list — a true no-op — when tracing is off), wired onto the
**client only** (`temporal/config.py: connect_client`). The worker applies its client's
interceptors automatically, so registering it again on the `Worker` would **double every
span** — a bug caught on the first live run; `temporal/worker.py` deliberately does *not* add
it. The interceptor auto-creates workflow/activity spans and propagates context over W3C trace
context across the process boundary, so the Phase 1/2 spans (which read the global current
span) nest under their activity automatically. `execute_graph_pass` additionally
`bind_trace_id`s the app `trace_id` and opens an `execute_graph_pass` span tagged with
`replan_count`, so the agentic spans beneath it carry the audit-log join key. Result: one
trace spans API request → workflow → activities → agentic loop → individual LLM/tool calls.

> **Sandbox caveat — resolved.** The interceptor's workflow-side half runs inside Temporal's
> deterministic sandbox. Verified live (`OrchestratorWorkflow` against the local docker
> Temporal, tracing on): the workflow completed with no sandbox import error, and one clean
> 11-span trace (`StartWorkflow → RunWorkflow → Start/RunActivity ×4 → CompleteWorkflow`)
> landed in Jaeger. `temporalio.contrib.opentelemetry` is sandbox-aware out of the box; no
> passthrough config was needed.

## The join in practice
See a slow/weird span in the tracing UI → its `trace_id` pulls the exact `AuditLogRow`s and
the run bundle for forensic detail. That is the replay path the original gap analysis asked
for, now reachable **live** instead of only post-hoc.

## Phasing
- **Phase 1 — `obs/tracing.py` + Layer 2.** ✅ **Done.** Instrumented `RecordingLLMClient`;
  every LLM call traced. Immediately useful (cost/latency per stage, live).
- **Phase 2 — Layer 1.** ✅ **Done.** `agent.step` spans + nested `tool.<name>` spans +
  `agent.stopped` attribute + `policy_block`/`needs_approval` span events.
- **Phase 3 — Layer 3.** ✅ **Done.** Temporal OTel interceptor on client + worker
  (`temporal_interceptors()`); `execute_graph_pass` binds the app `trace_id` + spans the pass.
- **Local dev:** ✅ a `jaeger` service in `docker-compose.dev.yml` (all-in-one, with its
  bundled OTLP receiver doubling as the collector — UI :16686, OTLP :4317/:4318). `uv sync
  --extra otel` + `export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318` and traces appear
  with no code change (verified end-to-end: `agent.step`/`tool.<name>` spans land in Jaeger
  with the `trace_id` join key). See SETUP.md → "Live tracing".

## Flag & dependencies
- `OTEL_EXPORTER_OTLP_ENDPOINT` unset = no-op everywhere (safe default).
- Deps: `opentelemetry-sdk`, `opentelemetry-exporter-otlp`, Temporal's OTel interceptor
  (`temporalio` already present). **LangSmith** is additive later as a second exporter — the
  OTLP layer makes it a config change, not a rewrite.

## Tests
- No endpoint → tracer is no-op, no spans emitted, zero behavior change (assert via a
  recording exporter that nothing is recorded).
- With an in-memory span exporter: a loop run produces the expected `agent.step` →
  `llm.complete` / `tool.<name>` span tree with the right attributes; stop reasons appear as
  events.
- `trace_id` on emitted spans equals the `trace_id` written to the matching `AuditLogRow`
  (the join holds).

## Ties to memory
Better-instrumented loop steps give the [consolidation loop](cross-run-semantic-memory.md)
cleaner episode boundaries to summarize from, and a memory's `evidence.trace_steps` can
deep-link to the span for the step that produced it — closing the audit loop on memory
itself.
