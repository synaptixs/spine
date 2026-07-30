# G2 — Document modality breadth: HTML + Office

**Status:** ✅ **COMPLETE — shipped in spine 3.8.2 (2026-07-22).** Both phases delivered; Phase 3
(Google Workspace) removed by decision, standalone YAML deliberately not ingested. Nothing outstanding.
**Owner:** delivered
**Depended on:** the reader seam, which shipped alongside it in the same release. Nothing else — in
particular, no separate "decide the vocabulary" step was needed: this track only adds *readers*
producing `DocPage`s and reuses the existing `Doc` node kind, so it never touched `pkg/facts.py`.

**Gap:** #2 in [graphify-vs-spine-comparison.md](graphify-vs-spine-comparison.md).

**One-liner:** extend doc ingestion beyond `.md`/`.rst`/`.txt`/`.pdf` to the remaining **file**
formats real organisations keep specs in — **HTML** and **Office (.docx/.xlsx)** — so those
documents become `Doc` nodes `MENTIONS`-linked to the code they describe.

## What shipped (3.8.2)

| Delivered | Where |
|---|---|
| **Reader registry** — formats register, they don't branch | `pkg/doc_source.py` `DocReader` / `register_reader` (`5668e21`) |
| **HTML** `.html`/`.htm`, no extra — `<h1..h6>` → ATX headings so HTML sections like markdown; inline `<code>` → backtick span; `<script>`/`<style>` dropped; malformed markup skipped | `_HtmlToText`, `_read_html` (`edee8e9`) |
| **Markdown front matter** → values as prose, keys/fences dropped | `_read_markdown`, `_front_matter_prose` (`edee8e9`) |
| **Word** `.docx` — heading styles → sections; **monospace runs → backtick spans**; table text kept | `_read_docx`, `_docx_paragraph_text` (`7e0dfd5`) |
| **Excel** `.xlsx` — sheet → section, string cells only, 20-sheet/500-row caps | `_read_xlsx` (`7e0dfd5`) |
| **`[office]` extra** (`python-docx`, `openpyxl`), lazy-imported | `pyproject.toml` |

**The lesson worth carrying into G3:** twice, the *first* implementation silently lost binding because
it discarded the format's own code marker — HTML's `<code>`, then Word's monospace runs. Both were
caught only by a smoke test that checked whether a symbol actually bound, not by "does it parse".
Any new format should be validated the same way: **ingest a realistic document and assert a real
symbol binds**, not just that text came out.

> **Google Workspace is out of scope — we are not supporting it.** See [Non-goals](#non-goals).
> This track is **local files only**, which keeps the whole spec on the existing repo-walk seam and
> removes the OAuth/credential/network design entirely.

---

## Why

3.8.1 turned the modality gap from a chasm into a gap: we ingest text docs and PDF; Graphify also
ingests Office, HTML, and YAML (and cloud sources we've chosen not to match). Closing the *file*
formats is the highest-value remaining comprehension work **because it's what an enterprise
evaluator hits on day one** — a `.docx` architecture spec sitting in the repo today ingests as
nothing. Unlike language breadth, it's a handful of readers, not a front-end per language.

## What already exists (reuse, don't rebuild)

| Piece | Gives us |
|---|---|
| `pkg/doc_source.py` | The repo walk, size caps, skip-lists, and the reader registry |
| `pkg/docs.py` | `DocPage`, `extract_mentions`, `DocReconciler` — the deterministic doc→symbol binder |
| `pkg/doc_link.py` | `link_docs` post-pass + `doc_drift`; you emit `DocPage`s, this does the rest |
| `doc_source.split_sections` | Heading-based section granularity — the model to mirror for Office headings |
| `pkg/doc_source.py` `_read_pdf_text` | The precedent for an optional, lazy-imported, degrade-never-crash reader |

**You should not need to touch `doc_link.py`, `docs.py`, or `store.py` at all.** If you find
yourself editing the binder, stop and re-read — your job is to produce `DocPage`s.

## Design decisions

1. **Readers register, they don't branch.** Every format is a reader on the `register_reader` registry.
   Adding `.docx` must not modify the dispatcher or any existing reader.
2. **Everything lands as `DocPage`.** Title = repo-relative path (`spec.docx`), section-granular
   where the format has headings (`spec.docx#installation`), `source_file` + `line` set so
   provenance points somewhere real.
3. **Deterministic or it doesn't ship.** Same file in → identical `DocPage`s out. No LLM, no
   network, no timestamps. Office parsing is deterministic; this is free if you don't get clever.
4. **Every new dependency is lazy + extra-gated.** Base install stays stdlib-only (invariant #7).
5. **Local files only.** `read_doc_pages` walks a *repo*. Nothing in this track fetches over the
   network or handles credentials — that's what dropping Google Workspace buys us.
6. **Degrade, never crash.** An encrypted `.docx`, a 200-sheet `.xlsx`, a binary blob named `.html`
   — skip it, like `_read_pdf_text` already does for scanned PDFs.

## Phases

| Phase | Work | Status |
|---|---|---|
| **1 ✅ — HTML + front matter** | `.html`/`.htm` via stdlib `html.parser` (`<h1..h6>` → ATX headings, inline `<code>` → backticks, script/style dropped) + markdown front matter reduced to its values. No new deps. | ✅ **Done — 3.8.2** (`edee8e9`). An HTML design doc sections correctly and its `<code>` mentions bind. **YAML descoped — see Non-goals.** |
| **2 ✅ — Office** | `.docx` (Word heading styles → sections, monospace runs → code claims, table text kept) and `.xlsx` (sheet → section, string cells only). New `[office]` extra, lazy-imported; encrypted/corrupt skipped. | ✅ **Done — 3.8.2** (`7e0dfd5`). Verified from the published wheel in a clean venv: 9 readers registered, a real `.docx` + `.html` both bind `calc_tax`. |

**Both phases shipped together in 3.8.2** (private PR #130 → release #131 → spine #50 → PyPI).
Full suite 1868 green; base install verified unaffected (readers return `None` and skip without the
extras).

## Invariants you must not break

- **No network, no credentials, anywhere in this track.** Every reader takes a local path. If a
  design starts needing an auth flow, it has left this spec's scope.
- **Base install stdlib-only** — `python-docx`/`openpyxl` behind `[office]`, imported inside the
  reader function, absence handled like `pypdf`'s (skip, never raise).
- **Deterministic output.** Add a test that ingests the same fixture twice and asserts identical
  node/edge sets.
- **Bound honestly.** Cap sections per document (`_MAX_SECTIONS` precedent) and sheets per workbook;
  record what was elided.

## Non-goals

- **Google Workspace (Docs / Sheets / Slides) — decided: we are not supporting it.** It was this
  spec's Phase 3 and has been removed. It is the only part that needed OAuth, network I/O inside an
  ingestion path, and a credential story; dropping it keeps G2 to local-file readers on the existing
  seam. Revisit only on a concrete customer requirement, as its own spec — not as a phase here.
- **SharePoint / OneDrive / any cloud document source** — same reasoning.
- **Standalone `.yaml`/`.yml` — decided during Phase 1: not ingested.** The phase was scoped as
  "HTML + YAML", but a repo's YAML is overwhelmingly *configuration* (CI, compose, manifests), and
  ingesting it would corrupt the two surfaces 3.8.1 shipped: `state`'s doc-coverage would climb
  because a CI file happens to name a module, and `doc_drift` would flag every identifier-shaped
  config value as a stale documentation claim. YAML's genuinely documentary case — **front matter** —
  did ship. Rationale sits beside the reader registrations with a test pinning it; one
  `register_reader` call enables it if that judgement is ever reversed.
- OCR of images embedded in Office files (that's G3).
- Writing *back* to Office formats.
- Rendering fidelity — we extract text and structure, not layout.

> A `.docx` synced to disk by Drive/SharePoint desktop clients still ingests via Phase 2, since by
> then it's just a local file. That covers a meaningful share of the use case without us building
> or maintaining any cloud integration.

## Decisions taken (was: open questions)

1. **`[office]` as its own extra** — *decided: own extra.* `[docs]`'s promise is "pure-Python and
   tiny"; `openpyxl` is heavier, so it would have broken that contract. Shipped as
   `pip install 'synaptixs-spine[office]'`.
2. **Should `.xlsx` ingest at all?** — *decided: yes, with guards.* A spreadsheet rarely describes
   code, so it ingests **string cells only** (numbers, dates and formula results are data and would
   never bind) and is capped at 20 sheets / 500 rows. Unlike YAML, a `.xlsx` in a repo is rare and
   usually a deliberate document, so the coverage-pollution risk that killed YAML doesn't apply.

## Follow-ups (not blocking)

- **No baseline was captured before this shipped**, so G2's actual effect on comprehension is
  unmeasured. That's [G6 Phase 1](gap6-benchmarks-roadmap.md) — worth doing before G3/G5 so the
  next tracks can prove their value rather than assert it.
