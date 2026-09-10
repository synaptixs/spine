# Design + Plan: adding Kotlin to the PKG (10th language) — the whole language, on real apps

**Status:** 🟡 **P0 done** (this plan, the branch, the baseline) · P1–P11 not started.
**Branch:** `feat/kotlin-support` off `develop` at `d84e666` (3.33.2). **Opened:** 2026-09-10.
**Scope decided 2026-09-10: complete.** Comprehension, call graph, Room and Retrofit, Compose
navigation and Hilt bindings, Gradle `.kts` modules, Ktor and Spring server routes, Kotlin
Multiplatform, and codegen for Kotlin/JVM and Android — nothing of the language is left for a
follow-on track. **One MR to `develop`** when every phase is done and tested (§5, §12).

**Validation repositories** (ephemeral: shallow clone to a scratch dir, extraction on a `.git`-less
copy, deleted after; names live in `docs/` only, never in `src/` or `tests/`):

| Repository | Exercises |
|---|---|
| [`synaptixs/aiandroid`](https://github.com/synaptixs/aiandroid) — a mirror of Now in Android: 263 `.kt` files, 22,662 lines, 28 Gradle modules, Compose, Hilt, Room, Retrofit | P1–P5, P9 (Android brownfield codegen) — the primary repository |
| [`spring-petclinic/spring-petclinic-kotlin`](https://github.com/spring-petclinic/spring-petclinic-kotlin) | P6 Spring routes; P8 Kotlin/JVM codegen on a Gradle project |
| [`ktorio/ktor-samples`](https://github.com/ktorio/ktor-samples) | P6 Ktor routes |
| [`touchlab/KaMPKit`](https://github.com/touchlab/KaMPKit) | P7 Kotlin Multiplatform source sets, `expect`/`actual` |

> Same track shape as PHP ([php-support-roadmap.md](php-support-roadmap.md)) and Go
> ([go-support-roadmap.md](go-support-roadmap.md)), with two things this track adds on purpose:
> **§8 measures the blast radius from the PKG before each phase touches code**, and **§9 carries
> generic work** — pieces built here that every later language or feature reuses. Two phases
> change Spine beyond one front-end and are called out as such: **P4 adds an edge kind**
> (`PROVIDES`, D15) because dependency injection has no vocabulary today, and **P8 adds the Gradle
> runner** the Java track never had (§9.3).

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
| **D11** | `.kts` Gradle scripts | (a) not registered, (b) parse as Kotlin source, (c) a **dedicated reader** for the Gradle DSL only | **(c), in P5.** 33 build scripts whose "functions" are DSL calls; parsed as Kotlin they would add a phantom component per module. Read as what they are instead: `settings.gradle.kts` `include(":core:data")` declares the **Gradle modules**, and each module's `dependencies { implementation(project(":core:model")) }` declares **module-to-module dependencies** — the architecture of an Android app, which nothing else states. `Module` nodes `gradle:core/data` (own prefix; the id is the module path) and `IMPORTS` between them; `libs.versions.toml` and version catalogs feed the profiler (D12). Every `.kt` file's package module gets a `CONTAINS`-free link to its Gradle module through provenance path only — no fabricated ownership edge. Until P5 the profiler reads them as markers. |
| **D12** | Profiler | `.kt` → `kotlin`; `build.gradle.kts` / `settings.gradle.kts` / `gradle/libs.versions.toml` read as markers; `androidx`/`compose` → framework `android`, `io.ktor` → `ktor`, `springframework` (already) → `spring`; `junit` (already) → `junit` | Today `profile_repo` on the validation repository returns **`languages: []`** — only `build.gradle` (no `.kts`) is read, and `.kt` maps to nothing. |
| **D13** | Codegen | (a) Kotlin/JVM first (P8), then Android brownfield (P9); (b) Android first | **(a).** Neither can reuse the Java track as the expansion roadmap assumed: `MavenTestRunner` is the only JVM runner and aiandroid, like most Kotlin, is Gradle. P8 builds `GradleTestRunner` + `KotlinToolEnvironment` (§9.3 — it unblocks Java Gradle projects too) and a greenfield `kotlin("jvm")` scaffold proven against real `./gradlew test`. P9 adds Android: placement into the right Gradle module, `package` from the module's existing sources, and tests through `./gradlew :module:testDebugUnitTest` — JVM unit tests, **never an emulator**; instrumented tests are the one thing explicitly out (§10). `"kotlin"` enters `SUPPORTED_LANGUAGES` **only in P8**, together with layout/scaffold/testenv/testrunner/prompts/`kotlin-conventions`; until then `--language kotlin` exits 2 rather than scaffolding Python. |
| **D14** | Compose navigation as routes | (a) `Endpoint` with verb `NAV` + `EXPOSES`/`CONSUMES`, (b) a new node kind, (c) nothing | **(a), in P4.** `NavHost { composable("topic/{topicId}") { TopicRoute(…) } }` declares a route and `navController.navigate("topic/$id")` consumes it — the same shape as an HTTP route, with the app as both provider and consumer. `java:endpoint:NAV topic/{topicId}`; `EXPOSES` to the one named composable the lambda calls (none if zero or several — the closure rule); `CONSUMES` from the function containing a **literal** `navigate("…")`; a template string with a segment is kept literal (`topic/{topicId}` ↔ `"topic/$id"` normalise to the same path). `NAV` cannot collide with an HTTP verb in `pkg joins`, so the D2 no-`ANY` rule is respected. No new node kind. |
| **D15** | Hilt / Dagger bindings | (a) a new **`PROVIDES`** edge kind + a consumer in the same phase, (b) reuse `IMPLEMENTS`, (c) nothing | **(a), in P4.** `@Binds fun binds(impl: OfflineRepo): Repo` and `@Provides fun provide(): Repo` say *which* implementation reaches every `Repo` injection site — the fact that answers "what breaks if I change `OfflineRepo`" for a DI codebase, and no existing edge carries it (`IMPLEMENTS` is already true of `OfflineRepo`, so (b) would be lossy and wrong for `@Provides`). `EdgeKind.PROVIDES`: provider → the provided `Type`; `@Inject constructor` parameters and `@Inject` fields become `REFERENCES`-free **injection sites** read by `blast_radius`, which in the same phase learns to follow `PROVIDES` from a type to its injection sites. The closed enum grows by one member with a consumer that reads it — the `facts.py` rule ("grow as needed"), honoured the way `INTENT`/`SERVES` did. `KNOWLEDGE_GRAPH.md` matrices, `corpus/README.md` and the capability matrix change with it. |
| **D16** | Ktor and Spring server routes | Ktor `routing { get("/x") { } }` → `Endpoint` (closure → no `EXPOSES`; `get("/x", ::handler)` → `EXPOSES`); Spring `@GetMapping`/`@RequestMapping(method=…)` on `@RestController` methods → `Endpoint` + `EXPOSES` with the class-level prefix | **In P6, for Kotlin and Java at once.** The Java front-end reads JAX-RS only; Spring in Java is the same missing reader, so the Spring reader lives in a shared `jvm_routes.py` and both front-ends call it. Verb-less `@RequestMapping` → nothing (the no-`ANY` rule). Ktor route groups (`route("/api") { … }`) compose like Laravel groups; a computed path silences the group (the PHP lesson). |
| **D17** | Kotlin Multiplatform | source sets and `expect`/`actual` | **In P7.** One `Function`/`Type` id per declaration, the package rule unchanged; an `actual` declaration's id carries its source set as a suffix (`java:pkg.platform@androidMain`) because two declarations with one id would collide and lose provenance; `IMPLEMENTS` actual → expect (an `actual` fulfils a contract exactly as a class fulfils an interface). `state` groups by source set. iOS **native** code (`.swift`, Objective-C) stays outside — Kotlin's own declarations for it are inside. |
| **D18** | Second validation repositories | one per shape the primary repository lacks | aiandroid has no Ktor, Spring, or KMP code, so P6 and P7 validate on the repositories in the header. Each is used exactly as the primary: ephemeral, `.git`-less copy, numbers pasted into §5. |
| **D19** | One MR, opened as a **draft** at the end of P1 | (a) draft MR early, merge once at the end; (b) MR only at the very end; (c) an MR per phase | **(a).** The decision is one merge, not one MR per phase; a draft that exists from P1 lets maintainers watch a 45–60 day branch and comment on the phase they know, without merging anything. It is converted from draft only when §5 is entirely DONE and `/review-pr` says mergeable. |

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
reconcile with a `.sql` schema through `data_layer_link` with no Kotlin-specific code; P4 →
`blast_radius` on a repository class reaches every screen that injects its interface, and the
in-app navigation graph is queryable; P5 → `state`'s architecture layers come from the Gradle
module graph instead of package-name heuristics; P6 → a Kotlin service is a **provider** in
`pkg joins`; P7 → a KMP repo renders per source set; P8/P9 → `sdlc feature --language kotlin`
generates, builds and tests code, greenfield and into the validation app.

### 3.5 The added scopes — fact mapping

| Scope | Source shape | Fact | Precision rule |
|---|---|---|---|
| **Compose navigation** (P4, D14) | `composable("topic/{topicId}") { TopicRoute(…) }` inside `NavHost`; `navigate("topic/$id")`, `navigate(Screen.Topic.route)` | `Endpoint` `java:endpoint:NAV topic/{topicId}` + `EXPOSES` → the single named composable called in the lambda; `CONSUMES` from the enclosing function to the route | literal route string, or a `const val route = "…"` resolved in the same file/imports; a computed route → nothing; a lambda calling several composables → `Endpoint` without `EXPOSES` |
| **Hilt bindings** (P4, D15) | `@Binds fun bind(impl: OfflineRepo): Repo`; `@Provides fun provide(...): Repo`; `@Inject constructor(private val repo: Repo)`; `@HiltViewModel`; `@Module @InstallIn(...)` | `PROVIDES` provider `Function` → provided `Type`; the provided type is resolved by the return type; injection sites are the typed constructor/field `Field`s that already exist (D6), joined to the provided type by their declared type | a `@Provides` whose return type is a generic or a type alias resolves only when the alias/type is declared in scope; qualifiers (`@Named`, custom qualifier annotations) are recorded on the edge's provenance only — two providers for one type with different qualifiers both get an edge, and `blast_radius` says "2 providers", never picks one |
| **Gradle modules** (P5, D11) | `settings.gradle.kts` `include(":core:data", ":feature:foryou")`; `build.gradle.kts` `dependencies { implementation(project(":core:model")); api(project(...)); testImplementation(...) }`; `plugins { alias(libs.plugins.nowinandroid.android.library) }` | `Module` `gradle:core/data` per included module; `IMPORTS` module → module for `implementation`/`api`/`compileOnly`/`runtimeOnly` on a literal `project(":…")`; the plugin alias names feed the profiler's framework detection (`android.application` → framework `android`) | only literal `project(":…")` strings; `libs.x` library coordinates are third-party and produce no node; a `project()` call with a variable → nothing; test configurations produce edges tagged in provenance so `state`'s test-coverage section can read them |
| **Ktor routes** (P6, D16) | `routing { route("/api") { get("/topics") { … } } }`; `get("/x", ::handler)`; `install(...)` ignored | `Endpoint` `java:endpoint:GET /api/topics`; `EXPOSES` only for a function reference or a single named call | computed path or group → nothing inside it |
| **Spring routes** (P6, D16) | `@RestController @RequestMapping("/api") class C { @GetMapping("/topics") fun list() }`, `@PostMapping`, `@RequestMapping(method = [RequestMethod.GET])` | `Endpoint` + `EXPOSES` → the method; read by `jvm_routes.py` for `.kt` and `.java` alike | verb-less `@RequestMapping` → nothing; a class prefix that is not a literal silences the class |
| **KMP** (P7, D17) | `src/commonMain`, `src/androidMain`, `src/iosMain`, `src/jvmMain`; `expect fun platform(): String` / `actual fun platform(): String` | the `expect` gets the plain id; each `actual` gets `@<sourceSet>`; `IMPLEMENTS` actual → expect; `Module` provenance records the source set | an `actual` with no matching `expect` in the tree gets no `IMPLEMENTS` (external placeholder for the expect, honestly) |
| **Kotlin/JVM codegen** (P8, D13) | greenfield: `settings.gradle.kts`, `build.gradle.kts` with `kotlin("jvm")` + `kotlin("test")`, `src/main/kotlin`, `src/test/kotlin`; brownfield: place into the existing module's package | `GradleTestRunner` (`./gradlew test --console=plain`, wrapper if present, else `gradle`), `KotlinToolEnvironment`, `kotlin-conventions` skill, prompts | the runner runs the module that owns the changed files (the Go 4.5 lesson); a repo with no wrapper and no `gradle` on PATH is a `FeatureRunError` with the hint, never a silent pass |
| **Android codegen** (P9, D13) | brownfield into aiandroid: choose the Gradle module by the target package, honour its existing `package`, add a Compose screen or a repository function with a JVM unit test | `./gradlew :module:testDebugUnitTest`; `android_toolchain_available()` checks `ANDROID_HOME`/`sdkmanager` and the wrapper | never an emulator or instrumented test; a generated screen gets a JVM-testable ViewModel/repository slice, and the UI test is left as an explicit TODO in the build document — said, not hidden |

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
| **P4 Compose navigation + Hilt** (D14, D15) | `kotlin_nav.py` (routes as `NAV` endpoints, `EXPOSES`/`CONSUMES`); `EdgeKind.PROVIDES` in `facts.py` + `kotlin_di.py`; `blast_radius` follows `PROVIDES` to injection sites; `KNOWLEDGE_GRAPH.md` matrices; corpus `compose_nav`, `hilt_bindings` | 4–6 d | on aiandroid: a `NAV` endpoint per `composable(...)` route with a literal string; `blast_radius` on `OfflineTopicsRepository` lists the screens that inject `TopicsRepository`; precision 1.00 | ⬜ | | | |
| **P5 Gradle modules** (D11) | `gradle_extractor.py` for `settings.gradle.kts` + `build.gradle.kts` (`gradle:` modules, `IMPORTS` from `project(":…")`); profiler reads version catalogs; `state` architecture layers use the module graph; corpus `gradle_modules` | 3–4 d | 28 `gradle:` modules on aiandroid with the `core/` ← `feature/` dependency direction visible in `state`; zero edges from `libs.x` coordinates | ⬜ | | | |
| **P6 Ktor + Spring routes** (D16) | `jvm_routes.py` shared by the Kotlin and Java front-ends; Ktor DSL reader; corpus `ktor_routes`, `spring_routes` (Kotlin and Java) | 3–5 d | `Endpoint`s on the Spring and Ktor validation repos; a Kotlin service joins as a **provider** in `pkg joins`; Java Spring repos gain endpoints too | ⬜ | | | |
| **P7 Kotlin Multiplatform** (D17) | source-set aware module naming, `expect`/`actual` ids and `IMPLEMENTS`; `state` per source set; corpus `kmp_expect_actual` | 2–4 d | KaMPKit renders per source set; every `actual` has an `IMPLEMENTS` to its `expect`; no id collisions (`pkg verify` clean) | ⬜ | | | |
| **P8 Kotlin/JVM codegen** (D13, §9.3) | `GradleTestRunner`, `KotlinToolEnvironment`, `kotlin_toolchain_available`; layout/scaffold for `kotlin("jvm")`; prompts + `kotlin-conventions`; `"kotlin"` into `SUPPORTED_LANGUAGES`; preflight via `./gradlew check` when `ktlint`/`detekt` are configured | 5–7 d | greenfield `sdlc feature --language kotlin` → real `./gradlew test` green **and** red proven (the Go 4.2 pair); brownfield on the Spring validation repo green, independently re-run | ⬜ | | | |
| **P9 Android codegen** (D13) | module placement by package, existing-`package` matching, `testDebugUnitTest` runner, `android_toolchain_available`, Android guidance in the prompts | 5–8 d | brownfield on aiandroid: a repository function + JVM unit test placed in the right `core/` module, `./gradlew :core:data:testDebugUnitTest` **genuinely green, independently verified**; grounding uses the P1–P5 graph | ⬜ | | | |
| **P10 Generic work** (§9.1, §9.2, §9.4) | `scripts/roadmap-status.py --check`; `scripts/validate-frontend.py`; reverse-DNS area grouping | 3–4 d | each item's own exit in §9; this table passes its own check | ⬜ | | | |
| **P11 Review + MR** (D19) | `/review-pr` on the branch (self-review with the same checklist a maintainer will run); fix; convert the draft MR to ready with the phase table and every validation number as its body | 1–2 d | verdict "mergeable"; the review's docs-audit table shows every §7.1 row updated; full suite with CI's extras green; no `episteme/` in the diff | ⬜ | | | |

Rough total: **45–60 days**, one engineer familiar with the PKG. **Delivery is one merge** (D19):
the branch carries every phase, a draft MR exists from P1 for visibility, and it is converted to
ready only when the whole table is DONE with evidence. Phase order is fixed: P8 depends on P5's
module graph for placement, P9 on P8's runner, P6 on P1's Java-shared helpers.

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
| `compose_nav` (P4) | a `NavHost` with a literal route, a `const val` route, a computed route, a lambda calling two composables | two `NAV` endpoints, one with `EXPOSES`; the computed one yields nothing; `CONSUMES` from a `navigate("…")` literal |
| `hilt_bindings` (P4) | `@Binds`, `@Provides`, two providers with qualifiers, an `@Inject constructor` site | `PROVIDES` edges; both qualified providers kept; `blast_radius` reaches the injection site |
| `gradle_modules` (P5) | three modules, `implementation(project(":a"))`, `api(project(":b"))`, a `libs.x` coordinate, a `project(variable)` | two `IMPORTS`; nothing from the coordinate or the variable |
| `ktor_routes`, `spring_routes` (P6) | Ktor group + closure + function reference; Spring class prefix + verb-less mapping (Kotlin **and** Java fixtures) | `Endpoint`s with the composed prefix; closure without `EXPOSES`; verb-less → nothing |
| `kmp_expect_actual` (P7) | `commonMain` `expect`, `androidMain` + `jvmMain` `actual`s, one `actual` without an `expect` | two `IMPLEMENTS`; the orphan `actual` has an external `expect` placeholder |

Codegen phases (P8, P9) are proven the Go way — a real toolchain, green **and** red — in
`tests/sdlc/test_kotlin_integration.py`, gated on `kotlin_toolchain_available()`, not in the corpus.

---

## 7. Files to change

**New:** `src/orchestrator/pkg/kotlin_extractor.py`, `kotlin_room.py`, `kotlin_http.py`,
`kotlin_nav.py`, `kotlin_di.py`, `gradle_extractor.py`, `jvm_routes.py`, optionally `jvm_names.py`;
`sdlc/kotlin.py` (layout, scaffold, Android placement), `GradleTestRunner` + `KotlinToolEnvironment`
in the existing `testrunner.py`/`testenv.py`; `tests/pkg/test_kotlin_*.py`, `test_gradle_extractor.py`,
`test_jvm_routes.py`, `tests/sdlc/test_kotlin_codegen.py`, `test_kotlin_integration.py`;
`corpus/kotlin/*`; `scripts/roadmap-status.py`, `scripts/validate-frontend.py` (§9); this file.

**Modified beyond the front-end (the phases that change Spine itself):** `pkg/facts.py`
(`EdgeKind.PROVIDES`, P4) and every renderer that enumerates edge kinds; `knowledge/current_state.py`
(architecture layers from `gradle:` modules, P5; source sets, P7); `sdlc/feature_runner.py`
(`SUPPORTED_LANGUAGES`, `_resolve_language`, P8), `layout.py`, `scaffold.py`, `codegen.py` prompts,
`catalog/catalog.py` + `skills.py` (`kotlin-conventions`), `preflight.py` (P8); `java_extractor.py`
gains the Spring reader through `jvm_routes.py` (P6).

**Modified (P1):** `pkg/extractor.py`, `pkg/capabilities.py`, `pkg/persistence.py`
(`_GRAMMAR_MODULES` — the PHP omission), `doctor.py`, `catalog/profile.py`, `pkg/scope.py`,
`pkg/docs.py`, `pkg/doc_link.py`, `pyproject.toml` (+ `uv.lock` one line), `.github/workflows/ci.yml`,
`tests/pkg/test_default_extractors.py`, `test_capabilities.py`, `test_verifier.py`, `test_scope.py`,
`tests/catalog/test_profile.py`, `tests/test_doctor.py`; docs: every row the docs matrix names for a
new front-end, `corpus/README.md`, `docs/specs/STATE-OF-SPINE.md`, `SPEC-INDEX.md`,
`language-expansion-roadmap.md`, `assets/spine-architecture.svg` via its script.

**Untouched until their phase:** `sdlc/*` and `SUPPORTED_LANGUAGES` until P8 (D13); the Java
extractor's declaration behaviour throughout (P6 adds a reader it calls, nothing it does changes).

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
| `USER_GUIDE.md` toolchain passages, `CLAUDE_GUIDE.md`/`CODEX_GUIDE.md` toolchain tables, `SETUP.md` | Kotlin/JVM and Android codegen rows: the `gradle` wrapper, `ANDROID_HOME`, `testDebugUnitTest`, and what is never run (emulator) | P8, P9 |
| `KNOWLEDGE_GRAPH.md`, `corpus/README.md`, `docs/specs/PRODUCT-KNOWLEDGE-GRAPH.md`, `assets/spine-architecture.svg` | the `PROVIDES` edge kind: matrices, decided rules, "N node kinds · M edge kinds" | P4 |
| `FEATURES.md`, `README.md` | rows/lines for Compose navigation, Hilt, Gradle modules, Ktor/Spring, KMP, and codegen as each lands | P4–P9 |
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

## 10. Out of scope — and why each is not "deferred"

Everything of the Kotlin language and its mainstream frameworks is in §5. What stays out is not
Kotlin:

- **Instrumented and emulator tests** (`androidTest`, Espresso, Compose UI tests) — they need a
  device; P9 proves codegen with JVM unit tests and states the UI test as an explicit TODO.
- **iOS and desktop native code** reached from KMP (`.swift`, Objective-C, C interop) — other
  languages; Kotlin's own `iosMain` declarations are in P7.
- **Generated sources** (`build/generated`, KSP/kapt output, Hilt's `_Factory` classes) — build
  output, skipped by directory like `obj/` and `bin/`.
- **Local functions** as `Function` nodes — no consumer asks for them; they are walked for calls.
- **Gradle Groovy DSL** (`build.gradle`) beyond the profiler marker — the Kotlin DSL is the
  Android default; add the Groovy reader when a validation repository needs it.

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
P0  plan + baseline            ✅ 2026-09-10   this document, branch, census, blast radius
P1  comprehension              → map_repo shows the app; drift < 57; draft MR opened (D19)
P2  corpus + CALLS + walker    → precision 1.00; call graph available; invention measured
P3  Room + Retrofit            → entities; first mobile consumer in pkg joins
P4  Compose nav + Hilt         → NAV endpoints; PROVIDES edge kind + blast_radius consumer
P5  Gradle modules             → gradle: modules and their dependency graph in state
P6  Ktor + Spring routes       → a Kotlin (and Java Spring) service as a provider
P7  Kotlin Multiplatform       → source sets; expect/actual
P8  Kotlin/JVM codegen         → GradleTestRunner; sdlc feature --language kotlin green + red
P9  Android codegen            → brownfield into the validation app; testDebugUnitTest green
P10 generic work               → roadmap-status check, validate-frontend script, reverse-DNS areas
P11 /review-pr, MR to ready    → one merge into develop
```
