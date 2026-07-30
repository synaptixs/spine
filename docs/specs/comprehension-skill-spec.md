# Spec: `/spine` — the drop-in comprehension skill (MCP tools + Agent Skill)

**Status:** Phases 1–3 shipped (branch `feat/comprehension-skill`).
**Date:** 2026-07-21 · spine v3.7.0
**One-liner:** package Spine's **read-only comprehension + graph-query** commands as MCP tools and
a Claude **Agent Skill**, so any AI assistant can call Spine's *engineering-decision graph* —
blast radius, coverage gaps, where-a-ticket-lands — the way `/graphify` maps a codebase, but with a
payload that says **what to do, not just what exists**.

> Chosen from [graphify-vs-spine-comparison.md](graphify-vs-spine-comparison.md) as the highest-leverage
> low-effort move (its takeaway #2): attack Graphify's one real moat — **distribution** — with a
> differentiated payload, using assets Spine already ships.

---

## Why

- **Distribution is Graphify's moat.** `/graphify` installs into 15+ assistants (~93k★). This rides
  that exact channel instead of competing with the heavier platform.
- **The payload is differentiated.** Graphify answers `explain X` / `path X → Y` / `affected` — a
  concept map. Spine answers "**what breaks if I change X**" (cross-layer blast radius), "**what's
  untested**" (coverage gaps), "**where does this ticket land**" (grounded symbols) — engineering
  *decisions*, each with `file:line` provenance.
- **It's cheap.** The engine functions all exist and are deterministic; the MCP server is already
  live. This is **façade + a skill manifest**, not new comprehension.

---

## What already exists (reuse, don't rebuild)

| Piece | Source | Gives us |
|---|---|---|
| MCP plugin server (FastMCP; stdio **and** remote streamable-HTTP + OAuth/bearer auth) | `plugin/server.py` (`build_server` / `build_http_server`, `_TOOLS`, `_register_tools`) | The transport + registration seam. Today it exposes `doctor`, `ingest_preview`, `pkg_grounding`, `read_memory_bank`, and the gated `sdlc_*` run — **but none of the comprehension/graph-query commands** |
| `state` engine | `knowledge/current_state.py` `build_current_state()` (markdown) / `load_current_state()` → `(CurrentState, FactBatch)` | Structure, hotspots, coverage, recommendations — deterministic, no LLM |
| `understand` / episteme | `knowledge.build_memory_bank()`, `read_memory_bank()` (already an MCP tool) | Committed code-true knowledge; the read side is already exposed |
| Graph queries | `pkg/store.py` `FactStore.callers_of` / `callees_of` / `impact_of` / `impact_across` | Callers + **cross-layer** blast radius (CALLS + IMPORTS + REFERENCES), `file:line` |
| `investigate` | `sdlc/investigate.py` `build_investigation()` + `render_investigation_md()` | Ticket → where it lands in the code |
| `localize` | `sdlc/localize.py` `localize_trace()` | Stack trace → the repo symbol it names |
| `regression` | `sdlc/coverage.py` `build_regression_plan()` + `resolve_target()` | Blast-radius coverage gaps |
| `rca` (LLM) | `sdlc/rca.py` `build_rca()` | Ranked root-cause hypotheses — the one comprehension surface that *uses* an LLM |
| Repo-or-git-URL resolution | `cli._repo_arg` → `resolve_repo_source` / `materialize_repo_source` | A tool can take a local path **or** a git URL (SSRF-guarded), like the CLI |

So the skill **consumes already-computed engine functions**; it adds a transport + packaging, not analysis.

---

## Design decisions

1. **Read-only, deterministic slice by default.** Every default tool is no-LLM, no external write —
   safe to expose to any assistant with no API key. The one LLM surface (`root_cause` / `rca`) is
   **opt-in** and clearly flagged; the gated `sdlc` run stays a separate, governed tool.
2. **Thin façades, the plugin house pattern.** Each tool is a module-level function (unit-testable
   *without* the `mcp` extra), added to `_TOOLS`; `build_server` registers it automatically. No new
   transport code.
3. **Structured returns, not just markdown.** An assistant consumes *fields* (symbols, `file:line`,
   caller counts, gap lists), so tools return JSON. Keep an optional `markdown` render for a human to
   read verbatim. Bound every list (top-N + a `truncated` marker) so a call yields signal, not a dump
   (invariant #7).
4. **Path or git URL.** `repo_path` defaults to the caller's cwd and also accepts a git URL (reuse
   `resolve_repo_source`) — CLI parity, so the skill works on a checked-out repo *or* a URL the user names.
5. **Two distribution forms, one payload.** The **MCP server** (already shipped, universal) is the
   substrate; a thin **Agent Skill** (`SKILL.md`) is the `/graphify`-style zero-friction wrapper that
   tells an assistant *when* to call *which* tool. Ship the MCP tools first; the skill is a wrapper, not
   a rewrite.
6. **No new comprehension.** This spec is packaging + surface. If a query needs a new fact, that's a
   `pkg` change, out of scope here.

---

## The tool surface (new read-only MCP tools)

All deterministic (no LLM) unless marked. `repo_path` is a local path or git URL.

| Tool | Wraps | Returns (JSON) |
|---|---|---|
| `map_repo(repo_path, lens="developer")` | `build_current_state` / `load_current_state` | languages, components, **call-hotspots**, **coverage gaps**, prioritized **recommendations** — the skim-first project map |
| `blast_radius(repo_path, symbol)` | `resolve_target` + `FactStore.callers_of` + `impact_across` | direct callers + the cross-layer set a change reaches, each with `file:line`; "changing X touches N across M files" |
| `explain_symbol(repo_path, symbol)` | `FactStore.find` / `callers_of` / `callees_of` / `children_of` | what it is, where it's defined, who calls it / what it calls |
| `investigate(repo_path, title, problem=None)` | `build_investigation` | where a ticket lands: real symbols (`file:line` + caller counts), ranked |
| `localize(repo_path, trace)` | `localize_trace` | each stack frame → the repo symbol it names → likely fault site |
| `regression_gaps(repo_path, symbol=None, trace=None)` | `build_regression_plan` (+ `resolve_target` / `localize_trace`) | blast-radius symbols with **no covering test** |
| `root_cause(repo_path, bug)` ✨LLM, opt-in | `build_rca` | ranked root-cause hypotheses + evidence + fix approach (needs a provider key) |

`read_memory_bank` (already shipped) rounds out the set: read the committed `episteme/` for a repo.

---

## The Agent Skill (`SKILL.md`)

A thin Claude Agent Skill packaged alongside the MCP config — the `/graphify`-shaped drop-in:

- **Install:** `pip install 'synaptixs-spine[mcp]'` (already on PyPI), point the host at the stdio
  server (`orchestrator plugin serve` / `build_server`).
- **Guidance (the skill body):** *"Before answering questions about an unfamiliar codebase or planning
  a change, call `map_repo`. For 'what breaks if I touch X?' call `blast_radius`. For a bug or stack
  trace, `localize` then `regression_gaps`. Ground any change in real symbols with `file:line`."*
- **Positioning:** the skill's one-liner is the differentiator — *"understand a codebase as engineering
  decisions: blast radius, test-coverage gaps, and where a change lands — not just a concept map."*

Keep the skill declarative and small; all behavior lives in the MCP tools.

---

## Phases

| Phase | Work | Effort | Exit |
|---|---|---|---|
| **1 — Read-only comprehension tools** ✅ | `map_repo`, `blast_radius`, `explain_symbol`, `investigate`, `localize`, `regression_gaps` as module-level functions in `plugin/server.py` (+ a shared `_load_store` helper over `load_or_extract`); registered in `_TOOLS`; structured JSON **+ a `markdown` field**; local path only. Codex packaging first (CODEX_GUIDE §6 + the marketplace `plugin.json` pitch). Unit tests call the functions directly (no `mcp` extra). | ~2–3 d | **DONE.** All six return bounded, `file:line`-grounded JSON on a local repo (verified on this repo + a synthetic fixture); 13 tests; gate green. Codex auto-advertises them via the existing plugin. |
| **2 — The Agent Skill + polish** ✅ | `plugins/spine/skills/understand-codebase/SKILL.md` (Claude Agent Skill — triggers + a which-tool-when guide); the Claude + Codex plugin/marketplace manifests re-pitched to lead with comprehension (+ Go); CLAUDE_GUIDE §6 documents the tools + a plain-language "try it". JSON shapes reviewed for consistency (structured fields + `markdown`). | ~1–2 d | **DONE.** The Spine plugin now ships the skill; Claude picks the right tool from a plain-language ask. Manifests + skill frontmatter unit-tested (`test_manifests.py`); gate green. |
| **3 — LLM + reach** ✅ | `root_cause` tool — deterministic RCA by default (fault site + ranked hypotheses + regression surface + fix approach), `use_llm=true` opts into LLM enrichment (fails clean without a model). **git-URL support** across all graph-query tools (refactored onto `_open_repo`/`_repo_store` context managers over `resolve_repo_source`/`materialize_repo_source` — same SSRF/host-allow-list guard as the CLI; bad path/host → `{error}`). Remote-HTTP hosting documented (`orchestrator-mcp --http`). | ~1–2 d | **DONE.** `root_cause` deterministic + opt-in LLM; git-URL verified end-to-end (`pallets/click`); disallowed host rejected. 6 new tests; gate green. |

**Phase 1 is independently useful** (any MCP host gains the tools). Phase 2 is the `/graphify`-style
drop-in. Phase 3 is reach.

## Effort & risk

- **~S total** (Phase 1–2 ≈ 3–4 days). **Low risk:** read-only, no-LLM, the engine exists; the MCP
  transport (incl. remote HTTP + OAuth) already ships.
- Main watch-item: **bound + structure the returns** (top-N, `file:line`, `truncated`) so an assistant
  gets a decision, not a wall of text — and keep the default set 100% no-LLM/no-key so install is frictionless.

## Non-goals

- **Not the gated `sdlc` run** — code generation / PRs stay a separate, governed tool with human gates.
- **No new extraction or comprehension** — packaging only; a new fact is a `pkg` change.
- **Not a CLI replacement** — same engine, different channel (parity, not migration).

## Open questions

1. **Skill format** — a Claude Agent Skill first, and add a generic/Codex manifest later? (Lean: the MCP
   server is already universal; ship one `SKILL.md`, let other hosts consume the MCP server directly.)
2. **`understand` write vs read** — expose only `read_memory_bank` (read the committed `episteme/`), or
   add a `map_repo`-style in-memory build so the tool works before `understand` has been run? (Lean:
   `map_repo` already computes in memory; `read_memory_bank` covers the committed case.)
3. **RCA in the default skill?** Keep the default 100% no-LLM/no-key and make `root_cause` opt-in?
   (Lean: yes — frictionless default; RCA is the one tool that needs a provider.)
