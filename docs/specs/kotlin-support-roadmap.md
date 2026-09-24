# Design + Plan: adding Kotlin to the PKG (10th language) — the whole language, on real apps

**Status:** 🟢 **P0–P11 done** — the whole track (plan + baseline; comprehension; corpus +
`CALLS` + invention walker; Room + Retrofit; Compose navigation + Hilt, incl. the `PROVIDES`
edge kind; Gradle modules; Ktor + Spring routes; Kotlin Multiplatform; Kotlin/JVM codegen;
Android codegen; generic work; review). Ready to merge. Last measured 2026-09-16 at 3.34.2.
**Branch:** `feat/kotlin_support` off `develop` at 3.34.2. **Opened:** 2026-09-10.
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
| **D4** | Top-level and extension functions | (a) `Function` under the package module, id `java:pkg.name`; an extension `fun T.name()` is the same, with the receiver recorded nowhere, (b) attach extensions to the receiver `Type` | **(a).** An extension is not a member — the receiver type does not own it, and attaching it would put a `CONTAINS` edge on a type declared in another module or in the SDK. Calls resolve to it by name (D8 row 5). ~~Two extensions with the same name in one package are a compile error in Kotlin, so the id is unique.~~ **Corrected 2026-09-15 against the validation repo — that claim was false.** The receiver is part of an extension's signature, so same-name extensions in one package are legal and common: `core.data.model` declares `NetworkTopic.asEntity`, `NetworkNewsResource.asEntity` **and** `NetworkNewsResourceExpanded.asEntity`, and `core.database.model` does the same for `asExternalModel`/`asFtsEntity`. The id is therefore **not** unique, and they collapse onto one node exactly as overloads do — which is the documented Java behaviour (`java_extractor.py`: "overloads collapse onto one id"), so the *decision* stands and only its justification changes. Measured cost on aiandroid: 9 of 738 function declarations collapse this way. |
| **D5** | `object` and `companion object` | (a) `object X` → `Type`; companion members fold into the enclosing class (`A.h()` → `java:pkg.A.h`), (b) a nested `Type` `A.Companion` | **(a).** Call sites name the class, never `Companion`; folding is how the graph will be read. A companion's own name, when given, is recorded in `name` only. 43 objects and 7 companions in the validation repo. |
| **D6** | `Field` | (a) `val`/`var` properties in a class body **and** `val`/`var` primary-constructor parameters, (b) body properties only | **(a).** In Kotlin a constructor `val` *is* a property, and in DI-heavy code (`class Repo @Inject constructor(private val dao: TopicDao)`) it is the typed receiver every call in the class goes through (D8 row 6). A bare constructor parameter without `val`/`var` is not a `Field`. Top-level `val`s are not `Field`s (corpus rule: a `Field` belongs to a `Type`). 799 properties in the validation repo. |
| **D7** | Inheritance | `delegation_specifier` → `IMPLEMENTS`, resolved by the Java rule (import → same package → external) | Kotlin does not distinguish `extends` from `implements` syntactically; neither does the edge. A guessed same-package target that nothing declares is repointed in `finalize`, the C#/PHP pattern. |
| **D8** | CALLS — see §3.2 | precision-first, seven shapes, no type inference | The one **net-new** rule: **typed receivers are the norm, not the exception** — Kotlin declares the type of every property and parameter, so `dao.getTopics()` resolves exactly through the import map. This is P2, not a later phase, because it is most of the call graph in this style of code. |
| **D9** | Invention oracle (`pkg/scope.py`) | (a) a `_Kotlin` walker, (b) `NOT_APPLICABLE` | **(a).** Unlike Java, a Kotlin local *can* shadow a call: `val helper = ::other; helper()` invokes the local through `invoke`. The TypeScript walker is the template (scope nodes: `function_declaration`, `lambda_literal`, `anonymous_function`; bindings: `property_declaration` / `variable_declaration` and parameters). A `shadowed_calls` corpus case pins it. |
| **D10** | Framework edges — an Android app is a **client** | Retrofit `@GET("topics")` → a **`CONSUMES` candidate**, not an `Endpoint`; Room `@Entity` → `Entity`; Ktor and Spring **server** routes are P6 (D16), where a Kotlin service is the provider | Nothing in an Android app *exposes* a route; it calls one. `python_client.py`'s `PendingCall` side-channel exists for exactly this — an unmatched call is a **cross-repo join candidate**, and `pkg joins` matches it against a provider's `Endpoint`s by verb and path. This makes a Kotlin app the first mobile **consumer** in the multi-repo join, which no other front-end can be today. Path from the annotation literal; base URL from a literal `baseUrl("…")` when present, else path-only. |
| **D11** | `.kts` Gradle scripts | (a) not registered, (b) parse as Kotlin source, (c) a **dedicated reader** for the Gradle DSL only | **(c), in P5.** 33 build scripts whose "functions" are DSL calls; parsed as Kotlin they would add a phantom component per module. Read as what they are instead: `settings.gradle.kts` `include(":core:data")` declares the **Gradle modules**, and each module's `dependencies { implementation(project(":core:model")) }` declares **module-to-module dependencies** — the architecture of an Android app, which nothing else states. `Module` nodes `gradle:core/data` (own prefix; the id is the module path) and `IMPORTS` between them; `libs.versions.toml` and version catalogs feed the profiler (D12). Every `.kt` file's package module gets a `CONTAINS`-free link to its Gradle module through provenance path only — no fabricated ownership edge. Until P5 the profiler reads them as markers. |
| **D12** | Profiler | `.kt` → `kotlin`; `build.gradle.kts` / `settings.gradle.kts` / `gradle/libs.versions.toml` read as markers; `androidx`/`compose` → framework `android`, `io.ktor` → `ktor`, `springframework` (already) → `spring`; `junit` (already) → `junit` | Today `profile_repo` on the validation repository returns **`languages: []`** — only `build.gradle` (no `.kts`) is read, and `.kt` maps to nothing. |
| **D13** | Codegen | (a) Kotlin/JVM first (P8), then Android brownfield (P9); (b) Android first | **(a).** Neither can reuse the Java track as the expansion roadmap assumed: `MavenTestRunner` is the only JVM runner and aiandroid, like most Kotlin, is Gradle. P8 builds `GradleTestRunner` + `KotlinToolEnvironment` (§9.3 — it unblocks Java Gradle projects too) and a greenfield `kotlin("jvm")` scaffold proven against real `./gradlew test`. P9 adds Android: placement into the right Gradle module, `package` from the module's existing sources, and tests through the module's own JVM unit-test task — `testDebugUnitTest`, or the flavoured variant Gradle names when that one is ambiguous (§11) — **never an emulator**; instrumented tests are the one thing explicitly out (§10). `"kotlin"` enters `SUPPORTED_LANGUAGES` **only in P8**, together with layout/scaffold/testenv/testrunner/prompts/`kotlin-conventions`; until then `--language kotlin` exits 2 rather than scaffolding Python. |
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
| `x.ext()` where `ext` is a same-file, same-package or imported **extension function** | by name — an extension name is unique in scope. A same-package one needs no import (#397), and resolves only when the receiver in scope is compatible and no supertype outside the repository, nor `Any`, could declare a member of that name | `CALLS` → `java:pkg.ext` |
| `prop.foo()` / `param.foo()` where the receiver has a **declared type** in scope | receiver type through the import map / same package | `CALLS` → `java:pkg.RecvType.foo` — placeholder if third-party. **The main event** (D8): `private val dao: TopicDao` + `dao.getTopics()` is the dominant shape in the validation repo. `?.` safe calls resolve the same way. **Two exceptions when the receiver is third-party** (#389): a scope function by name *and* shape is refused (row below), and when the file imports an extension under the called name, the placeholder is that import rather than `RecvType.foo` — `m.padding(8)` with `import androidx.compose.foundation.layout.padding` lands on the extension, because `Modifier` does not declare `padding` |
| **Scope functions given a function** — `let`, `run`, `also`, `apply`, `takeIf`, `takeUnless`, `use`, `runCatching` (`_SCOPE_FUNCTIONS`), written `x.let { }`, `x.let({ … })` or `x.let(::f)` | — | never — a `kotlin.*` extension is not a member of its receiver. **Name and shape both**, since `run`/`apply`/`use` are also genuine members: `r.run()` on a `Runnable` and `d.with(adj)` on a `LocalDate` must still land (#389). Kotlin's own `with(x) { }` is a top-level function and never reaches this row |
| `it.x()`, `map { … }` on an inferred receiver, callable references `::foo`, `invoke` on a lambda, calls on an unannotated `val x = something()` | — | never — inference or fabrication |

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
| **Android codegen** (P9, D13) | brownfield into aiandroid: choose the Gradle module by the target package, honour its existing `package`, add a Compose screen or a repository function with a JVM unit test | the module's real unit-test task, resolved from Gradle when `testDebugUnitTest` is ambiguous (§11); `android_toolchain_available()` checks `ANDROID_HOME`/`sdkmanager`, and `kotlin_project_error` the wrapper | never an emulator or instrumented test; a generated screen gets a JVM-testable ViewModel/repository slice, and the UI test is left as an explicit TODO in the build document — said, not hidden |

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
| **P1 Comprehension** | `kotlin_extractor.py` (§3.1, D1–D7, D11); `jvm_names.py` leaf if Java helpers are shared; registration: `default_extractors`, `FRONT_ENDS`, `EXTRA_PROBES`, `_GRAMMAR_MODULES`, profiler (D12), `scope.py` placeholder until P2, `docs.py`/`doc_link.py` `kt`; packaging (`kotlin` extra, `languages` meta-extra, mypy override, `ci.yml`); tests per [docs/reviewing/language-frontend-checklist.md](../reviewing/language-frontend-checklist.md); docs per [docs/reviewing/docs-matrix.md](../reviewing/docs-matrix.md) | 4–6 d | `map_repo` on the validation repo: modules/types/functions/fields > 0 with the census as the ceiling (232 / 43+ / 738 / 799); `pkg verify` 0 errors; drift claims below 57; **every P1 row of §7.1 updated** and `scripts/docs_audit.py` reports no STALE/MISSING; gate green with `--extra kotlin` | ✅ DONE | 2026-09-15 | 2026-09-15 | `map_repo` on aiandroid: **85 modules · 271 types · 713 functions · 488 fields** (census ceiling 232+43 types / 738 fns / 799 props — see the gap analysis below); `pkg verify` **0 errors**; drift **57 → 25**; `tests/pkg/test_kotlin_extractor.py` 19 P1 tests; capability matrix regenerated |
| **P2 Corpus + CALLS + invention walker** | `corpus/kotlin/{plain,typed_receivers,extensions,companions,shadowed_calls,mixed_java}` labelled from source first; §3.2 rows; `_Kotlin` walker (D9); `--scoreboard` | 4–5 d | precision 1.00 on every kind; `invention` `MEASURED` with 0; `state` "Call graph: available"; `blast_radius` on a validation-repo repository class lists its DI callers | ✅ DONE | 2026-09-15 | 2026-09-15 | `pkg accuracy --language kotlin`: **precision 1.00 on every node and edge kind**, 6 cases, CALLS recall 0.83 (both misses are labelled `known_gaps`); `invention` **`measured`, 0 invented across 2,167 bare calls**; `state` → "Call graph: available"; **2,169 CALLS** on aiandroid, `OfflineFirstTopicsRepository.getTopics → TopicDao.getTopicEntities` through a constructor property |
| **P3 Room + Retrofit** | `kotlin_room.py` (Entity/Field/REFERENCES; DAO READS/WRITES via sqlglot when present), `kotlin_http.py` (Retrofit `CONSUMES` candidates through the `PendingCall` side-channel); corpus `room`, `retrofit_consumer`; a two-repo `pkg joins` fixture with a tiny provider | 3–5 d | 6 entities on the validation repo; `pkg joins` proposes the consumer→provider join; `data_layer_link` reconciles against a `.sql` schema | ✅ DONE | 2026-09-16 | 2026-09-16 | **6 entities** on aiandroid (all grounded, named by `tableName`), 12 `READS` / 11 `WRITES` / 4 `REFERENCES`, **0 dangling**; `pkg verify` 0 errors; corpus `room` + `retrofit_consumer` **1.00 precision and recall on every kind**; `multirepo/http_join_kotlin_consumer` joins a Kotlin app to a Java JAX-RS provider (`CONSUMES` 1.00/1.00); `data_layer_link` merges Room entities onto `sql:` tables (`tests/pkg/test_kotlin_room.py`); 4 Retrofit candidates on aiandroid |
| **P4 Compose navigation + Hilt** (D14, D15) | `kotlin_nav.py` (routes as `NAV` endpoints, `EXPOSES`/`CONSUMES`); `EdgeKind.PROVIDES` in `facts.py` + `kotlin_di.py`; `blast_radius` follows `PROVIDES` to injection sites; `KNOWLEDGE_GRAPH.md` matrices; corpus `compose_nav`, `hilt_bindings` | 4–6 d | on aiandroid: a `NAV` endpoint per `composable(...)` route with a literal string; `blast_radius` on `OfflineTopicsRepository` lists the screens that inject `TopicsRepository`; precision 1.00 | ✅ DONE | 2026-09-16 | 2026-09-16 | **8 `NAV` endpoints** on aiandroid (incl. `NAV topic_route/{topicId}`, the `{$topicIdArg}` constant resolved per D14), **5 `EXPOSES`**, **4 `CONSUMES`**, **47 `PROVIDES`**; `pkg verify` 0 errors; `blast_radius(OfflineFirstTopicsRepository)` → `TopicsRepository` → `GetFollowableTopicsUseCase.invoke` + `topic.topicUiState` (**empty before this phase**); corpus `compose_nav` + `hilt_bindings` **1.00 precision and recall on every kind**; `EdgeKind.PROVIDES` + `FactStore.injection_reach_of`; 16 tests in `test_kotlin_nav.py` / `test_kotlin_di.py`; matrices, SVG and vocabulary counts regenerated (11→12 edge kinds) |
| **P5 Gradle modules** (D11) | `gradle_extractor.py` for `settings.gradle.kts` + `build.gradle.kts` (`gradle:` modules, `IMPORTS` from `project(":…")`); profiler reads version catalogs; `state` architecture layers use the module graph; corpus `gradle_modules` | 3–4 d | 28 `gradle:` modules on aiandroid with the `core/` ← `feature/` dependency direction visible in `state`; zero edges from `libs.x` coordinates | ✅ DONE | 2026-09-16 | 2026-09-16 | **29 `gradle:` modules** (27 `include(...)` + the root + `build-logic/convention`; the plan's “28” was an estimate) and **72 module→module `IMPORTS`**, **zero** from `libs.*`; `pkg verify` 0 errors; `state`'s components go from a single `com.google` area to the real build — `core/data`, `core/database`, `feature/foryou`, `sync/work` … — and its architecture arrows are the Gradle graph (`app → core/data`, `sync/work → core/testing`); corpus `gradle_modules` **1.00 precision and recall**; 9 tests in `test_gradle_extractor.py` + a `state` area test. **Direction caveat in the evidence, not hidden:** no `feature → core` edge exists because all six feature modules take those deps from a `build-logic` convention plugin (`.kt`), not from their own `.kts` — see §11 |
| **P6 Ktor + Spring routes** (D16) | `jvm_routes.py` shared by the Kotlin and Java front-ends; Ktor DSL reader; corpus `ktor_routes`, `spring_routes` (Kotlin and Java) | 3–5 d | `Endpoint`s on the Spring and Ktor validation repos; a Kotlin service joins as a **provider** in `pkg joins`; Java Spring repos gain endpoints too | ✅ DONE | 2026-09-16 | 2026-09-16 | spring-petclinic-kotlin: **18 endpoints · 18 `EXPOSES`** — all 18 mapping annotations in the repository, 0 missed, 0 invented. ktor-samples (151 files): **67 endpoints**, every provenance line audited to be a real route call. `jvm_routes.py` shared by both JVM front-ends, so **Java gained Spring** (it read JAX-RS only). Corpus `kotlin/ktor_routes`, `kotlin/spring_routes`, `java/spring_routes` and `multirepo/http_join_kotlin_provider` — **1.00 precision and recall on every kind**, 0 missing and 0 unlabelled; 63 cases total at P6 (64 with P7's). `tests/pkg/test_jvm_routes.py` (25) + `tests/pkg/test_kotlin_routes.py` (23). aiandroid unchanged (8 `NAV`, 5 `EXPOSES`) — no false positives from the new readers. |
| **P7 Kotlin Multiplatform** (D17) | source-set aware module naming, `expect`/`actual` ids and `IMPLEMENTS`; `state` per source set; corpus `kmp_expect_actual` | 2–4 d | KaMPKit renders per source set; every `actual` has an `IMPLEMENTS` to its `expect`; no id collisions (`pkg verify` clean) | ✅ DONE | 2026-09-16 | 2026-09-16 | KaMPKit (34 `.kt`, 6 source sets): `state` components **2 → 7** (`shared/commonMain`, `shared/androidMain`, `shared/iosMain` + their test sets, beside `app`); **7 source-set-suffixed declarations**; **4 of 4** representable `actual`s carry an `IMPLEMENTS` to their `expect`; `pkg verify` **0 issues**. aiandroid unchanged at 28 components — an ordinary Android module is not split. Corpus `kotlin/kmp_expect_actual` **1.00 on every kind**, 0 missing / 0 unlabelled, 2 declared false positives. `tests/pkg/test_kotlin_kmp.py` (16). |
| **P8 Kotlin/JVM codegen** (D13, §9.3) | `GradleTestRunner`, `KotlinToolEnvironment`, `kotlin_toolchain_available`; layout/scaffold for `kotlin("jvm")`; prompts + `kotlin-conventions`; `"kotlin"` into `SUPPORTED_LANGUAGES`; preflight via `./gradlew check` when `ktlint`/`detekt` are configured | 5–7 d | greenfield `sdlc feature --language kotlin` → real `./gradlew test` green **and** red proven (the Go 4.2 pair); brownfield on the Spring validation repo green, independently re-run | ✅ DONE | 2026-09-16 | 2026-09-16 | Real Gradle 8.13 / Kotlin 2.0.21. **Greenfield:** scaffold → `gradle test` **BUILD SUCCESSFUL**; assertion broken → **BUILD FAILED** naming the test (the Go 4.2 pair). **Brownfield:** `VisitFee` + test placed into spring-petclinic-kotlin's existing package → **3 tests, 0 failures, 0 errors**, re-run green with `--rerun-tasks`. `GradleTestRunner` verified red (`rc=1`), green (`rc=0`), wrapper-preferred on KaMPKit, and loud-failure with neither wrapper nor `gradle`. `GradlePreflightRunner` skips without a linter and finds ktlint in KaMPKit / nothing in petclinic or aiandroid. `tests/sdlc/test_kotlin_codegen.py` (26); `tests/sdlc` + `catalog` + `personas` **1,115 passed, 0 failed**. |
| **P9 Android codegen** (D13) | `android.py` (module placement by package, Android-module detection, SDK probe); variant-aware unit-test task in `GradleTestRunner`; `android_toolchain_available` + `kotlin_project_error`; `TargetLayout.module`/`.android`; Android guidance in the prompts | 5–8 d | brownfield on aiandroid: a repository function + JVM unit test placed in the right `core/` module, the module's real unit-test task **genuinely green, independently verified**; grounding uses the P1–P5 graph | ✅ DONE | 2026-09-16 | 2026-09-16 | Real Gradle 8.1 / AGP 8.1.0-beta01 / JDK 17 / Android SDK platform 33. **The exit criterion as first written names a task that does not exist** — `:core:data:testDebugUnitTest` is *ambiguous* in aiandroid, because `AndroidLibraryConventionPlugin` calls `configureFlavors`, so **every** library module is flavoured and the real tasks are `testDemoDebugUnitTest` / `testProdDebugUnitTest`. Measured, not assumed (§11). **Placement:** 27 modules, `build-logic` correctly excluded as an included build; `…core.data` → `core/data`, the *new* sub-package `…core.data.pricing` → `core/data` under that module's own `src/main/java`, `…core.model.data` → `core/model` with the plain `test` task (a Kotlin/JVM module inside an Android build); an unrelated package resolves to **nothing** and the run stops with the module list rather than guessing. Classification checked against **all 27** modules: 25 Android, 2 plain JVM (`core/model`, `lint`) — and `lint` is the case that vindicates detecting an `android { }` block rather than a plugin id, since it applies `com.android.lint`, which is the *standalone* Lint plugin on a JVM library and produces no variants. **Brownfield:** `syncBackoffMillis` + `SyncBackoffTest` placed into `core/data`'s existing package → **4 tests, 0 failures, 0 errors**; independently re-run with `--rerun-tasks` (**197 tasks executed**, nothing from cache) → **BUILD SUCCESSFUL**. **Red:** assertion broken → `passed=False`, `rc=1`, naming `SyncBackoffTest > each retry doubles the wait FAILED` at `SyncBackoffTest.kt:17` (the Go 4.2 pair). The runner found the module from `git status`, asked for `testDebugUnitTest`, recovered from Gradle's own candidate list, and cached the answer for later refine iterations. `detect_jvm_test_library` now reads `build-logic/` convention plugins — aiandroid declares `kotlin("test")` *only* there, so before this it was right by luck. **Mixed build proven too:** a second feature placed into `core/model` — a plain Kotlin/JVM module in the same Android repo — ran as `:core:model:test` while `core/data` ran as `:core:data:testDemoDebugUnitTest` in the *same* invocation (**3 tests, 0 failures**, independently re-run with `--rerun-tasks`). That second module is what exposed the per-module test-library finding (§11) and a `git status` flag: git collapses a wholly-new untracked directory, so a generated test that is the first file in a new directory was invisible to module attribution until `-uall`. `tests/sdlc/test_android_codegen.py` (43). |
| **P10 Generic work** (§9.1, §9.2, §9.4) | `scripts/roadmap-status.py --check` (two new checks + CI); `scripts/validate-frontend.py` proven on Kotlin; reverse-DNS area grouping; the dead `Toolchain.conventions_skill_id` removed | 3–4 d | each item's own exit in §9; this table passes its own check | ✅ DONE | 2026-09-16 | 2026-09-16 | **§9.1** — the gate existed but was missing the check §9.1 names, and this roadmap proved why: its own **Status** line read *“P0–P5 done · P6–P11 not started”* for three phases while the table below it showed P6–P8 DONE with evidence, and the gate stayed green. Two checks added — the header's phase claim against its own table's DONE rows, and the spec's claim against `SPEC-INDEX.md`'s — both structured on *both* sides, which is what separates them from the prose classification withdrawn at 33% precision. A third defect surfaced doing it: `_TOP_STATUS` matched `(.+?)\.` on one line, so a **wrapped** status line matched nothing at all — and every roadmap here wraps its own, which made every check reading it a silent no-op. Now 9 checks, 3 tables, **and wired into CI** (it was not). **§9.2** — `validate-frontend.py` existed and is now *demonstrated* on Kotlin rather than assumed: 1,635 grounded nodes, node counts split `kotlin` / `gradle`, `pkg verify` OK, the `state` stack line, and the top-10 unresolved imports. **§9.4** — reverse-DNS grouping: on the validation app **258 of 273 first-party types sat in one area called `com.google`**; they now render as **39 areas** — `core.data` (40), `core.database`, `feature.topic` … — while this repository is measurably **unchanged** (its deepest majority prefix is `orchestrator` at 48.1%, under the threshold, because `orchestrator.pkg` already *is* the area). The spec's literal rule — the prefix shared by *every* module — does not survive the validation repo: one first-party module declaring `package androidx.test.uiautomator` drags the common prefix of all 71 to nothing, so the rule is coverage-based, with the threshold placed in the measured gap (94.4% → 57.7%). 9 tests in `tests/knowledge/test_areas_reverse_dns.py`, 9 more in `tests/test_roadmap_status.py`. |
| **P11 Review + MR** (D19) | self-review against `docs/reviewing/language-frontend-checklist.md` — the checklist a maintainer runs; fix; MR body carrying the phase table and every validation number | 1–2 d | verdict "mergeable"; every §7.1 row updated; full suite with CI's extras green; no `episteme/` in the diff | ✅ DONE | 2026-09-16 | 2026-09-17 | **Maintainer review returned _not mergeable_ on 2026-09-17 and is the record that matters here — the self-review missed the class of defect that mattered most.** Eleven fabrication paths, two self-agreeing corpus cases, an area-grouping invariant break and two codegen holes; all fixed, all recorded individually in §11. The self-review's own findings stand below, but its verdict did not survive contact with a second reader: it checked every *registration* site and no *refusal*, and every fabrication it missed was hidden behind the same mechanism — a guessed id minted as an `external` placeholder, which makes `pkg verify` report clean and the D9 oracle report nothing. Measured effect of the fixes on the validation app: **72 fabricated edges over 36 invented ids → 0**, 46 invented nodes gone, and 126 true edges *recovered* that the per-file guess had displaced (`CALLS` 2,167 → 2,221); corpus `CALLS` recall **0.92 → 0.94** with precision still 1.00; every front-end, not only Python, now has an invention row gated at zero. **Self-review (2026-09-16), verdict mergeable after 6 findings fixed.** Every registration site checked with `file:line`: extractor, `default_extractors` (gated append), `FRONT_ENDS`, `EXTRA_PROBES`, `_GRAMMAR_MODULES` (the cache key — a warm cache would otherwise serve a Kotlin-less graph forever), `catalog/profile.py`, `scope.WALKERS`, `SUPPORTED_LANGUAGES`, the `Toolchain` row. `import_link` and `insights` need no Kotlin branch — Kotlin rides Java's `java:` prefix, and its visibility modifiers are not stored — and both now **say so** rather than leaving the next reader to infer it. **Findings fixed (7):** the doc binder read `.kt`/`.kts` as symbols (§11, drift 25 → 24); Kotlin was missing from `test_capabilities._FIXTURES` and `test_verifier._SOURCES`, two hand-written rosters that fail open (§11); the `understand-codebase` skill still advertised ten languages — the same class as P9's plugin-manifest miss, and a site the docs matrix names; two undocumented "no branch needed" decisions. **Gates:** `mypy src tests` **0 issues / 742 files** (`strict`), full suite **3,926 passed, 0 failed** with CI's extras, `ruff` clean over 779 files, all five generated-artifact gates green, `mutate-dispatch.py` **8 of 8 caught, 0 skipped**, `validate-frontend.py` green on the validation app, **no `episteme/` in the diff**. |

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
| `scope_functions` (#389) | `m.let { }` / `.run` / `.takeIf` / `.also` on an imported `Modifier` and `r.use { }` on a `BufferedReader`, plus `task.run()` on a `Runnable` as the control | the four scope functions are refused on an imported receiver, where no declared-member list exists to refuse them; the control proves the refusal keys on the call's *shape*, not just the member name |
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
| `USER_GUIDE.md` toolchain passages, `AGENT_GUIDE.md` §10 toolchain table (`CLAUDE_GUIDE.md`/`CODEX_GUIDE.md` are five-line redirects into it, not tables of their own — corrected P9), `SETUP.md` | Kotlin/JVM and Android codegen rows: the `gradle` wrapper, `ANDROID_HOME`, the per-module unit-test task (and that flavours rename it), and what is never run (emulator) | P8, P9 |
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
- **Android product flavours collide on one id, and nothing warns.** *(Found 2026-09-15, P1.)*
  `src/demo/` and `src/prod/` are separate source sets that declare the **same class in the same
  package** — aiandroid does it four times (`AnalyticsModule`, `FlavoredNetworkModule`,
  `NotificationsModule`, `SyncModule`, plus their 5 `@Binds` methods). One id, so `FactBatch`
  dedup keeps one node and the other file's provenance is lost. It resolves *deterministically*
  — the walk is sorted, so `demo` always wins — and it is arguably correct, since only one
  flavour compiles at a time and both are the same fully-qualified name. But the graph points at
  a file the build may not use and says nothing about it. This is the KMP problem in miniature and
  **P7's `@<sourceSet>` id suffix (D17) is the same fix**; until then it is a known, measured,
  9-declaration gap on the primary validation repo, recorded here rather than left to be
  rediscovered.
- **`@ForeignKey` is not an annotation — do not grep for one.** *(Found 2026-09-16, P3.)* §3.3
  writes it as `@ForeignKey(entity = X::class, …)`, and a census for `@ForeignKey` on the
  validation repo returns **0**, which briefly looked like "aiandroid has no foreign keys".
  It has two. Room spells them as *constructor calls inside* `@Entity(foreignKeys = [...])`,
  with no `@`. The same trap applies to `Junction`: it is `associateBy = Junction(...)`, an
  **argument** to `@Relation`, and the grammar parses it as a `call_expression` there while
  parsing the identical text as a `constructor_invocation` in annotation position. A reader
  that handles only the annotation-position spelling silently drops every junction table.
- **`@Relation` lives on a class that is not an `@Entity`.** *(Found 2026-09-16, P3.)* §3.3
  implies the relation annotations sit on entities. In real Room they sit on a *view* class —
  `@Embedded` names the parent entity, the annotated property's element type names the child —
  and that class is a query result shape, not a table, so it gets no `Entity` node of its own.
  Reading relations only from `@Entity` classes finds none of them.
- **The capability matrix cannot tell "reads a kind" from "emits a kind".** *(Found 2026-09-16,
  P3.)* `capabilities._kinds_in` attributes every `NodeKind.X` named by a front-end **or its
  direct delegates**, so importing `python_client.emit` — which mentions `NodeKind.ENDPOINT`
  to *look endpoints up* — made Kotlin claim it could emit `Endpoint` nodes, the exact inverse
  of D10. Worked around by keeping that import one level further out, in `kotlin_http`, where
  the matrix does not inherit it. Kotlin is the first consumer-only front-end, which is why
  nothing hit this before; a second one will hit it again.
- **`FEATURES.md` does not exist.** *(Found 2026-09-16.)* §7.1 assigns it four rows. There is
  no such file in the repository — the user-facing capability table lives in `README.md`, and
  the Kotlin row was added there instead.
- **A trailing lambda wraps the call it decorates.** *(Found 2026-09-16, P4.)*
  `composable(route = r) { Screen() }` is an **outer** `call_expression` whose callee is the
  inner `composable(route = r)`: the arguments live on the inner node and the lambda on the
  outer. Reading the node whose callee is named `composable` therefore finds the route and
  never the screen — measured as 8 endpoints and **0** `EXPOSES` until both halves were read
  from the right node. Every Compose and Kotlin DSL reader after this one hits the same shape.
- **`kotlin` was missing from `ci.yml`.** *(Found 2026-09-16, P4, by `scripts/docs_audit.py`.)*
  P1's row names `ci.yml` and it was still missed, so every Kotlin test `importorskip`-ed in
  CI and the front-end was unverified while the build looked green. This is the registration
  site the checklist did not catch; the audit did.
- **A Gradle module graph read from `.kts` alone is a lower bound.** *(Found 2026-09-16, P5.)*
  A mature build factors shared wiring into `build-logic` precompiled plugins, so a module's
  script says `plugins { id("nowinandroid.android.feature") }` and its dependencies on
  `core:*` live in that plugin's **Kotlin** source. Measured: every `app →` and `sync →`
  edge is visible and **no `feature → core` edge is**, because all six feature modules get
  theirs that way. The module *nodes* are complete; the edge set is not. Closing it means
  resolving a plugin id to the file that registers it and then interpreting Gradle API calls
  in ordinary source — a much less certain job than reading a literal, so it is recorded.
- **A nested `settings.gradle.kts` belongs to another build.** *(Found 2026-09-16, P5.)*
  `includeBuild("build-logic")` composes a *separate* build whose `include(":convention")`
  is relative to itself. Reading it as this repository's produced a phantom top-level
  `convention` module beside the real `build-logic/convention`.
- **§3.5's "test configurations tagged in provenance" is not representable.**
  *(Found 2026-09-16, P5.)* `Provenance` carries file, line, end_line and repo — there is no
  field for a tag, and adding one touches 46 non-test importers. Test-configuration
  dependencies are therefore emitted as ordinary `IMPORTS` (a test dependency is a real
  dependency) and `state` cannot separate them. Recorded rather than faked.
- **D16 names only `@RestController`; the validation repository uses `@Controller`.**
  *(Found 2026-09-16, P6.)* `@RestController` is `@Controller` + `@ResponseBody`; both
  register handler mappings, and every one of spring-petclinic-kotlin's six controllers uses
  the plain form. A reader following D16 literally finds **zero** endpoints there. Both are
  accepted. The *requirement* itself stands and is load-bearing: Spring Cloud OpenFeign puts
  the same mapping annotations on an interface to declare a **client**, so keying off the
  mapping alone turns every consumer into a provider.
- **A Ktor route module's prefix is not lexical, and D16 does not say what to do about it.**
  *(Found 2026-09-16, P6.)* D16 covers `routing { }` and `route(...)` groups, which are one
  tree. Real Ktor splits routes across files as `fun Route.orders()` extensions mounted by
  their caller — 20 of them in the Ktor sample corpus — so a route's path is set somewhere
  else entirely. Mount points are resolved across the whole repository in `finalize`, to a
  fixpoint since a module can mount another; a module nothing mounts yields **nothing**.
  Measured on `httpbin`: **1 endpoint before, 35 after**.
- **A chained call hides the route underneath it.** *(Found 2026-09-16, P6.)*
  `get("/get") { … }.describe { … }` is one call *on* another, so the outer node's callee is a
  `navigation_expression` and the route is in its receiver. Descending only into the outer
  lambda — the obvious reading after P4's trailing-lambda lesson — loses every route in such a
  file. This is the second shape in this track where the node you name is not the node you
  want; the fix is to descend through the whole unrecognised call, not just its lambda.
- **tree-sitter-kotlin 1.1.0 strands a parenthesised top-level annotation.**
  *(Found 2026-09-16, P6.)* `@Resource("/x")` or `@RequestMapping("/api")` above a declaration
  is parsed as a standalone `annotated_expression` — an argument-less `annotation` plus a
  `parenthesized_expression` — and the declaration loses its `modifiers` entirely. Every
  framework reading in this track (Room, Hilt, Retrofit, Spring) asks
  `kotlin_names.annotations_of` for a declaration's annotations, so the failure is silent and
  total: the class reads as a plain class. Measured across the three validation repositories
  (452 `.kt` files): **32 stranded blocks**, including `@AndroidEntryPoint class MainActivity`.
  `annotations_of` now recovers them from the preceding sibling. **1 of the 32** additionally
  swallows the declaration itself into an `infix_expression` (a bare `class X {` with no
  constructor and no supertype) and is unrecoverable without re-parsing — recorded as a gap.
- **`Annotation.arg` falls back to the first positional argument, which is wrong for a
  non-path argument.** *(Found 2026-09-16, P6.)* The fallback is deliberate and right for a
  path (`@GET(value = "x")` and `@GET("x")` are the same thing), but asking it for `method`
  on `@RequestMapping("/any")` returns the *path* — which was minted as an HTTP verb, giving
  an endpoint named `"/topics/anything" /api/topics/anything`. A non-path argument must be
  looked up by name exactly. Any future reader using this helper needs the same care.
- **`scripts/docs_audit.py --strict` does not catch a stale *count*.**
  *(Found 2026-09-16, P6.)* The audit passed green while `EXAMPLE.md` said "47 hand-labelled
  cases covering all 10 of Spine's front-ends" (it is 63 across 12), `BENCHMARK.md` said
  "there are ten now" and "eight now" for the front-end count, and `KNOWLEDGE_GRAPH.md` and
  `CLI_REFERENCE.md` both carried a `CALLS`-recall table that disagreed with the committed
  `scoreboard.json` in five rows. The audit checks that documented *symbols* still exist; a
  number that has drifted is prose to it. §7.1's instruction to grep the number word as well
  as the digit is the only control here, and it has to be run by hand — these four were found
  by reading every file that enumerates front-ends, not by a script. `language-expansion-roadmap.md`
  had likewise not been updated since the track opened, though §7.1 assigns it *every phase*.
- **The `actual` suffix does *not* fix Android product flavours.** *(Found 2026-09-16, P7.)*
  The flavour note above predicts that "P7's `@<sourceSet>` id suffix (D17) is the same fix".
  Measured at P7, it is not. A flavour variant carries **no keyword** — `src/demo` and
  `src/prod` declare plain classes — so a rule keyed on `actual` cannot see them, and the 9
  colliding declarations in the validation app are unchanged. Keying on the *directory*
  instead was considered and rejected: it would suffix declarations that never collided,
  and a call from `main` into flavour code would then point at an id nothing declares.
  Closing it properly needs a collision-driven rule computed across the whole tree, which
  makes an id depend on what else the repository contains. Still open, now with a reason.
- **Two of KaMPKit's three `expect` declarations are top-level properties, so they are not
  nodes.** *(Found 2026-09-16, P7.)* `expect val platformModule: Module` and its two
  `actual val platformModule` implementations produce nothing at all — not because of
  anything in P7, but because D6 makes a `Field` belong to a `Type` and a top-level property
  declares no node. The KMP reading is therefore complete over *what the graph represents*
  (4 of 4 `actual` classes and functions linked) while covering 4 of the repository's 6
  `expect`/`actual` pairs. The gap is D6's, not D17's, and it is recorded here because the
  phase's headline number is otherwise easy to misread.
- **`kotlin.test` is not the right default for brownfield, and the validation repo proves it.**
  *(Found 2026-09-16, P8.)* §4's P8 row says the scaffold depends on `kotlin("test")`, which is
  true — and the guidance derived from it told the model to use `kotlin.test` everywhere.
  spring-petclinic-kotlin declares `junit-jupiter-api` and no `kotlin("test")`, so a generated
  `import kotlin.test.Test` **does not compile** there, and the refine loop would spend a pass
  undoing it. `TargetLayout` gained `test_library`, read from the build scripts: measured
  `junit5` on petclinic, `kotlin.test` on aiandroid, neither on KaMPKit — three answers from
  three real repositories, which is why one hardcoded value was wrong. Any future JVM language
  inherits the same problem.
- **`Toolchain.conventions_skill_id` is declared and never read.** *(Found 2026-09-16, P8.)*
  Every one of the ten toolchain rows fills it in; nothing in `src/` consumes it. The skill
  actually reaches codegen through the catalog planner's `CapabilitySelector`, a completely
  separate mechanism — so `kotlin-conventions` could be registered on the toolchain while the
  skill itself did not exist, and every test stayed green. Both paths are now asserted in
  `tests/sdlc/test_kotlin_codegen.py`. Removing the dead field touches all ten rows and belongs
  in P10, not here.
- **A conventions skill is optional, and SQL is the proof.** *(Found 2026-09-16, P8.)* SQL ships
  as a supported codegen language with no conventions skill at all. What makes a run correct is
  the layout guidance and the prompt set, which are unconditional; the skill is guidance the
  planner *may* add. Worth stating because the reverse assumption would make every new language
  block on authoring a skill before it could generate anything.
- **`testDebugUnitTest` is not a task that reliably exists, and this plan named it four times.**
  *(Found 2026-09-16, P9.)* It is the documented Android unit-test task and it is what §3.4, §5,
  §7.1 and the D13 decision all specify. On the validation repository it does not exist at all:
  `AndroidLibraryConventionPlugin` calls `configureFlavors`, so **every** library module carries
  the `demo`/`prod` dimension and Gradle answers `:core:data:testDebugUnitTest` with *"task
  'testDebugUnitTest' is ambiguous in project ':core:data'. Candidates are:
  'testDemoDebugUnitTest', 'testProdDebugUnitTest'."* Flavour names cannot be read off a build
  script — they are computed in Kotlin, in a separate included build, from an enum — so the
  runner does not predict them: it asks for the documented task and, when Gradle rejects it,
  takes the replacement **from Gradle's own candidate list** and caches it. That is a derived
  answer rather than a guess, and it costs one configuration per module per session. Noted
  because "run the Android unit tests" reads like a solved problem and is not.
- **A convention plugin hides a module's dependencies from its own build script.**
  *(Found 2026-09-16, P9.)* `detect_jvm_test_library` read `build.gradle{,.kts}`, which is where
  a dependency is declared in every small project and in none of the large ones. aiandroid
  declares `kotlin("test")` exactly once, inside `build-logic/`, and **no** build script in the
  repository names a test library — so the detector returned "unknown" and the greenfield default
  happened to be correct. Right answer, wrong reasoning, and it would have been the wrong answer
  for a repo whose convention plugin picks JUnit. The scan now includes `build-logic/` and
  `buildSrc/`. The same blindness applies to any future detector that reads build scripts: on
  Android, the build script is often only a list of plugin ids.
- **Placement in a multi-module build must refuse, not guess.** *(Found 2026-09-16, P9.)* A
  27-module repository has no root `src/`, so P8's single-module resolver produced a path
  belonging to no Gradle project — a file compiled by nothing, verified by a task that does not
  exist. Placement is now keyed on the target package, with an exact match preferred over the
  longest prefix. When nothing matches, the layout is deliberately left **empty** and the run
  stops with the list of candidate modules and an instruction to pass `--package`. The failure
  mode being avoided is not a crash: it is a repository function written into a plausible-looking
  wrong module, where it compiles, its test passes, and nobody finds it.
- **One `missing_hint` cannot serve three different failures.** *(Found 2026-09-16, P9.)* P8's
  `Toolchain.available_in` returned a bool and reused the language's generic hint, which would
  tell a developer with a working JDK and Gradle that they need a JDK and Gradle. It is now
  `project_error`, returning the message itself, and Kotlin distinguishes "no Gradle here", "this
  is Android and there is no SDK" (which also has to say *no emulator is needed*, because the
  obvious reading of "install the Android SDK" is that a device is required) and "this build has
  27 modules and none of them holds your package". One field, one user, three sentences.
- **`--language`'s own help text never learned about Kotlin, and two error messages named
  flags that do not exist.** *(Found 2026-09-16, P9.)* P8 put `kotlin` into
  `SUPPORTED_LANGUAGES` and every test passed, but the `sdlc feature --language` help string
  still read *"auto (detect), python, java, typescript, csharp, c, cpp, go, php, perl, or
  sql"* — so the feature was shipped and undiscoverable, and `CLI_REFERENCE.md` (maintained by
  hand against that string) repeated the omission. Separately, P9's own "which module?" error
  first told the reader to pass `--package` and `--mode new`; the flags are `--package-name`
  and `--layout new`, so following the message exactly produced two further errors. A message
  that cannot be typed is not actionable, and nothing checked it — `tests/sdlc/test_android_codegen.py`
  now asserts that every `--flag` appearing in that message exists in the CLI source.
- **The test library is a property of the module, not the repository — P8's finding, one level
  down.** *(Found 2026-09-16, P9.)* P8 established that a generated `import kotlin.test.Test`
  does not compile in a project that depends only on JUnit, and read the answer per repository.
  That is still too coarse for a real Android build: `core/data` gets `kotlin("test")` from a
  convention plugin, while `core/model` — a plain `id("kotlin")` library in the *same* build —
  declares no test dependency at all. Generating into it produced `Unresolved reference: test`,
  caught by running the brownfield proof on a second module rather than by any test. Resolution
  is now per module, strongest evidence first: what that module's existing tests already
  **import** (proof, not inference), then what its own build script declares, then what the
  convention plugins it applies declare on its behalf — the plugin id maps to an
  `implementationClass`, which is the file name, so the hop is exact. When the answer is
  genuinely "nothing", the guidance says so and names the dependency to add, instead of picking
  one and hoping. Measured across the validation app: `kotlin.test` for `core/data`, `junit4`
  for `core/testing`, nothing for `core/model` and `lint`.
- **`test_the_scoreboard_is_deterministic` is order-sensitive — open, and not caused by this
  track.** *(Found 2026-09-16, P9/P10.)* The corpus-accuracy gate builds the scoreboard twice
  and asserts the two are byte-identical. Measured: `build_scoreboard` is **deterministic in a
  clean process** (four consecutive builds, byte-identical), the test passes on its own, passes
  with `tests/catalog` + `tests/knowledge` + `tests/mcp` ahead of it, and passes with the whole
  of `tests/pkg` (930 tests) — but it **failed in two of four full-suite runs and passed in the
  other two**. So something elsewhere in the suite intermittently leaves global state that
  changes extraction, and the gate that is supposed to stop corpus quality regressing is itself
  order-dependent. It predates this track and nothing here touches it; recorded rather than
  quietly re-run until green, and it belongs in P11's review.
- **Deferring `mypy` to the end hid 14 type errors in P9's own test file, and no other gate
  saw them.** *(Found 2026-09-16, gate.)* `ruff check`, `ruff format --check` and 3,885 passing
  tests were all green over a file with seven untyped `monkeypatch` parameters, two untyped
  async stand-ins and four `type: ignore` comments that were no longer doing anything. Type
  errors in *tests* are exactly what `mypy src tests` — rather than `mypy src` — exists to
  catch, and running it per phase would have caught them in P9. All fixed; the gate now reports
  **`Success: no issues found in 742 source files`**.
- **The gate could not run at all on this machine, for two environmental reasons worth
  recording.** *(Found 2026-09-16, gate.)* First, `mypy` sat at **0.1% CPU for 21 minutes**: the
  virtualenv lives under an iCloud-synced `~/Documents` and its files had been evicted, so every
  read was a network fetch — measured at **55 seconds for 150 files from `.venv`, against 0.04
  seconds for 150 from `src/`**, a ~1,400× difference. Pre-materialising site-packages with
  parallel reads took 9m43s once and made the run ordinary. Second, the last three errors were
  not code at all: `mcp` was absent from this venv, so the `type: ignore`s guarding its optional
  imports read as unused. CI installs that extra deliberately — *"an optional extra the repo
  ships code for is not optional to test"* — and installing it locally both cleared the three
  and took the suite from **3,885 passed / 27 skipped to 3,922 passed / 6 skipped**, because 21
  MCP tests had never run here.
- **A gate can be green because it is reading nothing.** *(Found 2026-09-16, P10.)*
  `roadmap-status.py`'s `_TOP_STATUS` was `^\*\*Status:\*\*\s*(.+?)\.` — one line, up to the
  first period. Every roadmap in this repository wraps its **Status** line across two or three
  lines, and not one of them puts a period on the first, so the pattern matched **nothing** and
  every check built on it silently passed. That is worse than a missing check: a missing check
  is visible in the list, while this one was counted in "7 checks each" on every green run. The
  pattern now reads the whole field, and the wrapped case is pinned by a test.
- **The prefix "shared by every first-party module" does not exist in a real repository.**
  *(Found 2026-09-16, P10.)* §9.4 specifies stripping the longest package prefix every
  first-party module shares. The validation app has exactly one module declaring
  `package androidx.test.uiautomator` — a file extending a third-party namespace on purpose —
  and that single module takes the common prefix of all 71 down to nothing, leaving the rule a
  no-op on the very repository its exit criterion names. It is coverage-based instead, and the
  threshold is measured rather than picked: the vendor prefix covers **94.4%** of modules and
  the next segment falls to **57.7%**, and that cliff is where a namespace stops naming the
  organisation and starts naming components. It also has to stay a no-op where namespaces are
  already shallow — this repository's deepest majority prefix is `orchestrator` at 48.1%, and
  `orchestrator.pkg` is already the right area.
- **This document's own status line went stale for four phases.** *(Found 2026-09-16, P9.)*
  §5's phase table was updated in the same change as every phase, exactly as the currency rule
  requires — but the one-line **Status** at the top of the file still read *"P0–P5 done · P6–P11
  not started"* while P6, P7 and P8 were finished and evidenced twelve lines below it, and §12's
  sequence block was stale the same way. The rule names the table, so the table is what got
  maintained. Both are now current; the durable fix is `scripts/roadmap-status.py --check` (§9.1,
  P10), which is precisely the gate that would have caught this.
- **Four more surfaces advertise the language list, and running the narrow suites hid all of
  them.** *(Found 2026-09-16, P9.)* P8 was validated with `tests/sdlc` + `tests/catalog` +
  `tests/personas`, which is where its code lives. The first *full* run afterwards failed at
  **collection**: `tests/plugin/test_manifests.py` derives its prose list from `TOOLCHAINS`, so
  adding `kotlin` raised `KeyError: 'kotlin'` — by design, with a comment saying the guard exists
  precisely because "the eight-language string survived PHP and Perl". Behind it sat three plugin
  manifests and the operator console's capability grid, which still said *"Nine languages"*. The
  lesson is about the gate, not the copy: a registry addition has blast radius outside its own
  test directory, so the phase gate is the **whole** suite, not the directories the phase touched.
- **The doc binder read `.kt` filenames as code symbols, and the checklist said so.**
  *(Found 2026-09-16, P11.)* `docs-matrix.md`'s registration table has a row — *"source
  extension in the drift/ident sets | doc binder treats `Foo.php` as a symbol"* — and the
  Kotlin track missed it through eleven phases. `symbolish_drift("Platform.kt")` returned
  **True**, so every `.kt` filename mentioned in prose was counted as an unresolved *symbol*
  rather than a path. Measured cost on the validation app: drift **25 → 24** once fixed. The
  same sets are still missing `.java`, `.cs`, `.go` and `.ts` — a pre-existing gap for three
  other tracks, left as a separate change because closing it moves their measured numbers,
  and recorded in `docs.py` beside the constant so it is not rediscovered as new.
- **Kotlin was absent from two per-language test rosters, and both fail open.**
  *(Found 2026-09-16, P11.)* `test_capabilities._FIXTURES` and `test_verifier._SOURCES` are
  parametrised over a hand-written dict, so a language missing from them is simply never
  tested — no error, no skip message. The capability matrix's Kotlin row was therefore checked
  for *existence* (`test_every_front_end_has_a_row` derives from `FRONT_ENDS`) but never
  cross-checked against a real extraction, and Kotlin never exercised the per-front-end
  freshness assertion. Both added. The pattern to watch: a roster derived from the registry
  cannot go stale, a roster written by hand always can, and this file has both kinds.
- **A third hand-written language list, found by the checklist rather than by a test.**
  *(Found 2026-09-16, P11.)* `plugins/spine/skills/understand-codebase/SKILL.md` still read
  *"It covers Python, Java, TypeScript, C#, C, C++, Go, PHP, Perl and SQL"* — ten languages,
  eleven phases after the eleventh landed. This is the same defect P9 found in the CLI help and
  the plugin manifests, and it survived P9's sweep because that sweep was driven by a failing
  test and this file has none; `docs-matrix.md` names the site, so only running the checklist
  finds it. Three lists of the same fact in one repository, none derived from the registry — the
  count of "places that enumerate languages by hand" is itself the risk.
### Maintainer review, 2026-09-17 — the fabrication class

A maintainer review of the merge request returned **not mergeable** on eleven fabrication paths,
two self-agreeing corpus cases and a comprehension-layer invariant break. They are recorded
individually below because they are **one defect repeated**, and the shape is worth naming: a
reader that could not answer a question returned a *plausible* answer instead of none, and the
plausible answer was then minted as an `external` placeholder — so the edge never dangled,
`pkg verify` reported clean, and the D9 invention oracle could not see it either. Every number in
this section was measured by running the branch's own code.

**What made them invisible is more important than any one of them.** A placeholder node is the
graph's way of saying "this exists outside the tree". Minting one for a *guess* makes a guess
indistinguishable from a library call, and every gate here checks consistency rather than truth.
The structural fix is `finalize`: a target that was assumed rather than read is checked against
what the repository actually declares, and dropped when nothing does.

**The class is narrowed, not closed.** The check above applies to targets the repository
*declares*. A second review round found the same rule applied one step short — to imported
types, to undeclared types, and to names that are unique but unreachable — plus two recall
losses where the fix drops a call it could resolve. Eight issues track them,
[#397](https://github.com/synaptixs/spine/issues/397); on the validation app the remaining
fabrication shapes measure **0 occurrences**. The two recall losses were inherited member
calls (fixed, #391) and same-file `IMPLEMENTS`, which turned out to be a `tree-sitter-kotlin`
parse collapse behind a one-line body, recovered since #396. Read the
numbers below as "these eleven paths, measured" — not as "the front-end cannot fabricate".

- **A same-package type guess reached `CALLS`, and a placeholder hid it.**
  *(Found 2026-09-17, review.)* `_resolve_type` fell back to `java:{package}.{name}` for any
  unresolved type, and `finalize` repointed `IMPLEMENTS` only — so `_calls` minted an external
  `Function` for the invented id. Kotlin's *default* imports (`String`, `System`, `Math`) need no
  import line, so this fired on essentially every file: `s.uppercase()` in `package app.ui`
  became a call to `java:app.ui.String.uppercase`, a class in a package that does not contain it.
  Measured on the validation app: **72 fabricated edges over 36 invented ids**, now **0**, and
  46 invented placeholder nodes gone (2,376 → 2,330). `CALLS` went **up**, 2,167 → 2,221: the
  same check that drops a guess also *recovers* the true edge the guess displaced, 126 of them
  here.
  Fixed by deferring every typed-receiver call to `finalize` (`_DeferredCall`) and resolving it
  through `pkg/finalize_names.py::resolve_or_drop`, which existed for exactly this and was unused.
  The fix also *recovers* true edges: a wildcard-imported sibling is now offered as a candidate,
  so `dao.getTopics()` resolves to the real `java:app.data.TopicDao.getTopics` instead of a
  same-named class under the caller's own package.
- **The scope functions §3.2 lists under "never" were emitted.** *(Found 2026-09-17, review.)*
  `topic.let { }`, `.apply`, `.run`, `.also` all resolved onto the receiver type, so
  `blast_radius` on any type named every file that had ever written `x.let { }`. No blocklist was
  needed for a repo-declared receiver: a type the repository *declares* has known members, so a
  call naming a member it does not declare is refused by the same `finalize` check. That leaves an
  **imported** receiver, which has no declared-member list to refuse against — `modifier.let { }`
  on an imported `Modifier` still minted a placeholder (#389, found 2026-09-17 in maintainer review
  of #380). Fixed with `_SCOPE_FUNCTIONS` in `_settle_calls`, applied only to the
  certain-but-undeclared-receiver case.
- **A name-only denylist for those scope functions cost true edges.** *(Found 2026-09-18, review of
  the #389 fix.)* `run`, `apply` and `use` are also genuine members of real library types, so
  refusing on the member name alone dropped `java.lang.Runnable.run`, `java.util.TimerTask.run`,
  `org.gradle.api.Project.apply` and every `java.time` `with` — measured, a three-call probe fell
  from three `CALLS` to one. The refusal now needs the name **and** the shape: a scope function is
  handed a function (`_passes_function` — a trailing lambda, or a lone lambda/callable-reference
  argument) and `r.run()` is not. `with` left the set entirely; Kotlin's `with(x) { }` is top-level,
  so it is dropped a line earlier as `certain=False` and listing it could only ever match a real
  member. Residue, stated rather than closed: `x.also(fn)` where `fn` is a variable reads exactly
  like a one-argument member call, and syntax cannot separate them.
- **The same fabrication survived one level further out, on the receiver-member fallback.**
  *(Found 2026-09-18, same review.)* `m.padding(8)` minted `java:androidx.compose.ui.Modifier.padding`,
  and `padding` is `androidx.compose.foundation.layout.padding` — an extension, which `Modifier` does
  not declare. Kotlin requires a file to *import* an extension to call it, so the true target was
  already in the graph, minted from the import and then discarded in favour of the invented id.
  `_settle_calls` now prefers that import (`imported_extension`) as the external placeholder. This
  reverses `_deferred_call`'s old rule that an `also` candidate is "never used as the
  external-placeholder fallback", and only for the external-receiver case: when the repository
  cannot introspect the receiver, the member reading is a guess while the import is written in the
  file. A **wildcard**-imported extension binds no simple name, so that case still lands on the
  receiver-member id — narrowed, not closed.
- **`string_value` dropped interpolation, so computed paths were emitted as literals.**
  *(Found 2026-09-17, review.)* One helper, five readers. `route("/api/${cfg.version}")` became
  the path `/api/`; `"/users/${user.id}/detail"` became `/users//detail`, an endpoint that exists
  at no version of that service; `include(":core:$it")` became the module `gradle:core/$it`.
  Detection needs both halves, because tree-sitter-kotlin 1.1.0 tags only *some* interpolations:
  `${x}` in a raw string is an `interpolation` node, but `$it` in an ordinary string arrives as
  two bare `string_content` children with no marker at all. A `$` surviving inside
  `string_content` therefore means interpolation too. Found while fixing it: escape sequences were
  silently *dropped* rather than decoded, so `"costs \$5 today"` came back as `costs 5 today` — a
  literal that is wrong rather than refused. Both fixed; `test_kotlin_literals.py` pins the lot.
- **A Compose route was read from raw source text, not from the parse.**
  *(Found 2026-09-17, review.)* `_resolve` recognised a literal by `raw.startswith('"')` and then
  stripped the first and last character, so `composable(route = "topic/" + BASE)` — an
  `additive_expression`, not a literal at all — produced the endpoint `NAV topic/" + BAS`. Now
  read from the argument node, so a concatenation is refused by the grammar rather than by a
  string test that cannot see the shape.
- **A Spring `@Value` placeholder was a literal path in the *Java* front-end.**
  *(Found 2026-09-17, review.)* `jvm_routes`'s docstring states the rule — "a `@Value`
  placeholder yields `None` rather than a guess" — but `${api.base}` is an ordinary Java
  `string_literal`, so it passed the node-type test and produced `GET /${api.base}/topics`. A
  regression in an already-shipped language, and the reason the rule now lives in
  `jvm_routes.literal_path` where both front-ends share it: Kotlin has the identical hole by a
  different spelling, since `"\${api.base}/x"` is an *escaped* dollar that decodes to the same text.
- **Hilt `PROVIDES` targets were invented, and generic wrappers were not unwrapped.**
  *(Found 2026-09-17, review.)* `@Provides fun provideClients(): Set<OkHttpClient>` read its
  return type with `bare_type` and got `Set`, minting a `Set` class in the module's own package —
  which `FactStore.injection_reach_of` then walked in `blast_radius`. The sibling
  `_first_parameter_type` had used `element_type` correctly all along, fifteen lines below.
  Separately, the reader minted the target node itself, which made a guessed target arrive
  pre-grounded and so invisible to `finalize`'s repoint; it now emits the edge only.
- **Two Room fabrications, from the same missing distinction.**
  *(Found 2026-09-17, review.)* `@Entity(tableName = TOPICS)` with a constant was
  indistinguishable from *no* `tableName`, so the class name was claimed as the table — one real
  table then produced two `Entity` nodes (a grounded class and an external node for the table its
  own `@Query` names), and `data_layer_link` matches by name, so the class never reconciled with
  the migration that creates it. A constant declared in the file is now resolved
  (`kotlin_names.string_constants`) and anything else refuses the entity outright. Separately,
  `@Insert fun insert(dto: SomeDto)` on a plain data class minted `java:entity:app.data.SomeDto` —
  an Entity whose name is a dotted FQN, for a class carrying no `@Entity` at all. Unlike an
  unknown *table*, there is nothing there for a placeholder to stand for, so it is dropped.
- **Ktor mounts and Compose route constants both resolved by bare name, repo-wide.**
  *(Found 2026-09-17, review.)* A `health()` call in one service mounted another service's
  `fun Route.health()` under the caller's prefix, ignoring package, imports and a same-file
  declaration of the same name; and two feature modules each declaring `const val route` merged,
  so the loser's `composable` was credited with a path its source never contains and one endpoint
  collected an `EXPOSES` to both screens. Both are the ordinary shape of a monorepo and of the
  Compose feature-module convention. Both now resolve own-package → explicit import → unique
  repo-wide, and refuse an ambiguous name — and the fix *raises* recall, because a collision no
  longer costs both routes.
- **The Gradle reader credited every nested block to the script's own module.**
  *(Found 2026-09-17, review.)* `project(":app") { dependencies { … } }` in a root script
  asserted a root → `core/ui` edge that no file declares *and* lost the real `app` → `core/ui`
  one: a false edge and a missing edge from a single read. `subprojects {}` applies to a set the
  file does not enumerate, so it now contributes nothing rather than one edge hung off the root.
- **The KMP source-set suffix came from the first `/src/` match.**
  *(Found 2026-09-17, review.)* In a path that already contains `src/`, two platforms' `actual`s
  collapsed onto one id and `FactBatch` dedup dropped one without a word — the exact failure the
  suffix exists to prevent. Now the rightmost match, walked rather than matched, because a regex
  for this must not consume the separator the next match needs.
- **The D9 invention oracle shared the extractor's blind spot.**
  *(Found 2026-09-17, review.)* Neither `_Kotlin.declares` nor `_collect_bindings` bound a `for`
  loop variable or a `catch` parameter, so `for (helper in fns) { helper() }` produced a
  fabricated `CALLS` that the invention check certified as clean. `scope.py`'s own docstring
  forbids precisely this ("a detector that agrees with the extractor by construction") and
  `_CFamily` has always walked `for_range_loop`. Both are *scopes*, not declarations — their name
  binds inside their own span, where a `val` binds only from the line after it ends.
  **And nothing in CI held any front-end but Python's invention at zero**, because the oracle had
  only ever been pointed at this repository, which is pure Python. `score_invention_over` now runs
  it across the corpus fixtures as well: every front-end has a row, and Kotlin's reads
  `measured, 0 invented` over 21 shadowable bare calls.
- **Two corpus cases dropped true edges and argued the omission away.**
  *(Found 2026-09-17, review.)* `ktor_routes` omitted `svc.module → svc.orders` behind a clause
  claiming the file's only `CALLS` edge was the one to `buildPath` — false, since `orders` is a
  first-party same-package declaration, not a wildcard import. `compose_nav` omitted both
  `this.navigate(...)` edges although the receiver type is imported on line 3 and
  `typed_receivers` scores the identical shape. Both were the two cases that would have caught
  the fabrications above, which is what makes a self-agreeing corpus worse than a small one.
  Both edges are now labelled and **emitted**: §3.2 row 1 refused a same-package call across
  files for want of a `finalize` backstop, and there is one now, so the call is *checked* rather
  than guessed or dropped. Kotlin `CALLS` recall **0.92 → 0.94**, precision still 1.00.
- **`false_positives` was used with the opposite of its documented meaning.**
  *(Found 2026-09-17, review.)* `corpus/README.md` defines it as "edges the front-end **emits**
  that are not true", and 18 of the 20 entries across every language described a fabrication the
  reader correctly *avoided* — so a reader counting the field concluded Kotlin invented eleven
  edges. Split into a new `refusals` field, which is also the only annotation here that is
  **enforced**: a refusal the extractor starts emitting fails the case on load rather than
  showing up as an anonymous dip in a precision number.
- **Two corpus cases did not contain the shape §6 assigns them.**
  *(Found 2026-09-17, review.)* `extensions` was given "an extension with the same name imported
  from elsewhere" and had no `import` at all; `companions` was given three call forms and had
  two, missing `Companion.make()` — the form most likely to mint the phantom D5 exists to
  prevent, because `Companion` is a capitalised bare name no file declares. Adding it showed D5's
  third form was never implemented. Both fixtures now carry their assigned shape, and the missing
  `import` exposed a further defect: an extension **imported** from another package was attributed
  to the receiver type, losing a grounded target for an invented one, because the per-file
  extension table only ever held that file's own.
- **`state` and `understand` computed different prefixes over the same store.**
  *(Found 2026-09-17, review.)* `AreaIndex` and `current_state` voted over every non-external
  module; `renderers.collect_areas` voted over non-*test* modules only. A prefix is decided by a
  majority of the voters, so a different electorate is a different prefix — measured on this
  repository: **103 area labels one way, 386 the other**. `areas.py`'s own docstring exists to
  prevent exactly this ("One definition, two renderers… a reader who noticed would stop trusting
  both"). One function, `store_namespace_prefix`, now serves all three.
- **Two more area defects behind it.** *(Found 2026-09-17, review.)* `coupling` was the only area
  in `current_state` computed *without* the prefix, so on a reverse-DNS repository its arrows
  named areas that did not exist and the whole "System architecture" section rendered empty. And
  `tested_areas` was structurally always `0` for **any** Gradle repository: an area there is a
  module directory (`core/data`), so no area is ever named "test" and `is_test_area` can never
  fire, while module-to-module `coupling` cannot express a test→source edge either. A fully
  tested repository reported "no automated tests detected". Coverage is now read from provenance.
  Separately, `build_module_paths` sorted a *set* by length alone, leaving ties in hash order on
  a path CLAUDE.md requires to be deterministic.
- **Codegen: an emulator task was selectable, and a package could be invented.**
  *(Found 2026-09-17, review.)* `connectedDebugAndroidTest` contains "Debug" and would have won
  the variant-task preference — §10's "never an emulator" rested on an undocumented property of
  Gradle's error text, and now rests on an explicit rule. Repo-controlled Gradle output also
  became an argv element, so a "candidate" spelled `--init-script` would have been a flag rather
  than a task; candidates are now matched against a task-name pattern. And on the default
  no-`--package-name` path, a repository whose only Kotlin module is rooted at `com.acme.app` was
  given `org.example.myrepo` — a package invented for a *brownfield* repo, which is exactly what
  D13/P9 says placement must never do. Every placement test passed `--package-name` explicitly,
  so nothing covered the default path the fallback exists to serve.
- **§9.3 was counted delivered and was half-built.** *(Found 2026-09-17, review.)* Its exit
  criterion is "the existing Java greenfield test passes on a Gradle scaffold", and `GradleTestRunner`
  was built in P8 with only Kotlin wired to it — so a Java Gradle project still ran `mvn test`
  against a build with no `pom.xml`. The Java row now selects its runner from the build tool the
  layout already detected.
- **Three smaller ones.** *(Found 2026-09-17, review.)* Retrofit did no import resolution, so any
  `@GET` with a literal became a `pkg joins` candidate — JAX-RS puts an identical annotation on
  an identical method, and `jvm_routes.resolves_into_spring` gets the same question right fifteen
  lines away. `unresolved_calls` leaked between repositories, because `ClientState.clear()`
  preserves `unmatched` by design and `finalize` rebuilt the front-end's list from it, so
  `reset_unresolved()` could not reach it — re-introducing the bug that function's docstring was
  written to fix. And `_is_test_path` never learned Kotlin, so `_prove_the_tests_test_something`
  and `_files_no_test_exercises` were silently dark for every Kotlin run.
- **Tests that could not fail.** *(Found 2026-09-17, review.)* `test_toolchains.py`'s dangling-
  capability check filtered by membership of `_SEED` while building the catalog *from* `_SEED`, so
  its result was unconditionally empty and the body could have been deleted. A Ktor test asserted
  an empty endpoint set with no positive control, which passes equally well if the reader stops
  working entirely. `test_java_extractor.py` had no Spring cases at all, although `test_jvm_routes.py`
  says the Java grammar half is tested there — 104 new lines of Java route reading shipped covered
  by one corpus case, which is how the `@Value` finding above reached a released language.
- **Validation-repository names in `src/` and `tests/`.** *(Found 2026-09-17, review.)* Thirteen
  sites, against this document's own rule (line 14: "names live in `docs/` only"). Replaced with
  neutral descriptions; the rule exists so the code does not read as if it special-cases four
  particular repositories.

- **`is_public` is in §3.1 but not in this codebase.** *(Found 2026-09-15, P1.)* `facts.Node` has
  no such field, and no front-end has such a method — §3.1's row was written from another
  project's shape. Nothing to implement; the row is noted here so the next reader does not go
  looking for the seam.

### Maintainer review, 2026-09-21 — the fix for the fabrication class fabricated

The round above closed eleven paths and left eight issues open under
[#397](https://github.com/synaptixs/spine/issues/397). The fix for six of them
([#429](https://github.com/synaptixs/spine/pull/429)) introduced four new defects, and the
shape is worth more than any one of them: **a precision check that is two-valued in a
three-valued world does not merely refuse too much — it refuses the best-evidenced reading
and then mints a worse one.** Every gate stayed green throughout.

- **The receiver check compared bare names, and a subtype receiver is the normal case.**
  *(Found 2026-09-21, maintainer review of #429.)* `_settle_calls` asked whether the
  extension's declared receiver name equalled the call site's, and read "no" as "refuse".
  Kotlin's rule is a *subtype-compatible* receiver. Measured on the validation app:
  `fun NavController.navigateToSearch()`, declared in-repo and explicitly imported, called on
  a `NavHostController` — the standard Compose navigation pattern — lost its grounded edge and
  gained an invented `androidx.navigation.NavHostController.navigateToSearch`. **Four edges,
  four ids nothing declares**, and `CALLS` stayed flat at **2,218** because the edges were
  *redirected* rather than dropped, so a count-based check saw nothing. On touchlab/KaMPKit
  the same shape replaced **three** edges to a real imported in-repo extension with a member
  of a SQLDelight-generated type. `pkg verify` reported both graphs clean, 0 errors.
  Fixed by making the question three-valued: compatible when the receiver ids intersect, when
  the extension takes a type parameter, or when `IMPLEMENTS` runs from the receiver up to a
  declared receiver; **incompatible only when both types are ones this repository declares and
  no such path exists**; unknown otherwise, and unknown accepts — an external receiver has no
  supertype list to walk, so a mismatch there proves nothing. Receivers are compared as
  resolved ids, which is what makes the walk possible and also closes the residue the previous
  round left open: `app.data.Topic` and `app.legacy.Topic` are no longer one receiver.
- **Two more shapes the same comparison refused, both idiomatic.** *(Found 2026-09-21,
  maintainer review of #429.)* `fun Int.toDp()` and `fun Float.toDp()` in one package are
  **one id**, so a table with a single receiver slot kept whichever file was parsed last and
  refused the other — and *which* one survived depended on filesystem order. `fun <T> T.x()`
  stored the literal `"T"`, which equals no real receiver; resolving it would have minted
  `java:<package>.T`, an id nothing declares. Both resolve on `develop` and both were dropped.
  Receivers now accumulate per id, and a type-parameter receiver is read from the
  declaration's own `type_parameters` rather than inferred from the name's shape.
- **The repo-wide extension table was never cleared, so a warm cache changed the graph.**
  *(Found 2026-09-21, maintainer review of #429.)* `self._extensions` was built per file and
  cleared nowhere, while its four siblings on the same class — `_nav`, `_ktor`, `_deferred`,
  `_client` — are all cleared in `finalize`. `load_or_extract_repos` hands **one**
  `RepoCodeExtractor` to every declared repository, so repo A's extensions verified repo B's
  imports and reinstated the very defect the table existed to fix. Worse, `load_or_extract`
  returns early on a cache hit and never runs `finalize`, so **whether repo A happened to be
  cached changed repo B's emitted graph for the same commit** — invariant 8 and the
  no-LLM-no-randomness guarantee both broken by an accumulator. This is the same leak
  `kotlin_http` fixed for `_client`, one attribute over, and its comment had already rejected
  `reset_unresolved` as the place to fix it.
- **"Same package" was a string comparison, and a file with no package has none.**
  *(Found 2026-09-21, maintainer review of #429.)* #395 restricted both route resolvers to
  what the calling file can see, keyed on `module` — which `module_name` falls back to the
  repo-relative path for a file declaring no `package`, **14 of 263 in the validation app**.
  Two default-package files therefore compared as two different packages, and a Ktor mount
  and a Compose route constant that Kotlin resolves with no import at all both went to
  nothing. The declared package is now read from the parse tree, not from `module` and not by
  regex; both resolvers keep "exactly one, or unresolved".
- **Three corpus fixtures could not compile, and one of them was scoring its own extractor.**
  *(Found 2026-09-21, maintainer review of #429.)* `extension_name_collision` called two
  members its receiver did not declare — an unresolved reference in any compiler — so it
  labelled **no `CALLS` at all** and its whole content was two refusals, while its twin
  `extension_name_collision_external` labelled the *byte-identical* call sites as real edges.
  The pair differed only by which branch of `_settle_calls` ran: a label with no source to be
  derived from, which is exactly what §6's rule forbids and the second time this track has hit
  it (#389). All three repaired to compile; the collision case now declares the members it
  calls, so a member outranks the extension, and it scores positives as well as refusals.
- **A recall ratio is the wrong thing to gate on, and this round proved it.**
  *(Found 2026-09-21, maintainer review of #429.)* Labelling the destructuring rule's cost as
  a `known_gap` — `tag(path)` where `tag` is a destructured `String` resolves to the member
  function in Kotlin, measured on ktor-samples as `method(method)`, **1,454 → 1,453** — moved
  Kotlin `CALLS` recall **0.94 → 0.93** while precision held at **1.00** and behaviour did not
  change at all. `corpus/README.md` says a `known_gap` still counts as a miss, so measuring a
  loss lowers the ratio. The durable criterion is precision at 1.00 plus no *unexplained*
  miss; a bare ratio penalises honesty about a gap and rewards leaving it unlabelled.

## 12. Sequence

```
P0  plan + baseline            ✅ 2026-09-10   this document, branch, census, blast radius
P1  comprehension              ✅ 2026-09-15   map_repo: 85 modules · 271 types · 713 functions
P2  corpus + CALLS + walker    ✅ 2026-09-15   precision 1.00; 2,169 CALLS; invention measured 0
P3  Room + Retrofit            ✅ 2026-09-16   6 entities; Kotlin consumer joins a Java provider
P4  Compose nav + Hilt         ✅ 2026-09-16   8 NAV endpoints; PROVIDES (12th edge kind); 47 bindings
P5  Gradle modules             ✅ 2026-09-16   29 gradle: modules; 72 module→module IMPORTS
P6  Ktor + Spring routes       ✅ 2026-09-16   petclinic 18 endpoints; ktor-samples 67; Java gained Spring
P7  Kotlin Multiplatform       ✅ 2026-09-16   KaMPKit 2 → 7 components; 4/4 actuals linked
P8  Kotlin/JVM codegen         ✅ 2026-09-16   greenfield green+red; petclinic brownfield 3/3
P9  Android codegen            ✅ 2026-09-16   brownfield into the validation app; the module's
                                              own unit-test task green, independently re-run
P10 generic work               ✅ 2026-09-16   roadmap gate +2 checks & into CI; areas 1 → 39
P11 review + MR to ready       ✅ 2026-09-17   self-review mergeable (6 findings); maintainer
                                              review NOT mergeable — 11 fabrication paths,
                                              2 self-agreeing corpus cases, areas, codegen;
                                              all fixed (§11). 72 fabricated edges -> 0,
                                              126 true edges recovered; recall 0.92 -> 0.94
```
