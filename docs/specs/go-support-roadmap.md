# Design + Plan: adding Go to the PKG (8th language)

**Status:** In progress on branch `feat/go-support`. **Phase 4.1 (comprehension) DONE** —
Go is the 8th PKG language. **Phase 4.2 (codegen + build) machinery DONE** — layout / scaffold
/ `GoToolEnvironment` / `GoTestRunner` (`go build`→`go test`) / Go prompts / `go-conventions` /
`--language` validation, proven against real `go`. **Phase 4.3 (deeper edges) DONE** — `CALLS`
(file-local), `REFERENCES` (same-package struct fields), and the net-new **`IMPLEMENTS` by
name+arity method-set matching** via a whole-repo `finalize` hook (proven on the OTel-Go mirror:
1,188 CALLS / 25 IMPLEMENTS / 21 REFERENCES). **Phase 4.4: greenfield LIVE-PROVEN GREEN**
(LLM-generated LRU). **Phase 4.5: brownfield multi-module hardening DONE** — closed the 4.4
false-green (per-module test targeting, existing-package-clause matching, root-module lib
placement); re-ran the OTel brownfield slice → `tool/data`, `package data`, **genuinely
`go test` GREEN (independently verified)**. **Go Track 4 COMPLETE.** Adds a Go front-end to the
PKG extractor plus
Go codegen, delivered as one self-contained track of four phases — the same cadence as
C# / C / C++ ([language-support-roadmap.md](language-support-roadmap.md)) and SQL
([sql-support-roadmap.md](sql-support-roadmap.md)).

> The extractor *pattern* is identical to the other tree-sitter front-ends (a new
> `LanguageExtractor` mapping a tree-sitter tree onto the universal `facts` vocabulary —
> see [java_extractor.py](../../src/orchestrator/pkg/java_extractor.py)). Two things are
> genuinely new and are where the design effort goes: **implicit interface satisfaction**
> (Go has no `implements` keyword) and **co-located tests** (`foo_test.go` sits next to
> `foo.go` — the first language where `tests_dir == source_dir`).

---

## Where Go already is today (it is half-wired)

Go is not a greenfield addition — it is already *detected* and then silently dropped:

| Already true | File | Consequence |
|---|---|---|
| `.go` → `go` in the profiler's suffix map | [`catalog/profile.py:34`](../../src/orchestrator/catalog/profile.py) | A Go repo profiles as `languages={"go"}` |
| `go.mod` is read as a project marker | `catalog/profile.py:107-117` | Framework / test-runner detection already sees Go |
| **No `go_extractor.py`** | `src/orchestrator/pkg/` | A Go repo yields **zero graph nodes** — `understand` / `state` / grounding are empty |
| **`--language` is never validated** | [`cli.py:702-708`](../../src/orchestrator/cli.py) | `sdlc feature --language go` **silently scaffolds a Python project** today (every dispatch chain falls through to the Python branch) |
| `go` absent from `_resolve_language` | [`feature_runner.py:108-129`](../../src/orchestrator/sdlc/feature_runner.py) | `--language auto` can never resolve to Go |

Detection and extraction are deliberately independent systems; Go sits in the gap between
them. **Phase 4.1 closes the graph half; 4.2 closes the codegen half and the silent
fallthrough.**

---

## Why Go now, and why it is cheaper than C/C++

```
Go is the LOW-RISK language of the remaining set:
  · one formatter (gofmt), one vet, one build tool, one test runner — no build-system
    sprawl (the single biggest cost in the C track), no framework choice (Java/TS), no
    TFM/runtime probing (C#).
  · `go build ./...` + `go test ./...` is the whole toolchain story. Modules are
    cached and hermetic — no BLAS/X11/CUDA class of problem.
  · `.go` collides with no existing suffix (unlike `.h`, resolved in favour of C).
The cost is NOT the plumbing — it is ONE algorithm: interface satisfaction by
method-set matching (4.3). Budget accordingly.
```

**Shared infrastructure, built once and reused:**

| Built in… | Reused by Go |
|---|---|
| tree-sitter parser-construction pattern (C# track) | 4.1 verbatim |
| codegen language-branch pattern (C# track) | 4.2 verbatim |
| build-then-test runner shape (`CTestRunner`, C track) | 4.2 — `go build` → `go test` is the same two-step |
| `_clip`-ed test output as the refine signal | 4.2 verbatim |

Each phase below is independently shippable as a version bump (mirroring the Java/TS/C#
cadence: comprehension → codegen → deeper edges → live-proven). Effort is for one engineer
familiar with the PKG.

---

## Track 4 — Go (`.go`)

Grammar: `tree-sitter-go`. Package-based (not class-based), structurally typed. Import/CLI
stay `orchestrator`.

| Phase | Work | Effort | Exit |
|---|---|---|---|
| **4.1 Comprehension** ✅ | `GoExtractor` (`.go`, [`go_extractor.py`](../../src/orchestrator/pkg/go_extractor.py)): `package_clause` + `import_declaration` → `Module` / `IMPORTS`; `type_declaration` → `Type` (struct / interface / alias); `function_declaration` → `Function`; `method_declaration` (receiver) → `Function` + `CONTAINS` from the receiver `Type`; struct `field_declaration` → `Field`. Also emits **interface method specs** as `Function`s under the interface `Type` (preps the 4.3 method-set match). **`module_name()` returns the package *directory*, not the file** — every file in a dir merges into one `Module` node (a first). Ids prefixed `go:`. Lazy `_go_parser()` + `TYPE_CHECKING`-guarded `TSNode`; gated append in `default_extractors()`; `go` extra; mypy override. tree-sitter yields ERROR nodes (never raises), so malformed Go degrades gracefully. **Embedded struct fields skipped in 4.1** (no name; matter for 4.3 promoted methods); generic receivers `*T[X]`→`T`. | ~3–5 d | **DONE.** `pkg extract` on the OTel-Go mirror: **3,077 Go nodes** (2,316 fn / 421 field / 194 type / 146 module) + 5,109 edges, from 0 before; package=dir merge verified (85 modules aggregate >1 file). `state` renders full structure/god-classes/entry-points. 20 unit tests + registry biconditional; gate green. |
| **4.2 Codegen + build** ✅ (machinery) | `layout.py`: `_SOURCE_EXT["go"]="go"`, `derive_go_module` / `detect_go_layout` (parse `module` from `go.mod`), `_resolve_go_layout` — **greenfield = a single package at the module root** (simplest thing `go build ./...`/`go test ./...` accept), **`tests_dir == source_dir`** (co-located `_test.go`). `scaffold.py`: `_go_files` → `go.mod` + a stub `<pkg>.go` (package clause) + README + `.gitignore` (empty package is a green `go test`). `testenv.py`: `GoToolEnvironment` (`ensure` = best-effort `go mod download`), `go_toolchain_available()`. `testrunner.py`: `GoTestRunner` — `go build ./...` then `go test ./... -v`, early-return on first non-zero. Toolchain guard in `feature_runner.py`. Go prompts in all three registries + a `layout.language == "go"` guidance block. `go-conventions` Capability + Skill. **Closed the fallthrough:** `--language` is validated against `SUPPORTED_LANGUAGES` at the CLI (unknown → exit 2, no more silent Python scaffold). | ~2–4 d | **DONE (machinery + real-`go` integration).** Scaffold → real `go build`/`go test` green **and** red proven (`test_go_integration.py`, 4 cases, run against Go 1.23); layout/scaffold/env/runner/validation unit-tested. **Remaining:** a full LLM-driven greenfield `sdlc feature --language go` run (needs model creds) — rolls into 4.4 live-proof. |
| **4.3 Deeper edges** ✅ | `CALLS` — resolved **file-local**, precision-first: unqualified `Foo()` to a same-file package func, and `r.M()` to a method of the receiver's own type (two-pass per file). Cross-file/cross-package + interface-value calls are **not** emitted (need a package symbol table / type inference — documented, not guessed). **`IMPLEMENTS` by method-set matching** (the net-new algorithm) — done in a whole-repo `finalize` hook (`RepoCodeExtractor` now runs `finalize` on any extractor that defines it): capture each interface's **(name, arity)** signature set (embedded interfaces expanded) + each concrete type's method sig set (value **and** pointer receivers, merged across files); emit `IMPLEMENTS` where concrete ⊇ interface. **Arity** guards the cross-package look-alike false positive (a name-only match wrongly linked gRPC `server`→`GreeterClient`; arity removed it). `REFERENCES` for a struct field whose type is a same-package named type (`next *Node`→`Node`); qualified/slice/map skipped. | ~4–6 d | **DONE.** On the OTel-Go mirror: CALLS 1,188 · IMPLEMENTS 25 (0 dangling; spot-checked correct) · REFERENCES 21; `state` now reports "Call graph: available" + hotspots, so blast-radius/design/rca/regression work on Go. Unit-tested incl. the arity precision guard. **Known limits (documented):** cross-file same-package CALLS + promoted methods from **embedded structs** not yet resolved. |
| **4.4 Live-proven** | Greenfield ✅; brownfield ⚠️ (multi-module gap found). **Greenfield GREEN + verified** (2026-07-21, `claude-sonnet-4-6`, `--safe`): `sdlc feature --language go` from a spec → scaffold → `go-conventions` skill selected → LLM wrote an idiomatic LRU (`container/list`+map) + table-driven tests → **`go build`/`go test` GREEN first pass (no refine)**; re-verified independently (5 subtests PASS). **Hypothesis CONFIRMED:** the environmental wall does NOT exist for Go — the OTel-Go mirror's root module `go build ./...`+`go test ./...` is hermetic and **green in ~22s**. **Brownfield exposed a real gap → a FALSE GREEN:** the run reported PASSED, but (1) `detect_go_layout` placed the code in an arbitrary first-walk package dir (`demo/app/grpc/server`); (2) the codegen wrote `package otelc` (module's last element) into a dir whose existing files are `package main` — a package conflict; (3) `GoTestRunner`'s root-level `go test ./...` doesn't reach that package (the repo has 67 sub-modules; `go list ./...` returns 0 demo pkgs), so the broken code was **never compiled/tested**. Grounding worked (1,453 chars of PKG context). | ~2–3 d | **Greenfield: DONE (verified GREEN).** Brownfield on a large multi-module repo: **not reliable yet** — see 4.5. |
| **4.5 Brownfield multi-module hardening** ✅ | Closed the 4.4 false-green: (a) `GoTestRunner` discovers the module(s) that own the **changed** `.go` files (`git status` → nearest `go.mod`) and runs `go build ./...`+`go test ./...` **from each** — so code generated into a sub-module can't pass untested; (b) `detect_go_layout` returns the placement dir's **existing `package` clause** (not the module's last element), and the codegen guidance emits `package <that>`; (c) `_pick_go_source_dir` prefers a **library** package (skips `package main` + demo/example/testdata/vendor) in the repo-**root** module. | ~2–4 d | **DONE.** Re-ran the OTel brownfield slice live: placed in `tool/data` (root-module lib, `package data`), grounded (1,453 chars), **genuinely `go test ./tool/data/` GREEN — independently verified** (the earlier false-green code landed in a nested-module `main` with the wrong package). Unit-tested: root-vs-submodule test targeting, root-module lib placement over main/demo, existing-package-clause detection. |

### Deferred to a follow-on (explicitly out of Track 4)
- **`Endpoint` / `EXPOSES` framework edges** (`net/http` `HandleFunc`, gin/echo/chi route registration) — the C# analogue (ASP.NET) was its own phase. Fold into 4.3 only if the 4.4 target repo makes it free; otherwise ship as 4.5.
- **`Entity` / data-layer edges** (GORM struct tags, `sqlc`, `database/sql`) — overlaps the SQL track's `Entity`/`REFERENCES` model; sequence *after* Go lands so it reuses that vocabulary.
- **Goroutines / channels** — no vocabulary in the closed `EdgeKind` enum, and no consumer. Do not invent one.

---

## Per-language fact mapping (reference)

| Construct | Go | Nearest precedent / how it differs |
|---|---|---|
| Module unit | **package (directory)** | First language where module ≠ file — C#/Java key on file, C on translation unit |
| Imports | `import` decl → `IMPORTS` | Same as Java/C# |
| `Type` | `struct` / `interface` / alias | Like C# minus classes/records |
| `Function` | free funcs **+** receiver methods | Like C++ (free + member); methods attach via `CONTAINS` from the receiver `Type` |
| `IMPLEMENTS` | **implicit — method-set matching** | **Novel.** Every prior language reads it off a base clause / `:` / `extends`. This is the one real algorithm in the track |
| `Field` | struct fields (incl. embedded) | Like C/C++ struct members; embedded fields also promote methods (feeds `IMPLEMENTS`) |
| Framework edges | `net/http` / gin / echo / chi (deferred) | The C# (ASP.NET) shape |
| Build/test | `go build ./...` → `go test ./...` | Two-step like C's CMake→ctest, but no build-system detection |

---

## Validation target (live-proven, phase 4.4)

**`ssmith-synaptixs/opentelemetry`** — a private mirror of the OpenTelemetry-Go tree
(**~413 `.go` files across ~67 `go.mod` modules**, default branch `main`). Chosen because it
is exactly what 4.4 wants to exercise:
- **Interface-rich** (`Tracer`, `Meter`, `SpanExporter`, `TracerProvider`, …) — the stress test
  for the net-new `IMPLEMENTS`-by-method-set-matching (4.3), including embedded interfaces and
  pointer-vs-value receivers.
- **Multi-module** (~67 `go.mod`) — verifies package-as-directory `Module` merging at scale and
  cross-module `IMPORTS`.
- **Idiomatic + hermetic** `go build ./...` / `go test ./...` — tests the 4.4 hypothesis that Go
  may be the first language where the full in-pipeline brownfield build succeeds.

Used **ephemerally** per the house rule: shallow-cloned to a temp dir, read-only, deleted after;
the repo name lives here in `docs/` only, **never** in `src/` or `tests/` (commit `e54ee4c`).

**Baseline confirmed (2026-07-21):** the profiler detects `go`, but `pkg extract` currently
yields **0 Go nodes** on this repo (413 `.go` files → nothing) — the precise "detected → empty
graph" gap 4.1 closes.

## Packaging changes
- `pyproject.toml`: new extra `go = ["tree-sitter>=0.21", "tree-sitter-go>=0.21"]`; add
  `tree_sitter_go` to the mypy `ignore_missing_imports` list (`pyproject.toml:173`) or CI
  type-check fails on the missing import.
- `default_extractors()`: one gated append (`find_spec` **before** the import, extractor
  import function-local) so the base install stays stdlib-only.
- Tests: `tests/pkg/test_go_extractor.py` (module-level `pytest.importorskip("tree_sitter_go")`,
  mirroring `test_java_extractor.py`); **two** additions to `test_default_extractors.py` — the
  biconditional registry test (`assert ("go" in langs) == have_go`) and an end-to-end test.
  Codegen side: `test_scaffold_go_*` (+ a mandatory idempotency test), `test_go_toolchain_available`
  (monkeypatched `shutil.which`), a `FeatureRunError` hint test, and optionally
  `tests/sdlc/test_go_integration.py` gated by `pytestmark = pytest.mark.skipif(not go_toolchain_available(), …)`.

## Docs to update (per the C++ precedent, commit `a00eab5`)
`README.md`, `FEATURES.md` (capability row), `USER_GUIDE.md` (**both** passages — the extras
list at `:64-72` and the "Multi-language" blockquote at `:171-190+`: language name, `[go]`
extra, `.go` suffix, and a Go sentence if it earns one), `KNOWLEDGE_GRAPH.md` (language list +
fact mapping), and this roadmap's Status line. Gate: `ruff` + `mypy src tests` + full suite green.
Validation-repo names may appear in `docs/` but **never** in `src/` or `tests/` (commit `e54ee4c`).

## Risks / gotchas
- **Implicit interfaces are the whole risk.** Method-set matching is O(types × interfaces) and
  needs care with pointer-vs-value receivers (`*T` has `T`'s method set, not vice-versa) and
  promoted methods from embedded structs. Get this wrong and `IMPLEMENTS` is confidently wrong,
  which is worse than absent. Scope to in-repo, name+arity keyed, and **skip it entirely in 4.1** —
  ship the graph without `IMPLEMENTS` first.
- **Module ≠ file.** Every existing front-end assumes one module per file. Returning a directory
  from `module_name()` merges many files into one `Module` node — verify `FactBatch` merge
  dedupes node ids cleanly before building on it.
- **Co-located tests** break the `source_dir`/`tests_dir` assumption baked into layout + scaffold.
  Check `derive_*`/`detect_*` consumers rather than assuming `tests_dir` is distinct.
- **Interface-typed call sites don't resolve** without type inference — same wall C++ hit with
  member calls on other objects. Document, don't chase.
- **Generics** (1.18+): emit the `Type`/`Function`, don't resolve instantiations — the C++
  templates precedent.
- **Vendored deps** (`vendor/`) and generated code (`*.pb.go`) will bloat the graph. Consider a
  skip list; `open5gs` at 8.6k files is the scale precedent.

## Two pre-existing gaps Go inherits (scope separately — Go does not cause them)
1. **Preflight is Python-only.** [`preflight.py:34-38`](../../src/orchestrator/sdlc/preflight.py)
   is a module-level `_CHECKS` constant (ruff/ruff-format/mypy) and `SubprocessPreflightRunner`
   takes no language and skips-as-pass when there's no `pyproject.toml` — so Java/TS/C#/C/C++
   already get a silent `passed=True`. Go would too. **But Go is the best forcing function to fix
   it**: `gofmt -l` + `go vet` are universal, zero-config, and always present with the toolchain —
   the cheapest possible real quality gate. Fixing it means a `make_preflight_runner(language)`
   dispatcher threaded from `worker.py:213` → `activities.py:610`. ~2–3 d, and it lifts all six
   other languages at once. Recommend doing it **with** 4.2.
2. **Grounding fence is hardcoded `python`.** `grounding.py:75-79` labels every symbol snippet
   ` ```python `. One-line fix (map from the language / provenance suffix); affects every
   non-Python language today.

## Rough totals
```
Go    ~11–18 days  (4.1 comprehension → 4.2 codegen → 4.3 edges → 4.4 live-proven)
      of which 4.3's method-set IMPLEMENTS is the only net-new algorithm (~4–6 d).
      4.2 is the cheapest codegen phase of any language so far (~2–4 d) — one build
      tool, one test runner, no build-system detection.
+2–3 d  optional: the make_preflight_runner(language) dispatcher (fixes 7 languages,
        not just Go — gofmt/go vet is the natural first real non-Python gate).
```
Go is the lowest-risk remaining language on plumbing and the highest-risk on exactly one
algorithm. Front-load 4.1 without `IMPLEMENTS` to get the graph shipping, then spend the
design budget on 4.3.
