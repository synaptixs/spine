# How a document binds to the graph — a walkthrough

**Written 2026-09-01 against 3.26.1; the fix it describes shipped in 3.27.0.** Every number here
is measured on this repository, and `scripts/state-numbers.py` re-derives nine of them — it prints
a `[trend]` line when this page has aged rather than failing the build.

Two sections. **The first is the whole mechanism in six steps** — read that if you want to know
what happens and when. **The second walks one real page through every one of those steps**, with
the actual values, so the rule and its consequences are visible at the same time.

Its siblings: [`document-ingestion-reference.md`](document-ingestion-reference.md) is the
*format-by-format* reference — what each file type does, what it costs, what it cannot express.
[`doc-ingestion-spec.md`](doc-ingestion-spec.md) is the design record — why the tier exists and
what shipped in which phase. This page is neither; it is the mechanism, traced.

---

# Section 1 — The summary

**When.** As a **post-pass**, after the code graph is already built — never during parsing. It
runs when you call `understand`, `state`, or `pkg export` (non-sqlite). The code graph has to
exist first, because it is the dictionary the binder looks words up in.

**Step 1 — walk.** Find every doc file from the repo root, in sorted order, skipping
dot-directories and anything no reader claims. Markdown, rst, txt, PDF, media transcripts.

**Step 2 — split.** Each document becomes **one `Doc` node per heading section** —
`doc:README.md#usage` — each carrying the line its heading starts at. PDF, `.rst` and `.txt`
cannot express headings, so those become one node for the whole file.

**Step 3 — pull out the claims.** From each section's prose, extract the spans that look like
they name code: backticked spans, dotted names, `snake_case`, `CamelCase`, and file paths. These
are *mentions* — claims the prose is making about the codebase.

**Step 4 — look each claim up.** Match the mention against the code graph: a file path against
real paths, a symbol name against real symbol names.

**Step 5 — bind, but only when it is unambiguous.** Exactly **one** match draws the edge:

```
doc:README.md#usage  --MENTIONS-->  py:orchestrator.pkg.store.FactStore
```

More than one match draws **nothing**. Guessing which was meant is the fabrication this graph
refuses to commit.

**Step 6 — what matched nothing becomes a signal.** A symbol-shaped claim matching *nothing* is
recorded as **drift**: the prose names code that does not exist.

**What you end up with**

- `Doc` nodes and `MENTIONS` edges in the same graph as the code, so you can ask *"which docs
  describe this function?"* and *"what code does this section talk about?"*
- A drift list of claims the graph cannot support.
- All of it **deterministic and model-free** — same commit in, same edges out, which is what
  lets it be gated.

---

# Section 2 — The same six steps, traced

The subject is `KNOWLEDGE_GRAPH.md`, and specifically its **Node kinds** section. It was chosen
because it is a section *about the graph's own vocabulary*, so almost every word in it is a code
term — and it still only binds a quarter of them. That is the whole tier in one page.

## Step 1 — walk, in detail

`read_doc_pages` walks from the repo root with `os.walk`, **sorted at every level**, so the
output is byte-identical between machines. Two exclusions:

- `DEFAULT_IGNORE_DIRS` — `.git`, `node_modules`, build output.
- **Any directory whose name starts with a dot.**

That second rule is load-bearing far beyond docs: it is why every on-disk test fixture root in
`corpus/` is named `.repo/`. Without the leading dot, fixture source would be walked like real
source and land in this repository's own graph.

A file is visited only if some registered reader claims its suffix. Formats **register** rather
than branch, so adding one touches no existing reader.

> **Docs are read from disk whether or not git tracks them.** A documentation tree that exists
> locally but is not committed makes your `episteme/` describe `Doc` nodes CI cannot see — and
> `understand --check` then fails on a diff you cannot reproduce. This is the reason
> `docs/specs/` is tracked on purpose.

## Step 2 — split, in detail

Each reader converts its format into **markdown-shaped text**: HTML's `<h1>`, Word's `Heading 1`
style and a spreadsheet's sheet name all become an ATX `#` heading. Everything downstream reads
that one shape and never learns which format it came from.

The splitter then cuts on headings. **Measured here: 1,602 `Doc` nodes** across the repository —
sections, not files.

Our subject becomes:

```
id      doc:KNOWLEDGE_GRAPH.md#node-kinds
source  KNOWLEDGE_GRAPH.md
line    73
```

The line matters: it is the section's *heading* line, which is what a finding anchors to. A
drift comment landing on line 1 of a long document tells a reader nothing.

**PDF, `.rst` and `.txt` collapse to one node per file** — not a special case, a property of the
formats: none of them carries a heading a splitter could recover.

## Step 3 — the claims, in detail

`extract_mentions` pulls five kinds of span, de-duplicated per page with backticks taking
precedence:

| Kind | Example |
|---|---|
| `BACKTICK` | `` `FactStore` `` |
| `DOTTED` | `orchestrator.pkg.store` |
| `SNAKE` | `link_docs` |
| `CAMEL` | `DocReconciler` |
| `FILE` | `src/orchestrator/pkg/store.py` |

Our section yields **12 mentions**. Repository-wide: **9,287**.

One rule worth knowing: a bare CamelCase word — "GitHub", "Python" — binds if it happens to
resolve, but **never counts as drift**. Prose capitalisation is not a code claim.

## Step 4 — the lookup, in detail

`DocReconciler` is built from the **code-only** batch, which has two consequences:

- `Doc` nodes are never themselves mention targets — documents cannot cite each other into the
  graph.
- The dictionary is fixed before any prose is read, so binding cannot be influenced by the
  documents being bound.

A `FILE` mention is matched against real repository paths, then against the filesystem relative
to the document's own directory. A symbol mention is matched against symbol names.

## Step 5 — the three outcomes, in detail

| Anchors found | Result | Why |
|---|---|---|
| exactly 1 | **`MENTIONS` edge** | an unambiguous claim about a symbol |
| more than 1 | **skipped** | an ambiguous edge poisons grounding — the same *skip rather than guess* rule that holds `CALLS` precision at 1.00 |
| 0 | **candidate drift** | the docs name something the code does not define |

Our section, every mention, with its real anchor count:

| Mention | Anchors | Outcome |
|---|---|---|
| `Function` | 1 | **edge** |
| `Entity` | 1 | **edge** |
| `Doc` | 1 | **edge** |
| `Endpoint` | 3 | skipped |
| `Intent` | 4 | skipped |
| `Type` | 6 | skipped |
| `Module` | 7 | skipped |
| `Field` | 9 | skipped |
| `language` | 18 | skipped |
| `name` | 52 | skipped |

**Three of twelve bind.** Not because the prose is wrong — every one of those words names a real
concept in this codebase — but because `Module` is the name of seven different things and the
binder will not pick one.

Repository-wide, every mention lands in exactly one of four buckets:

| | count | share |
|---|---|---|
| **one symbol anchor → `MENTIONS` edge** | **3,047** | 33% |
| more than one symbol anchor → skipped | 1,986 | 21% |
| resolved to a **file**, no symbol | 847 | 9% |
| nothing at all | 3,407 | 37% |
| **total mentions** | **9,287** | |

3,014 edges are drawn from those 3,047 — the 33 difference is de-duplication, where one section
names the same symbol twice.

> **These seven figures are derived, and reported rather than gated.**
> `scripts/state-numbers.py` re-computes every one of them and prints a `[trend]` line when the
> table has aged. It does **not** fail the build on them, and measuring is what decided that:
> they moved within an hour of first being written, because they count mentions across every
> markdown file and anchors across every symbol, so any commit touching either moves them.
> Gating that would fail nearly every pull request for refreshing seven numbers in a
> walkthrough — the failure that un-gated the doc-drift ratchet a day earlier.
>
> Deriving them still catches the error they exist for: a figure wrong **when written**, as
> opposed to one that has merely aged. Re-derive with:
>
> ```bash
> python scripts/state-numbers.py
> ```

> **This table replaced a wrong one on 2026-09-01, and the error is worth keeping visible.** The
> first version reported "at least one anchor: 5,721" and "more than one → skipped: 2,317",
> having **conflated file anchors with symbol anchors**: 1,370 mentions resolve to a real file
> and no symbol, so they cannot produce a `MENTIONS` edge — that edge points at a symbol id —
> and counting them as bound inflated both figures. `DocBinding.bound` is true for either kind;
> `link_docs` draws an edge only for `anchor_ids`. Two names for two different things, and the
> summary used the wrong one.
>
> The conclusion followed the bad number. It read *"ambiguity, not absence, is the larger
> loss"*, and that is **false**: absence is 3,343 against ambiguity's 1,959.

**What is true, and still worth saying:** ambiguity is not a rounding error. **1,986 mentions
found real code and were deliberately dropped** for naming more than one thing — 29% of
everything that fails to become an edge. That is qualitatively different from the 3,407 that
found nothing, and it matters for how the gap gets closed: reducing it means answering *which*
symbol was meant, not *whether* one exists. Doing that by proximity, or by "the most likely", is
precisely the guess this tier does not make.

> **This paragraph used to be wrong, and the correction shipped as a change.** It read: *"prose
> citing `src/orchestrator/pkg/store.py` is naming a path, not a symbol, and there is no symbol
> edge to draw."* **A `Module` is a node.** The path maps to the module the extractor built from
> it, by its own provenance — so there was an exact, deterministic edge to draw, and 941 of them
> were being discarded. Fixed 2026-09-02 per [`doc-file-binding.md`](doc-file-binding.md): a
> cited path binds to its module when the text matches **exactly one file** and **exactly one
> module** owns it — 534 edges, and 55 sections that bound to nothing now bind.

The 845 file-only mentions that remain are of two kinds: those with no module to name at
all — images, config files, other documents — and those whose text matches **more than one**
file, which is ambiguous about *which file* before it is ambiguous about which module. Both
are correctly unbound.

## Step 6 — drift, in detail

Of the 3,407 mentions that matched nothing, most are prose: ordinary words in backticks, URLs,
filenames. `symbolish_drift` narrows to identifier-shaped claims, leaving **about 900** on this
repository. Examples, all real:

```
comprehension_labels   comprehension_corpus   SyntaxWarning   SyntaxError   profile_select
```

Two of those illustrate the tier's own precision limit: `SyntaxWarning` and `SyntaxError` are
Python builtins, not this project's code. The graph has no node for them and never will, so
they will drift for ever without anything being wrong.

**That is why drift is reported and not gated.** It shipped as a ratchet on 2026-08-31 and was
un-gated one pull request later, after failing a documentation change: about a tenth of the
population cannot bind by construction — parameters, module constants, string literals, log
event names and builtins have no node kind. The number stands as an **upper bound** on drift,
never as a defect count.

> **The leaf-only fallback was audited on 2026-09-01, cleared, and the real defect fixed in
> 3.27.0.** `bind`'s last resort, when a
> dotted mention matches no id tail, is to match its **final segment alone** — throwing the
> qualifier away. That looks like a fabrication waiting to happen: with exactly one symbol named
> `json` in this repository, every `.json` filename could bind to `DummyResponse.json`, a test
> double's method.
>
> **It does not happen, and the suspicion was quantified before it was acted on.** The fallback
> rescues **76 mentions** into a single correct anchor — `store.find` onto `FactStore.find`,
> `Budget.max_replan_count`, `CapabilityResult.content_type` — turns 166 into ambiguous
> bindings that are skipped, and mis-binds **no filename at all**. Filenames never reach it:
> a recognised extension makes the mention `FILE`-kind, which resolves against the filesystem
> instead.
>
> Two `MENTIONS` edges *do* point at that method, and neither comes from the fallback. They come
> from the bare word `` `json` `` in prose, which resolves exactly and binds — the documented
> behaviour that single plain lowercase words bind when they resolve. That is a separate and much
> smaller question than the one investigated.
>
> **What the audit did find was a drift-precision defect, in a different place.** `md.js`,
> `graph.html` and `compose.dev.yml` are dotted, lowercase and multi-segment, so they are shaped
> exactly like symbol paths; the FILE pattern does not recognise their extensions, so they arrived
> as symbol claims and the drift list reported **58 filenames as prose naming code that does not
> exist**. Extensions were added to `_URL_TAILS` and checked in `_can_drift` — shipped in
> **3.27.0**. Drift went
> **1,685 → 1,627** and **not one `MENTIONS` edge changed** — the same 2,469 (source, target)
> pairs before and after, and the only binding bucket that moves is *nothing at all*, by the 12
> filenames that stop being mentions at all. A naming asymmetry, not a coverage gap.
> `README.md` and `src/a/b.py` keep their disk check, because a document linking a file that is
> not there is a real finding.
>
> **Twenty of the 58 name a file that exists in this repository** — `md.js`, `schema.sql`,
> `uv.lock`, `setup.cfg`, `main.go` — which settles what they are. Most of the rest are
> illustrative filenames in examples (`money.test.ts`, `CalculatorTest.java`, `compose.dev.yml`).
> **Four are removed for the wrong reason and are worth naming:** `javax.ws.rs` and
> `jakarta.ws.rs` are Java *package* prefixes, and `a.b.C` is a placeholder from a regex comment.
> They match because `rs` and `c` are also file extensions. The outcome is right — none is this
> project's code, so they were noise of the `SyntaxError` kind — but a package path whose last
> segment collides with an extension is a real limit of a shape-based rule, not a filename.

## What is added, and what is not

`link_docs` calls `add_node` for `Doc` and `add_edge` for `MENTIONS`, **and nothing else**. No
code node, edge or provenance is modified, and a repository with no docs comes back byte-identical.
That is what makes it safe to wire into every comprehension surface.

One nuance that looks like an exception and is not: a `MENTIONS` edge counts as a *use* in
dead-code detection, so a documented-but-uncalled symbol stops being flagged. That is an
inference over the graph changing, not the graph being rewritten.

## Where these facts go

| Consumer | What it does with them |
|---|---|
| `PKGCodegenGrounder._doc_block` | attaches a linked section's prose to that symbol's source in the codegen context — the model gets the code *and what it is for* |
| `state` / `episteme` | the Documentation section, and the drift list |
| `docs_for` / `mentions_of` | "which docs describe `X`?", answerable from a script or an assistant |
| `insights.py` | treats a `MENTIONS` edge as a use |
| `sdlc/builddoc.py` | lists the docs covering in-scope symbols, so a change plan names the prose that will need updating |

## The open gap

**827 of 1,602 `Doc` sections — 52% — bind to nothing at all.** Section 2 shows why in miniature:
prose that explains *why* something exists often names no identifier, and prose that does name one
often names an ambiguous one. Both causes are real, and **absence is the larger of the two** —
3,407 mentions against 1,986 — though the smaller one is the more interesting, because those 1,986
found the code and were refused.

> **Measured 2026-09-02: most of that 52% never made a claim.** The headline invites the
> reading that half the documentation failed to bind. It did not.
>
> Of the **809** unbound mentions in the 338 sections that bind to nothing:
>
> | | | |
> |---|---|---|
> | **not a claim at all** | **603** | 74.5% — `OpenSpec`, `TypeScript`, `ReAct`, `GitHub`, `OpenTelemetry`. The tier's own `_can_drift` already refuses these: CamelCase prose and plain words are not code claims |
> | no node kind exists | 68 | 8.4% — `repo_path`, `Context`: parameters, locals, attributes |
> | a file stem, cited without its extension | 16 | 2.0% — `comprehension_labels` for `comprehension_labels.yaml`. Deterministically reachable |
> | third-party or builtin | 19 | 2.4% — `SyntaxError` will drift for ever, correctly |
> | **found nothing, and is claim-shaped** | **103** | 12.7% — `startup_timeout_sec`, `bug_report`, `action_required`: mostly config keys and template filenames |
>
> **278 of those 338 sections — 82% — contain nothing bindable at all.** At most **60 sections
> of 1,602 (3.7%)** hold a mention a better tier could plausibly reach.
>
> That is the number a second tier would be chasing, and it is not 827. Re-derive with
> `python scripts/classify-unbound-mentions.py`.

**Nothing above closed any of it, and that is worth stating plainly.** The 2026-09-01 audit ran at
this gap and came back with a *drift-precision* fix: 58 filenames that the drift list was calling
missing code. Real, worth having, and **it moved this number by zero** — not one `MENTIONS` edge
changed. The investigation that goes looking for coverage and returns with precision has not
failed, but it has not made progress on the thing it set out to measure either.

Closing it needs semantic matching — meaning rather than string equality — which means a model,
which collides with the determinism that makes `understand --check` a gate at all. Any fix belongs
in a clearly-labelled second tier, measured and declared the way GraphIR Phase 2b's design
promotion was, and never smuggled into the deterministic path.

**What would count as progress**, in the order the evidence supports:

1. ~~**Answer *which* symbol was meant for the ambiguous mentions** — "the tractable half, and
   the half a deterministic rule might still reach."~~ **Measured 2026-09-02 and withdrawn.**
   Only **48 of 1,986** ambiguous mentions (2%) have every candidate anchor in one owning
   module, which rescues **5 sections**. And the compounding idea in that sentence — bind the
   file first, then use its module to pick among a mention's candidates — rescues **0**. It
   sounded right, cost nothing to check, and was wrong.
2. ~~**Characterise the 3,407 that found nothing** before trying to bind them.~~ ✅ **Done
   2026-09-02** — the box above. The estimate was "about a tenth cannot bind by construction".
   Measured, it is **87%**, and the dominant reason is not a missing node kind: three quarters
   of them are not code claims at all. **The cheapest remaining deterministic win is the file
   stem** — 16 mentions naming a file without its extension.

   This is the measurement that should precede any model tier, and it argues against one: the
   reachable population is ~60 sections, against a determinism cost that applies to every
   surface `understand --check` gates.
3. **Only then a second tier.** Measured against these figures, declared as non-deterministic, and
   kept out of the path `understand --check` gates.
