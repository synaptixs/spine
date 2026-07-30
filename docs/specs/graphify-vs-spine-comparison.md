# synaptixs-spine vs. Graphify — how we compare

**Audience:** internal team · **Date:** 2026-07-21 (rev. 2 — after the 3.7.0 Go release + the
drop-in comprehension skill)
**Compared:** synaptixs-spine `orchestrator` **v3.7.0** vs. Graphify (`graphifyy`, MIT, YC S26)
**Method:** both products read directly — Spine from source; Graphify from its public repo
(`Graphify-Labs/graphify`: README, ARCHITECTURE, BENCHMARKS, the `graphify/` package). Facts
below are evidence-based, not marketing.

---

## TL;DR

**These are different categories that overlap on one component.**

- **Graphify** is a best-in-class **code + docs comprehension & memory skill** for AI coding
  assistants. You run `/graphify` and it maps your project (code, docs, PDFs, images, video)
  into a **graph you query instead of grepping**. It *understands*. Huge adoption (~93k★),
  ~40 languages, polished interactive visualization.
- **Spine** is an **autonomous, governed SDLC platform**. A comparable code graph (the PKG) is
  the **substrate** underneath grounded design, RCA, and code generation that ends in a
  **reviewed, tested pull request**. It *understands **and** acts* — with human gates, policy,
  and an audit trail.

> **One line:** *Graphify maps the codebase; Spine changes it, under governance.*

Graphify is not a competitor to Spine's autonomous-delivery mission — it doesn't attempt code
generation, requirements→work, or governance. Spine is not a competitor to Graphify's reach or
breadth on pure comprehension. The overlap is a deterministic code graph; each product builds a
very different thing on top.

---

## Side-by-side

| Dimension | Graphify | synaptixs-spine | Leads |
|---|---|---|---|
| **Category** | Comprehension + memory skill for AI assistants | Autonomous, governed SDLC platform | — (different) |
| **Primary output** | A queryable graph (`graph.html` + `graph.json` + report) | A reviewed, tested **pull request** | — |
| **Acts on code?** | No — read-only understanding | **Yes** — generates code + tests, opens PRs | **Spine** |
| **Deterministic AST code graph** | Yes (tree-sitter, no LLM, file:line) | Yes (no-LLM `understand`/`state`, commit-keyed cache) | Tie |
| **Provenance / edge honesty** | `EXTRACTED` vs `INFERRED` tags | Every fact `file:line`-grounded; bounded honestly | Tie |
| **Code language breadth** | **~40** (Rust, Ruby, Kotlin, Swift, Elixir, PHP, Terraform, Verilog, …) | 7 (Python, Java, TS, C#, C, C++, **Go**) + SQL | **Graphify** (Go added in 3.7.0) |
| **Non-code ingestion** | **Docs, PDFs, images, video, Google Workspace** | Requirements text only (Confluence/Jira/Notion/OpenSpec) | **Graphify** |
| **Graph query commands** | `explain` / `path` / `search` / `affected` / `report` / `watch` | `investigate` / `localize` / impact / callers + retrieval | Graphify (UX); ~tie on substance |
| **Blast-radius / impact** | `affected` | `impact_of` + **composed cross-layer** (CALLS + IMPORTS + REFERENCES) | **Spine** (depth) |
| **Data-layer semantics** | SQL + `pg_introspect` | SQL + FK/`REFERENCES` edges | Tie |
| **Visualization** | **Interactive `graph.html`, Leiden communities** | Web UI + mermaid (no build step) | **Graphify** |
| **Grounded code generation** | — | PKG-grounded codegen loop | **Spine** |
| **Design / RCA workflows** | — | `design` (blast-radius), `rca` (root-cause + hypotheses), `regression` (coverage gaps) | **Spine** |
| **Requirements → work** | — | Intake → intents → specs → issues | **Spine** |
| **Governance / trust** | — (local skill) | Policy-as-code, **human approval gates**, append-only audit, provenance, sensitivity tiers, replay | **Spine** |
| **Durable execution** | — (one-shot CLI) | Temporal; resumable; per-run cost/budget audit | **Spine** |
| **Distribution / adoption** | **`/graphify` in 15+ assistants; ~93k★; large OSS** | Platform you run **+ a drop-in `/spine` comprehension skill** (Codex plugin + Claude `understand-codebase` skill; local *or* remote HTTP) | **Graphify** (reach) — but Spine now rides the same channel |
| **Comprehension as a drop-in skill** | The `/graphify` skill *is* the product | **NEW (3.7.0+):** seven read-only MCP tools (`map_repo` / `blast_radius` / `explain_symbol` / `investigate` / `localize` / `regression_gaps` / `root_cause`) + a Claude Agent Skill | ~tie on channel; **Spine** on payload (decisions, not lookups) |
| **Memory/retrieval benchmarks** | LOCOMO / LongMemEval (vs mem0, supermemory, dense RAG) | Not benchmarked as memory | **Graphify** |
| **License / availability** | MIT, fully open | `synaptixs-spine` on PyPI; public mirror + private source | — |

---

## Where Spine stands apart / ahead

1. **It ships code, not just answers.** Spine turns the graph into a **reviewed, CI-green
   PR** — requirements → grounded codegen → tests → review → merge, autonomous between two
   human bookends. Graphify's *output is the graph*; a human still does the engineering.
   This is the fundamental category difference.
2. **Grounded engineering *decisions*, not just lookups.** `design` (blast-radius-aware),
   `investigate`, `localize`, `rca` (ranked root-cause hypotheses + fix approach),
   `regression` (test-coverage gaps) turn the graph into *what to build/fix and what it will
   break*. Graphify answers "explain X" / "path X → Y."
3. **Governance is the moat.** Policy-as-code, two human approval gates, append-only audit,
   provenance, sensitivity tiers, replayable runs, durable (Temporal) execution. Graphify is a
   local, read-only skill with none of this. Spine is built to run **autonomously on real
   code, safely and auditably** — the hard part everyone else bolts on later.
4. **Deeper on the data/impact axis.** Composed **cross-layer** impact (code → module → data,
   via CALLS + IMPORTS + REFERENCES) and foreign-key edges — not just call edges.
5. **The full front of the SDLC.** Requirements intake from Confluence / Jira / Notion / MCP /
   OpenSpec → intents → specs. Graphify starts from artifacts already on disk.

## Where Graphify is genuinely ahead

Stated plainly so this reads as an honest assessment, not a pitch:

- **Language & modality breadth** — ~40 languages vs our 8 (7 code + SQL), and it folds **docs,
  PDFs, images, and video** into the same graph. We're code + requirements text.
- **Adoption & distribution** — ~93k stars, YC S26, a frictionless `/graphify` skill across
  15+ assistants, and published memory benchmarks. We're comparatively niche.
- **Visualization & query UX** — the interactive `graph.html` with Leiden community detection
  is a polished, headline experience; our comprehension surfaces are more utilitarian.

---

## Bottom line

On the **pure code-comprehension axis**, Graphify is broader and far more adopted. On the
**"turn understanding into governed, shipped software" axis**, Spine is in a different league —
because Graphify doesn't attempt it.

- Team wants *a graph to query and remember* across code and documents → **Graphify** wins on
  reach and breadth.
- Team wants *an agent that engineers within a codebase — plans, builds, tests, and opens a
  reviewed PR, safely and auditably* → **Spine**, and Graphify isn't a competitor there.

## Two takeaways for us

1. **Breadth is a closable gap, if it matters.** Graphify's ~40 languages and doc/PDF/video
   ingestion outrun our comprehension reach. If comprehension breadth becomes a priority, more
   front-ends and a document-ingestion path are the levers.
2. **Their go-to-market is a lesson — and we acted on it (3.7.0+).** The `/graphify` agent-skill
   model — zero-friction install into every assistant, ~93k stars — is a distribution channel.
   Spine's comprehension layer is **now** a drop-in skill on that channel: seven read-only MCP
   tools (`map_repo` / `blast_radius` / `explain_symbol` / `investigate` / `localize` /
   `regression_gaps` / `root_cause`) exposed via the Codex plugin and a Claude `understand-codebase`
   Agent Skill, local *or* over remote HTTP — independent of the heavier platform, and with a
   differentiated payload (engineering decisions, not lookups). See
   [comprehension-skill-spec.md](comprehension-skill-spec.md). *Remaining reach gaps: modality
   breadth (docs/PDF/video) and raw language count.*
