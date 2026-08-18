# G3 — Media ingestion: images, audio, video

> **"G3" is a label, not a position in a queue.** It's gap #3 in the Graphify comparison. The specs
> are not ordered and do not run in sequence — see *Working alongside other tracks* at the bottom.

**Status: ✅ COMPLETE — shipped in 3.10.0 (“Diagrams and recordings become facts”).**
All three phases delivered. *(Status corrected 2026-08-15: this line still read “Not started”
long after the work shipped, and the roadmap index repeated the error.)*
**Owner:** _shipped_
**Ships:** architecture diagrams, screenshots, recorded design reviews and demo videos become graph
content — **without** making `understand`/`state` non-deterministic, networked, or slow.

| Phase | Delivered as | Verified in 3.18.1 |
|---|---|---|
| 1 — Deterministic ingestion | `register_reader()` seam; committed artifacts under `.spine-media/` → `Doc` nodes + `MENTIONS`. No model in the build path | ✅ `pkg/doc_source.py:72` |
| 2 — Images / OCR | `orchestrator media extract`, local Tesseract, diagram-oriented (labels, not prose) | ✅ `pkg/media_extract.py`, `[media]` extra |
| 3 — Audio / video | `media extract` for audio/video, local Whisper or `--asr api` (off-machine only with `--allow-remote`), timestamped segments | ✅ `pkg/media_asr.py`, `[asr]` extra |

The deliberate split held: **`media extract` is the only thing that runs a model.** A repo with no
artifact is byte-identical to one with no media at all, so the deterministic build is untouched.

## Before you start

**Prerequisites: none. You can start today.** Everything this track needs already exists:

| What this track needs | State |
|---|---|
| A reader seam to register a new format on | ✅ **Already shipped in 3.8.2** — `pkg/doc_source.py` exposes `DocReader` + `register_reader()`. You add a reader; you touch no existing one. |
| A decision on how media is modelled in the graph (`Doc` vs a new `Media` kind) | **Yours to make — it's Phase 0 below.** No other track defines this. |
| Anything from G2, G5, G6, or the watch-items | Nothing. No shared files. |

**Read [The determinism problem](#the-determinism-problem-read-this-first) before writing code.**
Every other gap in the program is additive; this one attacks the property that makes
`understand`/`state` trustworthy. One sentence if you read nothing else: **model inference never
runs inside the graph build.**

---

## Why

Graphify ingests images, audio, and video (with transcription). Today this is an *explicit
non-goal* for us — a defensible position, but still a capability gap when someone side-by-sides a
datasheet. Real value exists: architecture diagrams and recorded design decisions are where a lot
of the "why" lives, and none of it is in the code.

## The determinism problem (read this first)

OCR, vision, and speech-to-text are **model inference**: non-deterministic across runs and versions,
slow, often networked, often credentialed. `understand`/`state` are deterministic and no-LLM — that
property is *why they're trusted*. Calling a transcription API from `read_doc_pages` would destroy it.

**The design that resolves this: split extraction from ingestion.**

```mermaid
flowchart LR
    media["Media file<br/>(.png · .mp4 · .wav)"]
    cmd["orchestrator media extract<br/>(explicit · opt-in · may use models)"]
    art["Committed transcript artifact<br/>(.spine-media/HASH.json)"]
    build["understand / state<br/>(deterministic · no model)"]
    graph["Doc nodes + MENTIONS"]
    media --> cmd
    cmd --> art
    art --> build
    build --> graph
```

- **`orchestrator media extract`** is a separate, explicit, opt-in command. It may call OCR/ASR, be
  slow, need credentials, and be non-deterministic. It writes a **content-addressed artifact**
  (keyed by file hash) that a human can review and commit.
- **`understand`/`state` never run a model.** They read the committed artifact — a plain JSON file —
  exactly like any other doc. Same commit in → same graph out. **Property preserved.**
- No artifact present → the media file is skipped, exactly as a scanned PDF is today. A repo that
  never runs `media extract` behaves **byte-identically to today**.

That is the whole trick, and everything below follows from it.

## Phases

| Phase | Work | Effort | Exit |
|---|---|---|---|
| **0 — Decide the model** | Does media reuse the existing `Doc` node kind, or get its own `Media` kind? Write the answer into `pkg/facts.py` docstrings, and define the artifact schema `.spine-media/<sha256>.json` (source path, hash, extractor + version, extracted text, optional page/segment offsets). **A decision + a schema, not a build.** | ~1 d | Both written down; Phase 1 and 2 can then proceed independently |
| **1 — Deterministic ingestion** | A reader registered via `register_reader()` that turns a *committed artifact* into `DocPage`s. **No model code at all in this phase.** | ~3–4 d | Hand-written artifacts ingest → `Doc` nodes + `MENTIONS`; a repo with no artifacts is bit-identical to today (asserted by test) |
| **2 — Images / OCR** | `orchestrator media extract` for `.png/.jpg/.webp`, local OCR (e.g. Tesseract via `pytesseract`) under a `[media]` extra. Diagram-oriented: extract labels, not prose. Records extractor + version in the artifact. | ~4–6 d | An architecture-diagram PNG yields an artifact whose labels bind to real symbols; deterministic for a pinned extractor version |
| **3 — Audio / video** | Extend `media extract` to `.mp3/.wav/.mp4/.mov` via a pluggable ASR backend (local Whisper **or** an API), segment timestamps preserved. Cost/size caps; long media truncated and reported. | ~5–8 d | A recorded design review becomes a searchable `Doc` with timestamped segments; opt-in, capped, never in the default path |

**Phase 1 alone is shippable and safe** — the ingestion half with zero model risk, letting someone
hand-write or externally generate transcripts.

**Phases 1 and 2+ parallelise.** Once Phase 0 fixes the schema, the *reader* (Phase 1) and the
*extractors* (Phases 2–3) read and write that schema independently — two people can take them
concurrently. Phase 0 is first only because both halves depend on the format.

## Invariants you must not break

These are the repo's rules (see [CLAUDE.md](../../CLAUDE.md)); a PR that breaks one is rejected
regardless of the feature it ships.

1. **No model inference in `understand`/`state`.** A test must assert that building the graph with
   media files present but no artifacts produces the same facts as with the media files absent.
2. **Deterministic given an artifact.** Artifact → `DocPage` must be pure. Pin and record the
   extractor version *in* the artifact so a re-extract that changes output shows up in a diff.
3. **The base install stays stdlib-only.** OCR/ASR behind a `[media]` extra, lazy-imported, absent →
   skip (the `pypdf`/`[office]` precedent in `doc_source.py`).
4. **Bound honestly.** Cap file size, duration, and segments; record what was elided ("top N of M").
5. **No silent network.** `media extract` with an API backend must state that it sends data
   off-machine, and be opt-in per run.
6. **Precision-first binding.** A wrong `MENTIONS` edge is worse than none — the rule doc ingestion
   already follows.

## Learn from how G2 went (it shipped in 3.8.2)

The document-modality track hit the same class of problem twice, and it's the trap waiting here:

- **Each format hides its own "this is code" marker, and losing it silently kills binding.** HTML's
  was `<code>`; Word's was monospace runs. Both first implementations flattened them to plain text,
  everything still "worked", and symbols quietly stopped binding.
- **It was only caught by a smoke test that asserted a real symbol bound** — not by "does it parse"
  or "did text come out". Do the same here: ingest a realistic diagram/recording and assert a known
  symbol binds. For OCR especially, **validate on one real diagram before building Phase 2** — if
  precision is poor, stop at Phase 1.

## Non-goals

- Vision *understanding* of diagrams (inferring architecture from boxes and arrows). We extract
  text/labels; we do not interpret pictures.
- Real-time or watch-mode media processing.
- Storing media binaries in the graph — extracted text + provenance only.
- Making media ingestion part of the default `understand` path. **Ever.**

## Open questions

1. Commit artifacts to the repo, or cache them outside it? (Lean: **committable but gitignorable** —
   committing makes CI deterministic and reviewable; teams that object can ignore the directory.)
2. Reuse `Doc`, or a distinct `Media` kind? **Your call, in Phase 0.** (Lean: `Doc` with a media
   `source_file`, so every existing doc surface — `docs_for`, coverage, drift — works unchanged.)

## Working alongside other tracks

You do not need to coordinate with anyone to start. For reference, the only files this track shares
with any other are **append-only**:

| You touch | Also touched by | Conflict |
|---|---|---|
| reader registry (`register_reader` call) | G2 (done) | one appended line |
| `pyproject.toml` extras | G2 (done) | one appended block |
| `pkg/facts.py` — *only if* Phase 0 adds a `Media` kind | G5 reads it, never writes it | none |

Everything else — the `media/` package, the `media extract` CLI — is yours alone.
