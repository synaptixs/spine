# Prompt — animated engineering memory for one product

A reusable prompt for generating an animated, self-contained diagram of **one product's**
knowledge foundation, and how it *compounds* into engineering memory over time.

**The pair this belongs to:**

- [knowledge-graph-architecture.md](knowledge-graph-architecture.md) — the concrete
  implementation both prompts generalise from.
- [knowledge-foundation-diagram-prompt.md](knowledge-foundation-diagram-prompt.md) — the
  **federated** version: many products, one organisational foundation.
- **This one** — a single product, and the time axis that makes memory accumulate. It is the
  *unit* the federated picture is built from, so it should read as a prelude to it.

**The essential difference in one line.** The federated prompt animates **convergence** — many
sources collapsing into one vocabulary. This one animates **accumulation** — one product, one
graph, getting deeper every commit. Same pipeline, different story, and they should be
recognisably the same visual family.

**The idea the diagram has to land:** a knowledge graph rebuilt from scratch each time is a
snapshot. Memory is what happens when the graph is keyed to a commit, kept, and compared —
so drift, recurring failure sites and decayed documentation become visible *because* you have
the earlier state to compare against.

---

```text
Build a single self-contained animated HTML page that explains **compounding engineering
memory** for one software product: how everything a team produces about that product becomes
one evidence-grounded graph that deepens over time, rather than a snapshot regenerated and
discarded.

Audience: an engineering leader or senior engineer seeing this for the first time. They should
grasp the shape in 30 seconds and the substance in three minutes. Explanatory, not decorative.

## The subject is ONE product

Everything depicted belongs to a single product or service — one codebase, its own docs, its
own tickets, its own incidents. Do not show multiple repositories converging; that is a
different diagram. This is depth, not breadth.

## What flows in

The sources a single team already generates, shown as ongoing streams rather than a one-time
import:

- The codebase — modules, types, functions, call and import relationships
- Its schema — tables, migrations, foreign keys, and the code that reads and writes them
- Its API surface — routes, handlers, contracts
- Product and user documentation
- Design records, ADRs, runbooks — the reasoning behind decisions
- Issues, epics and defects, including resolved ones
- Incidents and post-mortems

## The pipeline — five stages, left to right

1. **Extraction** — a reader per source type, each emitting the same shape.
2. **One universal vocabulary** — a small, closed set of entity and relationship kinds that
   every source collapses into. The narrow waist of the design.
3. **Enrichment** — relating what extraction saw only in isolation: binding documentation to
   the code it describes, folding migrations into the schema, linking issues and incidents to
   the components they touched.
4. **Versioned store** — the graph is a build artifact keyed to a commit, not a crawl. This is
   the hinge the whole diagram turns on: because each state is kept and addressable, states can
   be compared.
5. **Query layer** — who calls this, what breaks if I change it, which documents describe it,
   which incidents touched it, what changed since the last release.

## The compounding — this is the point of the diagram

Show **time as a visible axis**. The same product, sampled at successive commits or releases,
with the graph thickening as it goes. Three things must be legible:

- **Accumulation.** Each pass adds facts and keeps the prior state. The graph does not reset.
- **Comparison.** Because earlier states are retained, differences become first-class: what
  moved, what grew, what became more tangled, which documentation stopped matching its code.
- **Emergence.** Things invisible in any single snapshot surface across many: recurring failure
  sites, components that churn together, the areas incidents keep returning to, and drift
  between what the documentation claims and what the code does.

Make the contrast explicit somewhere on the page: **a regenerated snapshot answers "what is
this?"; retained history answers "what is happening to this?"** — and only the second is
memory.

## Two properties that must come across

1. **Provenance.** Every fact traces to a file and line, a document and section, or a ticket.
   Nothing is asserted that cannot be pointed at.
2. **Grounded facts and inferred hypotheses are different classes and never mixed.** Extracted
   facts are certain; model-inferred relationships are labelled, carry confidence, and are
   opt-in. Render them distinctly and say why the distinction is kept.

## What comes out

The same facts serving a team that owns one product: committed human-readable knowledge that
lives beside the code, blast radius before a change, regression scope, grounded context for AI
agents working in the repo, and drift reports where documentation and code have parted company.

## Close on the federation

End with a small, quiet gesture that this whole picture is **one unit**, and that several of
them — many products in one organisation — compose into institutional engineering memory. A
single node, an outline, a repeated silhouette. Do not develop it; it is the sequel, and the
restraint is the point.

## Animation

- **Animate accumulation, not convergence.** The primary motion is the graph deepening over
  successive versions along the time axis.
- **Animate the flow, never the layout.** Node positions are fixed and computed, never
  force-directed or randomised — the same diagram must draw identically every time.
- Loop gently, with a visible replay control.
- Respect `prefers-reduced-motion`: fall back to the fully revealed static diagram.
- Roughly 15–25 seconds for a full pass. Legibility beats spectacle.

## Technical constraints

- **One file.** Inline all CSS, SVG and JS. No external requests of any kind — no CDNs, fonts,
  images or analytics. It must work offline and when saved to disk.
- **No charting or graph library.** Hand-written SVG and vanilla JS.
- Responsive: readable on a laptop and a phone; wide content scrolls in its own container
  rather than the page scrolling sideways.
- Theme-aware: legible in both light and dark.
- Accessible: real text (not paths), sufficient contrast, a meaningful title and description
  for screen readers.

## Avoid

- Vendor, product or tool names — this is the general architecture, not one implementation.
- Multiple repositories converging; that is the federated diagram, not this one.
- Marketing language, superlatives, and adjectives doing work a label should do.
- A force-directed or randomised layout.
- Decorative motion that carries no information.
- Implying completeness where a view is bounded: if something is capped or summarised, say so
  on the diagram.
```
