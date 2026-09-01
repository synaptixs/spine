# How a document binds to the graph — a walkthrough

**Written 2026-09-01 against 3.26.1.** Every number here was measured on this repository the day
it was written.

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

The splitter then cuts on headings. **Measured here: 1,560 `Doc` nodes** across the repository —
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

Our section yields **12 mentions**. Repository-wide: **9,033**.

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

Repository-wide the same shape holds:

| | count |
|---|---|
| mentions | 9,033 |
| at least one anchor | 5,721 |
| **more than one anchor → skipped** | **2,317** |
| no anchor at all | 3,312 |
| `MENTIONS` edges actually drawn | **2,420** |

**Ambiguity, not absence, is the larger loss.** 2,317 mentions found real code and were dropped
for naming more than one thing. Any attempt to reduce that has to answer *which* one — and doing
that by proximity, or by "the most likely", is precisely the guess this tier does not make.

## Step 6 — drift, in detail

Of the 3,312 mentions that matched nothing, most are prose: ordinary words in backticks, URLs,
filenames. `symbolish_drift` narrows to identifier-shaped claims, leaving **901** on this
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

**56% of `Doc` sections bind to nothing at all.** Section 2 shows why in miniature: prose that
explains *why* something exists often names no identifier, and prose that does name one often
names an ambiguous one.

Closing it needs semantic matching — meaning rather than string equality — which means a model,
which collides with the determinism that makes `understand --check` a gate at all. Any fix
belongs in a clearly-labelled second tier, measured and declared the way GraphIR Phase 2b's
design promotion was, and never smuggled into the deterministic path.
