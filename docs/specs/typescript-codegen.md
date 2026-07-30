# Design: multi-language support — TypeScript

**Status:** Slice 1 (comprehension) implemented in 1.11.0 — `pkg/typescript_extractor.py`
(tree-sitter, `typescript` extra) + `default_extractors()` registers TS when the grammar is
present; `understand`/grounding/`pkg extract` now process `.ts`/`.tsx`. **Slice 2a + 2b
implemented in 1.12.0**: 2a — TS `TargetLayout` (`src/` + co-located `*.test.ts`, npm/yarn/pnpm
from lockfile), `detect_typescript_layout`, `derive_npm_package`, a **Vitest** scaffold template
(`package.json` + strict `tsconfig.json`), and `--language typescript` threaded through
`feature_runner._resolve_language`; 2b — `NodeTestRunner` (`<pm> test`), `NodeToolEnvironment`
(Node+pm preflight, `<pm> install`, auto-heal disabled), `node_toolchain_available`, and
`make_test_runner`/`make_test_environment` TS branches. **Live-verified**: a real
`npm install` + `vitest run` integration test (`test_typescript_integration.py`, skips without
the toolchain) passes for correct TS and fails for wrong TS; CI gains a `setup-node` step.
**2c implemented in 1.13.0**: language-parameterized codegen prompts (`_IMPLEMENT_SYSTEM_TS`/
`_TESTS_SYSTEM_TS`/`_REFINE_SYSTEM_TS` — strict TS, ESM with `.js` NodeNext imports, co-located
Vitest tests, deps in `package.json`), a TS `_layout_block`, the `typescript-conventions` skill
prompt, and the `_is_java` boolean refactored to a `language → prompt` map (`_IMPLEMENT_SYSTEMS`
etc.) so a fourth language is a new column, not another branch.
**2d (1.14.0) DONE & LIVE-PROVEN**: a verified `sdlc feature --language typescript --safe`
run — from a `spec.md`, the LLM (claude-sonnet-4-6) generated `money.ts` +
co-located `money.test.ts` (strict TS, ESM `export`, the `.js` NodeNext import, Vitest), a real
`npm install` + `vitest run` passed **first try, zero refine cycles** (26/26 assertions), and the
TS diff committed locally. The integration test + CI `setup-node` landed in 2b. **TypeScript
codegen is complete** — comprehension + live codegen, matching Python and Java.
TS is the next language after Python + Java (both shipped: comprehension 1.7.0, Java codegen
complete 1.10.0). TS is the highest dev-reach language and additionally unlocks a UI-component
slice (`components.md`) via a PKG UI extractor down the line.
**Goal:** make `sdlc understand` and `sdlc feature` work on TypeScript codebases —
comprehension first (cheap, lands now), then full codegen (scaffold → test runner → prompts
→ live). This mirrors the Java roadmap exactly; every language seam was already generalized
by the Java work, so TS slots into the same conditionals.

## Why TypeScript, why now
- **Reach:** TS/JS is the largest developer population; brownfield TS repos are everywhere.
- **Leverage of prior work:** the Java vertical already split `language` out of every
  Python-shaped surface (layout, scaffold, test runner, test env, codegen prompts, profile,
  feature_runner, CLI). Adding TS is "implement the third branch," not "re-architect."
- **Partly pre-wired:** `catalog/profile.py` `_detect_languages()` **already maps**
  `.ts/.tsx → typescript` and `.js/.jsx → javascript`, and `from_repo` already picks `jest`
  as the test runner for JS/TS. So profile detection is done; the gaps are extractor, layout,
  scaffold, runner/env, prompts, and the `--language`/resolve threading.
- **Bonus lever:** a tree-sitter TS extractor is the same machinery a future **UI-component
  extractor** (`components.md` for design-system work) would build on.

## Where TypeScript stands today
| Surface | Today | Needs for TypeScript |
|---|---|---|
| `catalog/profile.py` language detection | **already detects** `typescript`/`javascript`; jest as TS test runner | none (done) |
| `pkg/extractor.py` `default_extractors()` | Python always + Java when `tree_sitter`+`tree_sitter_java` present | add `TypeScriptExtractor` when `tree_sitter`+`tree_sitter_typescript` present |
| `pkg/*_extractor.py` | `PythonExtractor` (ast), `JavaExtractor` (tree-sitter) | new `typescript_extractor.py` (tree-sitter) |
| `sdlc/layout.py` `TargetLayout` + `resolve_layout` | branches `python` vs `java` (`_resolve_java_layout`) | add `typescript` branch: `src/` layout, package manager as `build_tool` |
| `sdlc/scaffold.py` | `_python_files` / `_java_files` dispatch on `layout.language` | add `_typescript_files` (package.json, tsconfig.json, vitest) |
| `sdlc/testrunner.py` | `SubprocessTestRunner` (pytest), `MavenTestRunner` | add `NodeTestRunner` (`npm test` / vitest run) + parse |
| `sdlc/testenv.py` | `VenvTestEnvironment`, `JavaToolEnvironment`; factories | add `NodeToolEnvironment` (node+npm preflight; `npm install`) |
| `sdlc/codegen.py` prompts | `_*_SYSTEM` (Python) / `_*_SYSTEM_JAVA`; selected via `_is_java()` | add `_*_SYSTEM_TS` + generalize selection beyond a boolean |
| `sdlc/feature_runner.py` `_resolve_language` | `auto → java only if java&&!python else python` | extend resolution to pick `typescript` |
| `cli.py` `--language` help | "auto, python, or java" | document `typescript` |

PKG **grounding** is language-agnostic once the extractor emits nodes/edges — it works for
TS the moment Slice 1 lands (module/type level; CALLS deferred, same as Java).

## Slice 1 — TypeScript comprehension (ships first, cheap)
Make `understand` / grounding / `pkg extract` process `.ts`/`.tsx`. **Two changes:**

1. **`pkg/typescript_extractor.py`** — a `LanguageExtractor` implementation mirroring
   `JavaExtractor`:
   - `language = "typescript"`, `suffixes = (".ts", ".tsx")` (skip `.d.ts` declaration files).
   - `module_name(path, root)`: repo-relative path without suffix, `index` collapsed to its
     dir (mirrors Python's `__init__` collapse) — TS modules are path-addressed, not namespaced.
   - `extract(...)` via `tree_sitter_typescript`, emitting the established node/edge types:
     - **Nodes:** `MODULE`, `TYPE` (class / interface / `type` alias / enum), `FUNCTION`
       (function decls, exported arrow consts, class methods), `FIELD` (class properties,
       interface members).
     - **Edges:** `IMPORTS` (from `import ... from "x"`), `CONTAINS`, and
       `IMPLEMENTS` for both class `extends`/`implements` and interface `extends`
       (the universal `EdgeKind` has no separate `EXTENDS`; Java reuses `IMPLEMENTS`
       the same way — subclass/interface impl).
   - **No `CALLS`** initially — same precision-first stance as Java (resolving TS calls needs
     type/alias resolution). Best-effort same-file calls are a later nicety.
2. **`pkg/extractor.py` `default_extractors()`** — append `TypeScriptExtractor()` behind a
   lazy `find_spec("tree_sitter") and find_spec("tree_sitter_typescript")` guard, exactly like
   the Java branch, so the base install stays dependency-free.

Add a `typescript` extra in `pyproject.toml` (`tree-sitter`, `tree-sitter-typescript`).

**Net:** `understand`, `PKGCodegenGrounder`, `load_or_extract`, and `pkg extract` process
`.ts`/`.tsx` automatically → memory bank (architecture / domain-model / glossary) and codegen
grounding work on TS repos. `conventions.md` stays Python-specific (a TS digest is a later
nicety; a pure-TS repo shows "no conventions sampled" — acceptable, noted, same as Java).

**Tests:** `tests/pkg/test_typescript_extractor.py` — a tiny `.ts` fixture (a class
implementing an interface, an import) → extract yields module + type + function/field nodes and
IMPORTS/IMPLEMENTS edges; skips cleanly if the `typescript` extra is absent (mirrors
`tests/pkg/test_java_extractor.py`).

## Slice 2 — TypeScript codegen (the heavy vertical)
Turn "we *understand* TS" into "we *ship* TS PRs." Phased so structure lands before the
LLM/toolchain work, exactly like Java 2a–2d.

### Language resolution
Extend `feature_runner._resolve_language`: when `--language auto`, choose `typescript` when TS
is the dominant language and Python isn't present (parallel to the Java rule). Explicit
`--language typescript` always wins. Update the CLI `--language` help text.

### Layout (`TargetLayout` `typescript` branch)
- `_resolve_typescript_layout()` parallel to `_resolve_java_layout()`:
  - `source_dir = "src"`, `tests_dir = "src"` co-located (`*.test.ts` next to source) — the
    dominant TS convention; (alternative `tests/` behind `--layout`).
  - `package_name` from `package.json` `name` if present, else repo slug.
  - `build_tool` carries the **package manager**: `pnpm` (pnpm-lock.yaml) | `yarn` (yarn.lock)
    | `npm` (package-lock.json / default).
  - `module_rel_path` extension → `.ts`.
- `detect_existing_typescript_layout()`: a `package.json` + `tsconfig.json` (and/or a `src/`
  with `.ts`) ⇒ mode=existing; else greenfield → scaffold.

### Scaffold (`_typescript_files`, template dispatch by language)
- **Vitest template (default):** `package.json` (type: module, `test` script → `vitest run`,
  devDeps: `vitest`, `typescript`, `@types/node`), `tsconfig.json` (strict, NodeNext), `src/`,
  `.gitignore` (node_modules, dist, coverage), README. Idempotent + never-clobber, like Java.
- Jest is a later option (one flag) — Vitest is the default for zero-config ESM + speed.

### Test runner + env
- **`NodeTestRunner`** (implements `TestRunner`): runs the `test` script (`npm test` /
  `pnpm test` / `yarn test` per `build_tool`) in the worktree; parse exit code + Vitest output
  → `TestRunResult`. Strips the same `_SECRET_ENV_PREFIXES`. Longer timeout (install + compile).
- **`NodeToolEnvironment`** (implements `TestEnvironment`): preflight `node` + the package
  manager present (clear error + `code=2` if not, like the pytest/Maven preflights); `ensure()`
  runs `npm install` (deps come from `package.json`); `python` property → n/a.
- Extend `make_test_runner` / `make_test_environment` with the `typescript` branch.
- **Auto-heal:** unlike Maven, npm *can* add a package — but codegen should declare deps in
  `package.json` and let `ensure()` install them. Recommend auto-heal **disabled** for TS in v1
  (deps declared, not pip-style injected); revisit if refine cycles show missing-dep churn.

### Codegen prompts (language-parameterized)
- Add `_IMPLEMENT_SYSTEM_TS` / `_TESTS_SYSTEM_TS` / `_REFINE_SYSTEM_TS`: runnable TypeScript,
  ESM `import`/`export`, strict types (no implicit `any`), one primary export per file, Vitest
  tests (`import { describe, it, expect } from "vitest"`) as `<name>.test.ts` co-located,
  declare new deps in `package.json`.
- Extend `_layout_block` with the TS path/module/export conventions.
- **Generalize prompt selection:** today `codegen.py` uses a boolean `_is_java()`. Replace with
  a small language→prompt map (`python`/`java`/`typescript`) so adding the 3rd language doesn't
  pile up booleans. Mechanical refactor; keep behavior identical for Python/Java.
- File-forms JSON (content/edits) is already language-agnostic — unchanged.

### feature_runner
Resolve language → TS `TargetLayout` → TS scaffold → `NodeToolEnvironment` preflight + install
→ codegen with `language="typescript"` → `NodeTestRunner` (auto-heal-disabled wrapper) → commit
→ PR. Branch/commit/PR plumbing is language-agnostic (already works).

## Phasing (each shippable)
- **Slice 1 — comprehension** (`typescript_extractor.py` + `default_extractors` + extra +
  tests). Cheap, independent value; ships first. ← recommended first.
- **2a — Layout + scaffold + `--language` threading** (deterministic, no LLM, no Node): TS
  `TargetLayout`, detection, Vitest scaffold template, resolve + CLI help. Fully unit-testable.
- **2b — Node runner + tool env**: `NodeTestRunner`, `NodeToolEnvironment`, runner/env
  selection. Unit-test output parsing; live needs node + npm.
- **2c — Language-aware codegen prompts** + the `_is_java` → language-map refactor.
- **2d — Integration + live verify**: end-to-end `sdlc feature --language typescript --safe` on
  a real TS repo → green Vitest + a committed TS diff. Add a CI `setup-node` step and a real
  integration test that skips without the toolchain (mirrors `test_java_integration.py`).

## Decisions to confirm
1. **Vitest first** (vs Jest)? Recommend **Vitest** — zero-config ESM, fast, single devDep;
   Jest later behind a flag. (Profile currently labels TS test runner "jest" — we'd set the
   *scaffold* to Vitest and can relax the profile label.)
2. **npm as default package manager** (detect pnpm/yarn from lockfile)? Recommend yes.
3. **Co-located `*.test.ts`** as the default test layout (vs `tests/`)? Recommend co-located —
   the prevailing TS convention; `tests/` available via `--layout`.
4. **Auto-heal disabled for TS in v1** (deps declared in `package.json`)? Recommend yes.
5. **Strict `tsconfig`** (strict: true, NodeNext)? Recommend yes — matches modern TS repos and
   gives the type checker as a free correctness gate.
6. **`.tsx` in scope for codegen** or comprehension-only at first? Recommend extractor handles
   `.tsx`; codegen targets `.ts` first (UI/JSX generation is the later `components.md` lever).

## Honest risks / limits
- **External toolchain:** like Java, TS needs `node` + a package manager we don't install →
  live runs + CI depend on the environment (`setup-node` in CI). Same shape as the Java JDK dep.
- **`npm install` latency/flakiness:** network-dependent installs make live runs slower and
  occasionally flaky vs Python's local venv. Cache where possible; generous runner timeout.
- **LLM TS reliability unproven here:** we've shipped Python + Java; TS strictness (types, ESM,
  no implicit any) may add refine cycles — 2c/2d carry the risk, as with Java.
- **No TS CALLS edges** (type/alias resolution) → call-hotspots/blast-radius stay Python-only;
  module/type comprehension works.
- **JS-vs-TS scope:** this spec targets **TypeScript**. Plain `.js` repos get comprehension via
  the same tree-sitter grammar later, but codegen targets `.ts` (types are the value).
- **Effort:** realistically 2–4 releases (Slice 1 + 2a small; 2b–2d larger). Comprehension
  delivers value independently, so this is incremental, not all-or-nothing.

## First step
**Slice 1:** add `pkg/typescript_extractor.py` + the `typescript` extra + wire it into
`default_extractors()`, then run `orchestrator understand` on a real TS repo and show an
architecture/domain map — multi-language comprehension extended to TS with one contained,
fully-tested change. Then build 2a (layout + scaffold) on top.
