# Language expansion roadmap — Go + 3 (focused)

**Status:** Roadmap / prioritization. **Scope decided: Go · Rust · Kotlin · PHP** (Go shipped, PHP
comprehension + `CALLS` shipped, Rust/Kotlin still proposals). Open question 1 (below) resolved
PHP over Ruby for the 4th slot on 2026-09-08 — Ruby stays queued, not dropped. **Perl is not
in this four-language set** — it is a demand-pulled addition, admitted under this document's
first prioritisation criterion; its own track is
[perl-support-roadmap.md](perl-support-roadmap.md) (P1–P5 landed with evidence, P6 review in
progress).
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

**8 code languages + SQL shipped** (Python, Java, TypeScript, C#, C, C++, Go + SQL), and **PHP P1
+ P2 are DONE** — the 9th front-end overall, and the resolution of open question 1 below (PHP
over Ruby for the 4th slot): `PhpExtractor` — `Module`/`Type`/`Function`/`Field` +
`IMPORTS`/`CONTAINS`/`IMPLEMENTS` (traits included, D3 of
[php-support-roadmap.md](php-support-roadmap.md)) from 0, plus a `CALLS` graph (§3.2: `$this->`/
`self::`/`static::`/`parent::`/`new X()`/`X::m()`, and a same-file or `use function`-imported bare
call — the global-namespace fallback for a bare call is never guessed). `state` reports "Call
graph: available" on a PHP codebase. Codegen is deferred to a follow-on spec regardless of PHP's
toolchain being clean (D6 — comprehension-first discipline, not a toolchain problem, unlike
Ruby's).

**Go is IN PROGRESS on `feat/go-support`**: **4.1 comprehension DONE** (`GoExtractor` — `Module`/`Type`/
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
Track 4 is COMPLETE** — comprehension + greenfield + brownfield codegen all proven. **PHP P1+P2
are COMPLETE** (the 4th-slot decision, above) — comprehension and `CALLS` both ship; only codegen
remains, deferred to a follow-on spec (D6). Next: **Rust** (traits → `IMPLEMENTS`, clean cargo
toolchain), then Kotlin (reuse Java/Gradle). Ruby stays queued behind both.

## The four targets

One from each major ecosystem quadrant — enough to answer "does it support my stack?" for the
large majority, without over-investing:

| Language | Quadrant | Comprehension + CALLS | Codegen | Net-new difficulty |
|---|---|---|---|---|
| **Go** | systems/services | ~1 wk (spec'd) | ✅ ~1 wk (`go build`/`go test`) | interface satisfaction by method-set matching; package = directory |
| **Rust** | systems / AI-adjacent | ~1 wk | ✅ ~1 wk (`cargo build`/`cargo test`) | traits → `IMPLEMENTS` (`impl Trait for Type`); `::` path resolution |
| **Kotlin** | JVM / Android | ~1 wk | ✅ *reuses Java/Gradle plumbing* | JVM; Java interop; coroutines/null-safety don't change the graph |
| **PHP** | dynamic / web (Laravel, WordPress) | **DONE** (P1+P2 — comprehension and `CALLS` both ship) | defer (D6 — comprehension-first discipline, despite a clean toolchain) | traits → `IMPLEMENTS` (no vocabulary precedent); class-name resolution is *static* per file (PHP RFC), unlike Ruby's |

Ruby (dynamic / web, Rails) stays queued behind these four — comprehension + CALLS-partial is
still the plan when it's picked up, codegen still deferred (dynamic typing, not strategy).

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
  language's weak call graph is exactly where the build/test loop pays off least). **PHP defers
  too, but for a different reason** (php-support-roadmap.md D6): its toolchain (`composer install`
  → `vendor/bin/phpunit`) is hermetic and would be affordable — deferred anyway, strategically,
  so `"php"` doesn't reach `SUPPORTED_LANGUAGES` without the layout/scaffold/runner set behind it
  (the silent-Python-scaffold trap the Go track closed).

So: **all four get comprehension + CALLS; only Go/Rust/Kotlin get codegen in this pass** — PHP's
codegen is a follow-on spec, not a "never".

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
2. PHP      comprehension + CALLS DONE (P1+P2)                    (codegen deferred, D6)
3. Rust     comprehension + CALLS + codegen   (clean cargo toolchain)
4. Kotlin   comprehension + CALLS + codegen   (reuse Java/Gradle plumbing → cheap)
5. Ruby     comprehension + CALLS             (codegen deferred, queued behind the above)
```

Order rationale: Go is blueprinted and lowest-risk; PHP's comprehension is unusually cheap (§2 of
php-support-roadmap.md: deterministic per-file class-name resolution, no repo-wide table) so it
slotted in right after Go rather than waiting on Rust/Kotlin; Rust and Kotlin have clean/reused
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
- **Codegen for PHP** — comprehension and `CALLS` (P1+P2) are done; codegen is deferred to a
  follow-on spec until it proves demand (D6 of php-support-roadmap.md), despite a clean toolchain.
- **Raw language-count chasing** — a stated non-goal; depth (real call graphs) over breadth.

## Open questions
1. **Ruby vs. PHP for the 4th slot — ✅ resolved 2026-09-08, PHP.** Ruby (Rails,
   startup/AI-adjacent) vs. PHP (Laravel/WordPress, larger raw install base). PHP won: the larger
   install base, and — decisively — PHP's class-name resolution is *static* per file (the PHP
   RFC: fully-qualified / qualified / unqualified → current namespace unless `use`d), so `CALLS`
   is tractable without a repo-wide symbol table; Ruby's is not. See D0 of
   [php-support-roadmap.md](php-support-roadmap.md). Ruby stays queued, 5th in the sequence above.
2. **First codegen after Go** — Rust (cleanest toolchain) vs. Kotlin (Java-plumbing reuse)?
