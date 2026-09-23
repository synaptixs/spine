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

**Every front-end has its own id scheme, and three of them are not what you would guess.**
Labelling in the wrong vocabulary scores 0.00 and reads as a catastrophic front-end failure.

| front-end | module id | type id | method separator |
|---|---|---|---|
| `python` | `py:dotted.path` | `py:mod.Cls` | `.` |
| `typescript` | `ts:path/to/file` | `ts:path/f.Cls` | `.` |
| **`javascript`** | **`ts:path/to/file`** *(TypeScript's prefix, not `js:`; suffix stripped)* | **`ts:path/f.Cls`** | `.` |
| `java` | `java:package` | `java:app.Cart` | `.` |
| `csharp` | `csharp:Namespace` | `csharp:App.Cart` | `.` |
| `go` | `go:package` | `go:cart.Cart` | `.` |
| `php` | `php:App.Svc` | `php:App.Svc.Cart` | `.` |
| `perl` | `perl:lib/Shop/Cart.pm` *(always a path)* | `perl:Shop.Cart` | `.` |
| **`kotlin`** | **`java:package`** *(Java's prefix, not `kt:`)* | **`java:shop.Cart`** | `.` |
| **`c`** | `c:src/cart.c` *(a path)* | — | **bare symbol: `c:subtotal`** |
| **`cpp`** | `cpp:src/cart.cpp` *(a path)* | **bare: `cpp:Cart`** | **`::`** |
| `sql` | `sql:schema.sql` | `sql:customer` *(an Entity)* | `.` |

C and C++ ids are **bare symbols, not module-qualified** — a symbol, not a location. Python's
scheme applied to either scores zero.

**Kotlin labels in `java:`, not `kt:`** — the third exception this table has to explain, and the
first case of a namespace deliberately shared between front-ends. Kotlin and Java share one JVM
package namespace: `import com.x.Y` names the same class whether `Y` is a `.kt` or a `.java`
file, and it cannot be both. So the Kotlin front-end mints `java:` ids and distinguishes itself
with `language: kotlin` on the node, which is what `pkg accuracy` and the capability matrix key
on. The payoff is the `mixed_java` case: a `.kt` beside a `.java` in one package — the normal
Android layout — produces **one** graph, with `IMPLEMENTS` and `CALLS` crossing the language
boundary onto real nodes. Under a separate prefix every one of those edges would dangle.
Labelling a Kotlin case in `kt:` scores 0.00. See D2 in
[kotlin-support-roadmap.md](../docs/specs/kotlin-support-roadmap.md).

**JavaScript labels in `ts:`, not `js:`** — the second shared namespace, for the same reason and
a tighter one. TypeScript compiles to JavaScript and the two share one module resolution: a `.ts`
file importing `./util` reaches `util.js`, and the reverse, so a module id is a path with the
suffix stripped whichever language wrote it. The front-end tags its nodes `language: javascript`,
which is what `pkg accuracy` and the capability matrix count by. The payoff is `mixed_ts_js`: a
gradual migration is **one** graph. Two consequences worth labelling against: every suffix is
stripped, so `import './mod.js'` names `ts:mod` (the `esm_explicit_extension` case refuses the
phantom `ts:mod.js`); and a mixed case needs `"requires": ["javascript", "typescript"]`, since
the TypeScript half is scored by the other front-end. Labelling a JavaScript case in `js:` scores
0.00. The reasoning lives in the docstring of `src/orchestrator/pkg/js_extractor.py`.

With the optional `clang` extra, the C++ `instance_calls` reference-parameter call
is resolved by a semantic post-pass. At the P3 checkpoint, aggregate C++ CALLS was 4 expected / 4 emitted /
4 matched (3 emitted / 3 matched without the extra). Ground truth and node ids are
unchanged. The CST-only limitation used to be recorded as a `known_gaps` entry, which was
removed on 2026-09-21: the scoreboard is built with the extra installed, so there the edge
*is* emitted and the entry asserted a limitation that did not exist. A gap must name an edge
the run actually misses (see below), and the limitation is documented here instead.

PHP's module id is namespace-keyed like C#/Java **only when the file has one**. A file with no
`namespace` (WordPress-style, legacy code) keys on its repo-relative path instead —
`php:inc/legacy.php`, not a dotted form — which is also what makes a literal `require`/`include`
target path-suffix-matchable, C-style. A `legacy_require`-shaped case labels module ids in that
path form, not the dotted one.

Perl's module id is **always** path-keyed — never dotted, unlike PHP's fallback — because a
Perl file has no single reliable namespace of its own (D2, perl-support-roadmap.md): it may
hold zero, one, or several `package`/5.38 `class` declarations, each its own dotted `Type`.
A `require "path.pl"` target (D7, the same path-suffix matcher as PHP's and C's) is
therefore *also* a path-shaped `perl:` id — the two shapes never collide the way they can't
in PHP either, since a `Type` id is dotted and a `Module` id ends in `.pl`/`.pm`/`.t`.

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
| `known_gaps` | edges from `edges` the front-end is *known* to skip, each with a `why` — and **does** skip: an entry naming an edge the extractor emits fails the case to load |
| `false_positives` | edges the front-end **emits that are not true** — invention, held visible |
| `refusals` | edges a plausible reader **would** emit and this one must not — predicted before scoring, and **enforced**: if the extractor emits one, the case fails to load |
| `excluded` | what is deliberately not labelled, and on what grounds |
| `open_questions` | vocabulary questions that must be decided before the label is meaningful |

**`known_gaps` is not an exemption.** A fact listed there still counts as a recall miss. It
records that the miss is understood rather than unnoticed, so the report can separate known
loss from new loss. Moving a fact into `known_gaps` must never change the score; if it does,
the implementation is wrong.

**That gate now exists, and it reads this field.** `compare_scoreboard` gates corpus recall on
*unexplained* misses — `expected - matched - known_gaps` — and on `matched` never falling,
rather than on the recall ratio. The published ratio is untouched and still counts a known gap
as a miss, exactly as the paragraph above says; what changed is that labelling one no longer
*fails a build*, because a ratio that falls when you write down a loss you already had punishes
measuring it. The two conditions are not one: unexplained misses catch a new miss nobody
accounted for, including the case a ratio is blind to (8/10 and 12/15 are both 0.80 while the
misses go two to three), and `matched` falling catches an edge that stopped resolving even when
a gap labelled in the same commit would otherwise pay for it.

**A gap must name an edge the run actually misses, and this is enforced.** Load-time validation
already refused an entry absent from `edges`; a *closed* gap still satisfied that, so an entry
whose edge the front-end had since learned to emit stayed valid and went on asserting a
limitation that no longer existed. Four such entries were found the day the gate changed. That
is not only stale prose: the gate subtracts gaps per language and per kind, so credit paying for
nothing silently absorbs a real new miss in another case of the same language — which is what
three dead TypeScript entries were doing for two genuine misses in `receiver_shapes`. A gap
naming an edge the extractor emits now fails the case to load, the same way a broken `refusal`
does. **Delete the entry when the gap closes**; if the answer depends on an optional extra, say
so in `excluded` and describe the configuration the scoreboard is built in.

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

**`refusals` are the opposite of `false_positives`, and the distinction is not pedantry.**
A refusal is an edge a *plausible* reader emits and this one deliberately does not — the
tempting fabrication the case was built around. Eighteen such entries were filed under
`false_positives`, whose definition one row above is "edges the front-end **emits** that are
not true", so a reader counting the field concluded Kotlin invented eleven edges it had in
fact correctly refused. They are also the only annotation here that is **checked**: a
refusal the extractor starts emitting fails the case on load, rather than showing up as an
anonymous dip in a precision number.

## Writing a case

1. Write `repo/` — small, realistic, and built around one shape that is hard to resolve.
2. Read it and write `expected.json` from the source. Do not run the extractor yet.
3. Predict which facts will be missed, and write them into `known_gaps` with a reason.
4. Score it. A miss you predicted validates the case; a miss you did not is the finding.
5. An emitted fact that is *not* in `expected.json` is either a label omission or an
   invention. Both matter — the second more.

Step 3 before step 4 is the discipline that keeps the corpus honest. A gap recorded after
seeing the output is a rationalisation; one recorded before is a prediction.

## Header routing fixtures

| Case | What it proves |
|---|---|
| `cpp/header_classes` | A class declared in a reached `.h` header and defined in `.cpp` is grounded by C++; the optional semantic pass resolves its reference-parameter call. |
| `c/header_unaffected` | A C-only `.h` stays C; its struct, field and free-function ids and calls are unchanged. |

Both fixture roots are `.repo/`, so their source does not enter Spine's graph.
