# Design + Plan: adding Kotlin to the PKG (10th language) — comprehension first, on a real Android app

**Status:** 🟡 **P0 done** (this plan, the branch, the baseline) · P1–P5 not started.
**Branch:** `feat/kotlin-support` off `develop` at `d84e666` (3.33.2). **Opened:** 2026-09-10.
**Validation repository:** [`synaptixs/aiandroid`](https://github.com/synaptixs/aiandroid) — a mirror of
Now in Android: 263 `.kt` files, 22,662 lines, 28 Gradle modules, Jetpack Compose, Hilt, Room, Retrofit.
Used ephemerally (shallow clone to a scratch dir, extraction on a `.git`-less copy, deleted after);
its name lives in `docs/` only, never in `src/` or `tests/`.

> Same track shape as PHP ([php-support-roadmap.md](php-support-roadmap.md)) and Go
> ([go-support-roadmap.md](go-support-roadmap.md)), with two things this track adds on purpose:
> **§8 measures the blast radius from the PKG before each phase touches code**, and **§9 carries
> generic work** — pieces built here that every later language or feature reuses, so the next
> track is shorter than this one. **Codegen is out** (§10): Android needs the SDK, and the Java
> plumbing the expansion roadmap assumed Kotlin would reuse is Maven-only (§9.3).

## Roadmap currency — the rule this document follows

Every phase row in §5 carries **Status · Started · Finished · Evidence**. A phase is DONE only when
its evidence column links a commit, a test name, or a pasted command result; "done" without evidence
is the [STATE-OF-SPINE §8](STATE-OF-SPINE.md) drift this repository keeps finding. The row is
updated **in the same commit** as the work, never after. §9.1 turns the rule into a check.

---

## 0. Decisions surfaced up front

| # | Decision | Options | Recommendation and why |
|---|---|---|---|
| **D1** | Parser | (a) `tree-sitter-kotlin` 1.1.0 (fwcd), (b) `tree-sitter-kotlin-ng`, (c) `tree-sitter-language-pack` | **(a).** On PyPI with abi3 wheels for every CI platform (`py>=3.9`; its `tree-sitter~=0.22` pin sits behind an optional extra, so it coexists with the 0.26 in `uv.lock`). **Measured 2026-09-10 on the validation repository: 0 files with an ERROR node out of 263, 0 of 22,662 lines.** Snippet probes fail only on single-line `;`-separated members and one-line `object X { val … }`, which real code does not write. (b) is not on PyPI. (c) is 20 MB for one grammar. |
| **D2** | Id prefix | (a) share **`java:`** (one JVM namespace), (b) own `kt:` prefix | **(a).** Kotlin and Java share one package namespace; a Kotlin `import com.x.Y` names the same class whether `Y` is `.kt` or `.java`, and `Y` cannot exist in both. Sharing the prefix means the placeholder `java:com.x.Y` is upgraded by `FactBatch` dedup regardless of which front-end declared it, Java's `_DOTTED_PREFIXES` import join works unchanged, and a mixed repo (`src/main/java/**/*.kt` beside `.java`, which is exactly aiandroid's layout: 193 of 263 files) gets one graph, not two. Nodes carry `language="kotlin"`; `pkg accuracy` and the capability matrix key on that, not the prefix. Precedent: `ts:` covers `.ts`, `.tsx`, `.js`, `.jsx`. |
| **D3** | Module unit | (a) the `package` header, path fallback, (b) file path | **(a)** — the Java rule (`JavaExtractor.module_name`), and it is what makes D2 work. 249 of 263 files declare a package; the 14 that do not key on their path. |
| **D4** | Top-level and extension functions | (a) `Function` under the package module, id `java:pkg.name`; an extension `fun T.name()` is the same, with the receiver recorded nowhere, (b) attach extensions to the receiver `Type` | **(a).** An extension is not a member — the receiver type does not own it, and attaching it would put a `CONTAINS` edge on a type declared in another module or in the SDK. Calls resolve to it by name (D8 row 5). Two extensions with the same name in one package are a compile error in Kotlin, so the id is unique. |
| **D5** | `object` and `companion object` | (a) `object X` → `Type`; companion members fold into the enclosing class (`A.h()` → `java:pkg.A.h`), (b) a nested `Type` `A.Companion` | **(a).** Call sites name the class, never `Companion`; folding is how the graph will be read. A companion's own name, when given, is recorded in `name` only. 43 objects and 7 companions in the validation repo. |
| **D6** | `Field` | (a) `val`/`var` properties in a class body **and** `val`/`var` primary-constructor parameters, (b) body properties only | **(a).** In Kotlin a constructor `val` *is* a property, and in DI-heavy code (`class Repo @Inject constructor(private val dao: TopicDao)`) it is the typed receiver every call in the class goes through (D8 row 6). A bare constructor parameter without `val`/`var` is not a `Field`. Top-level `val`s are not `Field`s (corpus rule: a `Field` belongs to a `Type`). 799 properties in the validation repo. |
| **D7** | Inheritance | `delegation_specifier` → `IMPLEMENTS`, resolved by the Java rule (import → same package → external) | Kotlin does not distinguish `extends` from `implements` syntactically; neither does the edge. A guessed same-package target that nothing declares is repointed in `finalize`, the C#/PHP pattern. |
| **D8** | CALLS — see §3.2 | precision-first, seven shapes, no type inference | The one **net-new** rule: **typed receivers are the norm, not the exception** — Kotlin declares the type of every property and parameter, so `dao.getTopics()` resolves exactly through the import map. This is P2, not a later phase, because it is most of the call graph in this style of code. |
| **D9** | Invention oracle (`pkg/scope.py`) | (a) a `_Kotlin` walker, (b) `NOT_APPLICABLE` | **(a).** Unlike Java, a Kotlin local *can* shadow a call: `val helper = ::other; helper()` invokes the local through `invoke`. The TypeScript walker is the template (scope nodes: `function_declaration`, `lambda_literal`, `anonymous_function`; bindings: `property_declaration` / `variable_declaration` and parameters). A `shadowed_calls` corpus case pins it. |
| **D10** | Framework edges — an Android app is a **client** | Retrofit `@GET("topics")` → a **`CONSUMES` candidate**, not an `Endpoint`; Room `@Entity` → `Entity`; Ktor server routes deferred | Nothing in an Android app *exposes* a route; it calls one. `python_client.py`'s `PendingCall` side-channel exists for exactly this — an unmatched call is a **cross-repo join candidate**, and `pkg joins` matches it against a provider's `Endpoint`s by verb and path. This makes a Kotlin app the first mobile **consumer** in the multi-repo join, which no other front-end can be today. Path from the annotation literal; base URL from a literal `baseUrl("…")` when present, else path-only. |
| **D11** | `.kts` Gradle scripts | (a) not registered, (b) parse as Kotlin | **(a).** 33 build scripts whose "functions" are DSL calls (`implementation(libs.x)`); parsing them would add a phantom component per module. Read as **markers** by the profiler instead (D12). |
| **D12** | Profiler | `.kt` → `kotlin`; `build.gradle.kts` / `settings.gradle.kts` / `gradle/libs.versions.toml` read as markers; `androidx`/`compose` → framework `android`, `io.ktor` → `ktor`, `springframework` (already) → `spring`; `junit` (already) → `junit` | Today `profile_repo` on the validation repository returns **`languages: []`** — only `build.gradle` (no `.kts`) is read, and `.kt` maps to nothing. |
| **D13** | Codegen | deferred to a follow-on spec | Android codegen needs the SDK and an emulator-free test target; even Kotlin/JVM codegen cannot reuse the Java track as assumed, because `MavenTestRunner` is the only JVM runner and aiandroid, like most Kotlin, is Gradle. `"kotlin"` stays out of `SUPPORTED_LANGUAGES` so `--language kotlin` exits 2 rather than scaffolding Python. §9.3 proposes the Gradle runner as generic work. |

---

## 1. Baseline — what Spine sees today (2026-09-10, measured)

Run against the validation repository with 3.33.2 through the MCP server (`profile_repo`, `map_repo`):

| | Today |
|---|---|
| `profile_repo` languages | **none detected** — framework, database, test runner all `-` |
| `map_repo` code facts | **0 modules, 0 types, 0 functions, 0 fields, 0 endpoints, 0 entities** |
| `map_repo` docs | 69 `Doc` nodes; **57 potential drift** claims, every one a symbol the graph does not have |
| Declarations in the source (tree-sitter census) | 232 classes · 43 objects · 738 functions · 799 properties · 7 companions · 2,587 imports · 249 package headers |
| Annotations that carry facts | 6 `@Entity` · 5 `@Dao` · 18 `@Query` · 5 `@Insert` · 3 `@GET` · 169 `@Composable` · 45 `@Inject` · 23 `@Module` · 185 `@Test` |
| Tests | 26 unit-test files, 13 instrumented |

"57 potential drift" is the number to watch: those are README claims naming code the graph cannot see. After P1 most of them should bind.

---

## 2. Why Kotlin is cheaper than it looks, and where it is not

```
CHEAP
· The grammar parses the whole validation repository clean (D1).
· Module, import, inheritance, and type resolution are the Java rules, verbatim —
  and with D2 the ids are Java's too, so a mixed repo joins for free.
· Types are declared everywhere. Typed-receiver resolution, a late phase for
  PHP and TypeScript, is the main event here and needs no inference.
NOT CHEAP
· Kotlin has more declaration shapes than Java: object, companion, data/sealed/
  enum/value class, extension functions, top-level functions and properties,
  primary-constructor properties, typealias. Each is a fact-mapping row (§3.1).
· A local can shadow a call (D9): the invention walker is real work.
· The framework story is inverted (D10): consumer, not provider. New edge
  direction for a mobile app; the join side already exists.
· Reverse-DNS packages make every module the same `state` area (§9.4).
```

**Reused verbatim:** lazy parser factory + `TYPE_CHECKING`-guarded `TSNode`; gated append in
`default_extractors()`; Java's `_ImportContext`, `_resolve_type`, `_supertypes`, `_annotations`,
`_string_literal` (import them or lift into a `jvm_names.py` leaf — the PHP lesson: no cycle);
two-pass CALLS; `finalize` repoint; the corpus method; the per-front-end freshness test.

---

## 3. Design

### 3.1 Fact mapping (P1)

| Kotlin construct (CST node) | Fact | Notes |
|---|---|---|
| `package_header` | `Module` `java:com.x.y` | D2/D3; no header → `java:<repo-relative path>` |
| `import` | `IMPORTS` module → `java:<fqn>` | External placeholder, dedup-upgraded when first-party (Java or Kotlin). `import a.b.*` retained as a wildcard prefix for resolution, no edge. A function import (`import com.x.helper`) feeds the resolver table |
| `class_declaration` (class / data / sealed / enum / value / annotation / interface) | `Type` `java:pkg.Name` | Nested classes `java:pkg.Outer.Inner` (Java precedent). Enum entries → `Field` |
| `object_declaration` | `Type` | D5 |
| `companion_object` | — | members fold into the enclosing `Type` (D5) |
| `delegation_specifier` | `IMPLEMENTS` type → super | D7 |
| `function_declaration` in a class body | `Function` `java:pkg.Type.name` | `override`, `suspend`, `operator`, `infix` modifiers ignored; `@Composable` is a normal function |
| top-level `function_declaration` (incl. extensions) | `Function` `java:pkg.name` | D4 |
| `property_declaration` in a class body; `val`/`var` `class_parameter` | `Field` `java:pkg.Type.name` | D6; the declared type is kept in the resolver table for D8 |
| `type_alias` | — | no node; recorded in the resolver table so `Cb` resolves for D8 |
| `CONTAINS` | module → type / top-level function, type → member | as every front-end |
| lambdas, anonymous functions, local functions, `when` branches | — | no name → no node; local functions could be a later `Function` if a consumer asks |

`is_public`: Kotlin's default is public and `private`/`internal` are keywords the graph does not
record — return `None`, the Java/C# answer.

### 3.2 CALLS (P2) — the typed-receiver language

Two passes per file: the resolver table (package, import map incl. imported functions, wildcard
prefixes, this file's types with their members and companion members, top-level functions, every
declared property/parameter type per scope), then each `call_expression`.

| Call shape | Resolution | Emit |
|---|---|---|
| `foo()` bare inside a class | member or companion member of the enclosing type; else a same-file top-level function; else an imported function (`import com.x.foo`); else a same-package top-level function **declared in this file** | `CALLS` → the exact id; otherwise **skip** (a same-package function in another file is a guess with no `finalize` backstop for a function id) |
| `this.foo()` | member | same |
| `Type.foo()` (capitalised receiver) | object / companion / enum member via the Java rule | `CALLS` → `java:pkg.Type.foo`, external placeholder if third-party — the Java rule, unchanged |
| `Type(args)` (constructor) | the `Type` node | `CALLS` → the `Type` (corpus rule: instantiation is a call to the type) |
| `x.ext()` where `ext` is a same-file or imported **extension function** | by name — an extension name is unique in scope | `CALLS` → `java:pkg.ext` |
| `prop.foo()` / `param.foo()` where the receiver has a **declared type** in scope | receiver type through the import map / same package | `CALLS` → `java:pkg.RecvType.foo` — placeholder if third-party. **The main event** (D8): `private val dao: TopicDao` + `dao.getTopics()` is the dominant shape in the validation repo. `?.` safe calls resolve the same way |
| `foo?.let { }`, `apply { }`, `run { }`, `it.x()`, `map { … }` on an inferred receiver, callable references `::foo`, `invoke` on a lambda, calls on an unannotated `val x = something()` | — | never — inference or fabrication |

Precision guard specific to Kotlin: the **shadowing** case (D9) is emitted by none of the rows
above only because the resolver checks local bindings first; the `_Kotlin` walker in `scope.py`
is the independent oracle that says so.

### 3.3 Framework edges (P3)

| Framework | Source shape | Fact |
|---|---|---|
| **Retrofit** (client) | `interface Api { @GET("topics") suspend fun getTopics(...) }`, `@POST`/`@PUT`/`@DELETE`/`@PATCH`; `@Query`/`@Path` parameters; a literal `baseUrl("https://…")` in the same module | a **`CONSUMES` candidate** per method: verb + path (`GET /topics`), caller = the interface method's `Function`. Matched against a provider's `Endpoint`s by `pkg joins` (the `python_client` side-channel — see D10). A path with `{id}` segments is kept literal; the joiner already normalises path parameters |
| **Room** | `@Entity(tableName = "topics") data class TopicEntity(@PrimaryKey val id: String, …)`; `@ForeignKey(entity = X::class, …)`; `@Relation(parentColumn, entityColumn)`; `@Junction(X::class)` | `Entity` `java:entity:pkg.TopicEntity` (parallel id, C#/PHP precedent) named by `tableName` when literal, else the class; constructor properties → the entity's `Field`s; `entity = X::class` / `@Junction(X::class)` → `REFERENCES` |
| **Room DAO** | `@Dao interface { @Query("SELECT … FROM topics …") fun …; @Insert/@Upsert/@Delete fun …(t: TopicEntity) }` | `READS` / `WRITES` from the DAO method to the `Entity`: `@Insert`/`@Upsert`/`@Delete` by parameter type; `@Query` by parsing the literal with `sqlglot` **when the `sql` extra is present** (the SQL front-end's own parser — the second use of it outside `.sql`), else skipped and said so |
| Ktor server routes (`routing { get("/x") { } }`), Spring `@GetMapping` in Kotlin | `Endpoint` + `EXPOSES` | **deferred** — none in the validation repo; Spring's reader in Java would port in a day when a repo needs it |
| Compose `@Composable`, Hilt `@Module`/`@Provides`/`@Binds` | — | plain functions and types; no vocabulary for "provides" and no consumer |

### 3.4 What each phase lights up in the product

Because the PKG is the substrate, each phase is visible in the shipped surfaces without further work:
P1 → `understand`, `state`, `map_repo`, `docs_for` (the 57 drift claims start binding); P2 →
`blast_radius`, `explain_symbol`, `investigate`, `localize`, `regression_gaps`, "Call graph:
available"; P3 → `pkg joins` can join an Android consumer to a Java/Go/PHP provider; Room entities
reconcile with a `.sql` schema through `data_layer_link` with no Kotlin-specific code.

---

## 4. Testing the phases with Spine itself

Every phase ends by running Spine on the validation repository and pasting the numbers into §5:

```bash
# baseline / after each phase (a .git-less copy so no commit-keyed cache is trusted)
uv run --frozen orchestrator pkg extract <copy> --json | jq .summary
uv run --frozen orchestrator pkg verify <copy>
uv run --frozen orchestrator state <copy> --lens developer | grep -E "^- Stack|^- Size|Call graph"
uv run --frozen orchestrator understand <copy>        # the knowledge base a reader would get
```

and through the MCP tools, which is how a user meets the result: `profile_repo`, `map_repo`,
`blast_radius` on a Room DAO method, `explain_symbol` on a repository class, `docs_for` on a
README claim that was drift at baseline, `pkg_joins` against a small provider fixture in P3.
Before each phase touches Spine's own code, `blast_radius` on the registration symbols (§8) is
re-run and the row updated — the graph is the blast-radius oracle for its own change.

The design of each phase goes through `design_change` (the grounded design MCP tool) with the
phase's D-rows as the intent, and the output is attached to the phase's evidence. Where the
result disagrees with this document, the document changes.

---

## 5. Phases — the living table

| Phase | Work | Effort | Exit criteria | Status | Started | Finished | Evidence |
|---|---|---|---|---|---|---|---|
| **P0 Plan + baseline** | This document; branch `feat/kotlin-support`; grammar census; `profile_repo`/`map_repo` baseline; PKG blast radius (§8) | 1 d | Decisions D1–D13 recorded with evidence; baseline numbers in §1 | ✅ DONE | 2026-09-10 | 2026-09-10 | §1, §8, D1 census (0/263 files with ERROR) |
| **P1 Comprehension** | `kotlin_extractor.py` (§3.1, D1–D7, D11); `jvm_names.py` leaf if Java helpers are shared; registration: `default_extractors`, `FRONT_ENDS`, `EXTRA_PROBES`, `_GRAMMAR_MODULES`, profiler (D12), `scope.py` placeholder until P2, `docs.py`/`doc_link.py` `kt`; packaging (`kotlin` extra, `languages` meta-extra, mypy override, `ci.yml`); tests per [docs/reviewing/language-frontend-checklist.md](../reviewing/language-frontend-checklist.md); docs per [docs/reviewing/docs-matrix.md](../reviewing/docs-matrix.md) | 4–6 d | `map_repo` on the validation repo: modules/types/functions/fields > 0 with the census as the ceiling (232 / 43+ / 738 / 799); `pkg verify` 0 errors; drift claims below 57; **every P1 row of §7.1 updated** and `scripts/docs_audit.py` reports no STALE/MISSING; gate green with `--extra kotlin` | ⬜ | | | |
| **P2 Corpus + CALLS + invention walker** | `corpus/kotlin/{plain,typed_receivers,extensions,companions,shadowed_calls,mixed_java}` labelled from source first; §3.2 rows; `_Kotlin` walker (D9); `--scoreboard` | 4–5 d | precision 1.00 on every kind; `invention` `MEASURED` with 0; `state` "Call graph: available"; `blast_radius` on a validation-repo repository class lists its DI callers | ⬜ | | | |
| **P3 Room + Retrofit** | `kotlin_room.py` (Entity/Field/REFERENCES; DAO READS/WRITES via sqlglot when present), `kotlin_http.py` (Retrofit `CONSUMES` candidates through the `PendingCall` side-channel); corpus `room`, `retrofit_consumer`; a two-repo `pkg joins` fixture with a tiny provider | 3–5 d | 6 entities on the validation repo; `pkg joins` proposes the consumer→provider join; `data_layer_link` reconciles against a `.sql` schema | ⬜ | | | |
| **P4 Generic work** (§9.1, §9.2, §9.4) | `scripts/roadmap-status.py --check`; `scripts/validate-frontend.py`; reverse-DNS area grouping | 3–4 d | each item's own exit in §9; this table passes its own check | ⬜ | | | |
| **P5 Review + MR** | `/review-pr` on the branch (self-review with the same checklist a maintainer will run); fix; open the MR to `develop` with the phase table as its body | 1 d | verdict "mergeable"; the review's docs-audit table shows every §7.1 row updated; CI green; no `episteme/` in the diff | ⬜ | | | |

Rough total: **16–22 days**, one engineer familiar with the PKG. Each of P1–P3 is release-worthy
on its own; the MR may be one PR or one per phase if a maintainer prefers smaller reviews.

---

## 6. Corpus cases (P2–P3)

Per `corpus/README.md`: `.repo/` fixture, `expected.json` from the source before the first run,
`known_gaps` predicted. Vocabulary row for the README: `kotlin` · module `java:com.x` · type
`java:com.x.Cart` · separator `.` · **language `kotlin`, prefix `java:` (D2)**.

| Case | Exercises | The finding it is built to catch |
|---|---|---|
| `plain` | package, imports, class + interface + data class, `override`, constructor `val`, `this.m()`, `Type(…)` | the control |
| `typed_receivers` | `class Repo(private val dao: Dao) { fun f() = dao.get() }` plus a `val x = make()` with no declared type | the declared one resolves; the inferred one is a permanent, predicted miss |
| `extensions` | `fun T.ext()`, called as `t.ext()` from another class; an extension with the same name imported from elsewhere | by-name resolution to the same-file one; the imported one is a hit only when its import is explicit |
| `companions` | `companion object { fun make() }` called as `A.make()`, `Companion.make()`, and bare `make()` from inside `A` | all three fold onto `A.make` (D5) |
| `shadowed_calls` | `val helper = ::other; helper()` inside a function whose class has `fun helper()` | the walker flags it; the extractor emits nothing |
| `mixed_java` (`requires: [java, kotlin]`) | a Kotlin class extending a Java class in the same package, and a Java class calling a Kotlin top-level function | one graph, `java:` ids on both sides, `IMPLEMENTS` and `CALLS` across the language boundary |
| `room` (P3) | two entities, a `@ForeignKey`, a `@Dao` with `@Query`/`@Upsert` | `REFERENCES`; `READS`/`WRITES` only with the `sql` extra |
| `retrofit_consumer` (P3) | an `@GET`/`@POST` interface, one computed path | two `CONSUMES` candidates; the computed one yields nothing |

---

## 7. Files to change

**New:** `src/orchestrator/pkg/kotlin_extractor.py`, `kotlin_room.py`, `kotlin_http.py`,
optionally `jvm_names.py`; `tests/pkg/test_kotlin_extractor.py`, `test_kotlin_room.py`,
`test_kotlin_http.py`; `corpus/kotlin/*`; `scripts/roadmap-status.py`, `scripts/validate-frontend.py`
(§9); this file.

**Modified (P1):** `pkg/extractor.py`, `pkg/capabilities.py`, `pkg/persistence.py`
(`_GRAMMAR_MODULES` — the PHP omission), `doctor.py`, `catalog/profile.py`, `pkg/scope.py`,
`pkg/docs.py`, `pkg/doc_link.py`, `pyproject.toml` (+ `uv.lock` one line), `.github/workflows/ci.yml`,
`tests/pkg/test_default_extractors.py`, `test_capabilities.py`, `test_verifier.py`, `test_scope.py`,
`tests/catalog/test_profile.py`, `tests/test_doctor.py`; docs: every row the docs matrix names for a
new front-end, `corpus/README.md`, `docs/specs/STATE-OF-SPINE.md`, `SPEC-INDEX.md`,
`language-expansion-roadmap.md`, `assets/spine-architecture.svg` via its script.

**Untouched:** `sdlc/*` (D13), `SUPPORTED_LANGUAGES`, the Java extractor's behaviour (Kotlin only
imports its helpers).

### 7.1 User-facing documentation — what changes, in which phase, and what proves it

A front-end is not shipped until the documents a user reads say so. The PHP track landed with
fifteen stale "eight front-ends" lines across nine documents because the lists were updated and
the counts were not. This table is the contract for this track: each row is updated **in the
phase that makes it true**, in the same commit as the code, and `scripts/docs_audit.py` must
report no STALE or MISSING line before that commit. `/review-pr` walks
[docs/reviewing/docs-matrix.md](../reviewing/docs-matrix.md) against it.

| Document | What must change | Phase |
|---|---|---|
| `README.md` | the intro language list, the "Works across …" line, the "Add a language" row (count **and** the next-language list); the "What's new" paragraph at the release cut | P1 · release |
| `FEATURES.md` | a Kotlin capability row (namespaces, objects/companions, extensions, typed-receiver call graph, Room entities, Retrofit consumers, codegen not shipped); the "Measured graph accuracy … all N front-ends" row count | P1, P3 |
| `USER_GUIDE.md` | the extras list (`[kotlin]`), the "Multi-language" blockquote (language, extra, `.kt` suffix, `.kts` not parsed), the corpus-results line; the multi-repo section gains "an Android app as a consumer" once P3 lands | P1, P3 |
| `KNOWLEDGE_GRAPH.md` | node and edge matrices, the language table row, the "Parser coverage" paragraph, a fact-mapping note on D2 (Kotlin shares `java:` ids) and D4/D5 (extensions, companions) | P1, P3 |
| `CLAUDE_GUIDE.md`, `CODEX_GUIDE.md` | the language sentence and "N front-ends"; the toolchain table gets **no** Kotlin row (D13) and says why | P1 |
| `CLI_REFERENCE.md`, `EXAMPLE.md`, `BENCHMARK.md` | corpus-results counts ("N fixture cases, N front-ends", "the other N front-ends") | P2 |
| `SETUP.md` | the `[kotlin]` extra where extras are enumerated | P1 |
| `corpus/README.md` | the id-vocabulary row: language `kotlin`, prefix **`java:`** (D2) — the third exception the table must explain, after C/C++ | P2 |
| `plugins/spine/skills/*/SKILL.md` | the language line | P1 |
| `docs/specs/STATE-OF-SPINE.md` | front-end count, the precision row's "all N front-ends", the `CALLS` recall row (Kotlin's number and denominator), source-module and test counts (`state-numbers.py --check`) | P1, P2 |
| `docs/specs/SPEC-INDEX.md`, `language-expansion-roadmap.md` | this spec's row and the expansion roadmap's Kotlin line, updated to the phase reached — never ahead of it | every phase |
| `CHANGELOG.md` | one entry under Unreleased per merged phase, in the house voice (what it does, what it refuses to guess, the extra to install) | every phase |
| `assets/spine-architecture.svg` (+ `.png`) | "across N language front-ends" — re-rendered by its script, which the gate checks | P1 |
| `docs/reviewing/language-frontend-checklist.md` | any registration site this track discovers that the list lacks (the way PHP added the cache key) | as found |

What to grep before each commit, beyond the audit script: the number word ("nine", "ten") as
well as the digit; `Go and PHP` / `Go, PHP` list tails; and `[php]` wherever extras are
enumerated, because a new extra is usually added to the first list and not the second.

---

## 8. Blast radius — measured from the PKG, per registration site

`blast_radius` on Spine's own graph at `d84e666`, before any Kotlin code. Re-run before each phase;
a count that moved is a reason to re-read the callers, not to skip them.

| Symbol | Callers | Touches | What the phase must respect |
|---|---|---|---|
| `pkg.extractor.default_extractors` | **13** — `RepoCodeExtractor.__init__`, `accuracy.score_corpus`, `verifier.GroundingVerifier._extractor_for`, 9 registry tests, the capability superset test | 28 | The gated append is read by the corpus scorer, the freshness verifier, and the matrix test — the biconditional test for `kotlin` is not optional |
| `pkg.java_extractor.JavaExtractor` | **3** — `default_extractors` and its own tests only | 16 | Safe to import Java's private helpers from; nothing else depends on their names. Lifting them into a leaf is a refactor with three callers to re-check |
| `pkg.persistence.extractor_fingerprint` | 8 — `_cache_path` + 7 tests | 10 | Adding `tree_sitter_kotlin` to `_GRAMMAR_MODULES` changes every warm cache's key on machines with the extra — intended; the cross-check test from #336 enforces the entry |
| `pkg.import_link.link_imports` | 3 — `RepoCodeExtractor.extract` + 2 tests | 12 | Nothing to add: D2 rides Java's dotted-prefix rule |
| `sdlc.feature_runner._resolve_language` | 4 — `run_feature`, `autorun._require_plan`, 2 tests | 9 | **Not touched** (D13). A Kotlin repo keeps resolving to `python` for codegen, which is the documented pre-existing behaviour for every comprehension-only language |
| `catalog.profile.ProjectProfile.from_repo` | via `_resolve_language`, `state`, `map_repo`, `profile_repo` | — | D12 changes what four surfaces report for every Gradle-Kotlin repo; the profile test pins it |

The pattern the numbers show: the registration sites with the widest fan-out are the ones the
[language checklist](../reviewing/language-frontend-checklist.md) already lists, and the PHP track
missed the one with the fewest callers (`extractor_fingerprint`, all tests) — few callers is not low
risk when the failure is silent.

---

## 9. Generic work — built here, reused by every later track

Ordered by how much each shortens the next language. The first two ship in P4 of this track; the
rest are proposed with their own exit criteria and can be picked up independently.

### 9.1 `scripts/roadmap-status.py --check` — the roadmap-currency gate
Reads every `docs/specs/*-roadmap.md` phase table with the §5 columns. Fails when a row marked
DONE has no Evidence or no Finished date, when a Started date follows a Finished date, or when the
spec's own **Status** line disagrees with `SPEC-INDEX.md`'s row for it. This is the "CI gate on
spec-status drift" [STATE-OF-SPINE §8](STATE-OF-SPINE.md) has listed as missing; the PHP track's
"P1+P2 done" in one file and "all four phases" in another is the case it catches. Stdlib only;
joins `state-numbers.py` and `docs_audit.py` in the gate. **Exit:** the check runs in CI and this
document passes it.

### 9.2 `scripts/validate-frontend.py <language> <git-url>` — the real-repo smoke test as a script
What `/review-pr` §4 does by hand: shallow-clone to a scratch dir, copy without `.git`, run
extract + verify + the `state` stack line, print node kinds by language and the top-5 unresolved
import targets, delete both. Never writes the repository name anywhere. **Exit:** used for every
phase's evidence in §5; the review skill's §4 points at it.

### 9.3 `GradleTestRunner` + Gradle-aware `JavaToolEnvironment` — the JVM codegen gap
`layout.py` detects Gradle but `testrunner.py` only knows `mvn`, so Java codegen on a Gradle
project cannot run its tests today, and the expansion roadmap's "Kotlin reuses Java/Gradle plumbing"
is only half true. A `./gradlew test` runner with the same `_clip`-ed output contract unblocks Java
Gradle projects now and Kotlin/JVM codegen later. **Exit:** the existing Java greenfield test passes
on a Gradle scaffold; not Android.

### 9.4 Reverse-DNS area grouping in `state`
`_area` groups module names by their first two segments, so every Android/Java/C# module in
`com.google.samples.apps.nowinandroid.*` becomes one area, `com.google`. Strip the longest package
prefix shared by every first-party module before grouping. Improves Java and C# today, Kotlin
tomorrow. **Exit:** the validation repo renders `core.data`, `feature.foryou`, … as areas; a
`state` snapshot test on a reverse-DNS fixture.

### 9.5 A language-track template
`docs/specs/templates/language-track.md` — the section skeleton this document, PHP's and Perl's
share (decisions table, baseline, fact mapping, CALLS rules, phases with the §5 columns, corpus
cases, files, blast radius, generic work), so the next track starts from structure instead of a
copy. **Exit:** the next language roadmap is written from it.

### 9.6 HTTP clients as a cross-language pass
`python_client.py` is Python-only; Retrofit (P3) will be the second client scanner. Generalise the
`PendingCall` collection into `pkg/http_clients.py` with per-language scanners, so `fetch`/`axios`
(TypeScript), `net/http` (Go), and Guzzle (PHP) are each a scanner, not a fork. **Exit:** the Python
and Kotlin scanners share the emit path; one new scanner is under 150 lines.

---

## 10. Deferred (explicitly out of this track)

Codegen (Android or JVM — D13, §9.3 first); Ktor/Spring server routes; Compose navigation graphs as
edges (no vocabulary); Hilt bindings as edges (no vocabulary); Kotlin Multiplatform source sets;
`.kts` build-logic comprehension; local functions as `Function` nodes.

## 11. Risks and gotchas

- **D2 is load-bearing.** A `kt:` prefix later would invalidate every corpus label and split mixed
  repos. Decide before P1's first line.
- **Do not attach extensions to receivers** (D4); it is the one tempting fabrication in Kotlin.
- **`persistence._GRAMMAR_MODULES`** — the PHP omission; the cross-check test now fails without it.
- **Same-package calls across files** are a guess with no backstop (§3.2 row 1): skip, do not chase.
  Recall on the validation repo will show it; record it in `known_gaps`, not in the extractor.
- **The `sql` extra is optional** — Room `@Query` edges must degrade to "not read", never to a guess.
- **Untracked `docs/specs/*.md` changes the spec count**; this file is tracked from its first commit.
- **Never commit `episteme/`.** The validation repository's bank stays in the scratch dir.

## 12. Sequence

```
P0 plan + baseline           ✅ 2026-09-10   this document, branch, census, blast radius
P1 comprehension             → map_repo shows the app; drift < 57
P2 corpus + CALLS + walker   → precision 1.00; call graph available; invention measured
P3 Room + Retrofit           → entities; first mobile consumer in pkg joins
P4 generic work              → roadmap-status check, validate-frontend script, reverse-DNS areas
P5 /review-pr, then the MR   → maintainers review with the same checklist
```
