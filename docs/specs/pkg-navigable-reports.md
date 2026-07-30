# Design + Plan: navigable markdown reports — comprehension you can walk

**Status:** **All four phases shipped** on branch `feat/episteme-navigable-reports`
(`5daf1b8`, `42d3776`, `c0b5579`, + phase 4). Turns the memory bank from seven flat,
dead-end documents into an **interlinked, walkable knowledge base**: a page per component,
bidirectional links between them, deep links into the source, and a high-level explanation
at the top of every page.

Supersedes the graphical/HTML direction (`state --format html`, SVG layouts, force
diagrams). **Markdown is the deliverable.** It renders everywhere — GitHub, IDEs, our own
`md.js` — needs no build step, is deterministic by nature, and reviews as a diff.

> The premise is unchanged from the visual track and is worth restating: the PKG already
> knows all of this. The work is a renderer, never an extractor.

---

## The problem, in one line of real output

From a live `memory-bank/architecture.md`:

```
## Call hotspots (most-called functions)
- `parse_metadata` — 4 call-sites
```

**Every question a reader now has is unanswerable from the page.** Which 4 call sites?
Where is `parse_metadata` defined? What does it call? What breaks if I change it? The PKG
answers all four — `callers_of`, `callees_of`, `provenance.file`/`.line` — and the renderer
drops every one on the floor. The reader's next move is to leave the document and grep.

**A navigable report's job is to make "leave and grep" unnecessary.**

## Where we are today

| Already true | Where | Consequence |
|---|---|---|
| `provenance` (file, line, end_line) is on every grounded node | `pkg/facts.py` | Exact source locations exist for every fact… |
| …and **`renderers.py` never reads `.file` or `.line`** | `knowledge/renderers.py` | **Not one link to source in the entire memory bank** |
| Links exist only in the index | `render_index` | 6 one-way links, README → file. Nothing links to anything else; nothing links back |
| Bidirectional queries already exist | `pkg/store.py` | `callers_of`/`callees_of`, `references_of`/`dependents_of`, `children_of`, `impact_of` — **the back-and-forth is already in the query layer**, unused by any renderer |
| The file set is fixed and flat | `understand.py:65-79` | 7 hard-coded names, `write_text`, **no orphan cleanup** — fine for fixed names, unsafe for a dynamic page set (see Risks) |
| Every page carries a "generated; edits advisory" header | `renderers.py:_doc` | The regeneration contract is already established |

## What we're building

A memory bank you can **walk**, not just read:

```
episteme/                — renamed from memory-bank/ in phase 1
  README.md              — index + how to navigate + graph size
  architecture.md        — the area map; every area links to its page
  domain-model.md        — entities; each links to its page
  tech-context.md        conventions.md  glossary.md  progress.md   (as today)
  areas/<area>.md        — one page per component: explanation, modules, uses/used-by
  modules/<module>.md    — one page per module: symbols (as anchors), imports, importers
```

Three link classes, and they are the whole feature:

1. **Down** — index → area → module → symbol anchor. Progressive disclosure: high-level
   first, detail on click.
2. **Back** — every page breadcrumbs to its parent, and every relationship is rendered
   **from both ends**. `parse_metadata` lists *Called by*; each caller lists *Calls*. That
   is the "navigate back and forth" ask, and `store.py` already answers both directions.
3. **Out** — every symbol deep-links to its definition:
   `[`metadata_parser.py:42`](../../src/metadata_parser.py#L42)`. Clickable in GitHub and
   every IDE. This is the single highest-value link class and it is pure `provenance`.

**Symbols get anchors, not files.** `modules/foo.md#parse_metadata` is linkable without
emitting a file per symbol — which matters at dlib scale (10.7k nodes). Pages are for
areas and modules only.

### What "high-level explanation" means (and the constraint it must respect)

Every page opens with prose, before any table: **what this is, how it fits, what to know**.
The report should teach, not just enumerate.

**This is where the design tension lives.** `understand` is deterministic and no-LLM today,
and that property is why it's trusted (see CLAUDE.md invariant 2). Genuine explanatory prose
is the one thing facts alone can't produce. Resolve it as **two clearly-separated tiers**:

| Tier | Source | Default |
|---|---|---|
| **Derived** — "what this is" | Templated from facts: role in the graph, size, its most-connected neighbours, whether it's a leaf/hub, what depends on it | **Always on. No LLM. Deterministic.** |
| **Authored** — "why it matters" | Cached prose, committed, regenerated only on explicit request | **Opt-in**, clearly marked, never blocks the derived tier |

The derived tier alone is a large step up from `- Function: 40`. Ship it first; treat
authored prose as a separate, later decision. **Never let tier 2 become a hard dependency
of tier 1** — a memory bank that needs an API key to regenerate is a memory bank that rots.

## Why markdown is the right call (not a consolation prize)

- **Renders everywhere already** — GitHub, IDE preview, `/app/memory-bank` via `md.js`. No
  renderer to write, no build step to protect, no CSP.
- **It diffs.** This is the sleeper feature. Committed reports mean `git diff` shows
  *architectural drift* in a PR: a new dependency edge, a hotspot that grew, an entity that
  gained a field. A picture can't do this. **Design for it explicitly** — stable sort order
  everywhere, one fact per line, so a real change is a small diff and not a reshuffle.
- **Deterministic for free** — no layout, no seeds, no positions to stabilise.
- **Greppable and tool-friendly** — the reader's fallback (grep) still works, and so does
  every downstream agent reading `read_memory_bank`.
- **Diagrams are markdown too.** Mermaid renders natively on GitHub and in IDEs, so the
  visual layer costs a fenced block, not a rendering stack (phase 3).

## Diagrams — what is and isn't derivable

Worth stating plainly, because "architecture diagram" blurs two very different things and
only one of them is buildable from the PKG.

**Derivable today — structure ("what depends on what").** `_architecture_mermaid()` in
`knowledge/current_state.py` already does this: areas as nodes, zones as `subgraph`s,
strongest coupling edges labelled with counts, test-origin edges dropped, bounded to 14
edges / 18 nodes. It is decent, and it is **trapped**: only in `state --lens developer`,
**absent from the memory bank entirely** (`architecture.md` has no diagram), and rendered as
raw text in our own UI (`md.js` escapes every fence). The work in phase 3 is **placement and
scoping — not building a renderer.**

**NOT derivable — behaviour ("what happens when X runs").** A flow chart implies order and
branching. The PKG is a structure graph:

| Wanted | PKG reality |
|---|---|
| Branching (`if`/loop/try) | **Not modelled at all.** There is no control flow to draw |
| Call ordering | `CALLS` is a *set*. Sorting call sites by `provenance.line` approximates order, but any conditional or loop makes that a guess |
| Entry points to root a flow at | **Only `csharp_extractor.py` emits `Endpoint`/`EXPOSES`.** Python, Java, TS, C, C++, SQL emit none — so "what happens on `POST /login`" cannot be rooted for most repos |
| Complete call edges | Static extraction misses dynamic dispatch, interfaces, DI, callbacks |

So: **do not ship anything called a flow chart.** A diagram that looks authoritative while
silently omitting the polymorphic call is worse than no diagram (principle 5). Behavioural
flow needs entry-point extraction across six front-ends — a *new fact*, therefore a
different spec (principle 1), and it should be priced as one.

## Phases

| Phase | Work | Effort | Exit |
|---|---|---|---|
| **1** ✅ | **Rename `memory-bank/` → `episteme/`** (directory only) **+ source links and backlinks in the existing pages.** No new files. Hotspots/module map link to source; hotspots list their callers | ~3d | **Done** — `5daf1b8`. See "Phase 1 — shipped" below |
| **2** ✅ | **Module pages.** `modules/<module>.md` with symbol anchors, imports/imported-by, source links; `architecture.md` links down. Orphan reaping | ~4d | **Done.** See "Phase 2 — shipped" below |
| **3** ✅ | **Area pages + derived explanation + scoped diagrams.** `areas/<area>.md` grouping modules (reuse `_area_of`'s CONTAINS-walk); derived "what this is" prose; a **scoped** mermaid map + linked legend per page | ~3d | **Done.** See "Phase 3 — shipped" below |
| **4** ✅ | **Domain-model + blast radius.** Entity pages, FK links both ways; `impact_of()` rendered as "what changes if I touch this" | ~3d | **Done.** See "Phase 4 — shipped" below |

**All four phases are shipped.** Phase 1 stood alone, as intended.

### Phase 4 — shipped

`domain-model.md` is a table linking to a page per table; each entity page carries its
columns, its foreign keys **both ways**, and the code that reads or writes it. Hotspots on
`architecture.md` now report transitive blast radius.

| Built | Where |
|---|---|
| READS/WRITES indexed by entity (the store had no query for them) | `renderers.DataLayer` |
| Entity pages, FK both ways, readers/writers | `renderers.render_entity_page` / `select_entity_pages` (`MAX_ENTITY_PAGES = 50`) |
| Domain model as a linked table | `renderers.render_domain_model(src=, entity_pages=)` |
| Blast radius on hotspots | `render_architecture` via `store.impact_of` |

**The FK backlink was missing, exactly like the import backlink in phase 2.**
`domain-model.md` rendered `references_of` only — what a table points *at* — so `customers`
never showed that `orders` depends on it. That is the half a reader needs before changing
anything. Both ends now render. *Third occurrence of this bug class: **whenever an edge is
rendered, check both directions are reachable.***

**Hotspots were dominated by code you cannot change.** The raw `top_called_functions`
ranking on this repo was `pytest.raises` (177), `json.dumps` (139), `httpx.AsyncClient`
(137), `os.getenv` (122) — all third-party, mostly called from tests. A blast radius for
`json.dumps` is worse than noise; it crowds out the code you own. Hotspots now filter to
grounded, non-test symbols (`create_app` — reaches 115 symbols; `run_env_checks` — 43), and
`understand` requests a deep candidate pool (`HOTSPOT_CANDIDATES = 200`) because ten raw
candidates filter down to nearly nothing. The module map had always excluded externals and
tests; the hotspot list never did.

**Measured before designing:** `impact_of` costs ~0.8s for 10 hotspots on a 981-module
repo, so it is bounded to the displayed hotspots and deliberately *not* offered per-symbol
on every module page. Total 5.7s (faster than the unfiltered 6.2s — first-party symbols
have smaller radii). 87/87 links resolve across both fixtures.

**Field names are table-qualified** (`customers.id`), so the page strips its own table
prefix — otherwise every row repeats the table it is already under.

### Phase 1 — shipped (`5daf1b8`)

Landed as specified, plus what the work turned up:

| Built | Where |
|---|---|
| `episteme/` rename, directory only; identifiers untouched | `understand.BANK_DIRNAME` |
| Read-side legacy fallback | `understand.existing_bank_dir()` |
| Link-or-degrade decision | `understand._source_prefix()` → `None` outside the repo |
| Source deep-links + caller backlinks | `renderers._link()`, `renderers._caller_line()`, `render_architecture(..., src=)` |
| The three helper-bypassing literals, folded onto the helper | `capabilities.py`, `cli.py`, `sdlc/activities.py` |
| `CLAUDE.md` — the repo had none | repo root |

**Verified live** (the point of shipping phase 1 first): 9/9 source links resolve from the
bank, regeneration is byte-identical, `--out` outside the repo emits no link syntax at all,
a legacy `memory-bank/` still reads. Gate green: `mypy src tests`, ruff, 1659 tests.

**Two latent diff-churn bugs found and fixed** — both would have made committed docs
reshuffle on unrelated code changes, destroying the diffability argument: the module map had
no name tie-break, and `stats.summarise_store`'s `most_common` had no id tie-break (both sat
on dict/insertion order). There is now a test asserting byte-identical output across runs.

**One design fix from looking at real output** (exactly what phase 1 was for): the
degraded no-link form first read `` `app.core` — `src/app/core.py:1` — 1 types``, two
em-dashes in one line. It is a parenthetical now.

**Carried into phase 2:**
- `_MAX_CALLERS = 5` elides the caller tail with "+N more" — itself a dead end. **Module
  pages are where the full list belongs**; link there instead of eliding.
- Two sentences of hardcoded orientation prose landed in `architecture.md` early (phase 3
  owns derived prose). Fine as placeholders; replace with the derived tier, don't grow them.

### Phase 3 — shipped

`architecture.md` now opens with an **Areas** table; each area page carries derived prose,
a scoped diagram, a linked legend, its modules, and its dependencies both ways.

| Built | Where |
|---|---|
| Shared area/zone grouping — one definition, two renderers | **`knowledge/areas.py`** (`area_of_name`, `zone_of`, `AreaIndex`); `current_state.py` now aliases it |
| `edges_of_kind()`, `parents_index()` — whole-graph aggregation + the upward walk | `pkg/store.py` |
| Module→module import graph, resolved | `renderers.ModuleDeps` |
| Areas, derived prose, scoped diagram + legend | `renderers.collect_areas` / `_area_prose` / `_area_diagram` / `render_area_page` |

**The seam is closed.** `state` and the episteme now share `knowledge/areas.py`, so they
cannot disagree about what a component is. That was the risk flagged in CLAUDE.md; the
refactor is behaviour-preserving (existing `state` tests pass untouched).

**A real bug in phase 2 surfaced — the fixture had lied.** `IMPORTS` edges target the
imported **symbol**, not the module (`py:web.views -> py:app.core.slugify`). So
`importers_of(module_id)` is *always empty* — nothing ever points at a module id — and
"Imports" listed symbols (`slugify`) rather than dependencies. Phase 2's test passed only
because its hand-built fixture wired module→module edges, encoding the wrong assumption.
`ModuleDeps` resolves both endpoints up to their owning module; the fixture now mirrors
what the front-ends actually emit. **Lesson: a fixture that asserts your assumption instead
of the extractor's output tests nothing.**

**Measured:** 5.5s for this repo (43 areas, 82 files) — areas cost ~0.2s over phase 2.
57/57 links resolve; 26 diagrams carry no `click` directives and no duplicate nodes.

**Derived prose is the deterministic tier**, as specified: it states size, zone, and role
("it's a foundation, so changes ripple outward" / "it's a leaf, so it's the safer place to
change") from graph facts alone. No LLM, no network. The authored tier remains unbuilt and
optional.

### Phase 2 — shipped

The walk exists: `README.md` → `architecture.md` → `modules/<mod>.md` → symbol → source,
with breadcrumbs back at every step.

| Built | Where |
|---|---|
| `imports_of()` / `importers_of()` — the module-level `callers_of`/`callees_of`, which the store lacked | `pkg/store.py` |
| Page selection, capped and size-ranked | `renderers.select_module_pages()` (`MAX_MODULE_PAGES = 50`) |
| Name → filename, collision-free | `renderers.module_page_slugs()` |
| The page: symbols as anchors, calls both ways, imports both ways | `renderers.render_module_page()` |
| Module map links *down* to pages | `render_architecture(..., page_of=)` |
| Orphan reaping | `understand._reap_orphans()` |

**Measured, not guessed:** this repo (981 modules / 530 first-party) → 57 files in **5.3s**,
so the O(modules × edges) `children_of` scan needs no optimisation yet. All 32 links resolve
across both directory levels. 1668 tests green.

**Decisions made while building:**
- **`MAX_MODULE_PAGES = 50`.** 530 pages is an unreviewable PR; open5gs would be thousands.
  The tail is reported on `architecture.md` ("showing the 25 biggest of 530").
- **A module with a page is linked *to*; one without links to its source.** Never link to a
  page we didn't write — a broken internal link is worse than an honest external one.
- **`_MAX_CALLERS_PAGE = 25` vs `_MAX_CALLERS = 5`.** `architecture.md` is orientation and
  elides at 5; the module page is the detail view and elides at 25. This is the phase-1
  "+N more is a dead end" note being paid off — partially. See below.

**Still open after phase 2:**
- The `+N more` elision *still* dead-ends on the module page, just later. A symbol with 40
  callers has no complete list anywhere. Either accept it (the graph is the record) or give
  hotspot symbols their own anchor target — decide in phase 3, don't let it drift.
- `select_module_pages` and `render_architecture` each walk `children_of` over every module
  (O(M×E) twice). Fine at 5.3s; revisit if area pages add a third pass.

### Phase 1 — the rename: `memory-bank/` → `episteme/`

**Decided.** The bank is renamed **`episteme/`** — Greek for *justified, grounded knowledge*,
explicitly opposed to *doxa* (mere opinion). That is not decoration: it states the contract
every generated file already carries (*"the PKG is source of truth, edits here are
advisory"*). This is knowledge derived from evidence, not a recollection — `episteme`, not
a memory bank.

It also **buys differentiation**. "Memory bank" is a term of art from hand-maintained
AI-assistant conventions; ours is *derived and code-true*, which is the whole argument in
[positioning-vs-supernova.md](../positioning-vs-supernova.md). An unfamiliar name signals
"this is not the hand-written thing you know" — the recognition we lose is recognition of
the wrong thing.

**Scope: the directory only.** Public identifiers stay put — `read_memory_bank` (MCP),
`GET /v1/capabilities/memory-bank`, `ORCHESTRATOR_MEMORY_BANK_DIR`, `build_memory_bank()`,
`memory_bank_dir()`. Spine ships on PyPI, so those are contracts with downstream consumers,
and this mirrors the split already in force: **brand = Spine, artifacts stay
`orchestrator`** ([spine-product-naming]). Renaming identifiers is a separate, later,
alias-or-major-bump decision — do not smuggle it in here.

**It is not a one-line change — the dir helper is bypassed in four places.** Any rename must
catch the literals, not just `memory_bank_dir()`:

| Hardcodes the dir name | Note |
|---|---|
| `knowledge/understand.py` | the helper itself — the only *intended* source of truth |
| `registry/api/capabilities.py` (~168) | `root / "memory-bank"` — **the HTTP endpoint bypasses the helper** |
| `cli.py` (~1370) | `Path("memory-bank") if is_remote else repo / "memory-bank"` |
| `sdlc/activities.py` (~452) | writes the bank into a worktree |

Fold them all onto `memory_bank_dir()` as part of this phase — otherwise the rename is
half-applied and the endpoint silently reads a directory that no longer exists.

**Back-compat for existing repos.** Writes always go to `episteme/`. **Reads fall back**: if
`episteme/` is absent and `memory-bank/` exists, read the legacy dir (and say so). Without
this, every repo that already committed a bank silently reports "no knowledge yet". Do
**not** auto-delete or auto-migrate the old directory — it is the user's committed content;
surface it and let them move it.

**Open:** the SDLC artifact bundle keys are prefixed `memory-bank/…`
(`sdlc/comprehension.py`, consumed by `sdlc/design.py` and `activities.py`). Those are a
*stored* contract — old runs already carry those keys — so renaming the prefix breaks
reading historical artifacts. Recommend leaving the keys alone in phase 1 and treating them
as internal storage naming.

### Phase 3 — diagram placement (scoping is the whole trick)

Reuse `_architecture_mermaid()`'s approach; change **what it's aimed at** and **where it
lands**.

**1. Scope the diagram to the page.** Today's diagram is capped at 14 edges / 18 nodes
*because it draws the whole repo* — the cap is what makes it a hairball. On an area page,
draw only **that area and its immediate neighbours**: ~8 nodes, no cap needed, readable
*because it's scoped*. Same renderer, aimed at less. Each page's diagram answers one
question — "where does this sit, and what touches it?"

Placement, one diagram per page, each scoped to that page's subject:
- `architecture.md` — the existing whole-repo area map (it belongs here and is missing today)
- `areas/<area>.md` — the area + its neighbours
- `modules/<module>.md` — the module's imports/importers (only if it earns its place; a
  two-node diagram is worse than a sentence)

**2. Clickable nodes: RULED OUT — verified, do not retry.** The plan was
`click A href "modules/foo.md"`, making the diagram the index. **GitHub does not allow it.**
Mermaid gates `click` behind `securityLevel` (strict since 8.2); GitHub renders with the
strict default and gives no way to set `loose`. Worse than merely inert: using `click` can
make GitHub refuse the diagram outright — *"This content is blocked. Contact the site
owner"* ([community #46096](https://github.com/orgs/community/discussions/46096),
[#106690](https://github.com/orgs/community/discussions/106690)). At best the cursor changes
and ctrl-click sometimes works.

**So: never emit `click`.** It buys nothing on our primary consumer and risks killing the
whole diagram. **Fallback (now the design): a linked legend directly beneath the diagram** —
node label → its page. The picture orients; the legend navigates. Degrades perfectly: it is
just a list, and it works everywhere markdown does.

This is why phase 3 costs ~3d, not 5 — the diagram is illustration, and the *pages* remain
the navigation. Answered the ~10-minute gate before building; the answer paid for itself.

**3. Keep it deterministic and diff-stable.** Mermaid text is generated, so node order and
edge order must be totally sorted (principle 4) or every regeneration churns the diff.
Mermaid computes layout at render time, so there are no positions to stabilise — one of the
reasons markdown beats SVG here.

**Reuse, don't fork.** `_architecture_mermaid` builds on `CurrentState.coupling`/`area_types`;
the bank builds on `FactStore`. Rather than a second diagram renderer, lift a
`render_area_diagram(nodes, edges, *, links) -> str` helper both call — otherwise `state`
and the memory bank will drift into showing different architectures for the same commit
(principle 6, and the zoom-level seam in CLAUDE.md).

## Risks / the things that will bite

- **Orphan pages.** `understand.py` writes a fixed dict with `write_text` and never deletes.
  A dynamic page set **must reap**: delete files under `areas/`/`modules/` not in this
  run's output. Miss this and deleted modules haunt the bank forever — worse than no page,
  because it's a confident lie.
- **Diff churn.** Any unstable ordering (dict order, ties broken arbitrarily) turns every
  regeneration into a 500-line diff and destroys the diffability benefit. **Sort totally and
  explicitly**, including tie-breaks.
- **Path escaping.** Module names become filenames — `src/smf/smf-sm.c`, `App.Api`,
  `cpp:A::A`. Need a deterministic, collision-free, filesystem-safe slug. Non-trivial across
  7 languages; get it right once in one helper.
- **Source links break when `--out` leaves the repo.** Links are relative to the bank's
  location; `$ORCHESTRATOR_MEMORY_BANK_DIR` or `--out /tmp/x` breaks them. Compute relative
  paths from the actual target dir, and degrade to plain text (never a broken link) when the
  target is outside the repo.
- **Scale.** 10.7k nodes (dlib) / 8.6k files (open5gs) must not become 8.6k pages. Bound
  pages to top-N modules by members, list the tail in a table on the parent, and report the
  cut honestly (CLAUDE.md invariant 7).
- **`md.js` link handling** — verify relative links between generated pages resolve at
  `/app/memory-bank`; it may only serve a flat file list.
- **`md.js` escapes ```mermaid** — phase 3's diagrams render as raw text in *our own* UI
  while looking fine on GitHub. Small fix, but it lands with phase 3 or the web view
  regresses from "no diagram" to "a wall of arrow syntax".
- **Two diagram renderers drifting.** `_architecture_mermaid` reads `CurrentState`; the bank
  reads `FactStore`. Share the emitter or they will disagree about the same commit.

## Principles

1. **Render, never re-derive.** Reads existing facts only. New fact → different spec.
2. **No dead ends.** Every symbol named is a link. Every relationship renders from both
   ends. If a page raises a question the PKG can answer, it must link to the answer.
3. **Deterministic, no-LLM by default.** The derived tier never needs a network call.
4. **Diff-stable.** Total sort order; one fact per line. A regeneration with no code change
   must produce a zero-line diff.
5. **Bound honestly.** "Top N of M", never an implied complete list.
6. **Explain before enumerating.** Prose first, tables second.

## Non-goals / open questions

- **Not a wiki.** Generated and regenerated; edits stay advisory (existing contract).
- **Deferred: authored/LLM prose tier.** Ship the derived tier first, decide later.
- **Deferred: all HTML/SVG rendering.** Superseded by this spec. Mermaid-in-markdown
  (phase 3) is **not** a revival of that track — it is markdown, and it needs no renderer.
- **Deferred: behavioural flow charts.** Not derivable (see Diagrams). Would require
  entry-point extraction across six front-ends — a new fact, a separate spec.
- **Open:** does GitHub strip mermaid `click` directives? Decides whether phase 3's diagrams
  navigate or merely illustrate. Test before building.
- **Open:** areas *and* modules, or areas only? Two levels may be one too many for small
  repos. Consider collapsing when module count is low.
- **Open:** does `state` get the same treatment, or does the memory bank become the
  navigable surface and `state` stay a single-file snapshot?

> See [current-state.md](current-state.md), [project-comprehension-memory-bank.md](project-comprehension-memory-bank.md),
> [pkg-code-grounded-understanding.md](pkg-code-grounded-understanding.md), and the
> invariants in [CLAUDE.md](../../CLAUDE.md).
