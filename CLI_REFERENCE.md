# Orchestrator (Spine) — CLI Reference

> **Spine** is the product; the command is **`orchestrator`** (package `synaptixs-spine`).
> Maintained by hand against the CLI — run `orchestrator <command> --help` for the
> authoritative version. If the two disagree, `--help` is right and this file is a bug.

**51 commands** across 7 areas. Every command supports `--help`; repo-analysis commands accept a local path or a git URL.

## Command map

**Getting started & operations** — Set up your environment and run the platform.  
`init` · `doctor` · `models` · `up` · `tui` · `task submit`

**Understand a codebase — the Knowledge Graph** — Extract and read the Product Knowledge Graph (PKG). Deterministic, no LLM. All accept a local path OR a git URL.  
`understand` · `state` · `profile` · `catalog list` · `catalog plan` · `pkg extract` · `pkg export` · `pkg docs` · `pkg capabilities` · `pkg verify` · `pkg accuracy` · `media extract`

**Grounded design, debugging & RCA** — The KG-grounded engineering commands: design a change, research a ticket, and trace/analyze bugs — all anchored to real code.  
`design` · `investigate` · `localize` · `rca` · `regression` · `audit`

**Requirements intake — source to backlog** — Turn a requirements source (Confluence, Jira, Notion, files, OpenSpec, MCP) into intents/specs and (optionally) tracker issues.  
`ingest` · `backlog` · `openspec draft`

**The SDLC pipeline — build features** — The autonomous build path: requirements → code → tests → reviewed PR, with human gates.  
`sdlc plan` · `sdlc approve` · `sdlc autorun` · `sdlc feature` · `sdlc run` · `sdlc runs` · `sdlc baseline` · `sdlc complete` · `sdlc address-review` · `sdlc remediate`

**MCP — external tools** — Consume onboarded Model Context Protocol servers (governed, audited).  
`mcp list` · `mcp contracts` · `mcp call` · `mcp ingest-db`

**Registry — templates & contracts** — Manage reusable capability templates and API contracts in the registry service.  
`template register` · `template list` · `template show` · `template publish` · `template deprecate` · `contract register` · `contract list` · `contract show` · `contract publish` · `contract deprecate`


---

## Getting started & operations

Set up your environment and run the platform.

### `orchestrator init`

Scaffold a new project: create a .env from the template, then guide setup.

Creates a commented .env skeleton (from the same env groups `doctor`
checks), then reports readiness. While required variables are still unset it
exits non-zero with a call to fill them in and re-run — so `init` is the
one-command setup loop: run it, fill the blanks, run it again until green.

Safe to re-run: an existing .env is never overwritten (only missing keys are
appended) unless --force.

```
orchestrator init [OPTIONS]
```

| Option | Description |
|---|---|
| `--path` | Directory to scaffold the .env into. (default: `.`) |
| `--force` | Overwrite an existing .env with a fresh template. |

### `orchestrator models`

What you can point the pipeline at, and what each stage is using now.

Read from the installed LiteLLM's own catalog rather than a list maintained in this
repo, so it reflects the client actually making the calls — upgrading `litellm`
brings new models with no change here.

```
orchestrator models [OPTIONS]
```

| Option | Description |
|---|---|
| `--provider` | Filter to one vendor: `anthropic`, `openai`, `gemini`, … |
| `--tools-only` / `--all` | Only models that support tool calling. (default: `--tools-only`) |

**Tool calling is a requirement, not a preference.** Codegen forces a `submit_files`
call and the acceptance judge forces `submit_verdict`. On a model without function
calling both fall back to parsing prose out of a text reply — the failure the forced
tool call was added to remove. `--all` lists the rest, marked **NO** in the Tools
column.

**Choosing a model per stage.** Each stage resolves independently: an explicit
`--model` flag, then its own environment variable, then the global one, then the
built-in default.

| Stage | Variable | Used for |
|---|---|---|
| codegen | `SDLC_CODEGEN_MODEL` → `ORCHESTRATOR_INTAKE_MODEL` | implement / refine / revise / author_tests |
| judge | `SDLC_JUDGE_MODEL` | the acceptance verdict |
| intake | `ORCHESTRATOR_INTAKE_MODEL` | intent extraction, spec writing |
| *(all)* | `ORCHESTRATOR_MODEL` | one knob for everything |

Pointing a stage at another vendor needs that vendor's key in the environment
(`OPENAI_API_KEY` for `gpt-*`, `ANTHROPIC_API_KEY` for `claude-*`).

**Reasoning models need their level named.** `ORCHESTRATOR_REASONING_EFFORT` (default `high`)
sets the reasoning level sent whenever a tool call is in play. It is not a tuning knob you can
ignore: OpenAI **rejects function tools alongside a reasoning model's implicit default**, so
without an explicit level every codegen call fails on models like `gpt-5.6-sol`. `low`,
`medium` and `high` all work and all keep the reasoning. The provider's own error suggests
`none` — that disables the reasoning these models are chosen for, and is the wrong fix. Lower
it to trade quality for latency and cost, never to clear an error.

### `orchestrator doctor`

Check environment readiness and print a diagnostic report.

Bridges `.env` into the process environment first (same as `ingest` /
`sdlc`), so the report reflects exactly what the pipeline will see — a
real exported variable still wins over the file.

```
orchestrator doctor
```

### `orchestrator up`

Bring up the whole local stack in one command, then open the inbox.

Starts Docker infra (Postgres + Temporal), applies migrations, and launches
the web/API server **and** the Temporal worker with sensible defaults — so a
non-technical user reaches the delegation inbox at `/app` without wiring up
three terminals. Streams logs until Ctrl-C, then stops the app processes
(infra containers are left running for fast restarts).

```
orchestrator up [OPTIONS]
```

| Option | Description |
|---|---|
| `--port` | Port for the web UI + API. (default: `8000`) |
| `--host` | Bind address for the API. (default: `127.0.0.1`) |
| `--no-docker` | Don't manage Docker; assume Postgres + Temporal are already up. |
| `--no-worker` | Skip the Temporal worker (browse-only; can't delegate runs). |
| `--compose-file` | Override the docker compose file to use. |

### `orchestrator tui`

Launch the terminal UI: watch runs, clear gates, and delegate a run.

A keyboard-driven cousin of the web inbox over the same `/v1` API. Needs the
`tui` extra: `pip install 'synaptixs-spine[tui]'`.

```
orchestrator tui [OPTIONS]
```

| Option | Description |
|---|---|
| `--api-url` | Registry API base URL. (default: `http://localhost:8000`) |
| `--api-key` | API key for the registry. (default: `dev-key`) |

### `orchestrator task submit`

Submit a task to the orchestrator and print the final state.

```
orchestrator task submit [OBJECTIVE] [OPTIONS]
```

**Arguments**

- `OBJECTIVE` —

| Option | Description |
|---|---|
| `--template` | Pin a specific template id; planner chooses otherwise. |
| `--version` | Pin a specific template version. |

---

## Understand a codebase — the Knowledge Graph

Extract and read the Product Knowledge Graph (PKG). Deterministic, no LLM. All accept a local path OR a git URL.

### `orchestrator understand`

Build a committed `episteme/` — a code-true project knowledge base.

Phase 0: extracts the Product Knowledge Graph + project profile and renders
architecture / domain-model / tech-context / conventions / glossary as
markdown in the target repo. Deterministic (no LLM); re-run to refresh.
`path` may be a local path or a git URL cloned on demand — for a URL the
clone is transient, so the knowledge base defaults to `./episteme`.

A doc-ingestion post-pass also folds the repo's docs into the graph as `Doc` nodes +
`MENTIONS` edges: Markdown, reST, plain text and **HTML** need nothing; **PDF** needs the
`[docs]` extra and **Word/Excel** the `[office]` extra. **Media** (images, audio, video) also
ingest — but only from a committed transcript artifact you produce first with `media extract`
(below); with no artifact, media files are skipped and the build is unchanged. No-op on a repo
with no docs.

```
orchestrator understand [PATH] [OPTIONS]
```

**Arguments**

- `PATH` — Repo path or git URL to comprehend. _(default: `.`)_

| Option | Description |
|---|---|
| `--out` | Knowledge-base dir (default: <repo>/episteme; ./episteme for a URL). |
| `--refresh` | Re-extract the PKG instead of using the commit cache. |
| `--check` | Verify the committed episteme still matches the code; write nothing, exit non-zero if not. |
| `--dialect` | SQL dialect (postgres\|mysql\|tsql\|oracle\|…); default: auto-detect. |
| `--intents` | Also record which ticket each symbol was last changed for (`Intent`/`SERVES`). Opt-in — see the caveat below. |

`--check` writes nothing: it re-renders and diffs against the committed bank, exiting non-zero
when they disagree. That makes `episteme/` *provably* current in CI rather than hopefully
current. Note it reads docs **from disk regardless of git** — an untracked or gitignored
Markdown file under a scanned directory still becomes a `Doc` node, and `--check` will report
the bank stale for a diff CI cannot reproduce.

> **`--intents` is opt-in because nothing reads its output yet.** It adds `Intent` nodes and
> `SERVES` edges to the graph, but no surface renders them — the only visible effect is a
> count in the graph-size line of `README.md` and `architecture.md` (e.g. `· 34 intents`). No
> page tells you which ticket a symbol serves, and `pkg export` / `pkg extract` have no
> `--intents` flag, so the facts cannot be read back out. Cost is roughly **3× CPU** (one
> `git blame` per file, 8 workers). It becomes a default when something renders it.

### `orchestrator state`

Current State — a team-facing snapshot of what a repo is today and how healthy it looks.

Synthesized from the Product Knowledge Graph + project profile (deterministic, no LLM),
layered on top of `understand`. `--lens developer` gives the technical view;
`--lens stakeholder` gives plain language. Includes a **Documentation** section — how much
of the code the ingested docs describe (symbol coverage %) plus top **doc drift**. A report
is a *view* of the code — re-run to refresh; nothing is written unless `--out` is given.

```
orchestrator state [PATH] [OPTIONS]
```

**Arguments**

- `PATH` — Repo path or git URL to summarize. _(default: `.`)_

| Option | Description |
|---|---|
| `--lens` | Audience: developer \| stakeholder. (default: `developer`) |
| `--out` | Write the report to this file (default: print to stdout). |
| `--refresh` | Re-extract the PKG instead of using the commit cache. |
| `--dialect` | SQL dialect (postgres\|mysql\|tsql\|oracle\|…); default: auto-detect. |
| `--no-timestamp` | Omit the generated-at time (byte-stable HTML for CI diffs). |
| `--intents` | Also record which ticket each symbol was last changed for (`Intent`/`SERVES`). Opt-in — same caveat as `understand` above; adds one count to the size line and nothing else. |

Output format follows `--out`'s extension: `--out report.html` emits a single self-contained,
shareable HTML report; any other extension (or stdout) emits markdown.

Output is byte-stable: pair `--no-timestamp` with any extension and two runs of the same
commit produce identical bytes, so a report can be diffed or checked into CI.

### `orchestrator profile`

Profile a project (languages, framework, DB, tests, task type) — read-only.

`path` is a local path or a git URL (github/bitbucket/gitlab/enterprise),
cloned on demand.

```
orchestrator profile [PATH] [OPTIONS]
```

**Arguments**

- `PATH` — Repo path or git URL to profile. _(default: `.`)_

| Option | Description |
|---|---|
| `--intent` | Intent title, to classify the task type. |
| `--json` | Emit the profile as JSON. |

### `orchestrator catalog list`

List the capabilities the orchestrator can assemble (read-only).

```
orchestrator catalog list [OPTIONS]
```

| Option | Description |
|---|---|
| `--json` | Emit the catalog as JSON. |

### `orchestrator catalog plan`

Show the capability plan the orchestrator would assemble for a project.

```
orchestrator catalog plan [PATH] [OPTIONS]
```

**Arguments**

- `PATH` — Repo path or git URL to plan for. _(default: `.`)_

| Option | Description |
|---|---|
| `--intent` | Intent title, to classify the task type. |
| `--json` | Emit the plan as JSON. |

### `orchestrator pkg extract`

Extract grounded code facts from a repo and print a summary (read-only).

```
orchestrator pkg extract [PATH] [OPTIONS]
```

**Arguments**

- `PATH` — Repo path or git URL to scan. _(default: `.`)_

| Option | Description |
|---|---|
| `--query`, `-q` | Show callers + blast radius of a symbol name. |
| `--json` | Dump all facts as JSON. |
| `--dialect` | SQL dialect (postgres\|mysql\|tsql\|oracle\|…); default: auto-detect. |

### `orchestrator pkg export`

Export the whole graph in a format another tool can read — so you can explore it in software
that already does layout, filtering and search rather than waiting for us to build a canvas.

```
orchestrator pkg export [PATH] [OPTIONS]
```

**Arguments**

- `PATH` — Repo path or git URL to scan. _(default: `.`)_

| Option | Description |
|---|---|
| `--format` | `sqlite` \| `graphml` \| `dot` \| `json` \| `obsidian`. _(default: `sqlite`)_ |
| `--out`, `-o` | Output file (or directory, for `obsidian`). _(default: `pkg-facts.<ext>`)_ |
| `--db` | **Deprecated** alias for `--out`, `sqlite` only. Use `--out`. |

| Format | What it's for |
|---|---|
| `sqlite` | The ontomesh-ready kind-per-table projection. |
| `graphml` | **Gephi, yEd, Cytoscape.** The one to reach for to explore a graph visually. |
| `dot` | Graphviz. |
| `json` | Scripts and custom tooling. Carries nodes **and edges** — unlike `pkg extract --json`, which is nodes plus a summary. |
| `obsidian` | An Obsidian vault: a copy of this repo's `episteme/` with `[[wikilink]]` syntax. Run `understand` first; it reads the knowledge base and never edits it in place. |

```bash
orchestrator pkg export . --format graphml --out spine.graphml
```

**Exports are complete, never truncated.** The bounded "top N of M" behaviour of the *visual*
surfaces is deliberately absent here — the point of handing the graph to Gephi is that Gephi
filters, and a silently truncated file would let you draw conclusions from a subset without
knowing it was one. Output is byte-identical for an identical commit, so a committed export
diffs cleanly.

The graph formats include `Doc` nodes and `MENTIONS` edges (documentation, and the media
transcripts that reuse them). `sqlite` does not — its kind-per-table schema has no doc table.

#### Two things to know before you load an export

**1. `IMPORTS` targets the imported *symbol*, not always the module.** `from app.core import
slugify` produces `py:web.views → py:app.core.slugify`, not `py:web.views → py:app.core` — 2,746 of
5,895 import edges on this repo. Keep only the edges whose endpoints are already modules and you
get 3,033 dependencies; resolve properly and you get **4,287**. The naive read silently loses
**1,254 real dependencies, 29% of the graph**, and it fails in the direction that looks plausible:
a sparser, tidier architecture than the one you have. Resolve both endpoints upward through
`CONTAINS` first:

```python
import json
d = json.load(open("spine.json"))
kind  = {n["id"]: n.get("kind") for n in d["nodes"]}
owner = {e["dst"]: e["src"] for e in d["edges"] if e["kind"] == "CONTAINS"}

def to_module(i):
    seen = set()
    while kind.get(i) != "Module" and i in owner and i not in seen:
        seen.add(i); i = owner[i]
    return i if kind.get(i) == "Module" else None

deps = {(to_module(e["src"]), to_module(e["dst"]))
        for e in d["edges"] if e["kind"] == "IMPORTS"}
deps = {(s, t) for s, t in deps if s and t and s != t}
```

This is the same `CONTAINS` walk the `episteme` renderers do, and it is why their module maps look
denser than a naive read of the raw edges.

**2. Scope before you lay out.** These exports are complete, and complete is large: ~10k nodes and
~29k edges for this repo. Graphviz took **3m44s** simply to *parse and canonicalize* that DOT file,
before attempting any layout — and the result would be unreadable anyway. Gephi handles a graph
this size because you filter inside it. If you are rendering a picture, slice first (one area, one
module and its neighbours) and lay out the slice.

### `orchestrator pkg capabilities`

Which node/edge kinds each language front-end can emit. Read off the front-ends' own source,
so it cannot drift from them — this is what generates the capability matrix in
[KNOWLEDGE_GRAPH.md](KNOWLEDGE_GRAPH.md). Needs no repo and touches nothing.

```
orchestrator pkg capabilities [OPTIONS]
```

| Option | Description |
|---|---|
| `--format` | markdown (the KNOWLEDGE_GRAPH.md matrix) \| json. (default: `markdown`) |

It reports **capability, not coverage**: a front-end that can emit `Endpoint` still emits none
for a repo with no routes. For that question run `pkg verify` and read the `source-parity`
check.

### `orchestrator pkg docs`

Reconcile the **named** documentation file(s) against the code's fact graph (read-only) and
print a binding/drift summary — the targeted counterpart to the automatic whole-repo doc
ingestion that `understand`/`state` perform (which folds *all* docs into the graph as `Doc`
nodes + `MENTIONS` edges).

```
orchestrator pkg docs [REPO] [OPTIONS]
```

**Arguments**

- `REPO` — Repo path or git URL to extract facts from. _(default: `.`)_

| Option | Description |
|---|---|
| `--doc`, `-d` | Markdown/text doc(s) to reconcile. (default: `[]`) |

### `orchestrator pkg verify`

Tier-1 graph invariants — self-consistency checks that need no ground truth, so they run on
any repo. Exits non-zero on any **error**, so it can stand guard in CI.

```
orchestrator pkg verify [PATH] [OPTIONS]
```

| Check | Severity | Asks |
|---|---|---|
| `dangling-edge` | error | does every edge endpoint exist as a node? |
| `stale-provenance` | error | does every `file:line` resolve to a real line? |
| `orphan-rate` | error | are first-party modules implausibly unimported? |
| `external-ratio` | error | are `IMPORTS` implausibly all-external? |
| `phantom-module` | warning | does an `external` module shadow a first-party one? |
| `source-parity` | warning | does a file declare more routes/tables than the graph holds? |
| `invented-call` | warning | does a `CALLS` edge target a name bound in the caller's own scope? |

Consistency is not accuracy: a graph can be perfectly self-consistent and still wrong. For
that, see `pkg accuracy`.

### `orchestrator pkg accuracy`

**How right is the graph?** `pkg verify` asks whether the graph contradicts itself, which needs
no oracle. This asks whether it is *correct*, which does — so it carries four, each answering a
different question at a different cost.

```
orchestrator pkg accuracy [PATH] [OPTIONS]
```

| Option | Description |
|---|---|
| *(none)* | Score the hand-labelled corpus: precision **and** recall per node/edge kind, per language. |
| `--oracle runtime` | **Executes the repo's test suite** under a call tracer and reports `CALLS` recall from real execution. Needs no labelling; works on any repo with tests. |
| `--oracle parity` | Per-file declared-vs-emitted counts for routes and tables. Reads only source — no corpus, no test run. |
| `--oracle invention` | `CALLS` edges targeting a name bound in the caller's own scope. Exactly detected, not sampled. |
| `--sample N --kind K` | With `--oracle invention`: also list N edges of kind K for human review, for the joins no detector reaches (`CONSUMES`, `EXPOSES`, `REFERENCES`). Deterministic, so two reviewers see the same facts. |
| `--scoreboard` | Write the committed baseline to `src/orchestrator/pkg/scoreboard.json`. |
| `--check` | Compare against that baseline and **exit non-zero on a gated regression**. This is the accuracy gate in the quality gate. |
| `--language` | Score only one language. |
| `--json` | Emit the report as structured data. |
| `--tests` | Test target(s) for `--oracle runtime`; defaults to the repo's own. |
| `--dialect` | SQL dialect (postgres\|mysql\|tsql\|oracle\|…); default: auto-detect. |

**Current corpus results** (19 fixture cases, 8 front-ends). Precision is **1.00 on every node
kind and every edge kind in all 8 languages**; recall is 1.00 on every kind except `CALLS`:

| language | `CALLS` recall |
|---|---|
| `c` `sql` | 1.00 |
| `python` | 0.73 |
| `cpp` `csharp` `go` `java` | 0.67 |
| `typescript` | 0.50 |

Every remaining loss is the documented instance-dispatch skip — a call whose receiver is a
variable rather than a name. Invention stands at **0 invented targets across 15,212 call
edges**; parity shortfall is **0**.

**What is gated, and what is only recorded.** Not everything can be gated on equality, and the
distinction is what each number is measured *against*:

| metric | gate | why |
|---|---|---|
| corpus precision & recall | **strict** — any drop fails | measured against committed fixtures; repo churn cannot move it |
| parity shortfall | **ratchet** — must not increase | rises only when the graph falls behind the source |
| invention count | **recorded, never gated** | measured against the repo, so it moves whenever anyone writes ordinary code |
| runtime recall | **recorded, never gated** | non-deterministic — it moves when the test suite grows |

**`--oracle runtime` runs the repository's code.** No other command here does. It is never
implied, always echoes the command first, and runs in a subprocess.

**It measures recall only.** A call the tests never made is *untested*, not *wrong*; precision
is not computable from a trace, and the report says so on every run.

**Two coverage limits worth knowing before you quote a number:**

- **`--oracle runtime` is Python-only.** It uses `sys.monitoring` (PEP 669), which has no
  equivalent in the other seven front-ends. "Runtime-verified" means "runtime-verified for
  Python".
- **`--oracle invention` only examines Python.** It resolves caller-scope bindings with
  Python's `ast`, so calls in other languages are counted as *unexaminable* rather than
  scored. On a C repository it reports `0 (0.00% of all calls)` with every candidate
  unexaminable — that is "not measured", not "clean". The corpus catches invention in the
  other front-ends; this repo-scale oracle does not.

### `orchestrator media extract`

OCR images and transcribe audio/video into reviewable transcript artifacts under `.spine-media/`,
so diagrams and recorded design reviews become graph content.

This is the **explicit, opt-in** producer half of media ingestion — it MAY run a model and be slow.
It writes a content-addressed JSON artifact (`.spine-media/<sha256>.json`) that you review and
commit; from then on the deterministic graph build (`understand`/`state`) reads that artifact like
any other doc and **never runs a model itself**. A media file with no artifact is simply skipped, so
this changes nothing until you run it.

- **Images** (`.png`/`.jpg`/`.jpeg`/`.webp`) use local OCR (Tesseract) — needs the `[media]` extra
  and a system `tesseract` binary. Diagram-oriented: it keeps box/edge labels, not prose.
- **Audio/video** (`.mp3`/`.wav`/`.mp4`/`.mov`) use a pluggable ASR backend. `--asr local` runs
  Whisper on your machine (the `[asr]` extra); `--asr api` **uploads the media to a remote service**
  and therefore requires `--allow-remote`. Segment timestamps are preserved in the artifact.

Everything runs on your machine unless you choose `--asr api`. Long media is capped and truncated
(recorded in the artifact); oversized files are skipped. Review and commit `.spine-media/` to ingest.

```
orchestrator media extract PATHS... [OPTIONS]
```

**Arguments**

- `PATHS` — Media file(s)/director(ies): images (OCR) + audio/video (ASR). _(required)_

| Option | Description |
|---|---|
| `--repo-root` | Root whose `.spine-media/` receives the artifacts. (default: `.`) |
| `--force` | Re-extract even if an up-to-date artifact exists. |
| `--asr` | Audio/video backend: `local` (Whisper) or `api` (remote). (default: `local`) |
| `--whisper-model` | Local Whisper model size (tiny/base/small/…). (default: `base`) |
| `--api-endpoint` | OpenAI-compatible transcription URL (with `--asr api`). |
| `--allow-remote` | Consent to uploading audio/video **off-machine** (required for `--asr api`). |

**Examples**

```bash
orchestrator media extract docs/architecture/            # OCR every diagram in a folder
orchestrator media extract review.mp4 --whisper-model small   # local transcription
orchestrator media extract talk.mp3 --asr api \
  --api-endpoint https://api.openai.com/v1/audio/transcriptions --allow-remote
```

> Requires `pip install 'synaptixs-spine[media]'` (image OCR) and/or `'[asr]'` (local audio/video).
> The remote API backend reads its key from `$OPENAI_API_KEY` — never a flag.

---

## Grounded design, debugging & RCA

The KG-grounded engineering commands: design a change, research a ticket, and trace/analyze bugs — all anchored to real code.

### `orchestrator design`

Grounded feature design: spec × knowledge graph → a design with blast radius.

Produces the M2 design for one feature anchored to the repo's real structure,
and annotates it with its **blast radius** (which modules it touches, who
depends on them, the call hotspots) and any **unverified references** (named
paths absent from the graph). Deterministic by default; `--llm` writes the
prose. `path` may be a local path or a git URL cloned on demand.

```
orchestrator design [PATH] [OPTIONS]
```

**Arguments**

- `PATH` — Repo path or git URL to design against. _(default: `.`)_

| Option | Description |
|---|---|
| `--title`, `-t` | Feature title (the thing to build). |
| `--summary`, `-s` | One-line feature summary. |
| `--criterion`, `-c` | Acceptance criterion (repeatable). |
| `--spec` | Read the spec from JSON ({title,summary,acceptance_criteria}) or a .md file. |
| `--out` | Write design.md here (default: print to stdout). |
| `--llm` | Let an LLM write the design (needs a provider; else heuristic). |
| `--refresh` | Re-extract the PKG instead of using the commit cache. |
| `--dialect` | SQL dialect; default: auto-detect. |

### `orchestrator investigate`

Investigation brief: a ticket × the codebase, before you design.

Researches where a ticket lands in the code (knowledge-graph retrieval, with
`file:line` + caller counts), the relevant committed `episteme/` knowledge,
and — when a registry DB is configured — prior-run notes. Deterministic, no
LLM. Pass the ticket via `--source` (e.g. `jira://PROJ-123`) or inline with
`--title`/`--text`. Feed the result into `orchestrator design`.

```
orchestrator investigate [PATH] [OPTIONS]
```

**Arguments**

- `PATH` — Repo path or git URL to research against. _(default: `.`)_

| Option | Description |
|---|---|
| `--source` | Fetch the ticket from a source, e.g. jira://PROJ-123, confluence://<id>, file://./bug.md. |
| `--title`, `-t` | Inline ticket title (instead of --source). |
| `--text` | Inline ticket body (with --title). |
| `--out` | Write the brief here (default: print to stdout). |
| `--refresh` | Re-extract the PKG instead of using the commit cache. |
| `--dialect` | SQL dialect; default: auto-detect. |

### `orchestrator localize`

Fault localization: a stack trace → the repo symbols it names.

Parses a Python traceback / pytest failure, resolves each frame to a
knowledge-graph symbol (`file:line`), and points at the likely fault site
plus who calls it. Reads the trace from `--trace <file>`, `--text`, or stdin.
Deterministic, no LLM — the first step of a root-cause investigation.

```
orchestrator localize [PATH] [OPTIONS]
```

**Arguments**

- `PATH` — Repo path or git URL to resolve the trace against. _(default: `.`)_

| Option | Description |
|---|---|
| `--trace` | File with the stack trace / failing-test output. |
| `--text` | Inline trace text (instead of --trace). |
| `--out` | Write the report here (default: print to stdout). |
| `--refresh` | Re-extract the PKG instead of using the commit cache. |
| `--dialect` | SQL dialect; default: auto-detect. |

### `orchestrator rca`

Root-cause analysis: a bug → grounded RCA + fix approach (no code changed).

Localizes the bug (a stack trace, a `jira://` Bug, or inline text) against
the knowledge graph, then reports the fault site, ranked root-cause
*hypotheses* with evidence (callers, recent churn, the exception), the
regression surface a fix must cover, and a scoped fix approach. Deterministic
by default; `--llm` enriches the hypotheses. It stops at the report — a human
decides whether to build the fix.

```
orchestrator rca [PATH] [OPTIONS]
```

**Arguments**

- `PATH` — Repo path or git URL to analyze against. _(default: `.`)_

| Option | Description |
|---|---|
| `--source` | Fetch the bug from a source, e.g. jira://PROJ-42 (a Bug ticket). |
| `--trace` | File with a stack trace / failing-test output. |
| `--text` | Inline bug text / trace (instead of --trace/--source). |
| `--out` | Write rca.md here (default: print to stdout). |
| `--llm` | Let an LLM enrich the hypotheses (needs a provider; else deterministic). |
| `--refresh` | Re-extract the PKG instead of using the commit cache. |
| `--dialect` | SQL dialect; default: auto-detect. |

### `orchestrator regression`

Regression coverage: what a change should re-test, from the call graph.

For a symbol you're about to change (`--symbol`) or a fault site (`--trace`),
computes the blast radius and splits it into tests that already exercise it
and production code in the radius with no covering test — the regression
gaps. Deterministic, no LLM. Needs a call graph (Python/C/C++/C#/Java/TS).

```
orchestrator regression [PATH] [OPTIONS]
```

**Arguments**

- `PATH` — Repo path or git URL to analyze. _(default: `.`)_

| Option | Description |
|---|---|
| `--symbol`, `-s` | The symbol you're about to change (by name). |
| `--trace` | A stack trace instead — the fault site becomes the target. |
| `--out` | Write the plan here (default: print to stdout). |
| `--refresh` | Re-extract the PKG instead of using the commit cache. |
| `--dialect` | SQL dialect; default: auto-detect. |

### `orchestrator audit`

Codebase-auditor persona: a read-only agentic audit → findings report.

The auditor navigates the repo via the PKG + file reads (no writes) and
reports findings anchored to real file:line. Needs an LLM provider (same
creds the pipeline uses); the model follows `resolve_codegen_model`.

```
orchestrator audit [PATH] [OPTIONS]
```

**Arguments**

- `PATH` — Repo or directory to audit. _(default: `.`)_

| Option | Description |
|---|---|
| `--focus` | What to look for. (default: `general code quality, correctness risks, and security`) |
| `--out` | Write the findings report to this file. |
| `--bundle` | Write the full run bundle (trace + policy blocks) as JSON. |

---

## Requirements intake — source to backlog

Turn a requirements source (Confluence, Jira, Notion, files, OpenSpec, MCP) into intents/specs and (optionally) tracker issues.

### `orchestrator ingest`

Source (Confluence / Notion / local files) → intents → gaps → specs → Jira backlog.

Dry-run by default: fetches the source tree, derives intents, flags
gaps, drafts specs, and prints the would-be Jira issues without writing
anything. Pass --create to write to Jira (refused when gaps gate
approval unless --force).

The lowest-friction source is local files — no SaaS account needed:

    orchestrator ingest --source file://./examples/intake/sample-spec.md

(An LLM key is still required for the intent/spec stages.)

```
orchestrator ingest [OPTIONS]
```

| Option | Description |
|---|---|
| `--source` | Source root, e.g. confluence://<page_id>, jira://<issue-or-project> (read), notion://<page_id>, openspec://<change-id> (spec-driven), or file://./spec.md. |
| `--create` | Create issues for real (default: dry-run preview). |
| `--rules` | Path to a gap-rules YAML (defaults to built-ins). |
| `--force` | Create even when gaps gate the intent-approval bookend. |
| `--refresh` | Re-extract from the source (default: reuse the cached backlog). |

### `orchestrator backlog`

Render the cached backlog + completion progress as markdown (read-only).

Reads the persisted backlog (from a prior ingest / sdlc feature run) and
prints a checkbox ledger: [ ] todo, [~] in progress, [x] done. Pass --out to
write a BACKLOG.md.

```
orchestrator backlog [OPTIONS]
```

| Option | Description |
|---|---|
| `--source` | Source URI whose cached backlog to render, e.g. confluence://<id>. |
| `--out` | Write the markdown here (default: print to stdout). |

### `orchestrator openspec draft`

Bootstrap OpenSpec change proposals FROM an unstructured source (the write-back).

Runs the LLM intake once (source → intents → specs), then renders each as a
structured `openspec/changes/<id>/` proposal (proposal.md + specs delta + tasks).
A human polishes the draft, then implements deterministically:

    orchestrator openspec draft --source confluence://<id> --out ./openspec
    # …review/edit openspec/changes/<id>/…
    orchestrator sdlc feature --source openspec://<id> --safe

```
orchestrator openspec draft [OPTIONS]
```

| Option | Description |
|---|---|
| `--source` | Unstructured source to bootstrap FROM, e.g. confluence://<id>. |
| `--out` | OpenSpec root to write into (changes/<id>/ is created under it). (default: `openspec`) |
| `--refresh` | Re-extract from the source (default: reuse the cached backlog). |
| `--overwrite` | Overwrite existing change files (default: never clobber). |

---

## The SDLC pipeline — build features

The autonomous build path: requirements → code → tests → reviewed PR, with human gates.

### `orchestrator sdlc plan`

The build document for one ticket, and then it stops. Runs intake → investigate →
validity → design, renders the twelve sections of
[`docs/specs/build-document.md`](docs/specs/build-document.md), and writes it to
`.spine/plans/<INTENT>-build.md`. No worktree, no codegen, no spend, and the tracker
is not touched — the ticket moves when work begins, not when someone thinks about it.

This is the gate *before* code exists; `--review` on `autorun` still gates the diff
after. Every section carries where it came from — quoted, computed, inferred or human —
and a section it cannot establish says so instead of vanishing.

What each section is made of:

| # | Section | From |
|---|---|---|
| 1–2 | Requirement, Intent | the ticket, quoted; the spec writer |
| 3 | Root cause | `sdlc/rca.py` — the exception, the fault site, ranked hypotheses. **Omitted** when nothing localizes |
| 4 | PKG | `FactStore`, ending with a verdict on whether the investigation brief is trustworthy for this ticket |
| 5 | Blast radius | `sdlc/impact.py` — a diagram, then *Reading it* / *Containment* / *Caveat* / *Evidence* (coverage today, endpoints crossed, regression surface, recent history, docs affected) |
| 6–7 | Design, Files | `sdlc/design.py` and the paths the spec states |
| 8 | Acceptance criteria | the spec, in three states — see below |
| 9 | Facts the generator needs | not established; no phase owns it yet |
| 10 | Codegen prompt | the system prompt, the payload manifest, and the context budget in bytes and percent |
| 11 | Token usage & cost | measured from this ticket's own runs where there are any, estimated from the installed catalog where there are not |
| 12 | Confidence | two bands with their basis — *is the analysis right* (five checks) and *will a run complete* (the base rate from the journey) |
| — | Journey | appended by each run, below the twelve. See `sdlc autorun` |

With `--spec` there is no LLM anywhere in this path: the same commit and the same
spec produce a byte-identical document, which is what makes the kept history
meaningful. Re-running overwrites in place; a document it replaces is snapshotted
under `history/`, keyed by the commit it was derived at.

```
orchestrator sdlc plan --spec ./SSPN-49.json --path .
```

| Option | Description |
|---|---|
| `--spec` | A hand-written spec (JSON). Skips intake entirely, and makes the run LLM-free. |
| `--source` | Derive the spec instead, e.g. `jira://<issue-key>`. One of `--spec`/`--source` is required. |
| `--intent` | Intent id to plan (default: the first). |
| `--path` | Repo to reason about — the graph the plan is grounded in. (default: `.`) |
| `--out` | Where the document goes (default: `<repo>/.spine/plans`). |
| `--language` | Target language named in the codegen-prompt section. (default: `python`) |
| `--quiet` | Write the document without printing it. |

**Section 8 takes one extra spec field.** `met_criteria` maps a stated criterion's exact
text to the evidence that it is *already* satisfied
(`"src/orchestrator/cli.py:134 — _check() already handles this"`). Those criteria stay on
the page, marked, rather than being quietly dropped — a run that reports them met having
changed nothing is the failure the document exists to catch. A key matching no criterion
is reported as a mismatch, not silently ignored.

**Nothing here is a model call** when `--spec` is used. That is what makes the digest in
`sdlc approve` meaningful, and it is why section 12 is a band with its basis rather than a
score: a model-written number would move on every render and stale every approval.

### `orchestrator sdlc approve`

Record that a human read the build document and decided. Writes
`.spine/plans/<INTENT>-approval.json` beside the plan.

```
orchestrator sdlc approve SSPN-49 --note "criteria reconciliation checked"
```

| Option | Description |
|---|---|
| `INTENT` | Intent id whose plan you are deciding, e.g. `SSPN-49`. **Required.** |
| `--path` | Repo the plan was written for. (default: `.`) |
| `--by` | Who is deciding. (default: `git config user.name`) |
| `--note` | Why — recorded with the decision. |
| `--reject` | Record a rejection instead of an approval. |
| `--out` | Where the plan lives. (default: `<repo>/.spine/plans`) |

The decision is bound to a **digest of the document body**, so a plan that changes
afterwards reads as *stale* rather than silently still approved. `sdlc autorun`
re-derives the plan and refuses when the digest no longer matches — an approval that
survives the code moving underneath it approves a document nobody has read.

### `orchestrator sdlc autorun`

One ticket, all the way through: research → design → validity gate → code → tests
→ review → PR. The stages are the commands below, called in order with the same
spec, and each result recorded.

Default `--safe` makes no external write anywhere in the chain: a local branch and
commit, dry-run tracker, no push. `--live` creates or adopts the issue, pushes the
branch, and opens a PR.

**It refuses to start without an approved build document** for the ticket (`sdlc plan`,
then `sdlc approve`). The plan is re-derived and re-digested, so an approval whose document
no longer matches the code refuses too. `--no-plan-gate` skips the check and says so.

**Each stage appends to the ticket's journey** — `.spine/plans/<INTENT>-journey.jsonl`,
rendered beneath the twelve sections the next time `sdlc plan` runs. Append-only: no stage
rewrites an earlier one, and when implement touches files the design did not name, that
disagreement is recorded rather than smoothed over. The run outcome carries the tokens and
the actual spend, which is what section 11's estimate is eventually judged against.

`--source` is required even when `--spec` supplies the spec; with `--spec` it is only the
URI the run is filed against, and intake never reads it.

```
orchestrator sdlc autorun --source jira://PROJ-14 --issue PROJ-14 --path . --safe --max-cost 10
```

| Option | Description |
|---|---|
| `--source` | Source root, e.g. `jira://<issue-key>`, `confluence://<page_id>`, `file://./spec.md`. **Required.** |
| `--issue` | Adopt an existing tracker issue instead of creating one. |
| `--intent` | Intent id to implement (default: the first). |
| `--repo` | Git URL to branch from (default `$SDLC_REPO_URL`). |
| `--path` | Repo to reason about — the graph the run is grounded in. (default: `.`) |
| `--live` / `--safe` | Write for real, or make no external write. (default: `--safe`) |
| `--max-refine` | Correction attempts allowed **per check** — a red suite, a type error and a coverage gap each get their own allowance, so one cannot starve another. (default: `5`) |
| `--review` / `--no-review` | Show the diff and ask before committing or pushing anything. (default: `--no-review`) |
| `--base` | Branch to build on **and** open the PR into (default `$SDLC_PR_BASE`, else the remote's default branch). The run's worktree is cut from this, so on a repo that merges to `develop`, leaving it unset builds the change on `main`. |
| `--language` | Target language (auto detects). (default: `auto`) |
| `--out` | Where run artifacts go (default: a run dir under the temp dir). |
| `--resume` | Continue a run by id — adopts the issue it already created. |
| `--max-cost` | Cap LLM spend (USD) for this run; exhausting it parks the run. |
| `--spec` | Implement a hand-written spec (JSON) instead of deriving one from the source. Intake is skipped entirely and recorded as `skipped`. |
| `--plan-gate` / `--no-plan-gate` | Refuse to build unless a human approved this ticket's build document (see `sdlc approve`). The plan is re-derived and re-digested, so an approval that no longer matches the code refuses too. (default: `--plan-gate`) |

**Implementing a spec you wrote (`--spec`).** Normally intake derives the spec from
the source document. Pass `--spec path.json` to supply it directly — the pipeline
builds exactly what the file says, and the `[intake]` line reports `skipped` rather
than implying a document was read.

Use it when the spec is already settled (a remediation, something agreed in review),
or when intake itself is what the ticket is about — letting a defective stage write
the specification for its own repair is circular.

```json
{
  "title": "Keep invented criteria separable",
  "intent_id": "SSPN-31",
  "summary": "Intake appends inferred criteria to the stated ones, losing provenance.",
  "acceptance_criteria": [
    "A criterion the source did not state is emitted as proposed, not as fact."
  ],
  "technical_notes": "FeatureSpec gains a proposed_criteria field."
}
```

Only `title` and `acceptance_criteria` are required; `intent_id` defaults to the
filename stem. The file is JSON rather than markdown on purpose — it is validated
strictly, so a misspelled `acceptance-criteria` is an error naming the valid fields
instead of a run that proceeds with no criteria and passes by default. An empty
criteria list is refused for the same reason.

The `--source` argument is still required and still what the run is filed against.

**Reading the output.** Each stage prints a bracketed line. The ones that decide
whether a change ships:

| Line | What it means |
|---|---|
| `[validity]` | The gate's verdict. Only `PROCEED` continues; `DUPLICATE`, `CRITERIA_WRONG`, `UNLOCALIZED` and `TOO_BIG` park the run with evidence. |
| `[run_tests #N]` | A pytest run. `refine` follows a red one. |
| `[typecheck]` | The repo's own type checker, over the lines this change touched. Errors go back to `refine` exactly like a test failure. |
| `[proof]` | The generated tests are re-run with the change reverted. They must fail — tests that pass without the change do not exercise it. |
| `[coverage]` | Each changed file is reverted on its own. Anything that leaves the suite green is a file no test reaches, and goes back to `author_tests`. |
| `[judge]` | Does the change satisfy the ticket's acceptance criteria? `REQUEST_CHANGES` sends the blockers back through `revise`; a verdict that cannot be read blocks the run rather than passing it. |
| `[gate]` | `--review` only: waiting for you. Nothing has been committed yet. |

**`--review` is the last gate before the first write.** It prints the full diff,
asks once, and defaults to no. Every other check above is a model or a heuristic;
this one is you. It **fails closed** when there is no terminal to ask on, so an
unattended run (cron, a background shell, the MCP server) stops rather than
assuming yes — pass `--review` only from an interactive session.

**Resuming.** A parked or failed run continues with `--resume <run-id>`, keeping
its id and adopting the issue it already created. A run parked on an approval
resumes only after the approval is decided (`orchestrator sdlc runs approve`).
Note that a resumed run re-runs its stages and builds a **fresh worktree** — it
does not continue editing the previous one.

### `orchestrator sdlc feature`

Linear pipeline for ONE intent, end to end.

source → intent → spec → Jira issue → worktree branch → code generation
→ test + refine → commit → (push + PR) → Jira update → ready for deployment.

Default --safe makes no external write: it creates a local branch, commits
the generated + tested code, and prints the diff. Pass --live to create the
Jira issue, push the branch, open a real PR, and comment the PR link back on
the issue.

Pass --issue <KEY> when the work is already tracked: the run adopts that
issue instead of creating a second one for the same story.

```
orchestrator sdlc feature [OPTIONS]
```

| Option | Description |
|---|---|
| `--source` | Source root, e.g. confluence://<page_id>, jira://<issue-or-project> (read), notion://<page_id>, openspec://<change-id> (spec-driven), or file://./spec.md. |
| `--intent` | Intent id to implement (default: first derived intent). |
| `--repo` | Git URL to branch from (default $SDLC_REPO_URL; scratch if unset). |
| `--model` | Codegen model (default: $SDLC_CODEGEN_MODEL or the adapter default). |
| `--max-refine` | Correction attempts allowed **per check** — a red suite, a type error and a coverage gap each get their own allowance, so one cannot starve another. (default: `5`) |
| `--live` | Write for real: create the Jira issue, push the branch + open a PR, comment on Jira. Default --safe stays local (branch + commit + diff, dry-run Jira, no push). |
| `--issue` | Adopt an existing tracker issue (e.g. SSPN-9) instead of creating one — the branch, PR, comment and transition all land on it. |
| `--base` | Branch to build on **and** open the PR into (default `$SDLC_PR_BASE`, else the repo's default branch). The worktree is cut from this — see `sdlc autorun` above. |
| `--layout` | Target structure: auto (scaffold only empty repos), new (always scaffold a src/<pkg>/ skeleton), or existing (follow the repo's layout). (default: `auto`) |
| `--package-name` | Override the scaffold package name (default: derived from repo). |
| `--spec` | Implement a hand-written spec (JSON) instead of deriving one from the source — see `sdlc autorun` above for the format. |
| `--refresh` | Re-extract intents from the source (default: reuse the cached, deterministic backlog). |
| `--language` | Target language: auto (detect), python, java, typescript, csharp, c, cpp, go, or sql. (default: `auto`) |

### `orchestrator sdlc run`

Start the Block-C SDLC workflow on the sdlc-tasks queue.

Generates a fresh sdlc_id and starts `SDLCWorkflow` with workflow id
`task-{sdlc_id}` — the id convention the REST `/v1/approvals/*` API
relies on to route gate decisions back to the workflow. The two human
gates persist real, decidable ApprovalRequest rows
(`sdlc-{sdlc_id}-0` for intents, `sdlc-{sdlc_id}-1` for merge).

A worker must be running on the sdlc-tasks queue
(`python -m orchestrator.sdlc.worker`).

```
orchestrator sdlc run [OPTIONS]
```

| Option | Description |
|---|---|
| `--source` | Source root, e.g. confluence://<page_id>, jira://<issue-or-project> (read), notion://<page_id>, openspec://<change-id> (spec-driven), or file://./spec.md. |
| `--actor` | Who is launching the run (recorded in audit rows). (default: `cli`) |
| `--create-jira` | Write Jira issues for real (default: dry-run synthetic keys). |
| `--wait` | Block until the workflow finishes and print its result (default: return after start). |
| `--max-features` | Cap features per run (0 = unlimited). |
| `--max-parallel` | Feature children per batch (1 = sequential). (default: `2`) |

### `orchestrator sdlc complete`

Close the Jira issue for a merged PR (the merge → Done bookend).

The linear `sdlc feature` path stops at an open PR for a human to review
and merge; this reconciles Jira afterwards. Verifies the PR is merged (via
`gh`), derives the issue key from the PR's head branch
(`feat/<sdlc_id>/<KEY>`) unless `--issue` is given, then transitions the
issue and comments the merge. Needs an authenticated `gh`.

```
orchestrator sdlc complete [OPTIONS]
```

| Option | Description |
|---|---|
| `--pr` | The merged PR URL whose linked issue to close. |
| `--issue` | Issue key (default: derived from the PR branch feat/<id>/<KEY>). |
| `--status` | Target Jira status to move the issue to. (default: `Done`) |
| `--allow-unmerged` | Transition even if the PR is not merged yet. |

### `orchestrator sdlc runs`

Inspect what `sdlc autorun` has running, parked or abandoned.

`reap` reports what a dead run left behind — worktree, branch, issue — and changes nothing: a
worktree may hold the only copy of someone's work, and a ticket's status is an outward-facing
write. Cleaning up stays a human's call.

```
orchestrator sdlc runs [ACTION] [RUN_ID] [OPTIONS]
```

**Arguments**

- `ACTION` — `list` | `show <run-id>` | `reap` | `approvals` | `approve <approval-id>` — inspect and decide autorun's durable state. _(default: `list`)_
- `RUN_ID` — Run id, or approval id for `approve`.

| Option | Description |
|---|---|
| `--reject` | Reject rather than approve. |
| `--note` | Why — recorded on the decision. |

### `orchestrator sdlc baseline`

Score the run agent against a corpus of tickets whose right answer is known.

Deterministic and free: the validity gate reads each ticket and a real graph, and every case
has an argued expected verdict. Run metrics come from the durable run records — observations
of what actually ran, not a simulation.

False refusals and missed refusals are counted separately. A single accuracy number would let
one hide behind the other, and they cost very different things.

```
orchestrator sdlc baseline [OPTIONS]
```

| Option | Description |
|---|---|
| `--path` | Repo whose graph the gate reads. (default: `.`) |
| `--json` | Emit the numbers as JSON. |

### `orchestrator sdlc address-review`

Read a PR's human review comments, revise the change, and push the fix.

Checks out the PR branch into a throwaway clone, feeds the reviewers'
comments to codegen, re-drives to green (tests + preflight), and pushes a
follow-up commit to the PR branch. Out-of-band and human-triggered — the
autonomous run's merge gate stays the bookend. Needs SDLC_CODEGEN=llm and
an authenticated `gh`.

```
orchestrator sdlc address-review [OPTIONS]
```

| Option | Description |
|---|---|
| `--pr` | The PR URL to address review comments on. |
| `--repo` | Repo clone URL (defaults to SDLC_REPO_URL). |
| `--bot-login` | Skip this author's own comments (the agent's account). |
| `--max-refines` | Refine cycles to reach green. (default: `3`) |

### `orchestrator sdlc remediate`

Spine Seam 3: a drift report → governed remediation runs (one per affected entity).

Plans scoped, guardrailed remediation tasks from the infodrift report (Phase 2) and
runs each through the codegen pipeline with the task as the spec (intake skipped),
grounded by ontomesh (Seam 1) when configured. Default --safe is human-gated: it
leaves a branch + diff to review; --live opens PRs.

```
orchestrator sdlc remediate [OPTIONS]
```

| Option | Description |
|---|---|
| `--report` | Path to an infodrift full_report JSON. |
| `--mappings` | Path to the confirmed code↔ontology MappingStore JSON. (default: `spine-mappings.json`) |
| `--repo` | Git URL to branch from (default $SDLC_REPO_URL). |
| `--min-severity` | Only remediate findings at/above: warning \| critical. (default: `warning`) |
| `--live` | --safe (default) leaves a reviewable branch+diff per entity (human-gated); --live opens PRs. |

---

## MCP — external tools

Consume onboarded Model Context Protocol servers (governed, audited).

### `orchestrator mcp list`

Discover the allow-listed tools across all configured MCP servers.

```
orchestrator mcp list [OPTIONS]
```

| Option | Description |
|---|---|
| `--config` | Path to an mcpServers JSON file (default: $ORCHESTRATOR_MCP_CONFIG or ./mcp.json). |

### `orchestrator mcp contracts`

Show the ToolContract derived for each onboarded MCP tool (governance view).

Each input is rendered `name (type)`, with the type read from the server's own
JSON Schema at display time — `string|null` for a union, and `any` when the
schema declares no top-level type (an `anyOf`, a `$ref`, or a tool with no
schema). A parallel `input_types` map carries the same labels keyed by argument
name. Display-only: nothing is stored, and `mcp call` serialisation is unchanged.

```
orchestrator mcp contracts [OPTIONS]
```

| Option | Description |
|---|---|
| `--config` | mcpServers JSON file path. |

### `orchestrator mcp call`

Invoke one onboarded MCP tool (server:tool) with JSON arguments.

```
orchestrator mcp call [TOOL] [OPTIONS]
```

**Arguments**

- `TOOL` — Qualified tool name: server:tool.

| Option | Description |
|---|---|
| `--args` | JSON object of tool arguments. (default: `{}`) |
| `--config` | mcpServers JSON file path. |

### `orchestrator mcp ingest-db`

Introspect a DB MCP server's schema into PKG data-layer facts (Entity/Field).

```
orchestrator mcp ingest-db [OPTIONS]
```

| Option | Description |
|---|---|
| `--server` | Name of an onboarded DB MCP server. |
| `--query-tool` | The server's SQL query tool name. (default: `query`) |
| `--sql-arg` | The query tool's SQL argument name. (default: `sql`) |
| `--schema` | DB schema to introspect. (default: `public`) |
| `--config` | mcpServers JSON file path. |

---

## Registry — templates & contracts

Manage reusable capability templates and API contracts in the registry service.

### `orchestrator template register`

Register a new agent template from a JSON or YAML file.

```
orchestrator template register [FILE]
```

**Arguments**

- `FILE` —

### `orchestrator template list`

List agent templates.

```
orchestrator template list [OPTIONS]
```

| Option | Description |
|---|---|
| `--tag` | Filter by tag. |
| `--status` | Filter by lifecycle state. |

### `orchestrator template show`

Show the latest published version (or a specific version).

```
orchestrator template show [ID] [VERSION]
```

**Arguments**

- `ID` —
- `VERSION` —

### `orchestrator template publish`

Promote a draft to published.

```
orchestrator template publish [ID] [VERSION]
```

**Arguments**

- `ID` —
- `VERSION` —

### `orchestrator template deprecate`

Mark a published version as deprecated.

```
orchestrator template deprecate [ID] [VERSION]
```

**Arguments**

- `ID` —
- `VERSION` —

### `orchestrator contract register`

Register a new tool contract from a JSON or YAML file.

```
orchestrator contract register [FILE]
```

**Arguments**

- `FILE` —

### `orchestrator contract list`

List tool contracts.

```
orchestrator contract list [OPTIONS]
```

| Option | Description |
|---|---|
| `--tag` | Filter by tag. |
| `--status` | Filter by lifecycle state. |

### `orchestrator contract show`

Show the latest published version (or a specific version).

```
orchestrator contract show [ID] [VERSION]
```

**Arguments**

- `ID` —
- `VERSION` —

### `orchestrator contract publish`

Promote a draft to published.

```
orchestrator contract publish [ID] [VERSION]
```

**Arguments**

- `ID` —
- `VERSION` —

### `orchestrator contract deprecate`

Mark a published version as deprecated.

```
orchestrator contract deprecate [ID] [VERSION]
```

**Arguments**

- `ID` —
- `VERSION` —

---
