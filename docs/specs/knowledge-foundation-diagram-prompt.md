# Prompt — animated Knowledge Foundation Architecture diagram

A reusable prompt for generating an animated, self-contained diagram of a **general** knowledge
foundation architecture: many sources, across many repositories and products, collapsing into
one evidence-grounded graph.

**Reference:** [knowledge-graph-architecture.md](knowledge-graph-architecture.md) — the concrete,
Spine-specific version this generalises from.

**Two deliberate departures from that reference**, worth knowing before you use this:

- **Federated, not per-repository.** The reference describes one repo's graph. This asks for many
  projects in one foundation, because cross-project engineering patterns cannot emerge from a
  single repository.
- **Product-neutral.** No tool or vendor names, so the output describes the architecture rather
  than one implementation of it.

The constraint most likely to be violated by a generator is *"animate the flow, never the
layout"*. It is the difference between a diagram you can diff and one you can only look at, and
most tools reach for a force-directed layout by default. Keep it.

---

```text
Build a single self-contained animated HTML page that explains a **Knowledge Foundation
Architecture**: how an engineering organisation turns many disconnected sources across many
repositories and products into one queryable, evidence-grounded knowledge graph.

Audience: a technical leader seeing this for the first time. They should understand the shape
in 30 seconds and the substance in three minutes. Explanatory, not decorative.

## The system to depict

**Left edge — heterogeneous inputs**, deliberately varied in kind, feeding in from many
projects at once:
- Source code (multiple languages, many repositories)
- User and product documentation
- Institutional knowledge (design records, ADRs, runbooks, wikis, meeting notes)
- Defect/bug databases
- Issue trackers and epics (Jira and similar)
- Schemas, migrations, API contracts

Show these as N repositories/products, not one — the point is a *federated* foundation
spanning an organisation, not a single-project tool.

**The pipeline — five stages, left to right:**

1. **Extraction** — per-source front-ends. Each source type has its own reader; each emits the
   same shape. Adding a source is additive, never a change to anything downstream.
2. **One universal vocabulary** — every source collapses into a small, closed set of entity and
   relationship kinds. This is the narrow waist of the whole design: show many arrows
   converging into one bus, then fanning out again.
3. **Enrichment passes** — relating what extraction could only see in isolation: resolving
   references across files and repos, binding documentation to the code it describes, folding
   schema and migrations together, linking issues to the components they touch.
4. **Grounded store** — content-addressed and versioned, so the graph is a *build artifact tied
   to a commit*, not a one-time crawl. Trusted only when the source is clean.
5. **Query layer** — the API that answers questions instead of walking edges: who calls this,
   what breaks if I change it, which documents describe this, which issues touched it.

**Right edge — projections.** The same facts rendered many ways: committed human-readable
knowledge, dashboards, interchange exports for external graph tools, grounded inputs to AI
agents, and cross-project pattern discovery (recurring designs, shared idioms, and drift
between what documentation claims and what code does).

## The two ideas that must come across visually

1. **Provenance.** Every fact traces to a file and line, a document and section, or a ticket.
   Nothing is asserted that cannot be pointed at. Make this visible — a fact carries its origin.
2. **Grounded facts and inferred hypotheses are different classes and never mixed.** Extracted
   facts are certain; model-inferred relationships are labelled, carry confidence, and are
   opt-in. Render them distinctly (solid vs dashed, full vs reduced opacity) and say why.

Also convey: a single source touches many projects, and patterns emerge only when several
projects are in the same graph.

## Animation

- **Reveal in stages**, following the flow left to right: sources appear, converge into the
  vocabulary, enrich, settle into the store, then fan out into projections.
- **Animate the flow, never the layout.** Node positions are fixed and computed, never
  force-directed or randomised — the same diagram must draw identically every time.
- Loop gently, with a visible replay control.
- Respect `prefers-reduced-motion`: fall back to the fully revealed static diagram.
- Aim for roughly 12–20 seconds for a full pass. Legibility beats spectacle.

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
- Marketing language, superlatives, and adjectives doing work that a label should do.
- A force-directed or randomised layout.
- Decorative motion that carries no information.
- Implying completeness where a view is bounded: if something is capped or summarised, say so
  on the diagram.
```
