# Design: multi-language support — Java

**Status:** Slice 1 (comprehension) implemented in 1.7.0 — `default_extractors()` registers
Java when tree-sitter is present; `understand`/grounding/`pkg extract` are multi-language.
Slice 2 (Java codegen: scaffold + JUnit runner + prompts) proposed.
**Goal:** make the orchestrator work on Java codebases — the enterprise-language
market-expansion lever. Java is a vertical slice across the pipeline; ship it in two
bounded slices so value lands early.

## Where Java stands today
- **PKG:** a `JavaExtractor` exists (`pkg/java_extractor.py`, tree-sitter behind the `java`
  extra) — modules/types/functions/fields + IMPORTS/CONTAINS/IMPLEMENTS (no CALLS by design;
  Java call resolution needs type inference). **But it's never used**: `RepoCodeExtractor`
  defaults to `[PythonExtractor()]`, so `understand` / grounding / `pkg extract` skip `.java`.
- **Profile:** `ProjectProfile` already detects Java (suffix + Spring/JUnit markers).
- **Codegen:** Python-centric — scaffold (Python `src/<pkg>`), test runner (`pytest`), layout
  detection, and prompts ("runnable Python") are all Python.

## Slice 1 — Java comprehension (this build)
Make the just-shipped comprehension wedge multi-language. **One core change:**

- `pkg/extractor.py`: add `default_extractors()` → `[PythonExtractor()]` plus `JavaExtractor()`
  **when tree-sitter is importable** (lazy `find_spec` guard, so the base install stays
  stdlib-only). `RepoCodeExtractor.__init__` uses it as the default.
- Net: `understand`, `PKGCodegenGrounder`, `load_or_extract`, and `pkg extract` now process
  `.java` automatically → the memory bank (architecture / domain-model / glossary) and codegen
  grounding work on Java repos. `tech-context.md` already reports Java/Spring.
- `conventions.md` stays Python-specific for now (Java digest is Slice 2); a pure-Java repo
  shows "no conventions sampled" — acceptable, noted.

**Tests:** a tiny Java fixture → `understand`/extract yields Java module + type nodes (skips
if the `java` extra is absent, mirroring `tests/pkg/test_java_extractor.py`).

## Slice 2 — Java codegen (follow-on)
The heavier vertical, scoped for a later release:
- **Scaffold template** — Maven/Gradle layout (`src/main/java/<pkg>/`, `src/test/java/`,
  `pom.xml`/`build.gradle`); extend `sdlc/scaffold.py` to pick a template by detected language.
- **Layout detection** — `sdlc/layout.py` recognizes `src/main/java/...` (vs Python `src/<pkg>`).
- **Test runner** — a `MavenTestRunner`/`GradleTestRunner` behind the existing `TestRunner` +
  `TestEnvironment` seams (1.4.0). The env installs/uses the JDK + build tool; auto-heal maps to
  Maven/Gradle dependency declarations instead of pip.
- **Language-aware codegen prompts** — `_IMPLEMENT_SYSTEM` etc. parameterized by language
  (Java imports, JUnit tests, package layout) instead of hardcoded "runnable Python".
- **Java conventions** — extend `sdlc/conventions.py` (or a Java digest) for `understand` +
  grounding.

## Honest limits
- No Java CALLS edges (type inference) → call-hotspots/blast-radius are Python-only; module/type
  comprehension still works.
- Slice 2 is real effort (build-tool integration, JDK in the env, JUnit) — kept separate so
  Slice 1 (comprehension) ships now.

## First step
Slice 1: `default_extractors()` + wire it as the `RepoCodeExtractor` default, then run
`orchestrator understand` on a Java repo and show a real architecture/domain map — multi-language
comprehension with one contained change.
