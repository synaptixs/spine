# TypeScript call resolution — what the number actually is

**Status:** **Written 2026-09-01 against 3.26.1.** Not started, and this spec argues the obvious
approach is the wrong one to start with.
**Owner:** _unassigned_

`STATE-OF-SPINE` §8 has carried *"TypeScript resolution via the TS compiler API"* as an idea
since 2026-08-21, on the strength of one number: `CALLS` recall **0.50** on TypeScript against
1.00 on C and SQL. This spec was meant to scope that work. Measuring first changed both halves
of the premise.

---

## 1. The number moved twice, and neither move was the code changing

| | recall | why |
|---|---|---|
| Published in three places | 0.50 | stale — nothing derives it |
| Scoreboard, 2026-09-01 | **0.571** (4 of 7) | what it had actually been |
| Scoreboard, after step 1 | **0.357** (5 of 14) | the corpus doubled |

**The drop to 0.36 is measurement, not regression.** No extractor changed. Two fixtures were
added for shapes nothing was testing — one that works, one family that does not — and the
denominator went from 7 labelled edges to 14. A number that falls when you look harder was
always that number.

Corrected in all three places. Nothing derives that figure — `scripts/state-numbers.py`
covers eight claims and `CALLS` recall is not among them — so it aged silently the way every
hand-carried number in this repository has.

**It is also a very small denominator.** Seven labelled call edges is a passing test, not a
measurement, and anyone quoting TypeScript call accuracy should quote the denominator with it.
The same caution already applies to SQL, whose 1.00 rests on exactly one labelled call edge.

## 2. The loss is one family of call shapes, and it is not what the corpus alone suggests

| Corpus case | `CALLS` expected | matched | recall |
|---|---|---|---|
| `typescript/plain` | 2 | 2 | **1.00** |
| `typescript/shadowed_calls` | 1 | 1 | **1.00** |
| `typescript/generics` | 0 | — | n/a |
| **`typescript/instance_calls`** | **4** | **1** | **0.25** |

Every corpus miss is in one fixture, and all three are the same thing:

```ts
import { Handler } from "./handler";

export function viaParameter(h: Handler): string {
  return h.run();          // missed
}

export function viaLocal(): string {
  const h = new Handler();
  return h.run();          // missed
}
```

tree-sitter sees `h.run()` and knows exactly two facts: `h` is an identifier, `run` is a
property. **It has no idea `h` is a `Handler`**, so the call cannot be resolved to
`Handler.run` and — under this graph's standing rule — is skipped rather than guessed. That is
`_resolve_call`'s *"return `None` instead of a guess"*, working as designed and costing recall.

**The corpus is too small to stop here**, so six shapes were probed directly against the
front-end rather than reasoned about:

| Shape | |
|---|---|
| `this.helper()` | **resolves** |
| `h.run()` where `h: Handler` is a parameter | missed |
| `h.run()` after `const h = new Handler()` | missed |
| `h.run()` after `let h: Handler; h = new Handler()` | missed |
| `o.h.run()` where `o = { h: new Handler() }` | missed |
| `new Handler().run()` | missed |

**`this.method()` — the most common call shape in class-heavy TypeScript — already works.** What
fails is one family: **the receiver is a variable, and its type is known only from a declaration
elsewhere in the function.**

The last row is the one to notice. `new Handler().run()` has no variable and needs no inference
at all: the constructor is right there in the call expression. It is missed anyway, which says
the gap is not really about *types* — it is that the resolver only handles a bare identifier and
`this`, and gives up on every other receiver shape.

## 3. That changes which fix to reach for

### Option A — a bounded local-type pass, in the existing front-end

Five of the six probed misses are **syntactically local**. The type is in the same tree-sitter
CST as the call:

| Shape | What resolves it |
|---|---|
| `new Handler().run()` | the `new_expression` *is* the receiver — no inference at all |
| `h: Handler` parameter | the type annotation on the parameter |
| `const h = new Handler()` | a `new_expression` on the right of a declarator |
| `let h: Handler` | the declarator's annotation |
| `o = { h: new Handler() }` then `o.h.run()` | harder — needs a shape for the object literal |

Walk a function body, record `identifier → type name` for the annotation and `new` forms, and
resolve a member call against it. **No new dependency, no new runtime, no determinism risk.** It
covers every corpus miss and four of the five probed shapes; the object-literal case is the one
that would stay open, and it is the rarest.

### Option B — the TypeScript compiler API

A real symbol table, resolving arbitrary expressions, imported types, generics and inference.
Strictly more capable. It carries three costs, and the third is disqualifying as stated:

| Cost | Weight |
|---|---|
| A **Node runtime dependency** for a Python package whose base install is stdlib-only | Contradicts the standing rule that every parser lives behind a lazy extra — but an extra could hold it |
| **Loss of tree-sitter's error tolerance** — a file that does not compile currently still yields the parts that parse | Real. Half a repository's value during a refactor is reading code mid-edit |
| **Resolution depends on installed packages** — `node_modules` present or absent changes what a type resolves to | **This breaks invariant 2.** Same commit, different graph on different machines. `understand --check` could not gate it, extraction could not be commit-keyed, and a run's evidence could not be replayed |

The third is not a trade to weigh; it is the property everything else in this system is built
on. **Option B is only viable if resolution is confined to first-party source** — no
`node_modules`, no ambient types — and at that point it is doing a more expensive version of
Option A's job over the same information.

## 4. The blocking unknown: we cannot measure this

Option A demonstrably fixes the corpus. **Whether it fixes TypeScript is unknown, and today
unknowable.**

- The corpus holds **7 labelled call edges**. Option A would take it to 7/7, and 7/7 on seven
  edges is not evidence about vue-core.
- The `runtime` oracle — which measures `CALLS` recall by *executing a repository's own tests*
  and watching what actually gets called — is **Python-only**. It is the one instrument that
  could answer this, and it does not point at TypeScript.
- The `invention` oracle measures the opposite direction: fabricated edges, not missing ones. It
  says nothing about recall.

**So the honest sequence is not "build Option A".** It is:

1. ~~**Extend the corpus first**~~ ✅ **done 2026-09-01.** Two cases added:
   `typescript/this_calls` pins the shape that works and was tested nowhere, and
   `typescript/receiver_shapes` puts a number on the family that does not — `new Handler().run()`,
   a `let` with an annotation, and a call through an object-literal field. It scores **CALLS
   recall 0.00 on 6 expected edges**, which is the honest cost of a resolver that handles only a
   bare identifier and `this`.

   **Still unprobed, and deliberately not invented:** generics, union-typed variables and
   destructured bindings. Each needs a judgement about what the *correct* edge is before it can
   be labelled, and a fixture asserting a contested truth is worse than no fixture.
2. **Then Option A**, sized against what the widened corpus actually shows.
3. **Option B only if** the widened corpus shows the loss is dominated by shapes no local pass
   can reach — and even then, only confined to first-party source, for the determinism reason
   in §3.

Skipping step 1 means building a fix for the one shape we happen to have written a fixture for.

## 5. Decisions

| | Decision | Recommendation |
|---|---|---|
| **D1** | Chase the compiler API now? | **No.** Every measured miss is reachable without it, and its determinism cost is disqualifying as normally implemented |
| **D2** | Build Option A now? | **Not yet.** It would take the corpus to 7/7 and teach us nothing about real TypeScript. Widen the corpus first |
| **D3** | ~~What is step 1 worth?~~ | ✅ **Done 2026-09-01.** It cost an afternoon and moved the published figure from 0.57 to 0.36 — measurement, not regression. **What it bought:** the family now has a number and a regression guard, and `this.method()` can no longer break silently |
| **D4** | Extend the `runtime` oracle to TypeScript? | Out of scope here, and the only thing that would make TS recall properly measurable. Worth its own row: it is currently the last Python-only oracle |

## 6. Invariants

- **Determinism is not negotiable.** Any resolution whose answer depends on what is installed
  produces a different graph for the same commit and disqualifies itself, whatever it buys.
- **Skip rather than guess.** A call whose target cannot be resolved emits nothing. Recall may
  be imperfect; precision is held at 1.00, because a fabricated edge sends an agent into a
  function nobody wrote.
- **Structure comes from a real parser.** Whatever resolves types reads the tree; it does not
  pattern-match source text.

## 7. Non-goals

- **Type checking.** Spine records what calls what, not whether the program is well-typed.
- **Raising the number by relaxing the guess rule.** Recall bought with fabricated edges is the
  bug 3.18.0 fixed at a cost of 497 edges.
- **Any change to the other seven front-ends.** They score their own recall and have their own
  shapes.

## 8. Open questions

1. **How much of real TypeScript is the variable-receiver family?** Still unknown. The probe
   shows *which* shapes fail, not how often they occur in vue-core. Counting member calls by
   receiver shape across the pinned corpus repository would answer it, needs no ground truth,
   and is the cheapest next measurement after step 1.
2. ~~**Does `this.method()` resolve today?**~~ **Answered while writing this: yes.** It is absent
   from the corpus, so the fixtures neither credit nor blame the front-end for the most common
   call shape in class-heavy TypeScript. **That is a fixture gap of its own** — a shape that
   works and is untested is one refactor away from silently not working.
