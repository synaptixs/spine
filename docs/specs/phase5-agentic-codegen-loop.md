# Phase 5 — skills executed in an agentic (ReAct) loop

> Status: proposed (design under review). The hinge phase of the
> [catalog-then-compose roadmap](catalog-then-compose-roadmap.md): it turns the
> catalog's skills, the PKG, and the (recorded-but-unconsumed) MCP onboarding
> into things the agent actually *uses* mid-task.

## Goal

Replace today's single-shot codegen — one `complete()` call → parse `{"files":...}`
— with a **think → act → observe loop**. The agent pulls the context it needs
mid-task (query the PKG, read a file, run the tests, call a governed tool)
instead of being handed one static grounding block and getting one attempt.

In scope:
- A tool-calling extension to the `LLMClient` seam.
- A bounded, budgeted, governed loop controller.
- A first set of in-loop tools: PKG queries, file read, file write/edit, run-tests.
- Codegen (implement / refine) driven by the loop.
- Deterministic record/replay for tests.

Out of scope (later sub-phases / Phase 6): review driven by the loop;
MCP tools in the loop; skill-conditioned tool exposure; adaptive planning.

## Why this is the hinge

Everything built in Phases 0–4 gets a consumer here:
- **Catalog skills** (Phases 0–2) currently only swap prompt blocks. In a loop,
  a selected skill can expose extra tools / change the system prompt and so
  genuinely change behavior.
- **MCP onboarding** was deliberately narrowed in PR #41 to "record + surface"
  because codegen had no way to call tools. The loop is that consumer.
- **The PKG** stops being a static text block stapled to the prompt and becomes
  a *queryable tool* — which is also what makes the data-layer edges
  (`REFERENCES`, code→entity `READS`/`WRITES`) worth building.

## The core change

### 1. Tool-calling on the `LLMClient` seam

Today (`core/llm/client.py`):
```python
async def complete(self, messages, *, model, response_format=None,
                   json_object=False, temperature=None, max_tokens=None) -> CompletionResult
```
`CompletionResult` carries only `text`. Extend minimally — additive, so existing
single-shot callers are untouched:
- add an optional `tools: list[ToolSpec] | None = None` parameter,
- add `tool_calls: list[ToolCall]` (name + arguments + id) to `CompletionResult`
  (empty for non-tool calls).

LiteLLM already maps OpenAI-style `tools` / `tool_calls` across providers, so
`LiteLLMClient` gains a thin translation; `MockLLMClient` / `RecordingLLMClient`
gain tool-call fixtures (see Testing). Weak/local models that don't support tool
calling fall back to the current single-shot path (capability-detected, not
assumed).

### 2. The loop controller

A new `orchestrator/agentic/loop.py` — `AgentLoop`:
```
loop(system, task, tools, *, max_steps, budget, on_step) -> LoopResult
```
- **think**: `llm.complete(messages, tools=tools, ...)`.
- **act**: for each returned `tool_call`, dispatch to the matching tool, append
  the observation as a tool-result message.
- **observe → repeat** until the model emits a final answer (no tool calls) or a
  terminal tool is called (e.g. `submit_changes` carrying the same
  `{"files":[...]}` schema `_apply` already validates).
- **bounds**: hard `max_steps` cap; `RunBudget` checked each step (a tripped
  budget ends the loop with the same `BudgetExceededError` + audit row used
  today); a no-progress detector (repeated identical tool calls) ends early.

### 3. The in-loop tool surface

Each tool is a small adapter over an existing seam — **no new capability**, just
made callable mid-task:

| tool | backed by | side-effect |
|---|---|---|
| `pkg_relevant_symbols(query)` | `GroundedRetriever.relevant_symbols` | read |
| `pkg_api_surface(query)` | `GroundedRetriever.api_surface` | read |
| `pkg_callers_of(symbol)` | `FactStore.callers_of` | read |
| `read_file(path)` | worktree read (sandboxed to root) | read |
| `run_tests()` | `SubprocessTestRunner` | read (no external) |
| `write_files(files)` | the existing `_apply` path (stdlib-shadow + brownfield + path guards intact) | **write** |
| `mcp:<server>:<tool>` (sub-phase) | `HandlerRegistry` → `ToolHandler` | per contract |

All write/destructive tools route through the **same governance the gateway
uses** — `ToolHandler` + `InvocationContext`, side-effect classification, and
the approval policy. The loop does not get a privileged path around the guards.

## Governance inside the loop

- File writes keep every current guard (`_safe_target`, stdlib-shadow,
  brownfield create-only, size caps) — `write_files` *is* today's `_apply`.
- MCP / destructive tools go through `HandlerRegistry` so write-gating + audit +
  approval policy apply per call, exactly as `/v1/tools/.../invoke` does.
- Every tool call is audited (tool, args digest, outcome) so a run is
  explainable — same bar we hold the gates to.

## Integration points

- `LLMCodegenAdapter.implement` / `refine` become loop-driven: build the tool
  set (PKG grounder for this worktree + read/write/test), run `AgentLoop`, and
  the terminal `submit_changes` feeds the unchanged `_apply`/test/refine
  machinery. `author_tests` can stay single-shot initially.
- The activity wrapper (`sdlc_implement`) keeps its `_budget_scope`; the loop
  charges the same `RunBudget`.
- Skill-conditioning (later): the capability plan's `skills` decide which tools
  / system-prompt fragments the loop is given.

## Phased PR breakdown

- **5a — seam + read-only loop**: `LLMClient` tool-calling extension (+ LiteLLM /
  Mock / Recording), `AgentLoop` with `max_steps` + budget, the read-only PKG +
  `read_file` tools, behind a flag; codegen still emits via the existing path.
- **5b — write/test in the loop**: `write_files` (over `_apply`) + `run_tests`
  as terminal/observe tools; `implement` runs loop-driven end to end; governance
  + audit wired; live e2e on a worktree.
- **5c — MCP + skills**: governed MCP tools exposed in the loop (closes the
  Phase-4 narrowing); skill-conditioned tool/prompt exposure.
- **5d — review in the loop**: the reviewer queries the PKG / reads files before
  ruling.

Each sub-PR is independently shippable and flag-gated; the single-shot path
stays the default until a sub-phase proves out live.

## Testing / determinism

- `MockLLMClient` gains **scripted tool-call fixtures** (a sequence of
  tool_calls → final answer) keyed by prompt fingerprint, so loop tests are
  fully deterministic and offline.
- `RecordingLLMClient` records real tool-call traces for replay.
- Unit tests: step cap, budget trip mid-loop, no-progress termination, a
  guard-rejected `write_files` surfaced back into the loop, terminal submit →
  `_apply`.
- A live e2e mirrors the Phase-4 one: a real loop run on a worktree producing a
  passing feature.

## Risks & mitigations

| Risk | Mitigation |
|---|---|
| Runaway / looping agent | hard `max_steps`, no-progress detector, `RunBudget` per step |
| Cost blowup | budget enforced inside the loop, not just per call; step cap |
| Nondeterministic tests | scripted Mock tool-call fixtures; record/replay |
| Model lacks tool-calling | capability-detect; fall back to single-shot path |
| Governance bypass | writes/MCP go through the same `HandlerRegistry` guards as the gateway — no privileged loop path |
| Big-bang risk | flag-gated sub-phases; single-shot stays default until proven |

## Open questions (for review)

1. **Terminal convention** — explicit `submit_changes` tool vs. "final answer
   with no tool calls carries the `{"files":...}` JSON". (Leaning: explicit tool
   — unambiguous, and it reuses `_apply`.)
2. **Loop budget** — a per-feature step cap *and* a sub-budget of the run's
   `SDLC_RUN_BUDGET_USD`, or just the existing run budget? (Leaning: both — a
   step cap bounds latency even when dollars remain.)
3. **`author_tests`** — keep single-shot in 5b, or also loop-drive it? (Leaning:
   single-shot first; loop-drive only if grounding shows it needs to read more.)
4. **Tool-calling fallback** — silently fall back to single-shot for non-tool
   models, or require a tool-capable model when the loop is enabled? (Leaning:
   fall back + log, so Ollama setups still work.)
