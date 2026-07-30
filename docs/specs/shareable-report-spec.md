# Spec: the shareable codebase-intelligence report (one self-contained HTML file)

**Status:** Phases 1–3 shipped (branch `feat/shareable-report`).
**Date:** 2026-07-21 · spine v3.6.0
**One-liner:** `orchestrator state . --out report.html` → a **single, self-contained HTML file**
you open in a browser and forward to your team — the engineering-grade counterpart to a
comprehension tool's `graph.html`. Deterministic, no LLM, nothing fetched.

---

## Why

The most viral, demoable asset of comparable code-graph tools is a **single shareable output**
(their `graph.html` + report — the thing people screenshot). Spine already **computes richer
data** than those reports (blast-radius hotspots, coupling, god-components, test-coverage gaps,
security surface, churn, recommendations) — but only emits it as markdown to stdout/file. This
spec packages that existing analysis into one shareable file.

Two payoffs:
1. **Differentiation.** A comprehension tool's report is a concept map ("key concepts,
   suggested questions"). Ours is an **engineering-decision artifact** — what's risky, what's
   untested, what a change would break. Understanding aimed at *action*, which read-only tools
   can't produce.
2. **Adoption, for free.** A team lead opens `report.html`, sees their codebase's risk and
   coverage, and forwards it — top-of-funnel with **no platform install**.

**It's cheap because the analysis already exists** — this is a rendering/packaging task, not new
comprehension.

---

## What already exists (reuse, don't rebuild)

| Piece | Source | Gives us |
|---|---|---|
| `build_current_state(root, lens)` → `CurrentState` | `knowledge/current_state.py` | layers, areas, coupling/external deps, **call hotspots**, god-components/complexity, **test coverage** (`tested_areas`/`untested_top`), **security surface** (`auth_surface`), **churn** (`recent_areas`), prioritized `recommendations` — all deterministic, no LLM |
| `build_overview(batch)` | `pkg/overview.py` | bounded module-level graph (modules, `module_edges`, `top_symbols`) for the diagram |
| `build_regression_plan` / `impact_across` | `sdlc/coverage.py`, `pkg/store.py` | the **blast-radius spotlight** + coverage-gap detail (C5/C8) |
| Self-contained-artifact rule | invariant #5 | shared artifacts inline CSS/SVG and fetch nothing — already the house rule |

So the report **consumes an already-computed `CurrentState`** (+ overview + coverage); it adds
rendering, not analysis.

---

## Design decisions

1. **Single self-contained file.** All CSS inline in a `<style>`; the architecture diagram is
   **inline `<svg>`**; zero external requests (invariant #5 — a served page that links
   `/static` loses its styling when saved, so a shareable report must inline everything).
2. **No mermaid dependency.** The web UI's `md.js` escapes ```mermaid fences (a known gotcha),
   and bundling `mermaid.js` breaks "self-contained + no build step" (invariant #4). Instead,
   **precompute the architecture diagram as deterministic inline SVG** from the same
   zones/components/weighted-arrows data `current_state` already builds for its mermaid block.
3. **Deterministic layout.** Positions are **computed/seeded in Python**, never a random/force
   layout (invariant #3) — same commit → same picture, so two reports diff cleanly. Any motion
   is a reveal animation, never the layout.
4. **No LLM.** Preserve the `understand`/`state` property: same code in → same report out
   (invariant #2). Fast, reproducible, and safe to run in CI. (Commit SHA is the report's
   identity; the timestamp is metadata and can be omitted with `--no-timestamp` for byte-stable
   diffs.)
5. **It's a *view*, not the knowledge base.** This is a throwaway shareable **snapshot** — it is
   **not** the committed `episteme/` (which stays interlinked markdown per
   [pkg-navigable-reports.md](pkg-navigable-reports.md)). Like `state` today, nothing is written
   unless `--out` is given, and reports aren't committed.
6. **Theme-aware.** Light/dark via `prefers-color-scheme` — it's a page people open in a browser.

---

## CLI surface

Extend the existing command by output extension (smallest new surface):

```bash
orchestrator state . --out report.html        # HTML report (this spec)
orchestrator state . --out STATE.md           # markdown (today's behavior, unchanged)
orchestrator state . --lens stakeholder --out report.html   # plain-language framing
```

`--out *.html` → HTML; anything else → markdown. Same repo-or-git-URL argument as `state`.
(Optional ergonomic alias `orchestrator report .` = `state --out report.html`; decide later.)

---

## Report content

Ordered for a skim-then-drill read. Everything below is already in `CurrentState` unless marked ✨ (small additive compute).

1. **Header** — repo name, commit SHA, language mix, node/grounded/edge counts, generated-at.
   Establishes provenance ("this is your real code, at this commit").
2. **Overview** — the plain-language summary (stakeholder-lens prose).
3. **Architecture** — inline-SVG diagram: components grouped into zones, weighted dependency
   arrows from the import/`#include` graph (the data behind current_state's mermaid block).
4. **Blast-radius hotspots ✨** — the differentiator. Top call-hotspots, each with its caller
   count and owning module; one **spotlight**: "changing `X` touches N callers across M modules"
   (deterministic via `impact_across` — no LLM, no target picking beyond the top hotspot).
5. **Risk & health** — coupling table, god-components/complexity, external-dependency surface.
6. **Test-coverage gaps ✨** — areas/symbols in the blast radius with **no covering test**
   (reuse C8's `build_regression_plan` signal at repo scope). The "what's untested" a concept
   map can't show.
7. **Security surface** — the name-based auth/security surface (`auth_surface`).
8. **Recent activity** — most-churned areas (`recent_areas`), so a reader sees where change
   concentrates.
9. **Recommendations** — the prioritized, deterministic recommendations (`_recommend`).

Stakeholder lens hides §4–§7's jargon and keeps §1–§3, §8–§9 in plain language (mirrors the
existing two-lens split).

---

## Components (where the code goes)

- **`knowledge/report_html.py`** (new) — pure `render_report_html(state: CurrentState, *,
  overview, coverage, sha, timestamp) -> str`. Emits the full self-contained document (inline
  `<style>` + inline `<svg>`). No I/O, fully unit-testable on a synthetic `CurrentState`.
- **`knowledge/report_svg.py`** (new, or a helper in the above) — `architecture_svg(state) ->
  str`: the deterministic layered-SVG layout (zones → components, weighted arrows). The one
  genuinely new bit of work.
- **`knowledge/current_state.py`** — no change to the computation; expose the `CurrentState`
  object to the CLI (it already returns rendered markdown — add/return the structured object so
  the HTML renderer can consume it without recomputing).
- **`cli.py`** `state` — branch on `--out` suffix: `.html` → `render_report_html(...)`, else the
  current markdown path.

---

## Phases

| Phase | Work | Effort | Exit |
|---|---|---|---|
| **1 — HTML shell + sections** ✅ | `report_html.py`: inline-CSS document rendering every `CurrentState` section as tables/prose; wire `state --out *.html` (`--no-timestamp` for byte-stable diffs); theme-aware; unit tests on a synthetic state. | ~2–3 d | **Shipped.** `state --out report.html` opens as a complete, self-contained, shareable report |
| **2 — Deterministic architecture SVG + blast-radius spotlight** ✅ | `report_svg.py` layered inline-SVG diagram (zones → components, grid-wrapped past `_MAX_ROWS`, weighted arrows); the hotspot spotlight via `impact_across` (resolved to the most-called same-named node); coverage-gap drill-down via C8's `build_regression_plan`. | ~2–4 d | **Shipped.** The diagram renders inline (no external deps), byte-stable per commit; differentiator sections quantified from the graph |
| **3 — light interactivity** ✅ | Vanilla-JS filter/search over the inlined data (sticky search box; hides non-matching rows/list-items/chips, dims non-matching SVG components, collapses emptied sections, live match count); **layout stays precomputed**, JS only shows/hides. | ~2–3 d | **Shipped.** Filterable in-browser, still one self-contained file, no build step |

**Phase 1 is independently shippable** and already delivers the shareable artifact. Phase 2 is
where it visibly out-classes a concept-map report. Phase 3 is polish. All three are shipped on
`feat/shareable-report`.

## Effort & risk
- **~S–M total** (Phase 1–2 ≈ 1 week). Low risk: the analysis is done; the only novel work is the
  deterministic SVG layout, which invariant #3 already requires spine to do for any visual.
- Main watch-item: keep the SVG layout **deterministic and bounded** (cap nodes/edges shown,
  record "top N of M" per invariant #7) so big repos stay legible and diffable.

## Non-goals
- **Not the committed knowledge base.** `episteme/` stays interlinked markdown
  ([pkg-navigable-reports.md](pkg-navigable-reports.md)); this is a shareable snapshot.
- **No mermaid.js / no bundler / no vector-graph library** (invariant #4).
- **No LLM prose** in v1 — deterministic only. An opt-in `--llm` executive summary is a possible
  later add, kept behind a flag so the default stays reproducible.
- **Not auto-committed** — a report is a view, regenerated on demand (like `state`).

## Open questions
1. **Dedicated `report` command** vs. `state --out *.html` only? (Lean: extension-detection now,
   alias later.)
2. **How much graph** to draw — module-level zones only (legible, diffable) vs. an optional
   symbol-level drill-down? (Lean: module-level in v1; symbol detail behind Phase 3 filtering.)
3. **Byte-stable mode** — is a `--no-timestamp` deterministic-diff mode worth it for CI/PR
   attachment? (Cheap; probably yes.)
