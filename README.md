<p align="center">
  <img src="https://raw.githubusercontent.com/synaptixs/spine/main/assets/spine-banner.png" alt="Spine — governed, provenance-grounded autonomous delivery" width="820">
</p>

# Spine

**Turn requirements into reviewed, tested pull requests, with a human in control.**

Spine reads requirements from Confluence, Notion, Markdown or
[OpenSpec](https://openspec.dev), builds a deterministic graph of your target repo,
and generates code grounded in its existing structure and conventions. You can
inspect the graph and a build plan before spending model tokens, build locally,
then choose when to push a pull request for human review.

The product is **Spine**, its package is **`synaptixs-spine`**, and its command is
**`orchestrator`**. Comprehension supports twelve front-ends: Python, Java, TypeScript,
C#, C, C++, Go, PHP, Perl, Kotlin and SQL — plus a Gradle reader that turns `.kts`
build scripts into the module graph an Android app is assembled from — with the
matching parser extras installed.

```bash
uv tool install synaptixs-spine
```

[SETUP.md](https://github.com/synaptixs/spine/blob/main/SETUP.md) owns prerequisites,
extras, credentials and troubleshooting. The base install is enough for the
Python worked example; add `[sdlc]` for builds or `[all]` for the agent plugin and
all language parsers.

## Start here

1. **[Run the worked example](https://github.com/synaptixs/spine/blob/main/EXAMPLE.md).**
   Follow one real ticket in a public codebase, with reproducible output. Its
   comprehension steps need no credentials; the model-dependent build is marked.
2. **[Build a feature locally](https://github.com/synaptixs/spine/blob/main/USER_GUIDE.md).**
   Configure a model, inspect the plan, and use `--safe` for a local branch and diff.
3. **[Go live after review](https://github.com/synaptixs/spine/blob/main/USER_GUIDE.md#step-4--go-live-a-real-issue--pull-request).**
   Use `--live` to open a PR, then close the tracker loop after a human merges it.

To look at your own repo first, run `orchestrator state /path/to/repo`. It writes
nothing unless you request an output file. `orchestrator understand` builds the
reviewable `episteme/` knowledge base; it is the comprehension command that writes.

The optional `[clang]` extra adds C/C++ member-call edges between existing
symbols. Installing it enables the pass automatically, including through `[all]`.
For extraction without clang, use a fresh environment with `[languages]` or
`[c,cpp]`. Installation details and limits are in
[SETUP.md](https://github.com/synaptixs/spine/blob/main/SETUP.md#optional-extras).

## What is measured

The graph comes from parsers, with `file:line` provenance. CI scores its precision
and recall against a hand-labelled corpus, and checks for regressions. Those
fixture scores are bounds on the tested cases, not a promise that arbitrary code
has no missing or incorrect edge.
On the corpus, TypeScript `CALLS` recall is **0.86**; CI re-derives this figure
from the committed scoreboard.

On a pinned five-project bug corpus, the fixing file appears in the top ten for
**27 of 38** tickets; the first guess is right for **12**. The method, confidence
intervals, graph accuracy and limits are in
[BENCHMARK.md](https://github.com/synaptixs/spine/blob/main/BENCHMARK.md).

Controlled codegen runs measure whether grounding improves integration, including
an arm without the graph and tickets that already name their target file. Read the
[internal results](https://github.com/synaptixs/spine/blob/main/docs/specs/codegen-model-comparison-results.md)
and [external replication](https://github.com/synaptixs/spine/blob/main/docs/specs/external-repo-grounding-results.md)
for the models, commands, counts and limits.

C/C++ semantic recovery and runtime vary widely by repository. Repository-local
include roots can improve resolution, while missing standard/generated headers
and unsupported identities still limit it. Recovered pending-site fractions are
not whole-repository recall. OpenCV's measured median extraction takes 300.296 s
with clang versus 29.501 s without it; this suits batch work only when that cost
is acceptable. Some measured profiles gain no useful relationships. See the
[support contract](https://github.com/synaptixs/spine/blob/main/docs/evals/clang-semantic-release-readiness.md#support-contract)
and [five-repository evaluation](https://github.com/synaptixs/spine/blob/main/docs/evals/clang-semantic-step3b.md).

## What's new

**3.42.0 (current)** — a round of Kotlin precision work, and a gate that was punishing
honesty. Six reported Kotlin defects are closed, including an extension call that resolved onto
an id nothing declares: comparing receiver *names* refused every subtype receiver, so
`fun NavController.navigateToSearch()` called on a `NavHostController` — the standard Compose
pattern — lost its real edge and gained an invented one. Four of those on the Android
validation app, with every gate green, because the fabricated edges *replaced* true ones rather
than adding to the count.

Alongside them, the accuracy gate no longer fails a build for **writing down a known
limitation**. Labelling a `known_gaps` entry lowered the recall ratio with nothing about the
extractor having changed, and the only remedy was regenerating the baseline — which accepts
everything that moved. Corpus recall is now gated on *unexplained* misses instead, the published
score is untouched, and enforcing that a gap must name an edge actually missed turned up four
dead entries, three of which were quietly paying for two real misses nobody could see.

`scoreboard.json` is version 2 as a result: run `orchestrator pkg accuracy --scoreboard` once
after upgrading.

**3.41.0** — a drafted spec now ships with the code's facts. `openspec draft` could
not see a repository: its whole signature was `--source/--out/--refresh/--overwrite`, so a draft
could only restate the ticket more formally, and its task list was the same two checkboxes for
every change ever drafted. Pass a repo path (or `--repos`) and the proposal carries where the
change lands with `file:line`, each stated criterion bound against the graph, and the criteria
that name code which **already exists** — evidence for a human, never a verdict, because a run
reporting a criterion met having changed nothing is the failure this is built to catch. The task
list becomes one checkbox per criterion, with the model's own suggestions kept in their own
labelled group. **It does not improve the prose:** the requirements are still written from the
source document alone, and the point is that you can now tell which half is which — a line with
no `file:line` has been checked by nothing. Absence is stated in four distinguishable ways,
because a reader who cannot tell *"we looked and found nothing"* from *"we never looked"* will
assume the flattering one: a language Spine has no front-end for yields zero nodes and looks
exactly like a repository with nothing to find, and a draft taken from an uncommitted tree says
so **in the file**, where a stderr warning would have scrolled away.

**3.40.0** — the briefs stop pointing at code and start showing it. `investigate` and
`root-cause` rendered the graph's index — symbol names, `file:line`, caller counts — and
contained **zero lines of source**, so a reader opened the files the brief had already located.
Each landing site now carries the code at its line and says whether a test reaches it; a
root-cause report quotes its fault site, so "ranked by evidence, not asserted" means the
evidence is on the page. A landing the brief itself calls **weak** gets neither — on a real
ticket the first attempt spent two excerpts on DTOs matched on a three-letter fragment and
printed "no test reaches this" in bold on all ten rows, which is a signal that has stopped being
one. Where the graph cannot answer, the brief says nothing rather than accusing: a front-end
that emits no call edges has not proven an absence of tests. And a merged multi-repo brief reads
each repository's own `episteme/` under its key, so the mode a cross-cutting ticket needs is no
longer the only one with no project knowledge.

**3.39.0** — a run now builds in the project the ticket is about. In a solution with
several projects the target used to be whichever one sorted first, so a WebApp ticket scaffolded
into an API client and failed six test runs against a type that project cannot even see. Spine
picks the project holding the files the plan names, else the one with the most source in that
language, and says which rule it used; `--package-name` now **retargets** rather than merely
renaming, so a human can overrule it. Java multi-module builds resolve at all — at any depth —
and Kotlin scope functions are refused by name *and* shape, so `r.run()` keeps its edge while
`m.let { }` stops inventing one. Two CI guards that could be skipped rather than passed are
closed.

**3.38.0** — two field reports from a React Native engagement, and the build document
stops flattering itself. A vendored `ios/Pods` is no longer walked, and a symlinked file keeps its
own path — so `node_modules` cannot return one header at a time through CocoaPods' public headers.
`--language auto` weighs what most of the source **is** rather than what merely exists, so one build
script no longer scaffolds a Python package into a React Native app. The coverage probe asks only
what a test could answer and names what it excluded; a test the run itself wrote to cover a gap is
**withdrawn and said so** rather than chased until the budget dies. Section 12 no longer scores the
brief agreeing with a design taken *from* that brief, and section 8's `stated` is earned by matching
a whole line of the ticket. Retrieval reads a ticket's inflections, so "account deletion" reaches
`DeleteAccountScreen`.

**3.37.0** — two field reports from a C#/.NET engagement, both diagnosed to defects
and both now fixtures. **Blazor components** enter the graph: `.razor` is read as line-aligned C#
through the C# front-end (no new grammar or extra), every symbol on its true line, with
`corpus/csharp/razor` at precision 1.00 / recall 1.00. A file in a shared namespace is no longer
reported "absent from the knowledge graph"; the spec writer no longer drops the identifiers a
ticket named, and a ticket can name its file in any language, by path or bare name. Retrieval
carries its evidence and floors weak hits, so an all-weak ticket says *locate the change before
building* instead of proposing five confident wrong paths. Jira attachments are read on intake,
not only named.

**3.36.0** — **Kotlin**, as the 11th language and the 12th front-end: `.kt`
comprehension and a typed-receiver call graph, Room entities and Retrofit calls (so an Android
app joins a backend as a cross-repo consumer), Compose navigation as routes, Hilt wiring through
a new `PROVIDES` edge kind, Ktor and Spring MVC routes — the Spring half shared with the Java
front-end, which had read JAX-RS only — Kotlin Multiplatform source sets, and codegen for both
Kotlin/JVM and Android on a new Gradle test runner that also gives *Java* codegen its first
Gradle support. `.kts` build scripts are read as a module graph rather than parsed as source.
Install with `pip install 'synaptixs-spine[kotlin]'`.

**3.35.0** — two additive features. An optional C/C++ semantic pass
(`pip install 'synaptixs-spine[clang]'`) resolves member calls the CST cannot, adding edges
only between symbols already in the graph — ids, nodes and determinism unchanged; the
standard library stays out of reach. And `pkg export --format cypher` loads the graph into
Neo4j, Memgraph or any openCypher store for the traversal questions the flat projections
cannot answer — transitive closure, cycles, shortest path.

**3.34.2** — maintainer tooling: a generic plan skeleton every development
plan starts from, and a roadmap-currency gate that can check a plan kept outside the
checkout. No engine changes.

**3.34.1** — documentation has one home per task: [AGENT_GUIDE.md](https://github.com/synaptixs/spine/blob/main/AGENT_GUIDE.md) replaces the two
host guides (its MCP tool inventory is generated and gated), [SETUP.md](https://github.com/synaptixs/spine/blob/main/SETUP.md) owns installation and
credentials, [USER_GUIDE.md](https://github.com/synaptixs/spine/blob/main/USER_GUIDE.md) the
everyday build, and [OPERATIONS.md](https://github.com/synaptixs/spine/blob/main/OPERATIONS.md)
the pipeline and dashboard walkthrough. No engine changes — the wheel is identical to 3.34.0.

**3.34.0** — Perl ships comprehension and codegen: packages, inheritance,
calls, Mojolicious/Dancer2 routes and DBIx::Class entities; builds use `perl -c`,
configured `Perl::Critic`, then `prove`, with optional `cpanm`. A single toolchain
registry now owns language dispatch, protected by **8 of 8** caught mutations.
Greenfield and brownfield validation is recorded in the
[Perl roadmap](https://github.com/synaptixs/spine/blob/main/docs/specs/perl-codegen-roadmap.md).

Full release history: [CHANGELOG](https://github.com/synaptixs/spine/blob/main/CHANGELOG.md).

## Capabilities

✅ shipped · 🟡 partial or operator-gated · 🔬 experimental, off by default.
Commands below use the `orchestrator` prefix. All flags and detailed behavior are
in [CLI_REFERENCE.md](https://github.com/synaptixs/spine/blob/main/CLI_REFERENCE.md).

| Capability | Status | Command or reference |
|---|---|---|
| Requirements → specs → tracked backlog; OpenSpec intake and write-back drafts | ✅ | `ingest`, `backlog`, `openspec draft` |
| Reviewable build document; digest-bound human approval before code | ✅ | `sdlc plan`, `sdlc approve`, `sdlc autorun` |
| Research evidence, code-bound acceptance criteria, validated design references | ✅ | `sdlc autorun`; evidence persists even when a run parks |
| Local feature build, live PR, review feedback, post-merge tracker completion | ✅ | `sdlc feature --safe` / `--live`, `address-review`, `complete` |
| Durable multi-feature pipeline and approval dashboard | ✅ | `sdlc run`, `up`; [Operations](https://github.com/synaptixs/spine/blob/main/OPERATIONS.md) |
| Inspect the execution graph, node results and selected workflow | ✅ | `sdlc explain`, `sdlc workflow` |
| Python, Java, TypeScript, C#, C, C++, Go, PHP, Perl and Kotlin comprehension/codegen | ✅ | `pkg extract`, `sdlc feature --language`; [toolchains](https://github.com/synaptixs/spine/blob/main/AGENT_GUIDE.md#10-language-support--toolchains) |
| Optional C/C++ member-call enrichment between grounded symbols; measured coverage limits | 🟡 | `[clang]` (also in `[all]`); [validation](https://github.com/synaptixs/spine/blob/main/docs/evals/clang-semantic-validation.md) |
| SQL schema/query/procedure comprehension, migration folding, UTF-16 and SQL Server `GO` batches | ✅ | `[sql]`; `pkg extract`, `understand` |
| SQL migration codegen validated in SQLite or opt-in Docker Postgres | ✅ | `sdlc feature --language sql`; `[sql-postgres]` |
| Framework endpoints and data-layer edges, including JAX-RS, Spring MVC, Ktor, ASP.NET Core and EF Core | ✅ | [Knowledge Graph](https://github.com/synaptixs/spine/blob/main/KNOWLEDGE_GRAPH.md) |
| C/C++ include graphs, C++ routing for included `.h` files and header/source merging; CMake or brownfield Meson builds | ✅ | `sdlc feature --language c` / `cpp` |
| Go packages, calls and interface satisfaction; multi-module build/test selection | ✅ | `sdlc feature --language go` |
| PHP namespaces/traits/calls, Laravel/Slim/Symfony routes, Eloquent/Doctrine entities; Composer/PHAR PHPUnit | ✅ | [PHP workflow](https://github.com/synaptixs/spine/blob/main/USER_GUIDE.md#php-code-generation) |
| Perl packages/inheritance/fields/calls, routes and data layer; syntax checks and `prove` | ✅ | [Perl workflow](https://github.com/synaptixs/spine/blob/main/USER_GUIDE.md#perl-code-generation) |
| Kotlin classes/objects/companions/extensions and typed-receiver calls; Room entities and DAO reads/writes; Retrofit calls as cross-repo consumers; Compose navigation routes; Hilt/Dagger wiring via `PROVIDES`; Gradle `.kts` module graph; **Ktor and Spring MVC server routes**; **Multiplatform source sets and `expect`/`actual`** | 🟡 comprehension only, no codegen | `[kotlin]`; [Kotlin roadmap](https://github.com/synaptixs/spine/blob/main/docs/specs/kotlin-support-roadmap.md) |
| Multi-repo graph across HTTP calls, shared tables and library imports; evidence-derived joins | ✅ | `.spine/repos.yaml`; `pkg joins --propose` / `--check`, `investigate --repos` |
| Markdown, reST, text and HTML docs bound to code; PDF and Word/Excel with extras | ✅ | `understand`, `state`, `pkg docs`; `[docs]`, `[office]` |
| OCR diagrams and transcribe audio/video into reviewed `.spine-media/` artifacts | ✅ opt-in | `media extract`; `[media]` + Tesseract, `[asr]` for local Whisper |
| Document-grounded codegen and committed `episteme/` with a currency check | ✅ | `sdlc feature`, `understand --check` |
| State report: infrastructure, structure, architecture, coverage and doc drift | ✅ | `state --lens developer` / `stakeholder` |
| Graph extraction/export, repo profile and model-assisted audit | ✅ | `pkg extract`, `pkg export`, `profile`, `audit` |
| Measured graph accuracy, regression gate and language-specific caveats in build plans | ✅ | `pkg accuracy`, `pkg accuracy --check`, `sdlc plan` |
| Per-file route/table parity and invented-call detection | 🟡 oracle-dependent | `pkg accuracy --oracle parity` / `invention`; see CLI limits |
| Runtime call recall by executing the repository's tests | 🟡 Python only | `pkg accuracy --oracle runtime` (explicit test execution) |
| Ticket provenance from blame: `Intent` nodes and `SERVES` edges | ✅ opt-in | `understand --intents`, `state --intents`, `investigate --intents`, `pkg export --intents` |
| Human gates, policy, spend budgets, append-only audit, run export/replay | ✅ | [Operations](https://github.com/synaptixs/spine/blob/main/OPERATIONS.md); registry trace/export |
| RBAC and multi-tenancy | 🟡 partial | `ORCHESTRATOR_PRINCIPALS`, `ORCHESTRATOR_TENANT_ID` |
| Profile-based capability catalog, convention learning and clarifying questions | ✅ | `catalog plan` |
| Agentic tool-use codegen with approved external tools | 🔬 | `SDLC_AGENTIC_CODEGEN=1` |
| Local/offline or mixed-provider models, selected per stage | ✅ | `models`; [configuration](https://github.com/synaptixs/spine/blob/main/SETUP.md#local-and-mixed-model-configuration) |
| PR reviewer/auditor personas, eval harness and cross-run semantic memory | ✅ | Persona registry, `evals`; `ORCHESTRATOR_SEMANTIC_MEMORY=1` |
| Live OpenTelemetry tracing joined to the audit log | ✅ opt-in | `OTEL_EXPORTER_OTLP_ENDPOINT`; [Setup](https://github.com/synaptixs/spine/blob/main/SETUP.md#6-live-tracing-optional) |
| Consume external MCP tools and database schema | ✅ | `mcp list`, `mcp call`, `mcp contracts`, `mcp ingest-db` |
| Expose Spine tools, prompts and resources to Claude Code, Codex or other MCP hosts | ✅ | [Agent guide](https://github.com/synaptixs/spine/blob/main/AGENT_GUIDE.md); stdio or authenticated HTTP |
| Domain-grounded build through ontomesh (semantic-spine seam 1) | ✅ opt-in | `SPINE_ONTOMESH_URL`, `SPINE_ONTOMESH_FLAVOR` |
| Drift remediation and shipped-unit registration (seams 3 and 2) | 🟡 operator-gated | `sdlc remediate`; [deployment sequence and gaps](https://github.com/synaptixs/spine/blob/main/OPERATIONS.md#the-semantic-spine) |

## Documentation

| Question | Guide |
|---|---|
| What does a real run look like? | [Worked example](https://github.com/synaptixs/spine/blob/main/EXAMPLE.md) |
| How do I install, configure or troubleshoot? | [Setup](https://github.com/synaptixs/spine/blob/main/SETUP.md) |
| How do I build and deliver a feature? | [User Guide](https://github.com/synaptixs/spine/blob/main/USER_GUIDE.md) |
| How do I use Spine from an assistant? | [Agent Guide](https://github.com/synaptixs/spine/blob/main/AGENT_GUIDE.md) |
| How do I run the pipeline and connect tools? | [Operations](https://github.com/synaptixs/spine/blob/main/OPERATIONS.md) |
| What does each command and flag do? | [CLI Reference](https://github.com/synaptixs/spine/blob/main/CLI_REFERENCE.md) |
| How is the graph built and persisted? | [Knowledge Graph](https://github.com/synaptixs/spine/blob/main/KNOWLEDGE_GRAPH.md) |
| How do the platform layers fit together? | [Architecture](https://github.com/synaptixs/spine/blob/main/ARCHITECTURE.md) |
| What is measured, and what are the limits? | [Benchmark](https://github.com/synaptixs/spine/blob/main/BENCHMARK.md) |
| What can I share with others? | [Community one-pager](https://github.com/synaptixs/spine/blob/main/COMMUNITY.md) |

## FAQ

**Does it merge code on its own?**
No. It opens a PR; a human reviews and merges. There are two approval gates — before
building and before merging — and safe mode makes no external writes at all.

**Where does my code/data go?**
To whichever LLM provider you configure — or nowhere external, if you run a local
model (Ollama). Generated code stays in a local branch until you choose `--live`.

**Do I need Docker or a database?**
Not for the everyday path (`sdlc feature --safe` builds one requirement locally).
The autonomous multi-feature pipeline + web dashboard needs Temporal + Postgres —
see the [Setup guide](https://github.com/synaptixs/spine/blob/main/SETUP.md).

**Which languages and models?**
Comprehension and codegen cover **Python, Java, TypeScript, C#, C, C++, Go, PHP, Perl and
Kotlin** — each
front-end going beyond structure into what that stack actually does (Java and C# REST
endpoints, EF Core entities, C's `#include` graph, C++ templates and namespaces, Go
interface satisfaction by method-set matching). **PHP** adds a call graph too (namespaces,
classes, interfaces, traits, `CALLS`), plus Composer/PHAR PHPUnit codegen with changed-file lint.
**Perl** adds a call graph too (packages, inheritance across its five spellings,
`$self`/`SUPER::`/qualified/bare `CALLS`) — codegen uses `perl -c` then `prove`,
with optional `cpanm` for dependencies. **Kotlin** covers comprehension and the data layer
(classes and objects in every flavour, companions folded onto their class,
extension and top-level functions, constructor properties, a `CALLS` graph built
on Kotlin's declared types — `dao.getTopics()` resolves exactly, with no
inference — plus **Room** entities, DAO reads/writes parsed from the SQL, and
**Retrofit** calls that make an Android app a *consumer* in the multi-repo join).
A Kotlin **service** is read the other way round: **Ktor** and **Spring MVC** routes
become `Endpoint`s, so a Kotlin backend is a *provider* the same join can pair against.
Spring is read by a module the **Java** front-end shares, which is how Java gained
Spring endpoints at the same time — it had only ever read JAX-RS. `sdlc feature --language kotlin`
generates and tests code in Kotlin/JVM **and Android** projects, picking the Gradle module from
the target package and running that module's own unit tests — never an emulator. **SQL** adds data-layer comprehension plus
greenfield migration codegen validated against an ephemeral database. **Docs** fold in
automatically; **media** (diagrams, screenshots, recorded reviews) via the opt-in
`media extract`. Any LiteLLM provider — Anthropic, OpenAI, Bedrock — or a local Ollama
model, and you can set a different model per stage. Extras and details:
[SETUP.md](https://github.com/synaptixs/spine/blob/main/SETUP.md#optional-extras).

**How is it safe to run on real repos?**
Write guards on generated files, allow-listed + write-gated external tools, a per-run
spend budget, an append-only audit trail, and human approval before any push or merge.

**CLI or web UI?**
Either — they drive the same engine and the same API. Use the CLI for scripting/CI,
the web UI for watching runs and approving gates by hand — or ask your assistant, which
has the same operator tools over MCP.

**Can other tools call it?**
Yes. It speaks MCP both ways: it can use external MCP servers, and it can run *as* an
MCP server so Claude Code / Codex / your IDE can call the pipeline (with the same gates).

## Security and contributing

Spine clones repositories and executes generated code. CI runs code and dependency
security checks; report vulnerabilities through
[SECURITY.md](https://github.com/synaptixs/spine/blob/main/SECURITY.md).

Work from `develop`, add a failing fixture for changed behavior, and run the gate
in [CONTRIBUTING.md](https://github.com/synaptixs/spine/blob/main/CONTRIBUTING.md).
Useful starting points are language front-ends (`pkg/*_extractor.py`), accuracy
fixtures (`corpus/`), and the tracked gaps in
[STATE-OF-SPINE](https://github.com/synaptixs/spine/blob/main/docs/specs/STATE-OF-SPINE.md).
Measure what changed and state what was not checked.

- [Report a bug](https://github.com/synaptixs/spine/issues/new?template=bug_report.md)
- [Request a feature](https://github.com/synaptixs/spine/issues/new?template=feature_request.md)
- [Ask a question](https://github.com/synaptixs/spine/discussions)
- [Code of Conduct](https://github.com/synaptixs/spine/blob/main/CODE_OF_CONDUCT.md)

## License

MIT License. See [LICENSE](https://github.com/synaptixs/spine/blob/main/LICENSE).
