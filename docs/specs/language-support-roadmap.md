# Design + Plan: adding C#, C, and C++ to the PKG (sequential, per-language)

**Status:** Design / blueprint (not started). Adds three new language front-ends to the
PKG extractor, **delivered one language at a time** in the order **C# → C → C++**, each a
self-contained track of phases. Sequencing is deliberate: each track builds shared
infrastructure the next track reuses.

> The extractor *pattern* is identical for all three (a new `LanguageExtractor` mapping a
> tree-sitter tree onto the universal `facts` vocabulary — see
> [java_extractor.py](../../src/orchestrator/pkg/java_extractor.py)). Only the language
> model and (for C/C++) the build story differ.

---

## Why this order

```
1. C#   → class-based, single build tool (dotnet). Lowest risk. Establishes the
          codegen language-branch pattern reused by C and C++.
2. C    → introduces the NEW model (translation units, #include graph, header/source
          split, build-system detection). Simpler than C++ (no classes/namespaces).
3. C++  → a superset: REUSES C's include graph + header-merge + CMake plumbing, adds
          classes, namespaces, multiple inheritance, templates.
```

**Shared infrastructure, built once and reused (the payoff of sequencing):**

| Built in… | Reused by… |
|---|---|
| tree-sitter parser-construction pattern (C# track) | C, C++ |
| codegen language-branch pattern (C# track) | C, C++ |
| `#include` graph + header/source signature-merge (C track) | C++ |
| build-system detection + CMake/ctest plumbing (C track) | C++ |

Each phase below is independently shippable as a version bump (mirroring the Java/TS
rollout: comprehension → codegen → deeper edges → live-proven). Effort is for one
engineer familiar with the PKG.

---

## Track 1 — C# (.cs)  ·  *do first, lowest risk*

Grammar: `tree-sitter-c-sharp`. Class-based like Java — the original blueprint applies
almost verbatim. Import/CLI stay `orchestrator`.

**Status (2.0.1): 1.1–1.4 DONE.** C# ships as the 4th full codegen language (the 2.0.0
milestone); 1.4 live-proven on .NET 10 (2.0.1). Greenfield: spec → scaffold → LLM C# +
xUnit → `dotnet test` GREEN, end to end. Real ASP.NET (`synaptixs/NN`): graph extraction
(287 endpoints/EXPOSES, 775 CALLS over 561 files) + the full brownfield codegen pipeline
(clone → PKG-grounding → generate → test). Live-proving drove four fixes: method-level
`[Route]` endpoints, SDK-matched target framework, build-artifact (`bin/obj`) exclusion,
and nested-`.sln` discovery. **Caveats (environmental, not orchestrator):** a green
brownfield `dotnet test` needs the repo to be `dotnet`-CLI-buildable cross-platform — NN
is a Visual-Studio/Windows solution (SSDT `.sqlproj`, Windows `System.Drawing`, net7
runtime) so it can't build on macOS; and EF Core entity/`REFERENCES` edges await an EF
Core repo (NN is raw ADO.NET).

| Phase | Work | Effort | Exit |
|---|---|---|---|
| **1.1 Comprehension** ✅ | `CSharpExtractor` (`.cs`): class/interface/struct/enum/record → `Type`; `using` → `IMPORTS`; base list → `IMPLEMENTS`; methods/ctors → `Function`; properties/fields → `Field`; namespace = id context. Register in `default_extractors()`; `csharp` extra; mypy override; tests. | ~2–4 d | `understand`/`pkg extract` correct on a real C# repo; CI green |
| **1.2 Codegen + test** ✅ | `language == "csharp"` branches in codegen/layout/scaffold/testenv/testrunner; greenfield scaffold solution + `.csproj` + xUnit (`DotnetToolEnvironment`/`DotnetTestRunner`); `dotnet test`. .NET SDK provisioning is a runner prerequisite (preflighted with a clear error). | ~3–5 d | Brownfield build → `dotnet test` green + diff; greenfield scaffolds buildable project |
| **1.3 Framework edges** ✅ | ASP.NET Core `[HttpGet]`/`[Route]` controllers + Minimal-API `app.Map*` → `Endpoint`+`EXPOSES`; EF Core `DbSet<T>`/`[Table]`/nav props → `Entity`+`REFERENCES`; conservative intra-type `CALLS`. | ~3–5 d | Endpoint/entity/data edges on a real ASP.NET+EF repo |
| **1.4 Live-proven** ✅ | End-to-end on real C#: greenfield green `dotnet test`; real ASP.NET (`synaptixs/NN`) extraction + brownfield pipeline. | ~1–2 d | Green greenfield build + real-repo grounding/generation |

**Establishes for later tracks:** the parser-construction helper and the codegen
language-branch wiring.

> **1.4 notes:** C# codegen requires the .NET SDK (`dotnet`) on the runner — the path
> preflights and fails fast when it's absent, and the scaffold targets the *installed*
> SDK (`net{major}.0`) so the project runs, not just builds. Greenfield is fully
> green end to end. A green **brownfield** `dotnet test` additionally requires the
> target repo to be `dotnet`-CLI-buildable cross-platform; Visual-Studio/Windows
> solutions (SSDT `.sqlproj`, `System.Drawing`, Windows-only TFMs — e.g. `synaptixs/NN`)
> can't build under the CLI on macOS/Linux, independent of the orchestrator. A literal
> `--live` PR awaits a CLI-buildable target (or a fork/sandbox base); EF Core
> entity/`REFERENCES` edges await an EF Core repo.

---

## Track 2 — C (.c, .h)  ·  *do second, new model but simplest language*

Grammar: `tree-sitter-c`. Procedural — **no classes, namespaces, inheritance, or
templates**. This track introduces the concepts C++ will reuse.

**Status (2.2.0): 2.1–2.4 DONE.** C ships as the 5th language — translation-unit
modules, the `#include` graph, header/source merge, CALLS + REFERENCES, current-state
diagrams, and codegen for **two build systems**: CMake (greenfield scaffold) and **Meson**
(brownfield). Live-proven end to end (safe mode): greenfield `cmake → ctest` GREEN, and a
brownfield **Meson** repo through the full loop (ground → implement → `meson` build fail →
refine → `meson test` GREEN). Comprehension/current-state proven on Open5GS (real Meson, 8.6k
files). A literal `--live` PR is a target-repo formality (open5gs needs heavy system deps).

| Phase | Work | Effort | Exit |
|---|---|---|---|
| **2.1 Comprehension** ✅ | `CExtractor` (`.c`, `.h`): `struct`/`union`/`enum`/`typedef` → `Type`; **free functions** → `Function` (`CONTAINS` from module); fields/globals → `Field`; **`#include` → `IMPORTS` with in-repo resolution** (existing local header → grounded; `<system>`/missing → external); **header/source signature-merge** (a `.h` prototype is an external placeholder upgraded by the `.c` definition, so the def's provenance wins, order-independent; `static` → file-scoped id). Module = the translation unit; include guards (`preproc_ifdef`) flattened. | ~4–6 d | `pkg extract` correct on real C; decl↔def merge works; CI green |
| **2.2 Codegen + build** ✅ | `c` branches in layout/scaffold/testenv/testrunner/codegen; build detection (CMake / Meson / Make); greenfield scaffold globs `src/*.c` into a library and each `tests/*.c` into a ctest executable (assert-based `main`, offline); `CTestRunner` (`cmake -S/-B` → build → `ctest`) **and `MesonTestRunner`** (`meson setup` → `meson test`) — selected by the repo's build tool; build-system-aware codegen prompts. | ~5–8 d | Greenfield `cmake`→`ctest` + brownfield `meson` GREEN (live-proven) |
| **2.3 Deeper edges** ✅ | `CALLS` from `call_expression` (name-keyed → resolves cross-file; local statics keep their file-scoped id); `REFERENCES` (a struct/union member whose type is another struct — the C data edge). | ~3–4 d | Blast-radius returns call-graph + data edges |
| **2.4 Live-proven** ✅ | End-to-end (safe) on both build systems: greenfield CMake (`ctest` GREEN) and a brownfield **Meson** repo (full ground→implement→build→refine→`meson test` GREEN). Comprehension/current-state proven on Open5GS (real Meson, 8.6k files). A literal `--live` PR needs a target C repo with light deps (Open5GS needs a heavy system-dep set). | ~2 d | Green end-to-end on real C build systems |

**Establishes for C++:** the `#include` graph, the header/source merge, and the
CMake/`ctest` build+test plumbing.

> **`.h` ownership:** the C track owns `.c` and `.h`. When C++ ships (Track 3), `.h` can
> be re-routed by a project heuristic, or left with C (most `.h` are C-compatible
> declarations). Note it; don't over-engineer it now.

---

## Track 3 — C++ (.cpp, .cc, .cxx, .hpp, .hh, .hxx)  ·  *do third, builds on C*

Grammar: `tree-sitter-cpp`. A superset — **reuses Track 2's include graph, header-merge,
and CMake plumbing** and adds the OO layer.

**Status (2.4.0): Track 3 COMPLETE (3.1–3.4).** C++ ships as the 6th language by reusing the
C front-end's helpers (include resolution, header/source merge, declarator/call/field
helpers) + the CMake/Meson runners, adding classes/namespaces/inheritance/members/templates.
Greenfield codegen live-proven (`sdlc feature --language cpp` → `cmake` → `ctest` GREEN);
**3.4 live-proven on a real CMake C++ repo** (private mirror of davisking/dlib): comprehension
at scale (10.7k nodes / 68k edges) + a grounded brownfield codegen slice → standalone `ctest`
GREEN → **reviewed PR** (synaptixs/dlib#1).

| Phase | Work | Effort | Exit |
|---|---|---|---|
| **3.1 Comprehension** ✅ | `CppExtractor` (`.cpp/.cc/.cxx/.hpp/.hh/.hxx`): C track's facts **plus** `class_specifier` → `Type` (namespace-qualified); `base_class_clause` → `IMPLEMENTS` (multiple); `namespace_definition` = id context; member functions (in-class decl + out-of-line `Class::method` definition merge by qualified name); templates emit the `Type`/`Function` (params excluded from REFERENCES); reuses the C include/header-merge helpers. `.h` stays with C (documented). | ~3–5 d | Correct graph (classes, namespaces, inheritance); CI green ✅ |
| **3.2 Codegen + build** ✅ | Reuses Track 2's CMake/`ctest` (+ Meson) plumbing — `cpp` shares `CToolEnvironment`/`CTestRunner`/`MesonTestRunner`; greenfield scaffold globs `src/*.cpp` into a CXX library + `tests/*.cpp` ctest executables (assert-based, offline); C++ codegen prompts (header/source, `#pragma once`/guards, RAII, C++17). `cpp_toolchain_available` (cmake + a C++ compiler). | ~3–5 d | Greenfield `cmake`→`ctest` GREEN (live-proven) |
| **3.3 Deeper edges** ✅ | `CALLS` — free-function + `Namespace::func` calls (name-keyed; overloads collapse) and an unqualified / `this->` call to a sibling method (member calls on other objects need type inference → not resolved); `REFERENCES` for a data member whose type is another class/struct. | ~3–5 d | Call-graph + data edges ✅ |
| **3.4 Live-proven** ✅ | Comprehension at scale on a real CMake C++ repo (private mirror of davisking/dlib): 10.7k grounded nodes / 68k edges, namespace-qualified types + templates + inheritance. Grounded brownfield codegen slice (PKG context → idiomatic header-only utility + test) builds + tests **standalone** (`cmake`→`ctest` GREEN) → **reviewed PR** (synaptixs/dlib#1). **Limit:** the repo's *full* in-pipeline build is environmentally heavy (BLAS/X11/CUDA + hundreds of test binaries exceed the runner's per-step budget), so a heavy-brownfield slice is verified in a self-contained subproject — same wall C# (NN) / C (open5gs) hit; future work = scope the native build to the touched subtree. | ~2–3 d | A real PR on a real C++ repo ✅ |

---

## Per-language fact mapping (reference)

| Construct | C# | C | C++ |
|---|---|---|---|
| Module unit | file (+ namespace) | translation unit (file) | translation unit (file) |
| Imports | `using` | `#include` | `#include` |
| `Type` | class/interface/struct/enum/record | struct/union/enum | + class |
| `Function` | methods (in types) | **free** + n/a | free + member |
| `IMPLEMENTS` | base list (single + ifaces) | — | base clause (**multiple**) |
| `Field` | properties / fields | struct members | struct/class members |
| Framework edges | ASP.NET / EF (rich) | — | — |
| Build/test | `dotnet test` | CMake/`ctest` | CMake/`ctest` |

## Packaging changes (per track)
- `pyproject.toml` extras: `csharp = [… tree-sitter-c-sharp]`, `c = [… tree-sitter-c]`,
  `cpp = [… tree-sitter-cpp]`; add each grammar module to the mypy `ignore_missing_imports` list.
- `default_extractors()`: a gated append per language (importable grammar → register), so
  the base install stays stdlib-only.
- Tests: `tests/pkg/test_{csharp,c,cpp}_extractor.py`, each mirroring `test_java_extractor.py`.

## Risks / gotchas
- **C#:** properties are dual-natured — map to `Field` first (optionally also `Function`
  get/set); partial classes → key ids on the namespace-qualified name so they merge.
- **C / C++:** preprocessor opacity (parse is pre-expansion; heavy macros → partial
  facts — document, don't run cpp); templates (emit, don't resolve); include resolution
  grounds only in-repo headers; **build-system sprawl is the biggest effort — scope to
  CMake first**; get signature-keyed ids right early or decl/def double-count.

## Rough totals
```
C#    ~9–16 days   (comprehension → codegen → framework → live)
C     ~14–20 days  (includes building the CMake/ctest + include-graph infra)
C++   ~11–18 days  (reuses C's infra)
```
Front-loading C# de-risks the codegen branch pattern; front-loading C (before C++) builds
the C-family infrastructure once.
</content>
