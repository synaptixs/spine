# Spec: doc & PDF ingestion — fold documentation into the PKG as code-linked facts

**Status:** Phases 1–3 ✅ shipped (Doc nodes + MENTIONS + PDF + `state` drift surface + `/spine`
`docs_for` tool + section-granular nodes + doc-grounded codegen + doc-drift review finding). The only
deferred item is the opt-in `--llm` doc summary — a deliberate v1 non-goal (keeps ingestion
deterministic; see Non-goals).
**Date:** 2026-07-21 · spine v3.8.0
**One-liner:** ingest a repo's **docs** (Markdown, reStructuredText, plain text, and **PDF**) into
the Product Knowledge Graph as first-class **`Doc`** nodes, each **`MENTIONS`**-linked to the code
symbols it describes — so `understand`/`state`/grounding and the `/spine` skill can answer *"which
docs describe `X`?"*, *"is this feature documented?"*, and *"do the docs still match the code?"* —
all **deterministic, no LLM**. This closes the biggest remaining reach gap vs. Graphify
(doc/PDF/modality breadth) — see [graphify-vs-spine-comparison.md](graphify-vs-spine-comparison.md).

---

## Why

- **The modality gap.** Graphify folds docs, PDFs, images, and video into one graph; Spine today
  is code + requirements text. Docs are where the *intent, rationale, and contracts* live — and
  they routinely drift from the code. Bringing them into the PKG is the highest-value slice of that
  gap (docs first; images/video are out of scope, see non-goals).
- **Docs make claims about code.** "`BacklogService` calls `create_issue`", "see `pkg.facts`". A
  graph that binds those claims to real symbols turns docs into *grounded context* (better codegen
  grounding, richer `investigate`) **and** surfaces **doc drift** ("the docs lie about the code").
- **It's mostly reuse.** Spine already has the deterministic doc→symbol engine (`pkg/docs.py`) and
  a structured-doc ingestion precedent (OpenSpec). This spec makes docs *first-class graph nodes*
  and adds formats — not a new comprehension engine.

---

## Spine already ingests structured docs — the OpenSpec precedent (highlight)

Spine is **not** starting from zero on "documents → facts". It already does the hardest, highest-
fidelity version of it, deterministically:

- **OpenSpec** (`intake/openspec_source.py`, `openspec://<change-id>`, **shipped in 2.6.0**) reads
  OpenSpec **change proposals** — a *structured* doc format (`### Requirement:` SHALL statements +
  `#### Scenario:` Given/When/Then) — straight into fully-formed `Intent`s with `acceptance_criteria`
  populated **verbatim**, via the `StructuredIntentSource` seam that **bypasses the LLM extractor**.
  There's even write-back: `orchestrator openspec draft` bootstraps OpenSpec changes from a wiki.

So Spine's document story is a **spectrum**, and doc/PDF ingestion completes it:

| | Structured requirement docs | Knowledge / prose docs |
|---|---|---|
| **Format** | OpenSpec change proposals (SHALL / Given-When-Then) | README, design docs, ADRs, `docs/*.md`, PDFs |
| **What Spine extracts** | Intents + acceptance criteria (verbatim) | `Doc` nodes + `MENTIONS` edges + drift findings |
| **Feeds** | intake → codegen (what to build) | comprehension → grounding (what exists / what's documented) |
| **Status** | **SHIPPED (2.6.0)** | **this spec** |
| **Property** | deterministic, no LLM | deterministic, no LLM |

The takeaway for positioning: *"Spine reads your specs (OpenSpec) and your docs — deterministically,
grounded to the code, no guessing."* This spec is the second column.

---

## What already exists (reuse, don't rebuild)

| Piece | Source | Gives us |
|---|---|---|
| **Doc-semantic layer** | `pkg/docs.py` — `DocPage`, `extract_mentions()`, `DocReconciler.bind()/reconcile()`, `DocBinding` (bound/unbound), `DocDriftFinding` | The deterministic **doc→symbol** engine: pull code mentions from a page, bind each to a PKG anchor by name/suffix, and flag *unbound* mentions as drift. Precision-first already. |
| Structured-doc ingestion | `intake/openspec_source.py` + `openspec_writer.py` | The precedent above — the "structured" column; stays in **intake**, untouched. |
| Extractor dispatch | `pkg/extractor.py` (`RepoCodeExtractor`, per-suffix `LanguageExtractor`, the new duck-typed `finalize` hook) | The seam a doc pass plugs into — but docs need the code graph *first*, so this runs as a **finalize/second pass** over the built batch, not a per-file front-end. |
| Retrieval + comprehension | `pkg/retrieval.py`, `knowledge/current_state.py`, `knowledge/understand.py`, the `/spine` MCP tools | Consumers that gain a doc dimension once `Doc`/`MENTIONS` are in the graph. |
| Fact model | `pkg/facts.py` (`NodeKind`/`EdgeKind`) | Small, "grow as needed" schema — add one node kind + one edge kind. |

---

## Design decisions

1. **Docs are a second pass, not a per-file front-end.** A `MENTIONS` edge needs the code graph to
   already exist (to bind against). So doc ingestion runs **after** code extraction — a
   `link_docs(batch, repo_root)` pass (like `link_data_layer`), reusing `DocReconciler`. It's a
   no-op on a repo with no docs, so it's safe everywhere.
2. **Two new facts, no more.** `NodeKind.DOC` (a doc page, optionally section-granular) and
   `EdgeKind.MENTIONS` (`Doc → symbol/module`). *Bound* mentions become edges; *unbound* mentions
   stay **drift findings** (not edges — a dangling edge would poison grounding). Ids prefixed
   `doc:` (e.g. `doc:README.md#installation`).
3. **Deterministic, no LLM** — preserve the `understand`/`state` property. Same docs in → same
   nodes/edges out. (An optional `--llm` doc *summary* is a possible later add, behind a flag.)
4. **Precision-first binding.** Reuse `DocReconciler`'s name/suffix binding; only emit a `MENTIONS`
   edge when the mention binds to exactly one anchor (or a confidently-best one). Bound honestly —
   cap mentions per page, record what was elided (invariant #7).
5. **PDF behind a `[docs]` extra.** Markdown/RST/plain-text parse with stdlib; **PDF** needs a
   parser (`pypdf`, pure-Python) under a new optional extra, lazy-imported so the base install stays
   stdlib-only (the same contract as the language extras). A PDF becomes a `DocPage` (text +
   page-number provenance); no OCR (a scanned PDF yields no text → skipped, reported).
6. **Section granularity, bounded.** A `Doc` node per **page** by default; optionally per top-level
   heading (`README.md#installation`) so `MENTIONS` point at the *section* that names a symbol.
   Cap sections/page to keep the graph legible.
7. **Docs enrich, never gate.** Doc nodes/edges are additive context. Nothing about code extraction
   or codegen depends on them; a repo with zero docs behaves exactly as today.

---

## New facts

```python
# pkg/facts.py
class NodeKind(str, Enum):
    ...
    DOC = "Doc"          # a documentation page or section (README, design doc, ADR, PDF)

class EdgeKind(str, Enum):
    ...
    MENTIONS = "MENTIONS"  # Doc → the code symbol/module it describes (bound, file:line-grounded)
```

`Doc` nodes carry `provenance` (`docs/design.md:1` or `spec.pdf:p4`). `MENTIONS` edges carry the
mention's line/offset. Drift (unbound mentions) remains a `DocDriftFinding`, surfaced by `state`,
not an edge.

---

## Components (where the code goes)

- **`pkg/doc_source.py`** (new) — read a repo's doc files into `DocPage`s: `.md`/`.rst`/`.txt` (stdlib)
  and `.pdf` (lazy `pypdf`, `[docs]` extra). Suffix-driven, skip-listed (`node_modules`, build dirs);
  section-splitting for markdown headings.
- **`pkg/doc_link.py`** (new) — `link_docs(batch, repo_root) -> FactBatch`: build `DocReconciler`
  from the batch, extract + bind mentions for every `DocPage`, **emit `Doc` nodes + `MENTIONS`
  edges** for bound mentions, and return drift findings. Modeled on `data_layer_link.py`; a no-op
  when there are no docs.
- **`pkg/docs.py`** — reused as-is for `extract_mentions`/`DocReconciler`; add only the node/edge
  emission helper if it doesn't already expose one.
- **`pkg/store.py`** — `docs_for(symbol_id)` (incoming `MENTIONS`) and `mentions_of(doc_id)`.
- **Comprehension wiring** — `understand.py`/`current_state.py` call `link_docs` alongside
  `link_data_layer`; `state` gains a **"Documentation"** section (doc count, coverage: % of public
  symbols with a doc `MENTIONS`, and top **drift** findings). A `/spine` tool `docs_for(symbol)` and
  doc context folded into grounding.

---

## Phases

| Phase | Work | Effort | Exit |
|---|---|---|---|
| **1 ✅ — Doc nodes + MENTIONS (Markdown/RST/txt)** | `DOC`/`MENTIONS` facts; `doc_source.py` for text docs; `doc_link.py` reusing `DocReconciler`; `link_docs` wired into `understand`/`state`; `docs_for`/`mentions_of` store queries; `doc_drift` (surfaced in Phase 2); unit tests (`tests/pkg/test_doc_link.py`). | ~2–3 d | **Done** — `understand`/`state` emit `Doc` nodes + `MENTIONS` edges (85 docs / 1352 edges on this repo); `store.docs_for("X")` answers "which docs describe `X`?" |
| **2 ✅ — PDF + drift surface** | `[docs]` extra + lazy `pypdf` (`doc_source._read_pdf_text`, malformed/scanned → skipped); `state` **Documentation** section (coverage % + top **symbol** drift, filtered by `_symbolish_drift` so paths/URLs/filenames don't drown the signal); `docs_for` `/spine` MCP tool (symbol → describing docs; no-arg → coverage summary). | ~2–3 d | **Done** — PDFs ingest (base install unaffected); `state` reports coverage + drift; `docs_for` answers "which docs describe `X`?" |
| **3 ✅ — reach** | **Section-granular `Doc` nodes** (`doc_source.split_sections`: markdown split by heading → `doc:README.md#usage`, capped at 40, provenance at the heading line; on by default, heading-less docs stay whole). **Doc-grounded codegen** (`PKGCodegenGrounder` folds each reused symbol's documenting prose into the context — "Documented in `README.md#usage`: …", bounded). **Doc-drift review finding** (`GroundingVerifier.doc_findings` → `GroundingFinding(rule="doc_drift")`, symbol-only, informational, anchored to the doc). Shared `symbolish_drift` filter moved to `pkg.doc_link`. `--llm` doc summary **deferred** (v1 non-goal). | ~2–3 d | **Done** — `MENTIONS` bind to the section that names a symbol; codegen sees the human prose for reused APIs; `review` can surface stale-doc claims |

**Phase 1 is independently useful** (docs become queryable). Phase 2 adds PDF (the headline gap) and
the drift dashboard. Phase 3 is reach: sharper granularity, docs that ground codegen, and drift as a
review signal — all still deterministic, no LLM.

## Effort & risk

- **~S–M** (Phase 1–2 ≈ 1 week). **Low risk:** the doc→symbol engine (`pkg/docs.py`) and the
  linker pattern (`data_layer_link.py`) already exist; this is facts + a source layer + wiring.
- Watch-items: **binding precision** (a `MENTIONS` to the wrong symbol is worse than none — keep it
  precision-first + bounded); **PDF variety** (scanned/DRM PDFs → no text → skip + report, never
  crash); **graph bloat** (cap sections/mentions per doc, record "top N of M").

## Non-goals

- **Images & video** — Graphify ingests them; we don't (no OCR/vision). Docs = text-bearing files.
- **Not an LLM doc summarizer** — deterministic extraction only in v1; summaries stay opt-in/later.
- **Not OpenSpec** — structured-spec → intents stays in **intake** (`openspec://`). This is the
  *knowledge-doc* half; the two are complementary (see the spectrum table), not merged.
- **Not a docs site / renderer** — `understand` already *writes* `episteme/`; this *reads* a repo's
  existing docs into the graph. Different direction.

## Open questions

1. **Node granularity** — page-level `Doc` nodes in v1, section-level behind a flag? (Lean: page in
   v1; sections in Phase 3, capped.)
2. **PDF dependency** — `pypdf` (pure-Python, permissive) vs `pdfminer.six` (richer layout, heavier)?
   (Lean: `pypdf` under `[docs]`; revisit if layout fidelity matters.)
3. **Drift as a review gate?** — should the `review`/`rca` paths treat high doc-drift as a finding,
   or keep it informational in `state`? (Lean: informational first; gate later behind policy.)
4. **Coverage metric** — "% of public symbols with a doc mention" vs "% of docs that bind" — which is
   the headline number in `state`? (Lean: both; lead with symbol-doc coverage.)
