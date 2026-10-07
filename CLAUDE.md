# Spine — working on this repo

Guidance for agents/humans **building on** this codebase. For *using* Spine from Claude
Code see [AGENT_GUIDE.md](AGENT_GUIDE.md); for PR/branch mechanics and the quality gate
see [CONTRIBUTING.md](CONTRIBUTING.md); for deploy/env see [OPERATIONS.md](OPERATIONS.md).

**Naming:** the product is **Spine**; the package, import, and CLI stay `orchestrator`
(PyPI: `synaptixs-spine`). Use "Spine" in docs and prose, `orchestrator` in commands.

The knowledge base `understand` writes is **`episteme/`** (was `memory-bank/`) — knowledge
grounded in evidence vs *doxa*, opinion. The same brand/identifier split applies: the
*directory* is `episteme`, but the published identifiers keep their names —
`read_memory_bank` (MCP), `GET /v1/capabilities/memory-bank`, `ORCHESTRATOR_MEMORY_BANK_DIR`,
`memory_bank_dir()`, and the `memory-bank/` SDLC artifact-key prefix. Don't rename those
casually; they're contracts. Get the dir from `understand.BANK_DIRNAME` /
`memory_bank_dir()` (writes) or `existing_bank_dir()` (reads — falls back to a legacy
`memory-bank/`), never a literal.

## Layout — where things live

| Package | What it owns |
|---|---|
| `pkg/` | The **PKG** (Product Knowledge Graph) — the source of truth. `facts.py` = the universal vocabulary (`NodeKind`/`EdgeKind`/`Node`/`Edge`/`FactBatch` — includes the `Doc` node + `MENTIONS` edge from doc ingestion); `extractor.py` dispatches per-suffix to language front-ends; `store.py` = query layer (incl. `docs_for`/`mentions_of`); `docs.py` = the deterministic doc→symbol binder, `doc_source.py` reads docs (md/rst/txt/PDF, section-split), `doc_link.py` = the `link_docs` post-pass (Doc nodes + MENTIONS) + drift; `overview.py` = bounded view for UIs; `persistence.py` = commit-keyed cache; `export.py`/`rdf.py` = projections |
| `knowledge/` | Synthesis **on top of** the PKG — `understand.py` (→ `episteme/*.md`), `current_state.py` (`state`, two lenses, incl. the Documentation section; runs `link_docs` as a post-pass), `renderers.py` |
| `registry/` | FastAPI service + the operator web UI (`registry/api/web/`) |
| `sdlc/` | The feature/run pipeline (largest package) |
| `catalog/`, `intake/`, `agentic/`, `personas/`, `evals/` | profiling, sources→intents, the codegen tool-use loop, personas, measurement |
| `cli/` | Every command surface — one module per help panel, `sdlc.py` and `pkg.py` on their own; `_app.py` holds the root app and panels, `_common.py` the shared helpers |

The design records already in **`docs/specs/`** are where the *why* is — read the relevant
one before changing a subsystem. `docs/specs/README.md` indexes them.

They stay **tracked on purpose.** `understand` ingests markdown from disk whether or not git
tracks it, so a docs tree that exists locally but not in the repo makes your `episteme/`
describe `Doc` nodes CI can't see — and `understand --check` fails on a diff you can't
reproduce. Committed docs are in CI's checkout too, so local and CI agree by construction.
**Never delete one to "tidy up", and never leave an untracked markdown tree inside the
checkout** — that is the trap this paragraph exists to close, and it is unchanged.

**New plans and new design records go outside the checkout** (ruled 2026-09-16; the earlier
instruction to add them here is superseded). Tracking one costs a `SPEC-INDEX.md` row, a
prose count, an `ls … wc -l` beside it, a spec count in `STATE-OF-SPINE.md` and an indexing
check — five gates, for a document no user reads. Write them to a scratchpad or notes
directory and point the currency gate at them:

```bash
python scripts/roadmap-status.py --check ~/plans/<track>.md
```

**The cost is real and worth stating:** the existing specs stop accreting, so "read the
relevant spec" degrades for subsystems built after this date. If a *why* is load-bearing for
reading the code, it belongs in the module docstring, where it travels with the thing it
explains — not in a record nobody can find.

## Invariants — break these and things get subtly wrong

1. **The PKG is the source of truth.** Comprehension surfaces *render* facts; they never
   re-derive them from paths or filenames. If you need a new fact, extend `facts.py` and
   the front-ends — not the renderer.
2. **`understand` / `state` are deterministic and no-LLM.** Same code in → same output out.
   That property is why they're trusted. Never introduce an LLM call, randomness, or a
   timestamp into these paths.
3. **Layout is computed, seeded, in Python.** Any visual surface precomputes positions
   deterministically — *never* a random/force layout. Animate the reveal, never the layout
   or the data. A picture that redraws differently for an identical commit can't be diffed.
4. **The web UI has no build step.** Vanilla JS, zero npm, no `node_modules`, no template
   engine — deliberate (see the preamble of `registry/api/web/shell.py`). CSS/JS are real
   files under `web/static/`, served at `/static`. Don't add a bundler or a d3/cytoscape
   class of dependency.
5. **Shared artifacts must be self-contained.** Anything meant to leave the building
   (reports, exports) inlines its CSS/SVG and fetches nothing. `page_shell()` links
   `/static`, so a saved copy of a served page loses its styling — that's a report you
   can't email.
6. **Group by owning module, never by symbol id.** A node's component is its owning
   module's name/path, resolved by walking `CONTAINS` upward (fall back to
   `provenance.file`). C/C++ ids are symbols (`cpp:HSL2RGB`), not locations — id-grouping
   makes every function its own component and floods any layout. See `_area_of` in
   `knowledge/current_state.py`.
7. **Bound honestly.** Aggregations cap their output and record what was elided (see
   `build_overview`'s `truncated{}`). Say "top N of M"; never let a clipped view imply
   completeness.
8. **Caches are commit-keyed and only trusted on a clean tree** (`pkg/persistence.py`).

## Gotchas that have bitten

- **`md.js` renders mermaid, but only a tiny subset** — `mermaidSvg()` (a ~90-line
  hand-rolled renderer, chosen over a 2.6 MB library because the UI has no build step and
  must work air-gapped) draws inline SVG for: `flowchart LR|TD|TB|RL`, `subgraph x["Zone"]`
  /`end`, **quoted** node decls `id["label"]` (`<br/>` for line breaks), and bare-id edges
  `a --> b` / `a -->|label| b`. Anything else — chained `a --> b --> c`, dotted `-. x .->`,
  decision `c{...}`, or a node declared inline in an edge line — returns null and the whole
  block falls back to `<pre>` ("no picture beats a wrong picture"). It renders fine on
  GitHub either way, so a broken diagram is invisible until you open our own UI. Declare
  nodes first, then edges, and verify rather than eyeball:
  `node scripts/check-mermaid.js *.md` (runs the real `md.js`; non-zero on any fallback).
- **`pkg extract --json` omits edges** — nodes + summary only.
- **Fixture source inside this repo lands in this repo's own graph.** The accuracy corpus
  under `corpus/` holds real `.py` files, and a repo-wide `pkg extract` walks them like any
  other source — measured at **73 phantom nodes** from two cases, with
  `py:corpus.python.plain.repo.shop.cart.Cart` presented as part of Spine and carried into
  `episteme/`. Both walkers (`extractor.py`'s `DEFAULT_IGNORE_DIRS` filter and
  `doc_source.py`'s) skip directories starting with `.`, so every fixture root is
  **`.repo/`** and the leading dot is load-bearing — see `corpus/README.md`. `pkg verify`
  does *not* catch this: fixture modules are perfectly self-consistent, so nothing will
  remind you. Same trap for any future on-disk fixture tree, not just this corpus.
- **A nested git checkout is a boundary, not a subdirectory.** Both walkers also stop at any
  child directory holding a `.git` entry — file *or* directory, because a submodule's is a
  file (`is_nested_repo` in `extractor.py`). Before that rule a superproject's graph walked
  into its submodules, and declaring both in `.spine/repos.yaml` scoped every symbol twice.
  `from_mapping` now refuses a declared root nested inside another unless the inner one is a
  checkout of its own. The reasoning is in the header of `.github/workflows/spine-sdlc.yml`.
- **`--language` is not validated in `cli/`** — an unsupported language silently
  scaffolds a *Python* project (every dispatch chain falls through to the Python branch).
  Detection (`catalog/profile.py`) and extraction (`pkg/`) are independent systems; a
  language can be detected but yield zero graph nodes.
- **Two aggregation zoom levels over the same facts:** `overview.py` keys modules by
  `provenance.file`; `current_state.py`'s `_area` groups `Module` *node names* by their
  first two segments. Complementary, but they key on different strings for the same thing.
- **`CapabilityResult.content_type` passes straight through** to the response `media_type`
  (`registry/api/jobs.py`) — a new deliverable format needs a writer, not API plumbing.
- **Changing a Protocol? Update its test fakes.** The gate runs `mypy src tests` — typing
  `src` alone passes locally and fails CI.

## Before pushing

Run the gate from [CONTRIBUTING.md](CONTRIBUTING.md) — `mypy src tests` (**not** just
`src`) and `ruff format --check .`. CI also runs the tests. Work off `develop`, never
commit to `main`.

**Every change starts on a new branch cut from `develop`** — `git checkout -b <name>
origin/develop` — never a commit made directly on a local `develop` or `main`. This holds
even for a one-line fix: `develop` only moves via a merged PR, and `main` only moves via a
`develop → main` release-promotion PR (see CONTRIBUTING.md). Branch, commit, push, open the
PR — in that order, every time.

**`develop` is the repository's default branch**, so a new PR is based on `develop` unless you
say otherwise. `main` takes **no** PR except the `develop → main` promotion — never open one into
`main` from any other branch. Both branches are ruleset-protected against deletion and
force-push; "Protect main" names `refs/heads/main` explicitly because `~DEFAULT_BRANCH` now
resolves to `develop`.

**Never commit `episteme/`.** It is regenerated after merge by
[`.github/workflows/episteme.yml`](.github/workflows/episteme.yml), and CI fails any PR that
carries it. A branch *cannot* keep it current — CI checks the merge ref, so anything landing
on `develop` stales every open branch, and only a rebase (never a re-run) clears it. Generate
it locally all you like for reading; just don't stage it.
