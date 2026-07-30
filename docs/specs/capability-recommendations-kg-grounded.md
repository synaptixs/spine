# Capability recommendations — grounding design, bug-fix & RCA in the knowledge graph

**Status:** Living roadmap — **C1, C9, C3, C4, C10, C6, C2, C5, C8 shipped**. Only **C7 (observability→defect loop) remains, deferred**. The whole KG-grounded design + RCA program is built.
**Date:** 2026-07-20 · `develop` (kept local/untracked — roadmap stays out of commits).
**Decision owner:** you. Mark each candidate **ACCEPT / DEFER / REJECT** in the box next
to it; shipped ones are checked with their branch. Nothing proceeds to build until you say so.

---

## How to read this doc

Each candidate has: what it is, why it matters, **what already exists vs. what's
net-new** (grounded in the current code), rough effort, dependencies, risks, and a
decision box. There's a recommended sequence at the end, and an explicit
**skip/defer** list so the doc is a filter, not just a wish-list.

Effort scale (solo/small-context): **S** ≈ 1–2 days · **M** ≈ 3–5 days · **L** ≈ 1–2 weeks.

---

## Where we are today (the honest baseline)

I mapped `intake/`, `sdlc/`, `knowledge/`, and `pkg/` before writing this. The relevant
facts:

- **The PKG can already answer impact questions.** `FactStore` exposes `find`,
  `callers_of`, `callees_of`, `touches` (one-hop blast radius), `impact_of` (transitive
  reverse-call BFS with hop distance), `dependents_of`/`references_of` (data layer). See
  `pkg/store.py`. `GroundedRetriever.diff_impact` composes these for a changed diff
  (`pkg/retrieval.py`).
- **But those impact primitives are barely used.** They feed exactly two places:
  post-hoc **code review** (`codereview/grounding.py` runs `diff_impact` on the finished
  diff) and an **optional in-loop tool** (`pkg_blast_radius`, `agentic/tools.py`) the model
  may or may not call. **Up-front design and planning don't use them at all.**
- **Feature design already exists** as a flag-gated stage (M2, `sdlc/design.py`), producing
  a grounded `design.json`/`design.md` per issue with an optional Gate 1.5 — but it grounds
  only on the **module-level `graph-overview.json`** + memory bank. It never asks "what will
  this change break?"
- **Planning (`sdlc_code_plan`) ignores the PKG entirely** — it's a deterministic
  spec-only transform (`codegen.py::plan`).
- **Bug / RCA / incident / defect are not first-class concepts anywhere.** The only trace
  is a keyword classifier (`catalog/profile.py::task_type_from_intent` → `"bugfix"`). No
  RCA stage, no defect model, no bug-DB source.
- **Jira is output-only** — `JiraAdapter` implements `IssueTrackerAdapter` (a sink). There
  is **no read/source side**, so existing tickets can't be *ingested* to research. The
  `SourceAdapter` seam (5 adapters today: confluence, notion, file, openspec,
  mcp-confluence) is clean and cheap to extend.
- **Coverage limiter:** `impact_of` follows **CALLS** edges only, and **TypeScript and
  Java emit no CALLS edges** (only structure/inheritance/imports). So today, true
  impact/RCA is strong for **Python, C, C++, C#**; **empty for TS/Java**.

That baseline makes the opportunity precise: **the graph already knows the blast radius;
the design and bug-fix workflows just don't ask.**

---

## Candidate capabilities

### C1 — Impact-aware feature design  ·  Effort: **S–M**  ·  Enhancement
> ` [ ] ACCEPT   [ ] DEFER   [ ] REJECT `

**What:** Extend the existing M2 design stage (`sdlc/design.py`) so a design doesn't just
reference "real modules/symbols" but names **what it will touch and what it will break**.
Resolve the spec's target symbols via `store.find`, run `callers_of` / `impact_of` /
`dependents_of`, and inject a *Blast-radius* section into the design prompt and `design.md`:
integration points, N transitive callers across M modules, data entities depended on,
regression-risk call-out. Gate 1.5 reviewers then approve a design that states its risk.

**Why:** Highest value-to-effort item. The primitives exist and are already proven in code
review; this just moves them **before** the code is written, where they change decisions
rather than catch mistakes. Turns "grounded-sounding" designs into designs a senior
engineer can trust.

**Exists vs net-new:** Stage exists, primitives exist, Gate 1.5 exists. Net-new is only the
wiring + prompt/section. Small.

**Depends on:** C5 for TS/Java repos (otherwise the blast-radius section is empty there — be
honest and say "call graph unavailable for this language" rather than imply zero impact).

**Risk:** Low. Additive, flag-gated like the rest of M2.

---

### C2 — KG-grounded bug-fix / RCA pipeline  ·  Effort: **L**  ·  Net-new (headline)
> ` [x] ACCEPT `   ✅ **v1 shipped** (`feat/rca-pipeline`) — `orchestrator rca`, gated at analysis

**Shipped v1 (CLI, gated at analysis — no autonomous code):** `orchestrator rca` localizes a bug
(a stack trace via C6, a `jira://` Bug via C3, or inline text), then produces a **gated `rca.md`**:
the fault site + callers, ranked root-cause **hypotheses with evidence** (exception-type priors,
recent git churn, call sites), the **regression surface** a fix must cover (via C1's blast radius),
and a scoped fix approach. Deterministic; `--llm` enriches hypotheses from the same evidence.
**Decisions taken:** (1) autonomy boundary = **stop at the gated report** (human decides), per the
recommendation; (2) bug entry point = **all three** (trace / Jira / inline) since C6+C3 exist.
**Deferred to v2:** driving the approved fix through design→implement→PR (the feature path already
exists — wiring RCA into it is the next step), and adversarial hypothesis verification.

**What:** A first-class **defect** path parallel to the feature path. Given a bug (a Jira
Bug via C3, a stack trace, or a failing test), the pipeline:
1. **Localize** — map error signals (stack frames, failing symbol, mentioned files) to PKG
   nodes via `store.find` + provenance (uses C6).
2. **RCA** — walk `callers_of`/`callees_of` around the fault plus recent git churn
   (`current_state` already computes `recent_areas`) to produce a **grounded `rca.md`**:
   suspected root cause, evidence (file:line), blast radius, and hypotheses ranked.
3. **Fix design** — a design.md scoped to the minimal change + explicit regression surface.
4. **Gate**, then **implement + author a regression test that reproduces the bug first**
   (red→green), reusing the existing drive-to-green loop.

**Why:** This is the genuinely new frontier and the thing that most enhances usability —
today there is *nothing* here. RCA is exactly the task where a knowledge graph beats a
context-window: "who calls this, what changed recently, what does it touch" is graph-shaped.

**Exists vs net-new:** Net-new workflow. Reuses: the drive-to-green loop, gates, grounding
retriever, `current_state` churn, and the `bugfix` classifier as the branch trigger.

**Depends on:** C6 (localizer) hard; C3 (Jira-read) to ingest real tickets; C5 for TS/Java.

**Risk:** Medium–high. RCA hypotheses are LLM-generated over graph evidence — must be framed
as *ranked hypotheses with evidence*, never asserted root cause. Keep the human gate.

---

### C3 — Jira / issue-tracker as a read *source*  ·  Effort: **M**  ·  Net-new adapter
> ` [ ] ACCEPT   [ ] DEFER   [ ] REJECT `

**What:** Add the **read side** of Jira (and generalize to other trackers) so existing
tickets/bugs/epics can be *ingested* — not just written. Implement the `SourceAdapter`
protocol (mirrors `ConfluenceAdapter`), so `jira://PROJ-123` or a JQL query becomes
`SourceDocument`s → intents/investigations. Bug issue-types route to C2; stories to the
feature path.

**Why:** Today you can only *push* to Jira. To "use Jira to research and investigate" (your
words) you need to *read* it. The seam already exists; this is a well-scoped adapter.

**Exists vs net-new:** `SourceAdapter` protocol + factory registration pattern exist; Jira
*write* auth exists and is reusable. Net-new is the fetch/normalize + JQL.

**Depends on:** none (foundational for C2/C4 research flows).

**Risk:** Low–medium. Auth already solved. Main work is mapping Jira fields → `SourceDocument`.

---

### C4 — Investigation / research brief (ticket ↔ KG ↔ episteme ↔ past runs)  ·  Effort: **M**  ·  Net-new
> ` [x] ACCEPT `   ✅ **v1 shipped** (`feat/investigation-brief`) — `orchestrator investigate`

**Shipped v1 (CLI, zero-infra, deterministic):** grounds a ticket on the two **local** sources
— PKG retrieval (`relevant_symbols` → where it lands, with `file:line` + caller counts + owning
module) and the committed `episteme/` (project knowledge). Ticket comes from any source URI
(`--source jira://PROJ-123`, reusing C3) or inline (`--title/--text`). Output: an
`investigation.md` brief that names its own gaps honestly.

**Deferred to v2:** the **cross-run semantic-memory** "has this been done before?" section needs
the registry **Postgres DB** (`MemoryRepo.search`), so it's kept out of the zero-infra CLI. The
core takes `prior_notes` as a passed-in param, so the pipeline (which has a DB session) can wire
recall in later with no change to the renderer. Also v2: fold the brief into the pipeline before
the design wave (surface at Gate 1), and query-filter the episteme excerpt.

**What:** A pre-design **investigation** step that, for a ticket, answers deterministically-
first: *Where in the code does this touch?* (`store.find` + retrieval), *Has this been done
or attempted before?* (cross-run **semantic memory**, already shipped), *What's the relevant
prior art / conventions?* (`episteme/`). Output: an `investigation.md` brief that feeds
design (C1) or RCA (C2) and surfaces at Gate 1.

**Why:** This is the "research and investigate before recommending" piece. It's the
connective tissue that makes C1/C2/C3 feel like an engineer who *looked things up* rather
than one who started typing. Reuses assets you already built (semantic memory, episteme,
retrieval) that are currently only used at codegen time.

**Exists vs net-new:** All inputs exist (retrieval, semantic memory, episteme). Net-new is
the orchestration + the brief renderer.

**Depends on:** C3 to research tickets (works standalone on a free-text problem statement too).

**Risk:** Low. Read-only synthesis.

---

### C5 — Cross-layer impact + CALLS edges for TS/Java  ·  Effort: **M (compose) + L (TS/Java)**  ·  Enabler
> ` [x] ACCEPT `   ✅ **shipped** (`feat/cross-layer-impact`)

**Shipped:** **5a** — `FactStore.impact_across` unions the reverse direction of CALLS + IMPORTS
+ REFERENCES for true cross-layer blast radius (backed by a one-pass reverse index). **5b** —
real CALLS extraction added to the Java and TypeScript front-ends (two-pass, precision-first
like C++: sibling/`this` calls, static/imported calls; instance calls on typed vars skipped).
TypeScript additionally resolves **relative import specifiers to the definition's module id**,
so cross-file call graphs connect (Java already connected via package == import FQN). Net:
impact, RCA (C2), and regression coverage (C8) now work on TS/Java, not just Python/C#.

**What:** Two related graph improvements:
- **(5a, M)** Make `impact_of` compose **CALLS + IMPORTS + REFERENCES** (today CALLS only),
  so cross-layer impact (change a table → who reads it → who imports that module) is real.
- **(5b, L)** Add **CALLS-edge extraction** to the TypeScript and Java front-ends (they emit
  structure/inheritance/imports but no calls today), so impact/RCA works for those
  ecosystems at all.

**Why:** Foundational multiplier. Without 5b, C1 and C2 are silently Python/C#-only — a real
limitation given TS/Java are the most common enterprise targets. 5a deepens every impact
query. This is infrastructure, not a user-facing feature, but it's what makes the others
credible on real codebases.

**Exists vs net-new:** `impact_of`/`touches` exist (single-edge). Net-new: composition logic
+ two extractor call-resolution passes (tree-sitter query work, the harder part).

**Depends on:** none. It's the dependency *for* C1/C2 on TS/Java repos.

**Risk:** Medium. Call resolution in TS (dynamic dispatch, re-exports) and Java (overloads)
is genuinely hard to do precisely; scope to same-file + imported-symbol resolution first and
**bound honestly** (mark unresolved calls), per invariant #7.

---

### C6 — Stack-trace / failing-test → PKG localizer  ·  Effort: **S–M**  ·  Net-new (building block)
> ` [x] ACCEPT `   ✅ **shipped** (`feat/fault-localizer`) — `orchestrator localize`

**What:** A utility that parses a stack trace, exception, or failing-test output, extracts
`file:line`/symbol frames, and resolves them to PKG nodes (provenance is `file:line`, so this
is mostly a reverse index). Returns the ordered fault path as graph nodes.

**Why:** The concrete entry point for C2 — turns raw error text into graph coordinates. Small,
self-contained, testable in isolation, and useful on its own (e.g. "explain this traceback
against the codebase").

**Exists vs net-new:** Net-new, but small — nodes already carry `Provenance(file, line)`.

**Depends on:** none. **Enables:** C2.

**Risk:** Low. Language-specific trace formats; start with Python tracebacks + pytest.

---

### C7 — Observability → defect loop (Sentry / OTel error ingestion)  ·  Effort: **M–L**  ·  Net-new (speculative)
> ` [ ] ACCEPT   [ ] DEFER   [ ] REJECT `

**What:** Ingest runtime errors (a Sentry `SourceAdapter`, or OTel error spans — Spine
already emits OTel) as candidate defects that feed C2 automatically.

**Why:** Closes the loop from production signal → RCA → fix. Compelling long-term story.

**Recommendation: DEFER.** More moving parts, external dependency, and it only pays off once
C2 + C6 exist and are proven. Not on the critical path.

**Depends on:** C2, C6.  **Risk:** Medium — external integration, alert-noise triage.

---

### C8 — Blast-radius-driven regression tests  ·  Effort: **M**  ·  Enhancement
> ` [x] ACCEPT `   ✅ **v1 shipped** (`feat/cross-layer-impact`) — `orchestrator regression`

**Shipped v1 (analysis, deterministic):** given a symbol (`--symbol`) or fault site (`--trace`,
reusing C6), walks the call graph to the blast radius and splits it into **covering tests**
(reachable from a test through CALLS) and **regression gaps** (impacted production code no test
reaches). "Covered" = exercised, not asserted-correct — gaps are the actionable half. **v2
(deferred):** auto-*generate* the missing regression tests (LLM), and wire the plan into the
SDLC test-author stage so a fix can't merge with an unfilled gap.

**What:** When implementing a change/fix, auto-author tests that cover the **impacted callers**
(`impact_of`), not just the changed symbol — the test-author stage becomes blast-radius aware.

**Why:** Directly raises the safety of autonomous changes; pairs naturally with C1/C2.

**Depends on:** C5 (for coverage) + C1 or C2.  **Risk:** Low–medium (test quality/noise).

---

### C9 — Design-doc linter (anti-hallucination guardrail)  ·  Effort: **S**  ·  Enhancement
> ` [ ] ACCEPT   [ ] DEFER   [ ] REJECT `

**What:** A cheap verifier that checks every module/symbol/path a `design.md` references
actually exists in the PKG (`store.find`/`store.node`), and flags invented ones before Gate 1.5.

**Why:** Small guardrail that makes "grounded design" *actually* grounded — catches the exact
failure mode (plausible but fake file paths) that undermines trust.

**Depends on:** C1 (or the existing M2).  **Risk:** Very low.

---

### C10 — Generalized MCP-backed sources (Confluence, Jira, any source)  ·  Effort: **M**  ·  Enhancement
> ` [x] ACCEPT `   ✅ **tier 1 shipped** (`feat/mcp-sources` → develop) — `mcp-confluence` / `mcp-jira` / generic `mcp`

**Two tiers** (ship tier 1 now; defer tier 2 until a real non-Atlassian server demands it):
- **Tier 1 (this build):** one generic, config-driven `MCPSourceAdapter` + **presets**
  `mcp-confluence` (backward-compatible) and `mcp-jira`, **plus** a generic `mcp` kind whose
  server + tool names come from `MCP_SOURCE_*` env — the escape hatch for *any* onboarded MCP
  server. Result parsing is **unified + lenient** (handles Confluence page shapes, Jira
  `fields.summary`/ADF `description`, and falls back to raw text), so the generic path works
  out of the box for sane servers and degrades honestly for odd ones. Server *onboarding*
  already exists (`MCPRegistry.from_config()` reads `mcpServers`) — tier 1 only adds the
  tool-mapping layer.
- **Tier 2 (deferred, not built):** a richer per-server field-mapping (JSON-path extraction for
  title/body/children). Don't build speculatively — the right abstraction only reveals itself
  once a second real non-Atlassian server exists. Presets stay perfect; generic stays
  best-effort until then.

**What:** Make **every** requirements source reachable through an onboarded **MCP server**,
not just Confluence. Today MCP-backed access is a one-off: `MCPConfluenceAdapter`
(`intake/mcp_source.py`, kind `mcp-confluence`) is hardcoded to Confluence tool names via
`MCPSourceConfig`. Generalize it into a **config-driven `MCPSourceAdapter`** that maps the
seam's two operations (fetch-document / list-children) onto whatever tools a given MCP server
exposes — so `mcp-jira` (issue/subtask tools), and any future source, come "for free" by
supplying tool/arg names, with **no new REST adapter and no direct creds**. The
`mcp-atlassian` server already exposes *both* Confluence and Jira tools, so one onboarded
server would cover both. Register `mcp-jira` (and optionally a generic `mcp://<server>/<root>`
scheme) in the factory + `parse_source_uri`, mirroring the native `jira://` (C3) exactly so
CLI/pipeline parity is automatic.

**Why:** Two motivations the native REST adapters don't serve: (1) **credential model** — many
orgs prefer to route Atlassian access through a governed MCP server rather than spread
`JIRA_*`/`CONFLUENCE_*` tokens into Spine's env; (2) **uniformity** — one MCP onboarding covers
every Atlassian surface, and the same generalized adapter absorbs the *next* source (Linear,
ServiceNow, GitHub Issues) without bespoke code each time. It turns "add a source" from "write
an adapter" into "point at a server + name its tools."

**Exists vs net-new:** The MCP client, registry, onboarding, and the Confluence-over-MCP proof
all exist. Net-new is **refactoring the one hardcoded adapter into a parameterized one** +
registering the extra kind(s). The native REST `JiraSourceAdapter` (C3) stays — MCP is an
*alternative transport*, not a replacement, and its field→`SourceDocument` mapping is a useful
reference for the MCP result-parsing.

**Depends on:** C3 (native Jira read) as the shape to mirror; the MCP client extra (`[mcp]`).

**Risk:** Low–medium. Atlassian MCP servers vary in tool/argument names and result shapes
(the existing adapter already parses leniently) — the config indirection absorbs that, but it
needs live testing against a real `mcp-atlassian` server to pin the Jira tool names/defaults.

---

## Recommended sequence

A pragmatic ordering that front-loads value and respects dependencies. Status as of the
latest work (all landed on the private `develop`):

1. ✅ **C1 — Impact-aware feature design** — *shipped* (`feat/impact-aware-design` → develop).
2. ✅ **C9 — Design-doc linter** — *shipped* (with C1; unverified-reference flag).
3. ✅ **C3 — Jira read-source** — *shipped* (`feat/jira-read-source` → develop; `jira://` ingest).
4. ✅ **C4 — Investigation brief** — *v1 shipped* (`feat/investigation-brief`;
   `orchestrator investigate`). Grounds a ticket on PKG + episteme; cross-run-memory recall
   deferred to a DB-backed v2.
5. ✅ **C10 — Generalized MCP-backed sources** — *tier 1 shipped* (`feat/mcp-sources` →
   develop); generic `MCPSourceAdapter` + `mcp-confluence`/`mcp-jira` presets + a generic `mcp`
   escape hatch. Tier 2 (field-mapping DSL) deferred. Independent of the RCA arc.
6. ✅ **C6 — Stack-trace localizer** — *shipped* (`feat/fault-localizer`); `orchestrator
   localize` parses a traceback/pytest failure → PKG symbols + fault site + callers.
7. ✅ **C2 — Bug-fix / RCA pipeline** — *v1 shipped* (`feat/rca-pipeline`); `orchestrator rca`
   → gated `rca.md` (fault site + ranked hypotheses + regression surface + fix approach).
   Stops at analysis (no autonomous code); deterministic, `--llm` enriches. Bug from a trace,
   `jira://` Bug, or inline text.
8. ✅ **C5 — Cross-layer + TS/Java CALLS** — *shipped* (`feat/cross-layer-impact`); composed
   `impact_across` (CALLS+IMPORTS+REFERENCES) + real CALLS extraction for Java & TypeScript
   (with cross-file resolution). Impact/RCA/coverage now work on TS/Java, not just Python/C#.
9. ✅ **C8 — Blast-radius regression coverage** — *shipped* (with C5); `orchestrator regression`
   → tests that exercise a target vs. impacted production code with no covering test (the gaps).

**Defer:** C7 (observability loop) until C2 is proven.

**Shipped milestone (all live on `develop`):** **C1 + C9 + C3 + C4 + C10** — grounded,
blast-radius-aware design; the design-doc reference linter; real-ticket intake (native `jira://`
+ MCP-backed `mcp-jira`/`mcp-confluence`/`mcp`); and the investigation brief. This completes the
entire **research-and-design front half**. The **RCA arc (C6 → C2)** is the next headline; C5
and C8 round it out.

**Open decisions before C6/C2:** (1) RCA autonomy boundary — gated `rca.md` + fix design
(recommended first) vs. autonomous through to a PR; (2) primary bug entry point — Jira Bugs
(have it via C3), raw stack traces (C6), or failing CI.

---

## What I'd push back on / explicitly consider skipping

- **C7 (Sentry/OTel loop)** — attractive but premature; defer.
- **C5b (TS/Java CALLS)** — *only* worth the L-effort if you actually target TS/Java repos.
  If your live targets stay Python/C#, skip it and keep C1/C2 honest about language limits.
- **A generic "path between any two nodes" query** — I considered proposing it; the concrete
  workflows here need *impact* (reverse BFS, which exists), not arbitrary shortest-path. Don't
  build it speculatively.
- **Making planning (`sdlc_code_plan`) PKG-aware** — deliberately left off. The code itself
  says planning is low-value next to codegen; C1 puts the graph where it matters (design), so
  investing in the plan stage would be redundant.

---

## Open questions for you

1. **Language targets:** are TS/Java real near-term targets? This decides whether C5b is on
   the roadmap or a "skip".
2. **RCA autonomy boundary:** should C2 stop at a gated `rca.md` + fix design (human decides),
   or attempt the fix autonomously through to a PR like the feature path? (I'd recommend the
   former first.)
3. **Bug source of record:** Jira Bugs (C3), raw stack traces (C6), failing CI, or all three —
   which is the primary entry point for C2?
4. **First milestone:** start with **C1 + C9** as a contained proof, or commit to the fuller
   bug-fix arc (C3 → C6 → C2) up front?
