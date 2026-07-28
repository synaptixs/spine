# Changelog

All notable changes to this project are documented here. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/); the package is `synaptixs-spine`
(import/CLI stay `orchestrator`).

## 3.9.0 — Comprehension you can trust

A six-phase overhaul of the understanding layer, driven by an assessment against a real
public repo. The headline: **the dependency graph stopped lying** — relative and
intra-package imports never resolved, so the graph saw almost no internal dependencies
and confidently called a codebase's most central module "a leaf, so it's the safer place
to change". That is fixed in every language front-end at once. On `pallets/click`, import
edges naming a submodule went from 27/232 joined to **321/321**, and
`impact_across("Context")` from **0 symbols to 61**.

Built on that, the committed knowledge base went from 4 sections to 18, gained a
provenance stamp and a CI gate that **proves** it still matches the code, and turned each
module page into a pre-change briefing. Spine now commits its own `episteme/` and fails
its own CI if that knowledge base degrades.

Everything here stays deterministic and LLM-free: same commit in, byte-identical output.

### The import graph stops lying

Phase 0 of the [`understand` enhancement plan](docs/specs/understand-enhancement-spec.md):
relative and intra-package imports now join the modules they denote, in every language
front-end at once — and a standing invariant check makes sure the bug class can't return
unnoticed. Measured on `pallets/click`: import edges naming a click submodule went from
27/232 joined (all from tests) to **321/321**; `impact_across(Context)` from **0 symbols to
61**; the `click.core` area page from *"it's a leaf, so it's the safer place to change"* to
*"it sits in the middle of the graph: 10 areas below it, 8 above."*

#### Fixed

- **Python front-end reads `stmt.level`** — `from .types import X` resolves against the
  module's own package (`py:click.types.X`), so it can no longer be conflated with the
  stdlib `types`. An import that climbs past the scanned tree keeps its dots and never
  falsely joins.
- **`link_imports` post-pass** (`pkg/import_link.py`) — a whole-repo join that repoints
  `IMPORTS` edges at the first-party modules they denote and drops the orphaned phantom
  nodes. One shared resolver; per-language matchers only: dotted-prefix walk (Python /
  Java / C#), relative-specifier resolution (TypeScript), `go.mod` module-path matching
  (Go), unique path-suffix matching for `-I`-style includes (C / C++). Runs inside
  `RepoCodeExtractor.extract`, so every consumer — `understand`, `state`, grounding,
  `pkg export`, the MCP tools — gets resolved imports with no extra wiring.
- Fact-cache format bumped to v2: pre-fix caches would silently reintroduce the dangling
  imports, so they re-extract.

#### Added

- **`orchestrator pkg verify`** — Tier-1 graph invariants, no oracle needed: every edge
  endpoint exists, every grounded provenance resolves to a real `file:line`, per-language
  orphan-rate and external-ratio tripwires (the completeness failures a does-it-run test
  can't see), and phantom-basename warnings. Non-zero exit on error, so it can stand guard
  in CI. Per-language regression fixtures pin the join: a repo using relative imports must
  show non-zero importers for the imported module.

### Episteme can prove it's current

Phase 1 of the [`understand` enhancement plan](docs/specs/understand-enhancement-spec.md):
a committed knowledge base whose entire value is being *code-true* could not tell a reader
whether it described HEAD or a commit from six months ago. Now it says where it came from,
and CI can prove whether it still holds.

#### Added

- **A provenance stamp on `episteme/README.md`** — which commit the bank was generated
  from, whether that tree was dirty, and which Spine rendered it. Deliberately carries **no
  timestamp**: invariant #2 requires the same code to produce byte-identical output, and a
  date would break the property the artifact is trusted for.
- **`orchestrator understand --check`** — writes nothing, re-renders, and diffs against the
  committed bank, exiting non-zero when they disagree. It reports what to look at: pages out
  of date, missing, or still describing code that's gone. The comparison ignores the fenced
  stamp, because committing the episteme itself creates a new commit — content, not the
  stamp, is what proves currency.

#### Fixed

- **`understand` no longer reads its own output.** `episteme/` and the legacy `memory-bank/`
  join the ignored directories. A committed bank was being ingested as the repo's own
  documentation: on a small fixture it turned 6 grounded nodes into 32, all 26 `Doc` nodes
  coming from Spine's own prose. Worse, it made the artifact unable to ever be self-
  consistent — writing the bank changed the graph that rendered it, so no bank could
  describe its own repo twice the same way, and `--check` could never pass.

#### Changed

- `build_memory_bank` is now a thin writer over a new `render_memory_bank`, so the build and
  the check share one rendering path and cannot drift apart.

### One analysis layer, two renderings

Phase 2 of the [`understand` enhancement plan](docs/specs/understand-enhancement-spec.md):
`state` computed sixteen sections and printed them, while the *committed* episteme rendered
four — the ephemeral report was richer than the knowledge base a team reads and an AI tool
grounds on. Both surfaces now read one analysis. On `pallets/click`, episteme goes from 4
top-level sections to 13.

#### Added

- **`knowledge/analysis.py`** — the single pipeline (extract → migrations → data layer →
  docs → profile → metrics) that both `understand` and `state` go through. What differs
  downstream is only rendering.
- **`architecture.md`** gains the **system-architecture diagram** and the strongest
  component dependencies (drawn from `current_state`'s own bounded `architecture_graph`, so
  the two surfaces can't disagree), **layers**, and **test coverage**.
- **`tech-context.md`** gains **infrastructure & runtime**, **entry points**, and
  **most-used external imports** — it was a six-row table, mostly `—`.
- **`progress.md`** leads with computed **suggested next steps** instead of only pointing at
  a `BACKLOG.md` that doesn't exist unless Spine built the repo.

#### Fixed

- **Test coverage measured what it claimed.** An area counted as tested if it *contained* a
  type with "test" in the name — which answers "which areas are tests", not "which areas
  have tests", and reported `click.core`, the most-tested module in click, as untested.
  Coverage is now test→source imports, a lookup that only became possible once Phase 0 made
  intra-package imports resolve. click reads 13 of 27 components exercised, and the untested
  list is now genuinely untested code (`click._winconsole`, the `examples/` trees).
- **Entry points exclude tests.** `main()` inside a test file is a fixture, not how the
  system starts; click's entry-point list was two test functions ahead of the real one.

#### Note

Git-history metrics stay out of the committed bank on purpose. `state`'s "Recent activity"
reads the last ~60 commits, so its value moves on every commit — including the one that
lands the bank — which would make episteme stale the moment it was committed and
`understand --check` fail forever after.

### The module page becomes a briefing

Phase 3 of the [`understand` enhancement plan](docs/specs/understand-enhancement-spec.md):
a module page told you what the code *is*. It now answers what depends on it, what breaks
if it changes, what isn't tested, what it inherits from, and which docs describe it.
Findings 3, 8 and 9 — all of it graph computation, no LLM.

#### Added

- **"Changing this safely" on every module page** — who tests the module, the symbols other
  code most depends on with their blast radius, and which of those have no visible test
  path. This was the product's most persuasive answer and it was only reachable by running
  `regression` against a symbol you had already chosen to change.
- **`store.implementors_of()` / `store.implements_of()`** and inheritance rendered from both
  ends. click's 42 `IMPLEMENTS` edges rendered nowhere; `click.exceptions` now shows all 12
  exception subclasses, and `Parameter` → `Argument`/`Option` is walkable in either direction.
- **"Documented in …"** on modules and symbols, from `MENTIONS`, plus a repo-level
  **Documentation** section with coverage and drift. Four releases of doc ingestion had
  reduced, in the committed bank, to a single `Doc: 264` line; click now reports 6% doc
  coverage and 250 potential drift where only `state` used to.
- **`api-surface.md`** — every route and the code behind it, keyed on `Endpoint`/`EXPOSES`.
  Written only for repos that have routes.
- **`CoverageIndex`** (`sdlc/coverage.py`) — whole-repo test reachability and blast radius
  indexed once. `build_regression_plan` rebuilds a predecessor index per call, which is
  quadratic when every module page needs it; `understand` on click stays at ~1.4s.

#### Note on honesty

The first cut of the safety block reported "16 of 20 symbols have no test" for `click.core`,
naming `Context` — one of the most tested classes in the Python ecosystem. Call resolution is
precision-first (ambiguous `obj.method()` chains are skipped rather than guessed), so an
invisible test path is not an absent one. It now flags only the actionable intersection —
depended upon **and** no visible path — says plainly that invisible ≠ absent, and takes
module-level "tested by" from test **imports**, which are complete in a way call edges aren't.

### No page is a stub or a directory listing

Phase 4 of the [`understand` enhancement plan](docs/specs/understand-enhancement-spec.md) —
Findings 4, 5 and 6 — plus the CI dogfooding left open since Phase 0.

#### Changed

- **`domain-model.md` is ranked, not alphabetised.** With no database it listed 40+ classes
  A–Z and called them *prominent* without computing anything; `Abort` led click's page
  because it starts with A. Now ranked by subtypes, production call-sites, members and doc
  mentions — click's page opens with `Context`, `ProgressBar`, `Command`, `Parameter` — and
  each row says *why* it matters. Retitled too: on a repo with no database, "domain model"
  promises a schema that isn't there.
- **`glossary.md` no longer promises definitions it can't write.** It was 60 alphabetical
  lines of `**Abort** — _TODO: definition_`. Each term now links to where it's defined and
  to the doc that explains it (from `MENTIONS`); private types are excluded.
- **The "Node kinds" dump is gone from `architecture.md`.** `Function: 1938, Field: 400` was
  database statistics in a section of its own. The counts survive as scale on the graph-size
  line, and **Complexity** (size distribution + largest types) takes the slot.
- **`conventions.md`** gains counted naming conventions, test layout, and the error idiom —
  it was four sampled rules and a lint config. **`tech-context.md`** gains the declared
  version and language floor.
- **Production and test call-sites are counted apart** (Finding 6). `echo`'s
  "most-depended-upon" callers were `test_echo`, `test_echo_color_flag`,
  `test_echo_custom_file`. Rankings now use production call-sites only — being called by
  thirty tests makes a symbol well covered, not central — while both numbers are displayed,
  and caller lists lead with production.

#### Fixed

- **Unresolved base classes are recorded instead of dropped.** The Python front-end emitted
  an `IMPLEMENTS` edge only when a base resolved to an import or a local definition, so a
  class extending a *builtin* had no base at all in the graph: `class Abort(RuntimeError)`
  and even `class ClickException(Exception)` answered "extends nothing", and anything
  walking a hierarchy under-counted it. Bare-name bases now emit an external node, exactly
  as unresolved bare *calls* already did. Click's exception hierarchy reads 12 types rooted
  at `ClickException`, matching the source; the name-matching approach found 4.
- A symbol with no edges rendered as a heading followed by silence. It now says so.

#### Added

- **CI runs `orchestrator pkg verify .` and `orchestrator understand . --check`**, and Spine
  commits its own `episteme/`. The product's flagship claim is detecting when docs drift
  from code; until now its own knowledge base could drift silently.

### Answers to questions nobody was asking yet

Phase 5, the last of the [`understand` enhancement plan](docs/specs/understand-enhancement-spec.md).
Finding 10's opportunistic list: findings that need no new facts, only questions aimed at
the *reader* ("where do I start?", "what can I ignore?", "what's tangled?") rather than at
the extractor.

#### Added

- **Onboarding path** on the index — "New here? Read these first": where execution starts,
  then what the most code depends on, each step saying why it's there.
- **Public surface split** — "400 public · 306 internal, of 706". The same codebase,
  reframed as approachable, with the most-depended-upon public symbols listed.
- **Import cycles** — strongly-connected components of the module graph. Undetectable
  before Phase 0, because the graph had almost no first-party import edges to form a cycle
  with; click turns out to have an 11-module core cycle. Iterative Tarjan, because a real
  dependency chain would blow the recursion limit.
- **Possibly-unused candidates** — internal symbols with no caller, subclass or doc
  reference. Restricted to *internal* on purpose: a public function with no in-repo caller
  is what an API looks like. Labelled candidates, not verdicts.
- **`symbol-index.md`** — every first-party symbol A–Z with the page that describes it, so
  the bank is searchable by name without grep.

#### Not done, deliberately

**Churn per module** is the one item from Finding 10 left out. It reads the last ~60
commits, so its value changes on *every* commit — including the one that lands the
knowledge base — which would make the bank stale the moment it was written and
`understand --check` fail permanently. It stays in the ephemeral `state` report.
Tests→module shipped earlier, as Phase 3's "Tested by".

## 3.8.4 — The architecture diagram now explains itself

The 3.8.3 diagram named its components but didn't say what they *do* — boxes read
`CLI · cli.py`, which tells you a module exists, not why it's there. Redrawn so every box
answers "what is this for?", and every layer carries a plain-English line describing what
happens there. Documentation only; no code change.

### Changed

- **Every box now has a purpose line** — `Command line · 41 commands · the main surface`,
  `Hand out credentials · only at the moment of use`. Package paths (`plugin/`, `runtime/`)
  drop to a dimmed third line: useful to a contributor, noise to everyone else. No box is
  labelled with a filename any more.
- **Each layer is narrated.** A sentence under every layer heading says what is happening —
  *"Before writing anything, Spine reads."* — and both gates now read **"Stop."**, spelling out
  that nothing has been written before gate one and nothing pushed before gate two.
- Plainer names over internal jargon: *Read the requirement* rather than `Intake`, *The plan,
  typed* rather than `GraphIR`.
- The image is **72% smaller** (1.3 MB → 0.37 MB) at the same resolution.

## 3.8.3 — Architecture diagram

Adds a full **architecture diagram** and an [ARCHITECTURE.md](ARCHITECTURE.md) that walks the whole
platform end to end — the six layers, every component, the two human gates, and the Product
Knowledge Graph they all read from. Documentation only; no code or behaviour change.

### Added

- **[ARCHITECTURE.md](ARCHITECTURE.md)** — how Spine fits together, layer by layer, with a diagram
  that renders on GitHub and in Spine's own web UI.
- A **static architecture image** (`assets/spine-architecture.png`), shown in the README.

## 3.8.2 — Doc ingestion reaches HTML and Office

3.8.1 folded Markdown, reST, plain text and PDF into the graph. This release adds the
remaining **file** formats teams actually keep specs in — **HTML** and **Word/Excel** — so a
`.docx` architecture doc or an exported HTML spec sitting in your repo becomes `Doc` nodes
`MENTIONS`-linked to the code it describes, exactly like a README.

Still deterministic, still no LLM, still a no-op on a repo with no docs.

### Added

- **HTML ingestion** (`.html`/`.htm`) — no extra needed. `<h1>`…`<h6>` become section
  boundaries (so an HTML doc sections exactly like markdown), and inline `<code>` is
  preserved as a code claim, so the symbols a doc names actually bind. `<script>`/`<style>`
  bodies are ignored; malformed markup is skipped rather than fatal.
- **Word & Excel ingestion** behind a new **`[office]`** extra
  (`pip install 'synaptixs-spine[office]'`). `.docx` maps Word's heading styles to sections
  and treats monospace runs as code claims — the Word equivalent of backticks — and keeps
  table text, which is where spec documents put API and field lists. `.xlsx` gives one
  section per sheet and keeps string cells only (numbers and formula results are data, not
  prose about code). Encrypted or corrupt documents are skipped.
- **Markdown front matter** is now read as prose: the *values* of a `---` block (`title:`,
  `module:`, `tags:`) bind like the text they stand for, while the keys and fences no longer
  leak into the graph as noise.

### Changed

- Documentation formats are now **registered readers** rather than hard-coded branches, so
  adding a format touches no existing one. Behaviour for existing formats is unchanged.
- Standalone `.yaml`/`.yml` files are **deliberately not ingested**. A repo's YAML is
  overwhelmingly configuration, and treating it as documentation would inflate the doc
  coverage `state` reports and flood doc-drift with config values that were never prose.
  YAML's documentary case — front matter — is covered above.

## 3.8.1 — Doc & PDF ingestion: your docs become code-linked facts

Spine now reads a repository's **documentation** — Markdown, reStructuredText, plain text,
and **PDF** — into the Product Knowledge Graph as first-class **`Doc` nodes**, each
**`MENTIONS`**-linked to the code symbol it describes. So comprehension can answer *"which
docs describe `X`?"*, *"how documented is this?"*, and *"do the docs still match the code?"* —
all deterministic, no LLM. This is the *knowledge-doc* half of Spine's doc story; the
*structured-doc* half (OpenSpec `openspec://` → intents) already shipped. It closes the
biggest remaining reach gap vs. doc-graph tools.

Nothing to configure: docs are folded in automatically when you run `orchestrator understand`
or `orchestrator state`. A repo with no docs behaves exactly as before.

### Added

- **Doc ingestion** — `understand`/`state` now emit `Doc` nodes + `MENTIONS` edges. Binding is
  **precision-first**: a mention becomes an edge only when it resolves to exactly one symbol.
  Reuses the deterministic doc→symbol binder already in `pkg/docs.py`.
- **PDF support** behind a new **`[docs]`** extra (`pip install 'synaptixs-spine[docs]'`, lazy
  `pypdf`). The base install stays stdlib-only; malformed or scanned (image-only) PDFs are
  skipped, never fatal — no OCR.
- **`state` Documentation section** — doc count, **symbol coverage %** (how much of the code the
  docs describe), and top **doc drift** (doc claims about code the graph can't resolve —
  renamed/removed symbols), filtered to real symbols so paths/URLs/filenames don't drown it.
- **`docs_for` `/spine` MCP tool** — with a `symbol`, the docs that describe it; with no symbol,
  a doc-coverage summary + top drift. Joins the read-only comprehension tool set; documented in
  the Claude/Codex guides and the `understand-codebase` skill.
- **Section-granular `Doc` nodes** — Markdown is split by heading into `doc:README.md#usage`
  nodes (bounded), so a `MENTIONS` edge points at the *section* that names a symbol, with
  provenance at the heading line.
- **Doc-grounded codegen** — `sdlc feature` grounding now folds a reused symbol's documenting
  prose into the codegen context, so generated code sees not just an API but what it's for.
- **Doc-drift review finding** — `GroundingVerifier.doc_findings` surfaces stale-doc symbol
  claims as an informational, source-anchored finding.

## 3.8.0 — The `/spine` comprehension skill

Spine's read-only comprehension is now a **drop-in skill** any assistant can call — Codex
(plugin) and Claude Code (an `understand-codebase` Agent Skill) — so you can ask about a
codebase in plain language and get engineering *decisions*, not just a map: what a change
breaks, what's untested, and where a ticket or bug lands, each grounded to `file:line`.

### Added

- **Comprehension MCP tools** on the Spine plugin server, all read-only, deterministic
  (no LLM), and needing no credentials: `map_repo` (structure, call-hotspots, coverage
  gaps, recommendations), `blast_radius` ("what breaks if I change X" — callers +
  cross-layer reach), `explain_symbol`, `investigate` (where a ticket lands), `localize`
  (stack trace → fault site), and `regression_gaps` (blast-radius symbols with no covering
  test). Each returns structured fields **plus** a `markdown` rendering. They join
  `read_memory_bank` (a repo's committed `episteme/`).
- **`root_cause`** — a grounded root-cause report (fault site, ranked hypotheses with
  evidence, regression surface, fix approach). Deterministic by default; `use_llm=true`
  opts into LLM-enriched hypotheses.
- **`understand-codebase` Agent Skill** bundled with the Claude Code plugin — tells Claude
  which tool to reach for, so you just ask in plain language.
- **git-URL support** across the comprehension tools — point them at a local path *or* a
  git URL (shallow-cloned behind the same host allow-list as the CLI). Serve them to a
  remote host over HTTP with `orchestrator-mcp --http`.

## 3.7.0 — Go: the 8th PKG language

Go is now a first-class language across the whole stack — comprehension, the call and
interface graph, and greenfield **and** brownfield codegen — so `understand`, `state`,
`design`, `investigate`, `localize`, `rca`, `regression`, grounding, and
`sdlc feature --language go` all work on Go repos. Install with the `go` extra
(`pip install 'synaptixs-spine[go]'`); codegen needs the `go` toolchain on PATH.

### Added

- **Go comprehension** (`go` extra, tree-sitter-go) — `Module`/`Type`/`Function`/`Field` +
  `IMPORTS`/`CONTAINS`. Go's module unit is the **package = its directory**, so every `.go`
  file in a dir merges into one component (the first front-end where that holds).
- **Go call + data + interface graph** — `CALLS` (same-file package functions and
  receiver-method calls), `REFERENCES` (same-package struct-field types), and the Go
  highlight, **`IMPLEMENTS` by method-set matching**: because Go has no `implements` keyword,
  a concrete type is linked to each in-repo interface it structurally satisfies (matched by
  method name + arity over value **and** pointer receivers). So blast-radius, `design`,
  `rca`, and `regression` light up on Go.
- **Go codegen** (`sdlc feature --language go`) — scaffolds/extends a module and builds +
  tests it with `go build ./...` / `go test ./...`, with co-located `_test.go` tests. It is
  **multi-module aware**: the runner builds and tests the module(s) a change actually
  touches (not just the repo root), so code generated into a sub-module is never a false
  green.

### Changed

- **`sdlc feature --language` is now validated** against the supported set — an unknown value
  errors instead of silently scaffolding a Python project.

## 3.6.1 — Shareable codebase-intelligence report

`orchestrator state . --out report.html` now emits a single **self-contained HTML file** you
open in a browser and forward to your team — the engineering-decision counterpart to a
concept-map `graph.html`. Deterministic, no LLM, nothing fetched. It packages the analysis
`state` already computes, so this is rendering, not new comprehension.

### Added

- **Shareable HTML report** — `orchestrator state . --out report.html` writes one
  self-contained, theme-aware (light/dark) file with a provenance header, plain-language
  overview, architecture diagram, blast-radius hotspots, risk & health, test-coverage gaps,
  security surface, recent activity, and prioritized recommendations. `--out *.html` selects
  HTML; any other extension keeps today's markdown. `--no-timestamp` gives byte-stable output
  for CI diffs. The `--lens stakeholder` view drops the jargon-heavy sections.
- **Deterministic architecture diagram** — an inline SVG (components grouped into zones,
  weighted dependency arrows) laid out seeded-in-Python, so the same commit renders the same
  picture; it grid-wraps large zones to stay legible and themes with the page (no mermaid, no
  external assets).
- **Graph-quantified blast radius** — the spotlight quantifies the cross-layer impact of the
  top hotspot via `impact_across` ("changing X → N dependents across M files") and lists
  blast-radius symbols with no covering test via the regression plan (`build_regression_plan`).
- **In-browser filter** — a client-side search box hides non-matching rows, dims non-matching
  architecture components, and collapses emptied sections; vanilla JS, no build step, still one
  self-contained file.

## 3.6.0 — Knowledge-graph-grounded design & RCA

A suite of new, deterministic-first CLI commands that ground engineering work — design,
debugging, and root-cause analysis — in the Product Knowledge Graph, plus the call-graph
extraction that makes them work across languages. Every command is inspectable and states
its own limits rather than implying certainty.

### Added

- **`orchestrator design`** — spec × knowledge graph → a grounded design with a **blast
  radius** (which modules a change touches, who imports them, the call hotspots) and an
  **unverified-references** flag for named paths absent from the graph. Deterministic by
  default; `--llm` writes the prose.
- **`orchestrator investigate`** — research a ticket against the codebase before designing:
  where it lands in the code (real symbols with `file:line` + caller counts) and the relevant
  committed `episteme/` knowledge. Ticket from a source URI or inline.
- **`orchestrator localize`** — parse a stack trace / pytest failure and resolve each frame to
  the repo symbol it names, pointing at the likely fault site and its callers.
- **`orchestrator rca`** — a gated root-cause report: fault site, ranked root-cause
  *hypotheses* with evidence (exception priors, recent git churn, call sites), the regression
  surface a fix must cover, and a scoped fix approach. Stops at analysis — no autonomous code.
- **`orchestrator regression`** — blast-radius regression coverage: split the call-graph
  impact of a change into tests that already exercise it vs production code with no covering
  test (the gaps).
- **Jira as a read source** (`jira://PROJ-123` / `jira://PROJ` / `jira://jql/…`) — ingest
  existing issues as requirements, the read counterpart to the Jira issue-tracker sink.
- **Generalized MCP-backed sources** — `mcp-jira` and `mcp-confluence` presets plus a generic
  `mcp` escape hatch, so any onboarded MCP server can back intake (route access through a
  governed server instead of spreading REST tokens).

### Changed

- **Call graphs across the stack:** the Java and TypeScript front-ends now extract `CALLS`
  edges (precision-first; TypeScript resolves relative imports to the definition, so
  cross-file call graphs connect). Impact, RCA, and regression coverage now work on Python,
  C, C++, C#, Java, and TypeScript.
- **`FactStore.impact_across`** — composed transitive blast radius over CALLS + IMPORTS +
  REFERENCES, so impact traces across the code, module, and data layers.
- The README banner now shows the platform's full capability map rather than a single pipeline.

## 3.5.0 — Security hardening

This release is the output of a security baseline of Spine's own source tree. Nothing
here is a claim that the codebase is "secure" — it is a description, verifiable against
this repository, of the checks we now run and the issues we found and fixed.

### 🔒 Security

- **Continuous checks in CI, on every pull request:**
  - **CodeQL** dataflow analysis for Python and JavaScript.
  - **`pip-audit`** over the resolved lockfile (not the ambient environment — bare
    `pip-audit` in a uv checkout audits the wrong thing and false-passes).
  - **`bandit`-class static analysis** via ruff's flake8-bandit (`S`) rules, wired
    into the existing lint gate.
  - **Dependabot** for weekly dependency and GitHub-Actions updates.
- **A multi-model adversarial self-review** across the full source tree: 863 candidate
  findings were triaged by one model, then independently verified by a stronger model
  instructed to *refute* each one. 174 of the high-severity candidates were refuted as
  safe-by-design; **7 confirmed issues were fixed, each with a regression test.**
- **All patchable dependency CVEs resolved** — 17 of 18 known advisories fixed by
  version bumps (aiohttp, starlette, cryptography, langsmith, langgraph, pydantic-
  settings). The one remaining (`click`'s `click.edit()` command injection) is
  unreachable — Spine never calls that function — and is documented rather than
  force-fixed, because the fix would regress the `semgrep` scanner by ~2 years.
- Coordinated disclosure via [SECURITY.md](SECURITY.md).

### Fixed

Security fixes from the review above, described at the level of *what class of issue*
rather than a reproduction:

- **Path traversal** in the knowledge-base reader and the `memory-bank` capability
  endpoint — an untrusted section name or a symlink committed in a cloned repo could
  read files outside the intended directory. Reads are now confined to the bank dir.
- **Stored XSS** in the operator web UI — the shared HTML escaper escaped `&<>` but not
  quotes, so an untrusted value (e.g. a cloned-repo file name) placed in a quoted HTML
  attribute could break out. The escaper now escapes quotes across all web UI files.
- **SSRF backstop** for remote-repo cloning — the internal-host guard missed obfuscated
  IPv4 encodings (integer, hex, octal, short-form) that resolve to loopback. These are
  now normalized and blocked. (The guard was already robust under its default
  restrictive host allow-list; this hardens the opt-in `*` mode.)
- **Prompt-injection hardening** in the codegen/design/review pipeline — untrusted
  cloned-repo content fed into LLM prompts is now fenced and marked as data, and the
  review judge is instructed to ignore injected verdicts. This is defense-in-depth; the
  human merge approval remains the authoritative gate.

### Added

- `SECURITY.md` disclosure policy surfaced in the README.
- Security review plan and methodology in `docs/specs/security-review-plan.md`.
