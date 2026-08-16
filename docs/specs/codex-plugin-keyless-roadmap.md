# Codex plugin — removing the API-key requirement

**Status:** Not started. **Written 2026-08-15 against 3.18.1.**
**Owner:** _unassigned_

**One-liner:** the plugin already works without any credentials for **17 of its 19 tools**. Two
need a model, and MCP **sampling** — supported by the installed SDK, unused by us — can route
those to the Codex host's own model instead of an `OPENAI_API_KEY`.

> **Read Phase 0 before planning anything else.** The whole sampling path depends on the *host*
> implementing sampling, which is unverified. If Codex does not support it, Phases 2–3 are dead
> and the keyless answer is Ollama (Phase 3b) — which is available today.

---

## 1. What is actually true in 3.18.1

Verified by reading `plugin/server.py`, `core/llm/`, and the installed `mcp` SDK.

### Tools needing **no credentials at all** (17 of 19)

`doctor` · `pkg_grounding` · `read_memory_bank` · `map_repo` · `blast_radius` · `explain_symbol` ·
`investigate` · `localize` · `regression_gaps` · `docs_for` · **`sdlc_plan`** · `sdlc_approve` ·
`root_cause` *(default)* · `sdlc_start_run` · `sdlc_run_status` · `sdlc_decide_gate` ·
`sdlc_run_result`

`sdlc_plan` is the surprising one and it is not an inference — its own docstring says
**"Deterministic — no LLM, no credentials."** The full twelve-section build document — requirement,
root cause, blast radius, design, files, acceptance criteria, cost and confidence — is produced
from the graph with no model involved.

### Tools that genuinely need a model (2)

| Tool | Why | Notes |
|---|---|---|
| `ingest_preview` | Runs the LLM intake (source → intents → specs) | Dry-run, writes nothing, but the derivation is model-driven |
| `sdlc_feature` | Grounded codegen → tests → branch | Delegates to the SDLC pipeline |

Plus `root_cause(use_llm=true)` — **opt-in, defaults to `false`**, and the only place in
`plugin/server.py` that constructs an LLM client (`server.py:424`).

### The sampling situation

```
grep -rn "sampling|createMessage|create_message" src/orchestrator/plugin/*.py   → no matches
python -c "from mcp import types; types.CreateMessageRequest, types.SamplingCapability" → both exist
```

**The SDK supports sampling. The plugin does not use it.** That is the entire gap for the two
model-dependent tools.

---

## 2. The gaps

| # | Gap | Impact | Cost |
|---|---|---|---|
| **A** | MCP sampling not implemented in the plugin server | The two model-dependent tools force a server-side key that the host could supply | M |
| **B** | Host sampling support unverified | Blocks A entirely. Many MCP clients do not implement sampling | **Spike first** |
| **C** | Codegen is architecturally incompatible with sampling | See §3 — this is a decision, not a defect | L |
| **D** | `codex login` credentials are not reusable | Wasted effort risk — see below | none |
| **E** | The keyless Ollama path is buried in a parenthetical | The one option that removes the key *today*, for *everything* | S |
| **F** | `media_asr.py:106` defaults `api_key_env="OPENAI_API_KEY"` | OpenAI-shaped API path; local Whisper avoids it | S |
| **G** | `CODEX_GUIDE.md` §4 understates keyless operation badly | Onboarding and positioning loss — see §4 | S |

### D — do not chase Codex's own login

`codex login` mints a session for Codex itself. It is **not an API key** and is not exposed for
third-party API calls; there is no supported way to borrow it. Sampling is the only legitimate
route to "use the host's model". Recorded here so nobody spends a week discovering it.

---

## 3. The real tension: keyless vs governed

**Codegen cannot simply move to sampling**, and the reason is worth stating before anyone tries.

The SDLC loop wraps `LiteLLMClient` in:

- `BudgetedLLMClient` / `RunBudget` (`core/llm/budget.py`) — enforces `SDLC_RUN_BUDGET_USD`,
  terminating a run that exceeds it
- `RecordingLLMClient` (`core/llm/recording.py`) — per-ticket cost accounting

Under sampling **the host pays and the host meters**. Spine sees no token counts and no price, so:

- **`RunBudget` becomes unenforceable** — a headline governance capability, and one of the rows
  where Spine leads in [`competitive-landscape.md`](competitive-landscape.md)
- **Per-ticket cost accounting disappears** — which removes `$/resolved` and `$/mergeable`, the
  procurement axis in [`codegen-benchmark-roadmap.md`](codegen-benchmark-roadmap.md)

So: **sampling buys keylessness at the price of two capabilities we currently lead on.** For
comprehension-tier tools that trade is free (they are cheap, single-shot, and not budget-governed).
For codegen it is a genuine strategic choice, and the answer is probably *not* sampling.

**Ollama is the keyless codegen answer that keeps governance.** `ollama/<model>` with
`OLLAMA_API_BASE` needs no key, runs locally, and stays inside `BudgetedLLMClient` — cost is zero
rather than unmeasurable, so the budget path still holds.

---

## 4. What `CODEX_GUIDE.md` §4 gets wrong

It currently leads with:

> The *minimum* for generating + testing code is **one LLM key**: `OPENAI_API_KEY=sk-...`

and closes with:

> Read-only tools (`doctor`, `pkg_grounding`) work without any creds.

Both are defensible for *codegen*, and badly wrong as a description of the plugin. Naming two
creds-free tools when there are **seventeen** understates the product and buries the best
onboarding story it has: *install the plugin, point it at a repo, and get grounded comprehension,
blast radius, fault localization, root-cause analysis and a full build document — with no key.*

The correction is accurate today and independent of every other phase here.

---

## Phases

| Phase | Work | Effort | Exit |
|---|---|---|---|
| **0 — Spike: does Codex support sampling?** | Minimal MCP server that issues one `sampling/createMessage` against Codex (and Claude Code, for comparison). Record the result in this doc. | ~0.5–1 d | A yes/no that decides whether Phases 2–3a exist at all |
| **1 — Docs (no code)** | Fix `CODEX_GUIDE.md` §4 per §4 above; promote the Ollama path from a parenthetical to a first-class keyless route; state which tools need creds and which do not. | ~0.5 d | A reader can tell exactly what works with no key |
| **2 — Sampling client** | An `LLMClient`-protocol implementation backed by `sampling/createMessage`, selected when the host advertises the capability. Wire it into `root_cause(use_llm=true)` and `ingest_preview` only. Fall back to `LiteLLMClient` when unsupported. | ~3–5 d | Both tools run on the host's model with no key set; falls back cleanly |
| **3a — Codegen decision** | Decide sampling vs budget for the SDLC loop (§3). **Recommended: do not route codegen through sampling.** | ~1 d | A recorded decision with its rationale |
| **3b — Ollama as the documented keyless codegen path** | Verify a full `sdlc_feature` run end-to-end on a local model; document the model requirements honestly (tool-calling support is the constraint). | ~2–3 d | A reproducible keyless codegen run, with its quality caveats stated |
| **4 — `media_asr` provider seam** | Make the ASR API path provider-agnostic rather than defaulting to `OPENAI_API_KEY`. | ~1 d | Remote ASR works against a non-OpenAI endpoint |

---

## Invariants

- **Never silently downgrade.** If sampling is unavailable and no key is set, a tool that needs a
  model must say so plainly — `root_cause` already models this well, returning a specific error
  naming the env var rather than failing obscurely.
- **Keylessness must not be bought with a false governance claim.** If a path cannot enforce
  `RunBudget`, the docs say so at that path, not in a footnote.
- **The deterministic tools stay deterministic.** Nothing in this program may introduce a model
  call into `understand` / `state` / `pkg *` / `sdlc_plan`.

## Non-goals

- Reusing Codex's ChatGPT session credentials (§2 D — not possible).
- Bundling or shipping model weights.
- Making `sdlc_feature` keyless via sampling (§3 — likely the wrong trade).

## Open questions

1. Does Codex CLI implement MCP sampling? **Phase 0 answers this.** Everything downstream waits.
2. If the host supports sampling but the user *also* has a key set, which wins? (Lean: **explicit
   key wins**, since it is the metered, budget-governed path.)
3. Which local models pass `sdlc_feature`'s tool-calling requirement? `orchestrator models` reports
   tool-calling support — use it rather than guessing.
