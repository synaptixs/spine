---
name: understand-codebase
description: >-
  Understand an unfamiliar codebase or plan a change safely, using Spine's deterministic
  knowledge-graph MCP tools. Reach for this before answering structural questions about a
  repo you don't know, before editing code, or when debugging — it hands you engineering
  decisions (what a change breaks, what's untested, where a ticket or bug lands), each
  grounded to file:line. Triggers: "how does this repo work", "what breaks if I change X",
  "where do I fix this / where does this land", "what's untested here", "explain this symbol",
  "map this codebase", "which docs cover this", "what depends on this in our other services".
  Tools: map_repo, blast_radius, explain_symbol, investigate, localize, regression_gaps,
  root_cause, docs_for, pkg_joins (all read-only, no credentials, from the Spine plugin).
---

# Understand a codebase with Spine

Spine reads a repository's **Product Knowledge Graph** — a deterministic, no-LLM index of its
modules, types, functions, call sites, and blast radius, with every fact grounded to `file:line`.
It covers Python, Java, TypeScript, C#, C, C++, Go and SQL.
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
| ask any of the above **across several repositories** | **`blast_radius`**, **`investigate`**, **`explain_symbol`**, **`regression_gaps`**, **`localize`**, **`docs_for`** all take **`repos=…`** — pass a `.spine/repos.yaml` instead of `repo_path`. `regression_gaps` then reports `uncovered_elsewhere` (a change reaching a *different* service nothing tests); `localize` says which repo each frame landed in and lists `ambiguous_frames`; `docs_for` answers each repo on its own |
| see or sanity-check the cross-repo topology | **`pkg_joins(config=…, mode="propose"\|"check")`** — read-only; it never writes a config |

Read a repo's committed knowledge base with **`read_memory_bank`** when one exists (built by
`orchestrator understand`).

`repo_path` defaults to the current repository, and also accepts a **git URL** (e.g.
`https://github.com/org/repo`) — Spine shallow-clones it, extracts, and cleans up.

## More than one repository

When a change's real blast radius leaves the repo, a single-repo answer is worse than no answer.
An HTTP handler reports **`0 caller(s)`** — which is *true*, nothing in its own source calls it —
and reads as safe to change.

If the project declares its services in a **`.spine/repos.yaml`**, pass it as `repos=` to any of
`blast_radius`, `investigate`, `explain_symbol`, `regression_gaps`, `localize` or `docs_for` — e.g. to
`blast_radius` or `investigate` instead of `repo_path`. Every match then also reports the
dependents it has **in other repositories**:

```
- **billing** · `create_order` (Function, 0 caller(s), **1 dependent(s) in other repos**) — billing:app/routes.py:7
```

- The topology is **declared, not guessed** — a `joins:` entry narrows the search; it does not
  invent the edge. `pkg_joins(mode="propose")` derives candidates from the evidence and prints
  them for review; `pkg_joins(mode="check")` reports the calls no declared join could place.
  Run `check` before trusting a quiet result: a *missing* join looks exactly like two services
  that aren't coupled, which reads as health.
- Every multi-repo answer carries a **`standing`** block. If `reproducible` is `false`, one of
  the declared repos has uncommitted work — say so rather than quoting a number nothing can
  reproduce.
- `map_repo` is single-repo only; there's no merged profile behind it. Call it per repository.
- **If a single-repo answer comes back carrying `multi_repo_available`, stop and re-ask.** It
  means the repo you pointed at declares siblings in its own `.spine/repos.yaml`, so the answer
  you have covers one of them. Nothing errored — pointing a tool at a directory always works —
  which is exactly why the note is there. Re-run with the `repos=` path it gives you before
  telling anyone a symbol is safe to change.

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
- **Understanding vs. changing:** these tools only *read*. Between them and codegen sits a middle
  tier — `sdlc_plan` writes a twelve-section build document (still no model, no credentials: it's
  rendered from the graph, git and the tree) and `sdlc_approve` records the decision on it. To
  actually change the code — spec → grounded codegen → tests → branch/PR — use Spine's gated
  `sdlc_feature`, which requires an explicit `confirm` for any external write. Work *down* the
  tiers: comprehend, then plan and get the plan approved, then build.
