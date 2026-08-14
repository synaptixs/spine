# The accuracy corpus — ground truth, and how it is written

Fixture repositories with hand-written expected facts, used by
`orchestrator pkg accuracy` to measure **precision and recall** per node kind and edge kind,
per language. See [`../docs/specs/pkg-accuracy-roadmap.md`](../docs/specs/pkg-accuracy-roadmap.md)
for why this exists and
[`../docs/specs/build-documents/PKG-ACC-1-build.md`](../docs/specs/build-documents/PKG-ACC-1-build.md)
for the comparator's design.

## Layout

```
corpus/<language>/<case>/
    expected.json     the labels
    .repo/            the fixture — extraction root, a faithful mini-repository
```

`.repo/` is a subdirectory so the fixture contains nothing but source. Extraction runs
against it, so module names are relative to it: `.repo/shop/cart.py` → `py:shop.cart`.

**The leading dot is load-bearing.** Fixture source is real `.py` in this repo, so without
it a repo-wide `pkg extract` pulls the fixtures into Spine's *own* graph — measured at 73
nodes for these two cases, with `py:corpus.python.plain.repo.shop.cart.Cart` presented as
part of Spine. Both walkers (`extractor.py` and `doc_source.py`) skip directories starting
with `.`, so the dot fixes it with no code change. `pkg verify` does **not** catch this —
fixture modules are perfectly self-consistent — so nothing will remind you.

`expected.json` stays outside the dot-directory, and `corpus/README.md` stays visible: the
method is meant to be read and published. Only the fixture source hides.

## The one rule

**Labels are written from the source by a human, never from extractor output.**

Bootstrapping `expected.json` by running the extractor and saving what it emits produces a
corpus that agrees with itself: recall is 1.0 by construction and the number measures
nothing. The same applies to generating a case with a model — a model that writes both the
fixture and its labels is doing the same circular thing more expensively.

Write what is *true of the code*, established by reading it against the language reference.
Then run the scorer, and treat every mismatch as a finding rather than a label to correct.
Only one kind of mismatch is a label bug: **the wrong vocabulary** — an id in a form the
graph does not emit. Correcting a label because the extractor missed the fact is the failure
this rule exists to prevent.

## The id vocabulary

Labels must use the exact ids the graph emits, or precision and recall both collapse for
reasons that have nothing to do with accuracy.

| | form | example |
|---|---|---|
| module | `py:{dotted.path}` — `src/` stripped, `__init__` collapsed to its package | `py:shop.cart` |
| symbol | `{parent_id}.{name}` | `py:shop.cart.Cart.total` |
| import target — symbol exists | **the symbol** | `from shop.tax import rate` → `py:shop.tax.rate` |
| import target — symbol has no node | **the module it lives in** | `from api.routes import router` → `py:api.routes` |

That last pair is the trap, and it has two halves. `from X import Y` binds to `py:X.Y`, so a
label naming the *module* scores `IMPORTS` recall at zero when `Y` is a function or class —
it looks like an extractor regression and is not. But when `Y` has no node of its own — a
module-level variable, a re-export, an alias — the import join rewrites the edge to the
nearest first-party module, and a label naming the *symbol* misses for the opposite reason.

The rule is one line of `import_link.py`: the rewrite fires **only when the target is an
external placeholder**. A real node is left alone. So ask whether `Y` is itself declared, not
what the import statement looks like — `rate` (a function) and `router` (a variable) are the
same syntax and different edges.

## Fields in `expected.json`

| field | meaning |
|---|---|
| `language`, `case` | identity |
| `why` | what this case exists to exercise |
| `root` | extraction root, relative to the case dir — always `repo` |
| `nodes` | `{id, kind}` — every node true of the fixture |
| `edges` | `{src, dst, kind}` — every edge true of the fixture |
| `known_gaps` | edges from `edges` the front-end is *known* to skip, each with a `why` |
| `false_positives` | edges the front-end **emits that are not true** — invention, held visible |
| `excluded` | what is deliberately not labelled, and on what grounds |
| `open_questions` | vocabulary questions that must be decided before the label is meaningful |

**`known_gaps` is not an exemption.** A fact listed there still counts as a recall miss. It
records that the miss is understood rather than unnoticed, so the report can separate known
loss from new loss — and new loss is the signal a regression gate would eventually watch.
Moving a fact into `known_gaps` must never change the score; if it does, the implementation
is wrong.

**`open_questions` is not a parking lot.** An entry there means the expected set is
provisionally incomplete in a way that affects every case sharing the shape, so the number is
provisional too. Resolve them rather than accumulate them.

## Decided rules

Settled 2026-08-12 while labelling the first two cases. Each affects every case, so they are
recorded here rather than argued again per case.

| question | decision | why |
|---|---|---|
| Do `external` nodes count? | **Nodes no, edges yes.** An external node stays out of the node ratio; an edge *pointing at* one still counts. | An external node is a placeholder for something outside the tree. An edge to it is a claim that the call happens — and that claim can be false. Excluding both is what let `build -> py:cls` score 1.0 precision on the first run. |
| Must every case contain a miss? | **The corpus must; a case need not.** | `plain` scores 1.0/1.0 by design. It is the control: the signal is any future run where it does not. |
| Is a module-level constant a `Field`? | **No — not labelled.** | A `Field` belongs to a `Type` in this vocabulary (`CONTAINS` is module→type, type→method). Labelling `DEFAULT` would be a category error, not a recall miss. |
| Is instantiation a `CALLS` edge? | **Yes — `CALLS` to the `Type` node.** | `Handler()` → `caller -CALLS-> py:svc.handlers.Handler`. Needs no synthetic `__init__` when the class defines none, and keeps constructor use visible in blast radius. |

**On `false_positives`:** an emitted edge that is not true costs precision, and it should keep
costing it. The field records the defect and its reasoning so a low precision number is
legible rather than mysterious — it never suppresses the penalty. Same contract as
`known_gaps`: recording a fact must not change the score.

## Writing a case

1. Write `repo/` — small, realistic, and built around one shape that is hard to resolve.
2. Read it and write `expected.json` from the source. Do not run the extractor yet.
3. Predict which facts will be missed, and write them into `known_gaps` with a reason.
4. Score it. A miss you predicted validates the case; a miss you did not is the finding.
5. An emitted fact that is *not* in `expected.json` is either a label omission or an
   invention. Both matter — the second more.

Step 3 before step 4 is the discipline that keeps the corpus honest. A gap recorded after
seeing the output is a rationalisation; one recorded before is a prediction.
