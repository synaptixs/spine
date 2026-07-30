# Language expansion roadmap — Go + 3 (focused)

**Status:** Roadmap / prioritization. **Scope decided: Go · Rust · Kotlin · Ruby** (Go track
already spec'd; the other three are proposals).
**Date:** 2026-07-21 · spine v3.6.0
**Why:** the PKG is the substrate every grounded capability stands on — `understand`, `state`,
`design`, `investigate`, `localize`, `rca`, `regression`, and grounded codegen all consume it.
Adding a language doesn't add *one* feature; it makes **every feature work on more codebases**,
and it's the clearest gap vs. comparable code-graph tools. We're deliberately **not** chasing
raw language count — four well-chosen languages that carry a real call graph beat forty shallow
front-ends.

> Companion specs: C#/C/C++ in [language-support-roadmap.md](language-support-roadmap.md)
> (shipped), Go in [go-support-roadmap.md](go-support-roadmap.md), SQL in
> [sql-support-roadmap.md](sql-support-roadmap.md). This doc is the master prioritization.

---

## Where we are today

**7 code languages + SQL shipped** (Python, Java, TypeScript, C#, C, C++ + SQL). **Go is IN
PROGRESS on `feat/go-support`**: **4.1 comprehension DONE** (`GoExtractor` — `Module`/`Type`/
`Function`/`Field` + `IMPORTS`/`CONTAINS`, 3,077 nodes from 0 on the OTel-Go mirror) and **4.2
codegen machinery DONE** (Go layout / scaffold / `GoToolEnvironment` / `GoTestRunner`
(`go build`→`go test`) / Go prompts / `go-conventions`, plus the one-time **`--language`
validation** cross-cutting fix — unknown values no longer silently scaffold Python), proven
against real `go`. So `understand`/`state`/`design`/`investigate`/`localize`/`rca`/`regression`
+ grounding work on Go, and greenfield Go codegen runs end-to-end. **4.3 deeper edges DONE** —
`CALLS` (file-local), `REFERENCES` (same-package struct fields), and the net-new **`IMPLEMENTS`
by name+arity method-set matching** (whole-repo `finalize` hook; arity guards cross-package
false positives), so Go now carries a call graph + interface-satisfaction edges (blast-radius /
design / rca / regression all light up). **4.4 greenfield + 4.5 brownfield LIVE-PROVEN GREEN**
(LLM-generated, `go test` green, both independently verified — 4.5 hardened multi-module
placement/package-clause/per-module testing after a 4.4 false-green on the OTel mirror). The
"Go build is hermetic, no environmental wall" hypothesis held (real repo green in ~22s). **Go
Track 4 is COMPLETE** — comprehension + greenfield + brownfield codegen all proven. Next: **Rust**
(traits → `IMPLEMENTS`, clean cargo toolchain), then Kotlin (reuse Java/Gradle) → Ruby.

## The four targets

One from each major ecosystem quadrant — enough to answer "does it support my stack?" for the
large majority, without over-investing:

| Language | Quadrant | Comprehension + CALLS | Codegen | Net-new difficulty |
|---|---|---|---|---|
| **Go** | systems/services | ~1 wk (spec'd) | ✅ ~1 wk (`go build`/`go test`) | interface satisfaction by method-set matching; package = directory |
| **Rust** | systems / AI-adjacent | ~1 wk | ✅ ~1 wk (`cargo build`/`cargo test`) | traits → `IMPLEMENTS` (`impl Trait for Type`); `::` path resolution |
| **Kotlin** | JVM / Android | ~1 wk | ✅ *reuses Java/Gradle plumbing* | JVM; Java interop; coroutines/null-safety don't change the graph |
| **Ruby** | dynamic / web (Rails) | ~1 wk (CALLS partial) | defer | fully dynamic — CALLS is best-effort (no static types); classes/modules/methods are clean |

**~7 engineer-weeks total** (4 comprehension + 3 codegen), shippable incrementally — each
language is its own version bump.

---

## Strategy: comprehension first

Comprehension and codegen have very different cost/payoff, so we front-load comprehension:

- **Comprehension tier** (`LanguageExtractor` + CALLS): cheap, additive (~1 wk/lang). The moment
  it lands, that ecosystem gets `understand`, `state`, `design`, `investigate`, `localize`,
  `rca`, `regression`, and codegen *grounding* — **the whole desirability win**, no toolchain.
- **Codegen tier** (layout/scaffold/testenv/testrunner/build): language-specific, ~1 wk/lang.
  Worth it only where the toolchain is clean — **Go / Rust / Kotlin, not Ruby** (a dynamic
  language's weak call graph is exactly where the build/test loop pays off least).

So: **all four get comprehension + CALLS; only Go/Rust/Kotlin get codegen.**

### The fixed recipe (per language)
Grounded in the shipped front-ends — a comprehension front-end is one module:
1. `LanguageExtractor` over a tree-sitter grammar → the universal `facts` vocabulary
   (`Module`/`Type`/`Function`/`Field` + `IMPORTS`/`CONTAINS`/`IMPLEMENTS`), lazy parser
   factory, `[lang]` extra, `TYPE_CHECKING`-guarded import, mypy override, registered in
   `default_extractors()`. Template: `java_extractor.py` / `typescript_extractor.py`.
2. **CALLS** via the two-pass pattern shipped this cycle (collect bodies → resolve callees,
   precision-first: sibling/`self`, local, and imported calls; skip what needs type inference).
3. **Detection wiring** — the suffix in `catalog/profile.py` + `_resolve_language`
   (`feature_runner.py`), so a repo profiles and resolves correctly.
4. **One-time cross-cutting fix (do with Go):** validate `--language` against the known set.
   Today an unsupported value **silently scaffolds Python** — a real trap the Go spec flags.
   Fixing it once benefits every language after.

---

## Sequence

```
1. Go       comprehension + CALLS + codegen   (spec'd; do first)  + the --language validation fix
2. Rust     comprehension + CALLS + codegen   (clean cargo toolchain)
3. Kotlin   comprehension + CALLS + codegen   (reuse Java/Gradle plumbing → cheap)
4. Ruby     comprehension + CALLS             (codegen deferred)
```

Order rationale: Go is blueprinted and lowest-risk; Rust and Kotlin have clean/reused
toolchains so their codegen is affordable; Ruby is comprehension-only, so it's the smallest
lift and can slot in whenever. Each comprehension milestone is release-worthy on its own.

## Prioritization criteria (how to reorder)
1. **Demand** — a real target repo/customer asking pulls a language forward.
2. **Toolchain cost** — single hermetic build tool (Go/Rust/cargo, Kotlin/Gradle) → codegen is
   cheap; dynamic → comprehension-only.
3. **CALLS tractability** — statically-typed → good call graph; dynamic (Ruby) → partial, and
   **bound honestly** (mark unresolved calls, per the invariant).

## Explicitly out of scope (for now)
- **Wave-3 breadth** (Swift, Scala, Dart, Elixir) — revisit only if a specific deal needs it.
- **Non-code ingestion** (Markdown/PDF → graph) — a separate modality track, not part of this
  language push.
- **Codegen for Ruby** — deferred until comprehension proves demand.
- **Raw language-count chasing** — a stated non-goal; depth (real call graphs) over breadth.

## Open questions
1. **Ruby vs. PHP for the 4th slot** — Ruby (Rails, startup/AI-adjacent) vs. PHP (Laravel/
   WordPress, larger raw install base). Current pick: Ruby.
2. **First codegen after Go** — Rust (cleanest toolchain) vs. Kotlin (Java-plumbing reuse)?
