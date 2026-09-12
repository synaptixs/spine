# Design + Plan: adding `<LANGUAGE>` to the PKG — comprehension (codegen is its own track)

*Template — §8.4 of the language-track roadmaps' shared generic work. Copy this file to
`docs/specs/<lang>-support-roadmap.md`, replace every `<...>` placeholder, and delete this
italic line and the bracketed guidance notes as you fill each section in. The shape below
is `perl-support-roadmap.md`'s, the most recent track to land — later tracks (Kotlin, and
whichever comes after) start here rather than re-deriving the shape from scratch.*

**Status:** Proposed — plan for review, no code written. **Date:** `<YYYY-MM-DD>` · spine
`<vX.Y.Z>`.
**Branch:** `feat/<lang>-support` off `develop` at `<base-sha>`. **Delivery: one MR** to
`develop` when every phase in §4 is done and tested.
Adds a `<LANGUAGE>` front-end to the PKG extractor as one self-contained track, the same
cadence as the languages already shipped (C#/C/C++, SQL, Go, PHP, Perl — link whichever
precedents are closest to this language's shape). **Codegen is a separate track**, opened
when this one merges, if it's in scope at all — name the split (Java/TypeScript precedent:
comprehension and codegen are always two tracks).

> [One paragraph: what's genuinely new about this language versus the tree-sitter front-ends
> already shipped. Every track so far has had exactly one or two real design decisions and a
> long tail of "same pattern, different grammar" — name which this is. State the recall risk
> honestly if the grammar is less mature or the corpus of real-world code is messier than
> what's shipped so far.]

## Roadmap currency — the rule this document follows

Every phase row in §4 carries **Status · Started · Finished · Evidence**, updated in the
same commit as the work. A phase is DONE only when its evidence column links a commit, a
test name, or a pasted command result. `scripts/roadmap-status.py --check` enforces the
mechanical half of this (a DONE row with an empty Evidence/Finished cell, a stale top
**Status:** line, a broken relative link, an unindexed roadmap, or a codegen phase started
before its dependency's required phase is DONE) — run it before every phase's commit.

---

## 0. Decisions surfaced up front

*One row per genuinely open design choice — not every implementation detail. Perl's table
had ten; a simpler language may need three. Each row needs real options considered and a
recommendation with a reason, not a recommendation with no alternative shown.*

| # | Decision | Options | Recommendation and why |
|---|---|---|---|
| **D1** | Parser | (a) a dedicated tree-sitter grammar, (b) `tree-sitter-language-pack`, (c) a subprocess around the language's own tooling | *Probe the candidate grammar against every construct §3 needs before committing — a grammar that can't parse the language's own idioms cleanly is the wrong D1, however convenient the package.* |
| **D2** | What the language's primary declaration unit maps to in the vocabulary | (a) `Module` = file, `Type` = the language's class/struct/interface unit; (b) collapse `Module` and `Type`; (c) other | *Load-bearing — every id keys on it. Changing it after the corpus is labelled invalidates the corpus (see Perl D2's own risk note, §12 below).* |
| **D3** | Id separator | dots vs the language's native scope operator | *Match whatever `import_link.py`'s `_DOTTED_PREFIXES` and `docs.py`'s doc→symbol binder already consume, unless there's a reason to add a new convention.* |

---

## 1. Where `<LANGUAGE>` is today — nowhere

| Fact | File | Consequence |
|---|---|---|
| `<ext>` is not in the profiler's suffix map | `catalog/profile.py` | A `<LANGUAGE>` repo profiles as `languages=∅` |
| No `<lang>_extractor.py`, no `<lang>` extra, no grammar probe, no `_GRAMMAR_MODULES` entry | `pkg/`, `pyproject.toml`, `doctor.py`, `persistence.py` | **Zero graph nodes**; a warm cache would not notice the extra |
| `--language <lang>` is rejected (exit 2) | `feature_runner.py` | Correct until the codegen track (if any) adds the machinery |

---

## 2. Why `<LANGUAGE>` is cheaper than it looks, and where it is not

```
CHEAP
· [What the grammar already handles cleanly, verified by probing — not assumed.]
NOT CHEAP
· [Where the honest recall number will land below 1.00, and why. State it up front
  rather than discovering it in the corpus and quietly not writing it down.]
```

**Reused verbatim:** [name the pieces genuinely shared across every tree-sitter front-end —
lazy parser factory, `TYPE_CHECKING`-guarded CST node type, gated append in
`default_extractors()`, the two-pass CALLS pattern, `finalize` repoint, the corpus method,
the per-front-end freshness test.]

---

## 3. Fact mapping

*One subsection per PKG concern this track touches — comprehension (Module/Type/Function/
Field/IMPORTS/CONTAINS/IMPLEMENTS), CALLS, framework routes (Endpoint/EXPOSES), data layer
(Entity/Field/REFERENCES). Table shape: construct → PKG fact, with the literal-only rule
stated per row where invention is possible (a guessed target is worse than a missing one).*

### 3.1 Comprehension

| Construct | Fact |
|---|---|
| ... | ... |

### 3.2 `CALLS`

| Shape | Resolves to | Notes |
|---|---|---|
| ... | ... | ... |

---

## 4. Phases — the living table

| Phase | Work | Effort | Exit criteria | Status | Started | Finished | Evidence |
|---|---|---|---|---|---|---|---|
| **P1 Comprehension** | `<lang>_extractor.py`; registry sites per `docs/reviewing/language-frontend-checklist.md`; tests; docs per `docs/reviewing/docs-matrix.md` | `~X d` | `pkg extract` on both validation repos yields real nodes from 0; `pkg verify` 0 errors; recall on the harder validation repo measured and written here | ⬜ | | | |
| **P2 Corpus + `CALLS`** | `corpus/<lang>/{...}` labelled from source first; the shared `finalize` name-resolution helper (§8.5) if this track needs one | `~X d` | precision 1.00 on every kind; `CALLS` recall stated with predicted `known_gaps` | ⬜ | | | |
| **P3 Routes** *(if this language has a web framework worth covering)* | `<lang>_routes.py` | `~X d` | `Endpoint`s with `EXPOSES` on a real framework repo | ⬜ | | | |
| **P4 Data layer** *(if this language has an ORM worth covering)* | `<lang>_orm.py`; `corpus/<lang>/<case>` | `~X d` | entities linked; `data_layer_link` reconciles against a `.sql` schema with zero invented `REFERENCES` | ⬜ | | | |
| **P5 Generic work** (§8) | whichever of §8.1-8.6 no earlier track has already built | `~X d` | each item's own exit in §8; this table passes §8.1 (`roadmap-status.py --check`) | ⬜ | | | |
| **P6 Review + MR** | `/review-pr` on the branch; fix; one MR to `develop` with this table and every validation number as its body | `~1 d` | verdict "mergeable"; every §6.1 row updated; CI green; no `episteme/` in the diff | ⬜ | | | |

---

## 5. Corpus cases

| Case | Shape | Exit |
|---|---|---|
| ... | ... | ... |

---

## 6. Files to change

**New:** `pkg/<lang>_extractor.py` (+ `<lang>_routes.py`/`<lang>_orm.py` if in scope);
`tests/pkg/test_<lang>_extractor.py`; `corpus/<lang>/*`; this file.

**Modified (P1):** `pkg/extractor.py`, `pkg/capabilities.py`, `pkg/persistence.py`,
`doctor.py`, `catalog/profile.py`, `pkg/scope.py`, `pkg/import_link.py`,
`knowledge/insights.py`, `pkg/docs.py`, `pkg/doc_link.py`, `pyproject.toml` (+ `uv.lock`),
`.github/workflows/ci.yml`, the registry tests; docs per §6.1.

**Untouched:** everything under `sdlc/` unless this track's own D9-equivalent says codegen
is in scope for this track (it usually isn't — see the Perl/Java/TypeScript split).

### 6.1 User-facing documentation — what changes, in which phase

| Document | What must change | Phase |
|---|---|---|
| `README.md` | every language list; "What's new" at the release cut | P1 · release |
| `FEATURES.md` | a capability row; the "all N front-ends" accuracy row count | P1 |
| `USER_GUIDE.md` | the extras list, the "Multi-language" blockquote, the corpus-results line | P1 |
| `KNOWLEDGE_GRAPH.md` | node/edge matrices, language table row, a fact-mapping note on D2 | P1, and each phase that adds a node/edge kind |
| `CLAUDE_GUIDE.md`, `CODEX_GUIDE.md` | the language sentence and "N front-ends" | P1 |
| `docs/specs/STATE-OF-SPINE.md`, `SPEC-INDEX.md` | front-end count, this spec's row (never ahead of the phase actually reached) | every phase |
| `CHANGELOG.md` | one entry under Unreleased per phase, house voice | every phase |

---

## 7. Blast radius — measured from the PKG, per registration site

`blast_radius` on Spine's own graph at the branch's base commit. Re-run before each phase;
a count that moved is a reason to re-read the callers, not to skip them.

| Symbol | Callers | Touches | What the phase must respect |
|---|---|---|---|
| `pkg.extractor.default_extractors` | | | the gated append is read by the corpus scorer, the freshness verifier and the matrix test |
| `pkg.persistence.extractor_fingerprint` | | | the grammar module belongs in `_GRAMMAR_MODULES` or a warm cache never notices the language |
| `pkg.import_link.link_imports` | | | whether this language's ids join `_DOTTED_PREFIXES` or need a path-suffix matcher (D3-dependent) |

---

## 8. Generic work — built here or by whichever track reaches it first, reused by every later one

**Rule:** whichever track reaches its generic-work phase first builds a shared item; the
other tracks rebase onto it and record "reused" in their own evidence column, not a second
implementation.

### 8.1 `scripts/roadmap-status.py --check` — the roadmap-currency gate *(shared)*
### 8.2 `scripts/validate-frontend.py <language> <git-url>` — the real-repo smoke test *(shared)*
### 8.3 `scripts/parse-census.py <grammar-module> <dir>` — the grammar recall-ceiling measurement *(shared; build it in whichever phase needs the first D1 recall number)*
### 8.4 This template *(shared)*
### 8.5 The shared whole-repo name-resolution `finalize` helper, `pkg/finalize_names.py` *(shared if this track's `CALLS` needs one — reuse it if a prior track already built it)*

*Add track-specific generic-work items past 8.5, numbered onward, if this track produces
something a later one should reuse rather than reimplement.*

---

## 9. Packaging

- The grammar/package dependency: extra name, version pin, why (wheel availability,
  platform coverage, known binding gaps — probe before committing, don't assume the happy
  path a README shows).
- `pyproject.toml`: `<lang> = [...]` extra; `languages` meta-extra; mypy
  `ignore_missing_imports` override if the grammar ships no type stubs.
- `.github/workflows/ci.yml`: `--extra <lang>` on the sync line.

---

## 10. Validation targets (ephemeral, docs-only names)

Two poles, both public, shallow-cloned to a scratch dir via `scripts/validate-frontend.py`
(§8.2), extracted on a `.git`-less copy, deleted after; names live in `docs/` only, never in
`src/` or `tests/`:

- **`<repo A>`** — [what it exercises: modern idioms, a framework, roles/mixins].
- **`<repo B>`** — [what it exercises: legacy/classic idioms, the hardest recall case].

**Baseline to record before P1:** `pkg extract` on each yields 0 nodes for this language and
the profiler reports no language.

---

## 11. Out of scope

- **Codegen** — its own track, unless explicitly folded in with a stated reason.
- [Anything genuinely out of bounds for comprehension: a sub-dialect, an embedded foreign
  language inside string literals, a routing style too dynamic to resolve statically.]

---

## 12. Risks and gotchas

- **D2 is load-bearing** (or name this track's own load-bearing decision). Changing it after
  the corpus is labelled invalidates the corpus.
- [Any other decision whose reversal costs more than a normal refactor — name it here before
  it's discovered mid-P3.]
- **Recall below 1.00 on the harder validation repo is the honest number.** Write it down;
  never add labels that only exist to make a score look better.

## 13. Sequence

1. P1 → P2 → ... in order, one phase at a time, each landing on the track's branch.
2. Nothing opens against `develop` until every phase in §4 reads DONE.
3. `/review-pr` on the branch, fix findings, then convert the draft MR to ready.
