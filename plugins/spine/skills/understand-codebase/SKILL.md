---
name: understand-codebase
description: >-
  Understand an unfamiliar codebase or plan a change safely, using Spine's deterministic
  knowledge-graph MCP tools. Reach for this before answering structural questions about a
  repo you don't know, before editing code, or when debugging — it hands you engineering
  decisions (what a change breaks, what's untested, where a ticket or bug lands), each
  grounded to file:line. Triggers: "how does this repo work", "what breaks if I change X",
  "where do I fix this / where does this land", "what's untested here", "explain this symbol",
  "map this codebase", "which docs cover this". Tools: map_repo, blast_radius, explain_symbol,
  investigate, localize, regression_gaps, docs_for (all read-only, no credentials, from the Spine plugin).
---

# Understand a codebase with Spine

Spine reads a repository's **Product Knowledge Graph** — a deterministic, no-LLM index of its
modules, types, functions, call sites, and blast radius, with every fact grounded to `file:line`.
These MCP tools turn that graph into *decisions*, not just lookups: what a change breaks, what's
untested, where work lands. They're **read-only, need no credentials**, and take a local
`repo_path` (default: the current repository).

Use them before grepping or guessing about an unfamiliar repo — they're faster and more accurate,
and they cite their sources.

## Which tool for which question

| You want to… | Call |
|---|---|
| get oriented in a repo you don't know | **`map_repo`** — languages, components, call-hotspots, test-coverage gaps, prioritized recommendations |
| know what changing a symbol will affect | **`blast_radius(symbol=…)`** — direct callers + the cross-layer set a change ripples into, each `file:line` |
| understand one symbol | **`explain_symbol(symbol=…)`** — kind, location, who calls it, what it calls, what it contains |
| find where a feature/ticket lands | **`investigate(title=…, problem=…)`** — the real symbols to start from |
| pin a bug from a stack trace | **`localize(trace=…)`** — resolve each frame to the repo symbol; the likely fault site |
| see what a change could break silently | **`regression_gaps(symbol=… or trace=…)`** — blast-radius symbols with **no covering test** |
| root-cause a bug (hypotheses + fix approach) | **`root_cause(bug=…)`** — fault site, ranked hypotheses with evidence, regression surface, fix approach; deterministic (add `use_llm=true` for richer hypotheses) |
| find which docs describe code (or how documented it is) | **`docs_for(symbol=…)`** — the doc pages that mention a symbol; call with no symbol for a doc-coverage summary + top drift. Ingests `.md`/`.rst`/`.txt`/PDF |

Read a repo's committed knowledge base with **`read_memory_bank`** when one exists (built by
`orchestrator understand`).

`repo_path` defaults to the current repository, and also accepts a **git URL** (e.g.
`https://github.com/org/repo`) — Spine shallow-clones it, extracts, and cleans up.

## How to work

1. **Orient first.** For an unfamiliar repo, call `map_repo` before answering structural questions
   or planning a change — one call beats many greps.
2. **Check the blast radius before editing.** `blast_radius(symbol=…)` shows who depends on what
   you're about to touch; `regression_gaps` shows what has no test, so you know what could break
   silently.
3. **For a bug, go trace → fault → coverage.** `localize(trace=…)` finds the fault site; then
   `regression_gaps(trace=…)` shows the coverage around it.
4. **Cite `file:line`.** Every tool returns provenance and a `markdown` field you can show the user
   directly — ground your answer in it rather than paraphrasing.

## Good to know

- **Deterministic:** same commit in → same answer out (a commit-keyed cache makes re-runs cheap).
- **Structured + readable:** each tool returns typed fields (symbols, counts, `file:line`, gaps)
  **and** a `markdown` rendering.
- **Understanding vs. changing:** these tools only *read*. To actually change the code — spec →
  grounded codegen → tests → branch/PR — use Spine's gated `sdlc_feature`, which requires an
  explicit `confirm` for any external write. Keep the two separate.
