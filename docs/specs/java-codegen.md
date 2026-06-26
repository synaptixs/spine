# Build spec: Java codegen (multi-language Slice 2)

**Status:** Phases 2a (1.8.0) + 2b + 2c (1.9.0) implemented. 2a: Java layout + Maven
scaffold + `--language`. 2b: `MavenTestRunner` (`mvn -B -q test`) + `JavaToolEnvironment`
(JDK/Maven preflight; auto-heal disabled) + `make_test_runner`/`make_test_environment(lang)`.
2c: Java implement/test/refine prompts + Java layout block. **2d (1.10.0) DONE & LIVE-PROVEN**:
a real-Maven integration test (`test_java_integration.py`, skips without the toolchain), a CI
JDK step (`setup-java`), and a verified live `sdlc feature --language java` — the LLM generated
`Calculator.java` + `CalculatorTest.java` and a real `mvn test` passed (`VERDICT: PASSED`). Also
fixed: build output (`target/`, `.sdlc-venv`) no longer pollutes the changed-files summary, and
the Python venv is created OUTSIDE the worktree. Java codegen is complete.
**Goal:** make `sdlc feature` generate, test, and ship **Java** changes — turning "we
*understand* Java" (1.7.0) into "we *ship* Java PRs." This is the heavy vertical of the
multi-language roadmap; phased so structure lands before the LLM/build-tool work.

## What's Python-shaped today (the surfaces to generalize)
| Surface | Today (Python) | Needs for Java |
|---|---|---|
| `sdlc/layout.py` `TargetLayout` | `src/<pkg>/`, dotted-less pkg | `src/main/java/<pkg path>/`, dotted reverse-DNS pkg, build tool |
| `sdlc/scaffold.py` | `pyproject` + `src/<pkg>/` + `tests/` | `pom.xml`/`build.gradle` + `src/main/java` + `src/test/java` |
| `sdlc/testrunner.py` | `python -m pytest` | `mvn test` / `gradle test` + output parse |
| `sdlc/testenv.py` | per-project **venv** + pip auto-heal | JDK + Maven/Gradle toolchain check (deps declared in `pom`, not pip-installed) |
| `sdlc/codegen.py` prompts | "runnable **Python**", JUnit-less | "runnable **Java**", JUnit tests, `package` decls, one public class/file |
| `sdlc/feature_runner.py` | threads none of the above as language | thread a resolved `language` everywhere |

PKG **grounding already works** for Java (1.7.0) — module/type level (no CALLS).

## Design

### Language resolution
A single `language` ("python" | "java") resolved once per run and threaded through:
- **brownfield:** from `ProjectProfile.from_repo` (dominant language).
- **greenfield:** no code to detect → CLI `--language` (default: detected, else `python`).
- `sdlc feature --language java` (+ existing `--layout`, `--package-name`).

### Layout (`TargetLayout` gains `language` + `build_tool`)
- Add `language: str` and `build_tool: str` ("maven"|"gradle"|"") to `TargetLayout`.
- Java derive: `package_name` reverse-DNS (`org.example.<repo-slug>`; override `--package-name`),
  `source_dir = src/main/java/<pkg-as-path>`, `tests_dir = src/test/java/<pkg-as-path>`.
- `detect_existing_package`: recognize `src/main/java/...` + `pom.xml`/`build.gradle` → existing
  Java layout (mode=existing); else greenfield → scaffold.

### Scaffold (template dispatch by language)
- Refactor `scaffold()` to dispatch on `layout.language`: `_python_template` (today) vs
  `_java_template`. The `profile` param (already accepted, unused) becomes the selector.
- **Maven template (default):** `pom.xml` (JUnit 5 dep, surefire), `src/main/java/<pkg>/`,
  `src/test/java/<pkg>/`, Java `.gitignore`, README. Idempotent + never-clobber, like today.
- Gradle is a later option (one flag).

### Test runner + env
- **`MavenTestRunner`** (implements `TestRunner`): `mvn -q -B test` in the worktree; parse
  surefire output / exit code → `TestRunResult`. (`GradleTestRunner` later.)
- **`make_test_runner(language, env)`** picks pytest vs maven.
- **`JavaToolEnvironment`** (implements `TestEnvironment`): verifies `java` + `mvn` present
  (clear error + `code=2` if not, like the pytest preflight); `python` property → n/a; auto-heal
  is **disabled for Java** (Maven resolves declared deps — codegen must put them in `pom.xml`).
- `make_test_environment(language)` picks venv vs java toolchain.

### Codegen prompts (language-parameterized)
- Parameterize `_IMPLEMENT_SYSTEM` / `_TESTS_SYSTEM` / `_REFINE_SYSTEM` by language: Java variant
  says runnable Java, `package` matching the layout, one `public` class per file named after the
  file, JUnit 5 tests under `src/test/java`, declare new deps in `pom.xml`.
- Extend the existing `_layout_block` to express the Java package/path/class conventions.
- File-forms JSON (content/edits) is already language-agnostic — unchanged.
- `extract_conventions` is Python-only → Java runs show "no conventions sampled" (a Java digest
  is a later nicety, not a blocker).

### feature_runner
Resolve language → build Java `TargetLayout` → Java scaffold → `JavaToolEnvironment` preflight →
codegen with `language` → `MavenTestRunner` through the (Java-disabled) auto-heal wrapper →
commit → PR. The branch/commit/PR plumbing is language-agnostic (already works).

## Phasing (each shippable)
- **2a — Layout + scaffold + `--language` threading** (deterministic, no LLM, no JDK): Java
  `TargetLayout`, detection, Maven scaffold template, CLI flag. **Lands the structure; fully
  unit-testable.** ← recommended first.
- **2b — Maven runner + Java tool env**: `MavenTestRunner`, `JavaToolEnvironment`, runner/env
  selection. Unit-test output parsing; live needs JDK+Maven.
- **2c — Language-aware codegen prompts**: Java implement/test/refine prompts + layout block.
- **2d — Integration + live verify**: end-to-end `sdlc feature --language java --safe` on a real
  Java repo → green JUnit + a committed Java diff.

## Decisions to confirm
1. **Maven first** (vs Gradle)? Recommend **Maven** (simpler template, ubiquitous); Gradle later.
2. **JDK/Maven dependency** — Java codegen requires a JDK + Maven on the machine/CI. Gate it
   behind a toolchain preflight (fail fast with a clear message) and document the requirement.
   CI for live Java tests needs a JDK step. Accept?
3. **Auto-heal disabled for Java** (deps declared in `pom.xml` by codegen) — recommend yes;
   Maven doesn't have a clean "install the one missing package" like pip.
4. **Reverse-DNS package default** `org.example.<repo-slug>` (override `--package-name`)? 
5. **Scope of the first PR** — recommend **2a only** (structure, no LLM/build-tool), then 2b–2d.

## Honest risks / limits
- **External toolchain:** unlike Python (we create the venv), Java needs a JDK + Maven we don't
  install — live runs + CI depend on the environment. This is the biggest difference from Slice 1.
- **LLM Java reliability is unproven:** we've only generated Python. Java is stricter/more verbose
  (package decls, one-class-per-file, imports) → expect more refine cycles; 2c/2d carry the risk.
- **No Java CALLS edges:** grounding is module/type level (call-hotspots/blast-radius Python-only).
- **Effort:** realistically 2–4 releases (2a small; 2b–2d larger). Comprehension (1.7.0) already
  delivers Java value independently, so this is incremental, not all-or-nothing.

## First step
**Phase 2a:** generalize `TargetLayout` (+language/build_tool), Java detection in `layout.py`, a
Maven scaffold template in `scaffold.py`, and `sdlc feature --language`. Deterministic and fully
unit-tested — prove a `--safe` greenfield Java run scaffolds a correct Maven project (no codegen
yet), then build the runner/prompts on top.
