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
**`orchestrator`**. Comprehension supports ten front-ends: Python, Java, TypeScript,
C#, C, C++, Go, PHP, Perl and SQL, with the matching parser extras installed.

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
symbols. It is included in `[all]`; installation details and limits are in
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
not whole-repository recall. See the
[five-repository evaluation](https://github.com/synaptixs/spine/blob/main/docs/evals/clang-semantic-step3b.md).

## What's new

**3.34.2 (current)** — maintainer tooling: a generic plan skeleton every development
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
| Python, Java, TypeScript, C#, C, C++, Go, PHP and Perl comprehension/codegen | ✅ | `pkg extract`, `sdlc feature --language`; [toolchains](https://github.com/synaptixs/spine/blob/main/AGENT_GUIDE.md#10-language-support--toolchains) |
| Optional C/C++ member-call enrichment between grounded symbols; measured coverage limits | 🟡 | `[clang]` (also in `[all]`); [validation](https://github.com/synaptixs/spine/blob/main/docs/evals/clang-semantic-validation.md) |
| SQL schema/query/procedure comprehension, migration folding, UTF-16 and SQL Server `GO` batches | ✅ | `[sql]`; `pkg extract`, `understand` |
| SQL migration codegen validated in SQLite or opt-in Docker Postgres | ✅ | `sdlc feature --language sql`; `[sql-postgres]` |
| Framework endpoints and data-layer edges, including JAX-RS, ASP.NET Core and EF Core | ✅ | [Knowledge Graph](https://github.com/synaptixs/spine/blob/main/KNOWLEDGE_GRAPH.md) |
| C/C++ include graphs, C++ routing for included `.h` files and header/source merging; CMake or brownfield Meson builds | ✅ | `sdlc feature --language c` / `cpp` |
| Go packages, calls and interface satisfaction; multi-module build/test selection | ✅ | `sdlc feature --language go` |
| PHP namespaces/traits/calls, Laravel/Slim/Symfony routes, Eloquent/Doctrine entities; Composer/PHAR PHPUnit | ✅ | [PHP workflow](https://github.com/synaptixs/spine/blob/main/USER_GUIDE.md#php-code-generation) |
| Perl packages/inheritance/fields/calls, routes and data layer; syntax checks and `prove` | ✅ | [Perl workflow](https://github.com/synaptixs/spine/blob/main/USER_GUIDE.md#perl-code-generation) |
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
Comprehension and codegen cover **Python, Java, TypeScript, C#, C, C++, Go, PHP and Perl** — each
front-end going beyond structure into what that stack actually does (Java and C# REST
endpoints, EF Core entities, C's `#include` graph, C++ templates and namespaces, Go
interface satisfaction by method-set matching). **PHP** adds a call graph too (namespaces,
classes, interfaces, traits, `CALLS`), plus Composer/PHAR PHPUnit codegen with changed-file lint.
**Perl** adds a call graph too (packages, inheritance across its five spellings,
`$self`/`SUPER::`/qualified/bare `CALLS`) — codegen uses `perl -c` then `prove`,
with optional `cpanm` for dependencies. **SQL** adds data-layer comprehension plus
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
