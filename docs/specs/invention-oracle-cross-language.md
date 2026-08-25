# The invention oracle across eight front-ends — Phase 1 results

**Status:** Phase 1 complete. Phases 2–4 rescoped by what Phase 1 measured — see the last
section before acting on them.
**Measured:** 2026-08-24, against `develop` at `3ac6a81`.
**Reproduce:** `orchestrator pkg accuracy --oracle invention <repo>` — every number below.

---

## The defect

Four front-ends emit a `CALLS` edge to the wrong function when a name is **shadowed** by a
parameter or a local. Two lines of TypeScript are enough:

```ts
export function send(x: string): void {}
export function outer(send: (v: string) => void): void { send("hi"); }
```

The graph asserts `ts:a.outer -CALLS-> ts:a.send`. `outer` calls its own parameter; the
module-level `send` is never reached. The same construction reproduces in Go (parameter
shadowing a package function), C++ (function-pointer parameter shadowing a free function)
and C# (delegate parameter shadowing a sibling method).

**This is the bug Python fixed in 3.18.0, in a form the fix did not reach.** Python's
front-end invented an *id* — `py:echo` for a module that did not exist — and the fix was to
return `None` instead of guessing. These four do not invent an id. They resolve the shadowed
name against their own file-level table and land on a **node that really exists**.

**C is the exception, and it is the reason this is a port rather than a design.**
`c_extractor._calls` already computes `_bound_names(fdeclr, body, source)` and skips a callee
the function bound itself, with a comment naming the Python `py:echo` invention as its
motive. One front-end was fixed and given a corpus case (`corpus/c/function_pointers`); the
other four were never revisited.

### Why nothing caught it

| Check | What it said | Why |
|---|---|---|
| `pkg verify` dangling-edge | 0 for TypeScript, Go, C# | the target is a real node — just the wrong one |
| `pkg accuracy` corpus | precision **1.00**, all four | no fixture carries the shape. `invention.py` says so in its own docstring: the shadowing cases are *"the whole gap between Python's 0.80 corpus precision and TypeScript's 1.00"* |
| `pkg accuracy --oracle invention` | 0 | the detector re-parsed with the stdlib `ast` and looked at Python only |

Three green checks over a case none of them examined — §9 of
[STATE-OF-SPINE](STATE-OF-SPINE.md), again.

## What Phase 1 built

The detector is now stated language-neutrally, over
[`pkg/scope.py`](../../src/orchestrator/pkg/scope.py):

> a `CALLS` edge is invented when the call site at `file:line` is a **bare-identifier** call
> whose name matches the target, and that name is **bound inside the calling function**.

Both halves are load-bearing. Without the bare-call test, `this.send()` on a line that also
declares a local `send` is flagged. Without *inside the function*, every correct call to a
file-level definition is flagged, since a file-level `function send()` binds `send` too — the
difference between a declaration and a shadow is which scope holds it.

**The oracle re-implements binding analysis rather than importing C's.** An oracle that
shares its subject's code agrees with its subject's bugs. What it does *not* re-implement is
the parser: each language is walked with the same factory the front-end used, so a
disagreement is about scope, never about syntax.

**Java and SQL are excluded with a reason, not reported as clean.** Java gives variables and
methods separate namespaces (JLS §6.5.7), so `int send` cannot shadow `send()`; a shadowing
test there would manufacture findings. SQL's `CALL`/`PERFORM` fallback matches two keywords
and a parenthesis, with no lexical scope to shadow. Both carry `status: not-applicable`, and
a language with no walker carries `status: unwalked` — because a `0` that means *nothing ran*
is the thing this project keeps mistaking for health.

**The graph is untouched.** No `*_extractor.py` was modified; the module measures only.

## Results — 11 public repositories, pinned

The denominator is **bare-identifier calls**, not all `CALLS`. Only a bare call can be
reached by a shadow; `0 of 1,677 CALLS` claims a sweep three times wider than `0 of 946 bare
calls`, and only the second is what was at risk.

| Front-end | Repositories (pinned) | Bare calls | Invented | Rate |
|---|---|---|---|---|
| **cpp** | leveldb `7ee830d02b62`, fmt `e27cc20bd93a` | 9,808 | **46** | 0.47% |
| **typescript** | zod `3a49696865f3`, nest `4f783326fc99`, vue/core `e2bede96134f` | 5,376 | **1** | 0.02% |
| **go** | gin `dcaa4296d111`, cobra `adbc8813901b`, grpc-go `9d1988d75f21` | 6,435 | 0 | 0.00% |
| **csharp** | Dapper `6d48ef664acc`, Newtonsoft.Json `09bb545d7296` | 2,127 | 0 | 0.00% |
| **c** *(control)* | libuv `f87c8e4f70f2`, + the C files of leveldb and fmt | 14,856 | 0 | 0.00% |
| **python** | this repository | 15,982 | 0 | 0.00% |

Every one of the 47 was read against its source before being counted. Two examples:

- `fmt/test/format-test.cc:262` — `check_forwarding` is a local lambda declared eight lines
  above. The graph asserts a call to a free function `check_forwarding`, which fmt does not
  have.
- `vue/core packages/runtime-dom/src/components/Transition.ts:101` — `hook.forEach(h => h(...args))`.
  `h` is the arrow-function's parameter; the graph asserts a call to Vue's `h()` render
  function imported from `@vue/runtime-core`.

**46 of the 47 dangle; 1 does not.** The C++ front-end emits a name-keyed target and no node,
so `pkg verify` reports these as dangling edges — visible, in principle. In practice they do
not stand out: leveldb alone carries 4,485 dangling edges from unrelated header-only
`REFERENCES`. The single TypeScript finding lands on a real external node and is invisible to
every check that exists. The fixtures show all four front-ends can produce the invisible kind;
this sample happened to catch mostly the other.

### Two false positives, found by hand and fixed before publication

The first run reported 4 for TypeScript and 5 for Go. Reading each against its source showed
both were bugs in the **oracle**:

- **A destructuring default is not a binding.** `const { arg: slotName = createSimpleExpression(…) } = x`
  binds `slotName`. Sweeping the pattern for identifiers also bound `createSimpleExpression`,
  so every later call to that import read as shadowed — 3 of the 4 TypeScript findings.
- **Go's `:=` is not in scope on its own line.** The spec starts a short variable
  declaration's scope at the *end* of the statement, so `cmd := cmd(binaryPath, …)` calls the
  package-level `cmd`. Idiomatic Go, reported as fiction — all 5 Go findings. C and C++ read
  the same way; the rule is now applied to every language.

Both have a regression test in [`tests/pkg/test_scope.py`](../../tests/pkg/test_scope.py),
each written against the language reference rather than against what the walker did.

## What this means for Phases 2–4

**The defect is real and the fix is still worth making — but it is roughly an order of
magnitude rarer than Python's was, and the phases should be sized to that.** Python's
shadowing bug was 496 edges, **3.16% of its whole call graph**. The four front-ends here run
at **0.20%** — 47 findings across **23,746 bare calls** — and two of the four scored zero on
8,562 of those. (The denominators are not the same: 3.16% is of all `CALLS`, 0.20% is of the
bare-call population, which is the tighter and more honest one. The comparison is a size
check, not a like-for-like.) This is a correctness defect, not a flood.

Concretely:

- **C++ first, alone if only one gets done.** It holds 46 of 47, and at 0.47% it is 25× the
  next front-end. `fmt` alone accounts for 43.
- **TypeScript second.** One finding, but it is the *invisible* one — a real node, no dangling
  edge, no corpus case. It is also the front-end whose `CALLS` recall is already 0.50, so it
  has the least margin to spend on a wrong edge.
- **Go and C# are unproven at zero, not proven clean.** The construction reproduces in both;
  this sample simply does not contain it. Fix them with the others — the change is the same
  four lines C already carries — but do not claim the fix moved a number.
- **The corpus cases (Phase 2) are the durable half.** A `shadowed_calls` fixture per
  front-end costs an hour and turns "we measured 11 repos in August 2026" into a check that
  runs on every commit forever. The measurement above is a snapshot; the fixture is a gate.
- **Decision on record:** once all four refuse a shadowed name, `invention` moves to
  **`strict` at zero, per language** — recorded today as `target_gate` in
  `scoreboard.json` and in the `GATES` comment. It is not flipped yet, because a gate that
  fails on the day it lands teaches everyone to ignore it.

## Reproducing any row

```bash
git clone --depth 1 https://github.com/fmtlib/fmt && git -C fmt checkout e27cc20bd93a
orchestrator pkg accuracy --oracle invention fmt
```

The per-front-end block prints `status`, invented count, bare-call denominator and total
`CALLS`. A `status` other than `measured` means the row is not evidence of anything.
