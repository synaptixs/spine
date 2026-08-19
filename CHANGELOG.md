# Changelog

All notable changes to this project are documented here. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/); the package is `synaptixs-spine`
(import/CLI stay `orchestrator`).

## 3.20.0 — The evidence drives the run

3.19.0 measured what the code graph is worth. This release puts it in front of the work: every
SDLC run now begins with a **deterministic research pass**, and design, codegen and the
acceptance criteria are judged against what it found instead of against the ticket's own
account of itself.

Phases 1, 2a and 2b of [`graphir-sdlc-workflow.md`](docs/specs/graphir-sdlc-workflow.md).

### Fixed — four defects in how a ticket reached the code

The pipeline checked a ticket against the graph **once**, at `validity`, and thereafter the
*ticket* drove every downstream stage. Each of these had shipped for months:

- **Root-cause analysis never ran.** `build_rca` is deterministic and produces a fault site,
  ranked hypotheses and a regression surface — and `sdlc autorun` did not call it. An autonomous
  bug run did no root-cause work at all. It now runs on every ticket.
- **The blast radius described the design's own guess.** `design.py` computed impact from its
  own `files_to_touch`, so a wrong proposal produced a faithful analysis of a fiction — and it
  read as verification. It is now computed from **where the ticket lands**, before design runs,
  and handed in. `design.py` no longer computes one, and a test fails if that call comes back.
- **The research was flattened to filenames.** `Landing{name, where, kind, callers, module}`
  became `where.split(":")[0]` before anything downstream saw it, so design and codegen received
  file paths where the research had proved symbols. The whole fact now survives the stage
  boundary.
- **Acceptance criteria were taken on trust.** They originate in intake — a model reading a
  document, not the code — and were read straight through by design, codegen and grounding.
  Each one is now bound to a symbol and a `file:line`.

### Added — Evidence, and two refusals

- **`Evidence`** — one artifact per run composing `investigate` + `rca` + `blast_radius`. No
  model, so it costs nothing and is reproducible at a commit. Written **even when the run parks**,
  because a parked run's evidence is what a human is being asked to judge. Lands as `evidence.md`
  and `evidence.json`.
- **Criterion binding refuses a false premise.** A criterion naming code the graph does not hold
  stops the run — a criterion nobody can locate is a test nobody can write. **Prose never parks a
  run:** CamelCase, `ALL_CAPS` env vars, tool names and plain English are not claims about your
  code and are reported as "not a code claim". The rule is borrowed from the doc-drift
  reconciler rather than invented, so there is one definition of "names a symbol".
- **A validator on the design.** A design naming a directory or module the repository does not
  have parks the run instead of reaching codegen. A new file in an existing directory is fine —
  that is a file being created. **0 false positives across 100 measured runs.**
- **`orchestrator sdlc explain <run>`** renders the graph a run actually executed, skipped nodes
  included, from a typed per-node `Case` with the digest of what each node produced.
- **`orchestrator sdlc workflow`** prints the validated SDLC profile — `sdlc/profiles/default.yaml`,
  the pipeline as data rather than as Python stage ordering.
- **`NodeType.TOOL`** in GraphIR: a deterministic node with no model call, no network, no clock
  and no RNG, whose output is digested — so "deterministic" is checkable rather than asserted.

### Measured — and one feature declined

- **Phase 1 gate:** 0 divergences over 20 runs across 5 commits, plus a determinism test that
  renders under five `PYTHONHASHSEED` values in subprocesses. **Free** — the compared nodes are
  deterministic.
- **Phase 2a gate:** verdict parity over 20 runs across 5 commits, 0 unexplained mismatches, and
  a parking-rate delta showing 5 new parks, all the one ticket naming a symbol no repository has.
  Also free.
- **Phase 2b: the design promotion was measured and declined.** A 100-run A/B across two frontier
  models found no acceptance difference a 50-run arm can resolve, a held-out rate favouring the
  deterministic design (**0.60 [0.46, 0.72]** against **0.40 [0.28, 0.54]**), and **1.98× the
  cost**. `design` stays deterministic and the model-call budget stays at three. The validator
  ships regardless. The held-out gap is one model, so the finding is *"helped neither and hurt
  one"* — full result, including what would reopen it, in
  [`design-promotion-ab-results.md`](docs/specs/design-promotion-ab-results.md).

### Fixed — three defects the benchmark harness found before it ran

Pre-flighting the A/B with four real calls, about $2.50, caught three things that would each have
produced a confident wrong number:

- **The benchmark had no design stage.** It drives `LLMCodegenAdapter` directly and never called
  `autorun`, so `produce_design` was never in its path — the 200-run grounding study measured
  codegen with no design in the loop whatsoever.
- **`_llm_design` had never worked.** It parsed with `json.loads`, the model answers inside a
  markdown fence, and `produce_design` catches every exception and returns the deterministic
  design. The model arm would have **silently measured the skeleton and reported it as the
  model's work.** It now uses codegen's tolerant loader, and a fallback is recorded as the
  *absence* of a measurement.
- **The validator refused prose.** Models write sentences into `data_changes`; a sentence
  containing a path was judged as a path. Every ticket in the model arm would have been rejected
  and reported as the model inventing code.

### Changed

- **`assets/spine-architecture.png` is generated, not drawn.** Its predecessor was stamped
  `3.8.4`, claimed `41 commands` against 53, and carried `7 node kinds · 9 edge kinds` two
  releases after `ARCHITECTURE.md` had corrected them to **8 and 11**. Every number is now read
  from source at render time by `scripts/render_architecture_svg.py`, the SVG is the committed
  source, and **CI fails if the image no longer matches**.
- `CLI_REFERENCE.md` documented `SPINE_IR_SHADOW` and a `shadow.json` artifact, both removed in
  Phase 2a. Replaced with what a run actually writes.
- `SPINE_SDLC_IMPERATIVE=1` restores the pre-3.20 path for one release: stages re-derive their
  own view, and neither refusal above can park a run. Documented in `.env.example` and
  `OPERATIONS.md`.

### Note for operators

Two verdicts can park a ticket that previously built — an unbound acceptance criterion, and a
design naming a place your repository does not have. Both park with their evidence on disk;
`sdlc runs approve` continues a run you disagree with, and `SPINE_SDLC_IMPERATIVE=1` turns both
off wholesale.

## 3.19.0 — What the graph is worth, measured; and four ways it was wrong

3.18.x claimed the code graph makes generated code better. This release **measures that claim**
in a controlled A/B — and every extraction defect below was found by pointing the measurement at
codebases that are not this one.

### Added — the grounding claim is now a number

- **200 runs, 2 frontier models, 5 passes each, grounded against an identical ungrounded
  control.** Every new module that integrated correctly came from a grounded run — **29/50**
  under `mypy --strict` plus correct placement; the same tickets ungrounded produced **none**.
  On tickets that already name the target file the two arms tie (**98/100**), which rules out a
  generic more-context effect and locates the payoff precisely where the model cannot see.
  Method, Wilson bounds and the abort accounting are in
  [`codegen-model-comparison-results.md`](docs/specs/codegen-model-comparison-results.md).
- **Replicated on an unrelated external codebase**, so the result is not an artefact of Spine
  grading itself — see
  [`external-repo-grounding-results.md`](docs/specs/external-repo-grounding-results.md).
  Combined across both: **47/68 grounded, 3/68 ungrounded**, with the `edit`-ticket control at
  122/124 either arm.
- **Held-out grading.** Each ticket's acceptance suite is authored separately from the ticket;
  the model never writes the test that grades it. A pass that never reached the model is
  recorded as **aborted and excluded**, not silently counted as a failure.
- `scripts/bench_aggregate.py` — Wilson score intervals (they stay sensible at small *n* and
  near 0 or 1, where the normal approximation does not), aborted passes excluded, and a warning
  below five passes.

### Added — preflight judges the change, not the repository

`sdlc` preflight now captures a **baseline** of `ruff check`, `ruff format` and `mypy` findings
before generation and diffs against it, so a repo that already has findings no longer fails
every build on pre-existing debt. A check whose baseline could not be captured is reported as
skipped rather than assumed clean. Design record:
[`preflight-baseline-diff.md`](docs/specs/preflight-baseline-diff.md).

### Fixed — extraction defects, each found on real code

- **A SQL Server database project was 95% invisible.** SSMS scripts UTF-16 by default and
  separates batches with `GO`, a client directive that is not valid T-SQL — so an entire file
  failed to parse and was skipped in silence. Encoding is now sniffed from the BOM and files are
  split on `GO` and parsed batch by batch. On one real project: **676 of 709 `.sql` files
  skipped → 0**, `READS` 2 → 186, `WRITES` 0 → 26. Neither cause was a dialect problem, so
  `--dialect tsql` never helped.
- **931 dangling edges on a real C#/TypeScript/SQL codebase.** TypeScript emitted edges to
  imported symbols with no node to land on (**854** of them); C# qualified an external base type
  into the deriving type's own namespace, inventing a target that does not exist. Both now
  resolve to explicit external nodes.
- **`extractor.py` discarded `finalize()`'s return value** — a front-end's whole-repo post-pass
  computed its corrections and threw them away. C# is the first front-end to need one, which is
  how this surfaced.
- **TypeScript resolved relative imports three different ways**, so the same module reached the
  graph under up to three ids. One resolver now serves declaration, call and type resolution.
- **`state` rendered a different report for identical input.** Areas tying on their symbol
  counts sorted out of a `set` on score alone, and Python randomises string hashing per process
  — five identical runs produced three distinct outputs. The sort key is now total. The
  regression test renders under five `PYTHONHASHSEED` values **in subprocesses**, which is the
  only way to see the bug: the seed is fixed for the life of a process. `state` is byte-stable
  again, as [ARCHITECTURE.md](ARCHITECTURE.md) always claimed; the 3.18.1 caveat is withdrawn.
- **LiteLLM client.** `reasoning_effort` is sent only to models that declare support (from
  `litellm.model_cost`, configurable with `ORCHESTRATOR_REASONING_EFFORT`, default `high`);
  `response_format=json_object` is no longer sent alongside function tools, which OpenAI
  rejects; every completion is wrapped in `asyncio.wait_for`, so a hung request fails the pass
  instead of hanging the run; and the served model name is recorded from the response rather
  than assumed from the request.

### Documented

- [`capability-matrix.md`](docs/specs/capability-matrix.md) and
  [`competitive-landscape.md`](docs/specs/competitive-landscape.md) — Spine's column verified
  against source, every other column marked public-documentation-only. RBAC is scored **🟡**
  after an audit found `has_role` called at exactly one site, while multi-tenancy holds at ✅.
- [`parsing-and-the-pkg.md`](docs/specs/parsing-and-the-pkg.md) — why every front-end is a real
  parser (AST or tree-sitter or `sqlglot`) and never a regex, written for an engineering
  audience.
- `ORCHESTRATOR_REASONING_EFFORT` documented in `.env.example`, `CLI_REFERENCE.md` and
  `USER_GUIDE.md`; the SQL Server capability in `KNOWLEDGE_GRAPH.md` §4 and `FEATURES.md`.

## 3.18.1 — The documentation catches up, and corrects itself

No code changes. 3.17.0 and 3.18.0 shipped two releases of PKG work without the user-facing
documentation following, and this is that sweep across all eleven documents. It **corrects more
than it adds**, because auditing the docs against the CLI and the fact vocabulary turned up
claims that were simply untrue.

### Fixed — documentation that was wrong, not merely incomplete

- **`CLI_REFERENCE.md` was missing four flags and two whole commands.** `--check` and
  `--intents` on `understand`, `--no-timestamp` and `--intents` on `state`, and the commands
  `sdlc baseline` and `sdlc runs` appeared nowhere. The command count read 48; it is **51**.
  The file also claimed to be *"auto-generated from the CLI"* — **there is no generator**, and
  that false claim is precisely how it drifted this far without anyone checking. It now says it
  is maintained by hand, and that `--help` wins any disagreement.
- **`ARCHITECTURE.md` undercounted the fact vocabulary** — "7 node kinds and 9 edge kinds"
  against an actual **8 and 11**. `Intent`, `CONSUMES` and `SERVES` had been shipping
  unmentioned.
- **`KNOWLEDGE_GRAPH.md` carried a literal `</content>` tag at end of file**, omitted Go from
  its parser list, and documented the deprecated `--db` flag instead of `--out`.
- **`CODEX_GUIDE.md`'s `language` parameter listed six values**, missing `go` and `sql`;
  `CLAUDE_GUIDE.md` still said "seven languages".
- **`FEATURES.md` marked the runtime and invention oracles as fully shipped.** Both are
  **Python-only**, which is a material qualification on any accuracy claim made from them.

### Added

- **Measured accuracy, stated wherever the graph is described** — precision **1.00 on every
  node kind and every edge kind across all eight front-ends**, with `CALLS` recall from 1.00
  (C, SQL) to 0.50 (TypeScript). README, USER_GUIDE, KNOWLEDGE_GRAPH (a new §10), ARCHITECTURE
  and EXAMPLE now carry the numbers rather than the adjective "grounded".
- **The four oracles, the `--check` currency gate, and the language extras** documented in the
  guides and OPERATIONS, including that a missing tree-sitter extra makes a language
  *unmeasured* rather than scored zero.

### Documented as limits, rather than smoothed over

Each of these would otherwise be read as a guarantee it does not make:

- **`state` is not byte-stable.** Components tying on their symbol counts sort out of a `set`
  with a non-total key, so identical runs differ — five runs produced three distinct outputs.
  `PYTHONHASHSEED=0` is the workaround. `ARCHITECTURE.md`'s determinism claim now carries the
  exception instead of overstating it; `understand` is unaffected.
- **`--intents` has no reader.** Not merely opt-in: nothing renders the facts, no export format
  emits them, and the only visible effect is a count in the graph-size line. Saying "opt-in"
  alone implied a payoff that is not there.
- **`invention` and `runtime` reporting `0` on a non-Python repository means *not measured*,
  not *clean*.** Every candidate returns unexaminable and the total prints as zero.

### Note on the design records

`docs/specs/pkg-accuracy-gaps.md` and `docs/specs/comprehension-test-plan.md` are committed
rather than left untracked. `understand` ingests markdown from disk regardless of git, so an
untracked *or gitignored* file still becomes a `Doc` node and makes the local knowledge base
disagree with CI's — verified: gitignoring them changed nothing and `--check` still failed on
the same three files. Tracking them is what makes local and CI agree.

## 3.18.0 — Every front-end, and one finding withdrawn

**Scope, first.** 3.17.0 measured two of the eight front-ends. This measures **all eight** —
and that sentence needs a boundary drawn around it, because it is easy to read as more than it
is. The numbers come from **19 hand-written fixture repositories**, two cases per language. They
describe *this extractor's behaviour on shapes we chose*, not anyone's real codebase. The one
oracle that measures real code — the runtime tracer — still covers Python only.

### Added

- **A corpus for every front-end Spine supports.** `java`, `csharp`, `c`, `cpp`, `go` and `sql`
  each gain a control case and one hard shape. Every node kind and every edge kind except
  `CALLS` scores 1.00/1.00 in every language.

  | language | `CALLS` precision / recall |
  |---|---|
  | `c` `sql` | 1.00 / 1.00 |
  | `python` | 1.00 / 0.73 |
  | `cpp` `csharp` `go` `java` | 1.00 / 0.67 |
  | `typescript` | 1.00 / 0.50 |

  `CALLS` is the only kind needing *resolution* rather than parsing, and the only one that
  loses anything. Every language's remaining loss is the documented instance-dispatch skip.

- **CI measures every front-end it ships code for.** The six tree-sitter extras are now
  installed in CI. Before this, only `python` and `sql` front-ends registered there — so 89
  tests skipped, and the TypeScript figures in the committed baseline had been measured once on
  a developer machine and skipped by every run since. A TypeScript regression could not have
  failed a build.

- **`--intents` on `understand` and `state`.** The intent scan is 3× faster (23.0s → 7.6s):
  `git blame` runs concurrently at 8 workers, and the commit lookup is one `git log` instead of
  one per commit. It stays **opt-in** — nothing renders or queries `Intent` facts yet, so
  default-on would cost 10s per build for byte-identical output. It becomes a default when
  something reads it.

### Fixed

- **The graph no longer asserts a call to anything that does not exist — in any language.**
  Two front-ends invented call targets for a name they could not resolve:

  - **Python** emitted `py:{name}` for any bare call that was not a builtin, an import, or a
    module-level def — every parameter, local and nested function. **497 edges here, 3.16% of
    the call graph.** The resolver already stated the right rule for ambiguous attribute
    chains — *skip rather than guess* — three lines below; it simply was not applied to names.
  - **C** emitted `c:{name}` for a call through a function-pointer parameter. C could not take
    Python's fix: an unresolved callee in C is usually a function declared in a header and
    linked from another translation unit, and skipping those would silence every
    cross-translation-unit call in every C repository. The test is *"did this function bind the
    name"*, not *"can we resolve it"*.

  Python `CALLS` precision 0.80 → 1.00, C 0.67 → 1.00, with recall unchanged in both — the
  evidence that nothing true was removed.

- **Per-file parity credits an endpoint to every file that declares it.** `Endpoint` ids are
  keyed on verb+path, so services sharing a route collapse into one node; counting by node
  provenance credited one file and reported every other as short. Attribution now follows the
  `EXPOSES` edge. Shortfall 5 → 0.

### Withdrawn

- **"Two live routes invisible to the graph" (3.17.0) was false.** `GET /healthz` and
  `GET /readyz` were always extracted and `EXPOSES` always reached every handler. The finding
  came from reading *"this file declares 2, graph holds 0"* and not checking whether the nodes
  existed elsewhere — they did. The entire shortfall was the artifact described above.

  It is recorded rather than deleted, here and in the design records, because of what it is: a
  measurement built to catch under-reporting produced a false finding, and nothing in the check
  could tell the difference. That is the failure this work exists to prevent, occurring inside
  it.

## 3.17.0 — Is the graph right?

**Scope, stated first so nothing below is read as more than it is.** This release makes the
PKG *measurable* and measures **two of the eight front-ends** — `python` and `typescript`.
The other six (`java`, `csharp`, `c`, `cpp`, `go`, `sql`) gain the machinery and no numbers:
`pkg accuracy` will score them the moment a corpus exists for them, and reports them as
**skipped** rather than zero until it does. The capability matrix still says what a front-end
*can* emit; only Python and TypeScript now also say how well.

### Added

- **Measured graph accuracy.** Every claim Spine makes about a codebase is a claim about
  the PKG, and until now those claims were adjectives — "grounded", "deterministic",
  "precision-first" — none of which survives *"how do you know?"*. `orchestrator pkg
  accuracy` answers it with four oracles, each a different trade of cost against reach:

  | oracle | needs | answers |
  |---|---|---|
  | corpus *(default)* | hand-labelled fixtures | precision **and** recall, per kind, per language |
  | `--oracle runtime` | a test suite to run | `CALLS` recall from real execution, on any repo |
  | `--oracle parity` | only the source | declared-vs-emitted routes and tables, per file |
  | `--oracle invention` | only the source | `CALLS` edges that are fiction, exactly |

  The corpus is published alongside the numbers, in `corpus/`, with the labelling method —
  a recall figure nobody can reproduce is an assertion with a decimal point in it.

- **An accuracy regression gate.** `pkg accuracy --check` compares against a committed
  baseline and fails a build when a *gated* number drops. It is now in the quality gate
  next to `mypy` and `ruff`. What may be gated depends on what a number is measured
  against: corpus scores come from committed fixtures and are gated strictly; per-file
  parity ratchets one way; the invention count is recorded and never gated, because it is
  measured against the repository itself and moves whenever anyone writes ordinary code.

- **The number where a person will actually see it.** The build document's blast radius now
  states the measured recall for its language — *"Measured `CALLS` recall for python is 0.73
  (against the extractor's own test corpus, not this repository) — treat this list as a
  lower bound."* The parenthetical is not decoration: a reader who takes that figure as a
  statement about their own repository has been misled.

- **The intent layer — `Intent` nodes and `SERVES` edges.** The graph's vocabulary was
  entirely mechanical: what calls what, what contains what. It now records *what a symbol
  was last changed for*, joining `git blame` to the issue key in each commit's message.
  Deterministic, no model, and it reports its own coverage — which depends on the
  repository's history, not on the method.

  **Library only in this release.** `orchestrator.pkg.intent_link.link_intents(batch, root)`
  is not wired into any command, because the scan costs about 11× an extraction (2.0s → 23.0s
  on this repository) and `git blame` runs once per file. Making it default-on needs a
  commit-keyed cache or a single `git log --numstat` walk; that is its own change.

- **CI measures every front-end it ships code for.** Previously CI installed neither the
  `typescript` extra nor the five other tree-sitter ones, so only `python` and `sql`
  front-ends registered — 89 tests skipped, and the TypeScript figures in the committed
  baseline were measured once on a developer machine and skipped by every run since. A
  TypeScript regression could not have failed a build. All six extras are now installed.

### Fixed

- **A systematic false-positive class in the Python front-end, now measured.** A call
  through a parameter, a local variable, or a nested function emits a `CALLS` edge to an
  `external` node that does not exist — `py:echo` where `echo` is a callback argument.
  **496 such edges here, 3.16% of the call graph.** Detected exactly rather than sampled;
  the front-end fix is its own ticket, with this number to prove itself against.

- **`source-parity` counts instead of testing for presence.** It asked *"does this language
  have **any** `Endpoint` node?"*; it now asks, per file and with `file:line`, *"this
  declares 4 route decorators and the graph holds 1 — where did 3 go?"*.

  ~~It found two live routes invisible to the graph: `GET /healthz` and `GET /readyz`.~~
  **Corrected after release:** that finding was false. Both routes were always extracted and
  `EXPOSES` always reached every handler. `Endpoint` ids are keyed on verb+path, so services
  sharing a route collapse into one node, and per-file parity counted by node provenance — so
  every other declaring file read as short. The shortfall was the metric's own artifact. Fixed
  in the next release; the claim is struck rather than deleted because a measurement that
  produced a false finding is worth recording.

## 3.16.2 — A host that owns the model, and four failures that read as crashes

### Added

- **The build document as an MCP tool.** `sdlc_plan` renders the twelve sections for a
  host that has its own model and its own tracker credentials — **no key on Spine's side,
  no credentials, nothing spent.** The case it exists for: an enterprise machine whose
  only model lives in the desktop app and whose Jira sits behind a Server/DC personal
  access token that Spine's own adapter cannot speak to. The answer was not to teach Spine
  to reach that Jira but to invert the dependency — the host reads the ticket, drafts the
  spec, and hands it over; Spine has the graph and the twelve sections. `build_plan` was
  already free of both, so exposing it cost a wrapper.

  A spec arriving from a model is validated before anything renders, and the refusal is
  **returned rather than raised** — the caller is a model that can read the error and fix
  the spec, which an exception on the host's side does not let it do. A test asserts the
  tool and the CLI render byte-identical documents: two surfaces over one renderer must
  not be able to disagree.

- **`sdlc_approve` as an MCP tool**, so the gate has a path on a machine driven entirely
  through the plugin. It binds to a digest of the document exactly as the CLI does, and
  **refuses to invent an approver** — a host may know its user; this process does not, and
  an approval attributed to nobody is a rumour rather than a record.

### Fixed

Four expected conditions printed tracebacks where one line belongs. **Every one was found
by someone running a command, not by a test.**

- **A mistyped `--source` printed forty lines.** `SourceUriError` was uncaught in `ingest`,
  `openspec draft` and `sdlc plan`, while `investigate` had caught it since before any of
  them existed. One line, exit 2.
- **Missing credentials printed sixty, and named the wrong provider** — someone with
  `OPENAI_API_KEY` set was told the *Anthropic* key was missing, because the key and the
  model are separate settings and nothing connected them. The message now names the model
  that actually resolved and the variable that changes it.
- **litellm's "Give Feedback / Get Help" banner is suppressed.** It printed on every
  exception litellm *mapped*, including ones we catch and recover from, so a successful run
  emitted six blocks that read as six failures.
- **The temperature warning reads as a sentence** rather than a bare event name, and says
  what it costs: a spec derived on that model may differ between runs.

### Changed

- **The plugin's tiers are three, not two, and "gated" says what it costs.** It means two
  separate things — every call spends real money whether or not it succeeds, and `live=true`
  writes where you cannot take it back. **Safe mode still costs tokens**; it keeps writes
  local. Between comprehension and the gated run sits a tier that costs nothing and writes
  only under `.spine/`: plan, and approve. Both guides carry the table and the rule — work
  down the tiers, never up.

### Documentation

- Both guides gain `sdlc_plan` and `sdlc_approve`, including the thing an assistant cannot
  infer from a docstring: **`met_criteria` is where the integration earns its place.** A
  host has read the ticket *and* can call `explain_symbol`, so it can spot a criterion the
  code already satisfies — the one judgement no deterministic pass can make.

## 3.16.1 — The paths nobody ran

Everything in 3.16.0 was exercised through `--spec`, which makes no model call. The first
run through `--source` found that path broken, and the sweep that found it is now written
down.

### Fixed

- **A model that refuses our temperature no longer kills the run.** Intake pins
  `temperature=0.0` so a spec is stable for a given intent; `claude-opus-5` — the default
  for every stage — accepts the parameter but only at `1`. Every `--source` path therefore
  died on its first model call: `ingest`, `sdlc plan --source`, and `autorun` without
  `--spec`, each with a forty-line traceback where one line belongs. A refused temperature
  is retried once without it, matched on the message rather than the exception type,
  because litellm raises `UnsupportedParamsError` for several unrelated parameters and
  retrying an unrelated failure turns one clear error into two. **The determinism the pin
  buys is gone when this fires, so it is logged rather than swallowed.**

- **A confidence check that cannot apply is no longer scored as a failure.** Section 12
  read "Root cause: a file at best — no line established" for every feature ticket, capping
  each enhancement a point below every bug and asserting a file had been named when section
  3 said none was. Inapplicable rows now render `n/a` and leave the denominator, so an
  enhancement reads "3 of 4 applicable checks" against a bug's "3 of 5".

### Changed

- **Section 11 prices a swap across providers.** The cost table listed the resolved model
  and its provider siblings, which cannot answer what switching costs. It now shows the
  nearest three by input price from anthropic, openai and gemini, with a provider column.
  By price tier, not recency — the catalog carries no release date, so "the latest model"
  is not a fact available to the renderer, and a hardcoded list of latest ids goes stale
  silently.

### Documentation

- **`docs/specs/cli-test-plan.md`** — every one of the 50 CLI commands with prerequisites
  and acceptance criteria, tiered by blast radius, with per-sweep result recording. Four
  known defects are marked as currently failing rather than written as if they pass.
- **`.env.example` restructured** so its blocks read in the order they are configured, and
  seven variables already in use gain documentation — including that MCP is preferred for
  reads and is not a path for writes at all.

## 3.16.0 — Nothing gets built that nobody read

### Added

- **Plan before code: `orchestrator sdlc plan` renders a build document, and
  `sdlc approve` gates on it.** Six live runs on one ticket produced usable source most
  times and completed zero times, at roughly $4.75 — and every failure was a decision made
  silently: a design naming the wrong files, a prompt rule forbidding the shape the spec
  asked for, and two acceptance criteria describing behaviour the code already had. All
  three were visible in a document that costs nothing to produce. One ticket now yields
  twelve fixed sections — requirement, intent, root cause, what the graph knows, blast
  radius, design, files, criteria, facts for the generator, the codegen prompt, cost and
  confidence — assembled from the ticket, the graph, git and the repo's own tests. **Every
  section carries where it came from**: quoted, computed, inferred, or decided by a person.
  A document that mixes a quoted requirement with an inference and does not say which is
  which lends the authority of the first to the second. See
  [`docs/specs/build-document.md`](docs/specs/build-document.md).

- **Acceptance criteria have three states, not one.** Stated, *stated but already met by
  code that exists*, and proposed. The ticket this was built for filed six criteria of
  which two described behaviour `_check()` already had; a run would have reported them met
  having changed nothing. `FeatureSpec.met_criteria` maps a criterion's exact text to the
  evidence that satisfies it, and the criterion stays on the page marked rather than
  quietly disappearing from the list. A key matching no criterion is reported as a
  mismatch rather than silently ignored.

- **`CONSUMES` — the client half of the route join.** `EXPOSES` gave a route its handler
  and nothing pointed *at* an endpoint, so a public route was a leaf: something the server
  declared that nothing in the repo appeared to want. That is why a ticket about "the
  registry API" retrieved the server modules and never reached the CLI that calls them.
  Python source now yields `CONSUMES` edges from a caller to the endpoint it calls, and
  `impact_of` follows them, so changing a handler reaches the code that would break.
  Literal paths only — a request built from an f-string yields nothing, because a wrong
  edge is worse than an absent one.

- **A plan is approved against a digest of what was read.** `sdlc approve` records who,
  when, why, and the commit it was derived at. `sdlc autorun` re-derives the plan and
  compares: same tree, same digest, approval stands; anything else refuses, because an
  approval that survives the code moving underneath it approves a document nobody has
  read. The gate is on by default (`--no-plan-gate` to skip, and it says so out loud), and
  a refusal parks rather than fails — the ticket is fine, the review has not happened.

- **The journey: each run appends, no run rewrites.** Stage results, the run outcome with
  measured tokens and spend, and — the reason it earns its place — the disagreement between
  what implement touched and what the design named, in both directions. Append-only by
  construction: there is no update and no delete, because a later stage that could tidy an
  earlier one away removes exactly the evidence worth keeping.

### Changed

- **A bug ticket is not a traceback.** Fault localization read anchored exception lines and
  `File "x.py", line N` frames, so a ticket carrying `ConnectError: [Errno 61] Connection
  refused` in plain prose localized to nothing at all. An exception named anywhere in the
  text is now read — still requiring the `SomeError: …` colon form, so "this Error handling
  is poor" stays a complaint — and source paths the text names are resolved against the
  graph. A stated file is a *module*, never a fault site, and the two are never presented
  as each other. `orchestrator rca` and `orchestrator localize` were blind the same way and
  both improve.

- **`pkg extract` counts edges by kind.** One total cannot show a kind that stopped being
  emitted: `edges: 31073` reads identically whether the call graph resolved or collapsed to
  zero while imports doubled. `FactStore.summary()` now carries an `edges_<kind>` count for
  every kind, printed under the scan line and included in `--json`. Kinds with no edges
  report `0` rather than being omitted — `REFERENCES 0` on a repo with entities is the line
  worth reading, and a missing key looks like a question nobody asked. Existing keys are
  unchanged, so the ten callers of `summary()` are unaffected.

### Fixed

- **The repair pass can revise a file it already wrote.** It was told *"do NOT include them
  again"*, which is right about stale anchors and wrong about everything else: a run wrote
  `api_errors.py` on one attempt, rewrote `cli.py` against renamed helpers on the next, and
  could not rename the module to match. The import failed and no later stage could reach
  the file. The instruction now says what it means — leave them out if they are correct,
  and re-anchor against the current content, which is shown, if one needs changing.
- **A refine that describes a change it did not send is refused.** `refine` and `revise`
  allow an empty submission, because a pass with nothing to change is a legitimate no-op.
  One answered *"Rewrote orchestrator/api_errors.py to export api_call…"* and sent no files;
  that read as "nothing to change", the loop stopped, and a run that had correctly diagnosed
  its own failure ended without fixing it. A claim now needs a past-tense change verb **and**
  a path before it counts as one, so "no changes needed in cli.py" stays a no-op.

- **A new module is not a "parallel module".** The implement prompt said *"when the SPEC
  names existing files, change THOSE files; do not create a parallel module instead"* — a
  rule added after a run wrote a helper beside a file and never wired it in. As written it
  also forbade the ordinary shape of an extract-a-wrapper refactor, so a spec asking for
  "one place that wraps the request" left the model choosing between its instructions; three
  runs submitted zero files. The prohibition is now on the outcome it was protecting
  against: the named files must appear with `edits`, and a new module alongside them is
  allowed.

- **A design honours the paths its spec names.** The heuristic design read only the ticket's
  title and summary, matching those words against the graph — so a ticket about "the
  registry API" whose acceptance criteria named `src/orchestrator/cli.py` twice came back
  proposing the registry *server* modules, and omitted the one file the spec named. Codegen
  is handed that design as an instruction, and on a live run it submitted nothing at all
  rather than choose between a spec and a design that disagreed. Paths stated in the
  criteria and technical notes now come first; the graph reading and the overview remain as
  fallbacks. A stated path that does not exist is dropped — naming a file to *create*
  belongs in the approach, not in a list of files to open.

- **The repair path no longer crashes on a symlinked worktree.** `applied_paths` names the
  files an attempt already wrote so the repair retry knows not to resend them — computed
  with a bare `Path.relative_to`, which raised on macOS, where `/tmp` is a symlink to
  `/private/tmp`: the written paths come back resolved and the worktree root does not, so
  nothing is "in the subpath of" anything. The `ValueError` killed the whole run, and fired
  only when an attempt partially succeeded, which is the exact case the information exists
  to rescue. Both sides are resolved now, and a path genuinely outside the worktree is
  reported rather than raised.

## 3.15.0 — Everything that reports success has to be able to report failure

### Added

- **The validity gate weighs codegen's context budget.** A spec can be right-sized by every
  other measure and still not fit in front of the model: `_check_size` counts criteria and
  modules, and neither correlates with bytes — one 56 KB file passes the module count and
  exceeds the whole budget alone. The gate now sums the files a change names and compares
  them to `codegen._MAX_CONTEXT_BYTES`. Past 1.5x it returns `TOO_BIG`; in the margin below
  it warns and still proceeds, because anchor-located excerpting copes there and refusing
  would block runs that work. Inert without a repo root, so callers that cannot measure get
  exactly the verdicts they got before.

### Changed

- **Migrated to the MCP Python SDK v2** (`mcp>=2`). v1 spelled three things differently:
  the client transport is now `streamable_http_client` (and takes a configured
  `http_client` rather than `headers`, yielding two streams not three), the result flag is
  `is_error`, and the server class is `mcp.server.MCPServer` — `mcp.server.fastmcp` is
  gone. Transport settings moved off the server constructor onto the run call, so
  `build_http_server` returns an `HttpServer` carrying both. Reading the error flag with
  `getattr(result, "isError", False)` is also fixed: under v2 that silently reported every
  tool error as a success, which is worse than the AttributeError the default was avoiding.

### Fixed

- **`author_tests` may answer "nothing to test" — but only when that is true.** The stage is
  unconditional and no spec can turn it off, and an empty submission was always treated as a
  skipped job and retried. On a documentation-only change it answered correctly first
  ("no tests submitted: this is a prose edit"), was retried anyway, and the retry invented a
  test module for a paragraph and corrupted it with markup — discarding a change that was
  already complete and correct. Empty is now valid when the change touched no testable
  source, and still refused when it did, so a source change that skips its tests is caught
  exactly as before.

- **v2 renamed `Tool.inputSchema` and `readOnlyHint` too, and the defaulted reads hid it.**
  `_to_tool` fetched both with `getattr(..., None)`, so the rename did not raise —
  `input_schema` silently became `None` and every tool lost its argument types. That took
  the `mcp contracts` type labels with it and stopped `MCPRegistry.call` coercing a
  structured argument a server declares as a string, so a governed `jira_create_issue`
  started failing validation again. Nothing went red, because every existing test builds an
  `MCPTool` directly and never exercises the translation from the SDK's own type. A new test
  module builds a real `mcp.types.Tool`, so the next rename fails a test instead of a run.

- **Generated test fixtures are shown the types they construct.** `author_tests` was given
  the source under test but not the definitions of the types that source imports — so a
  fixture building a fake LLM client or a graph store wrote the constructor from inference.
  Three consecutive runs produced correct source and a broken test module that way:
  `CompletionResult` with an invented `usage` kwarg, then `CompletionResult` missing four
  required fields, then `FactStore()` without its `batch`. Writing one signature into the
  spec did not help — the next run failed on a different type. The stage now reads the
  class-shaped names the code under test imports and pulls their definitions from the
  graph, which covers all three.

- **A stub submission no longer counts as progress.** A run wrote a file literally named
  `PLACEHOLDER` containing `x`, and the refine loop treated it as a file change — its stop
  condition is "no file changes", so a model with nothing to say kept the loop alive and
  spent two of three attempts on it while the real failure went unfixed. Placeholder names
  (`PLACEHOLDER`, `TODO`, `FIXME`, `TBD`, `XXX`, `stub`) and essentially-empty bodies are
  dropped at the write, so the loop's existing stop condition works. `__init__.py`,
  `py.typed` and `.gitkeep` are exempt — they are legitimately empty. A submission that is
  *only* placeholders is recoverable and routes to the same corrective retry as submitting
  nothing at all.

- **A partially-applied codegen attempt can now be repaired.** `apply_files` is per-file
  atomic, not per-batch: a successful file is written before the rest are attempted, so a
  later failure leaves earlier ones on disk. The repair then said "re-emit the full JSON
  object" while showing current content only for the files that *failed* — so the model
  resent edits whose `find` text its own previous attempt had replaced, and the retry could
  not succeed. A live run produced a complete, correct change and failed anyway on `edit 0
  'find' text not found`. The error now carries which files landed, and the repair tells the
  model to omit them.

- **`mcp` pinned below 2.0 until the v2 migration lands, and CI now installs the extra.**
  v2 renames what this package uses — `streamablehttp_client` → `streamable_http_client`,
  `CallToolResult.isError` → `is_error` — and drops `mcp.server.fastmcp` entirely. The bump
  passed CI because CI installed `--extra dev` only, so every MCP test skipped and the
  breakage was invisible until someone installed the extra. An optional extra the repo ships
  code for is not optional to test.

- **The repo-invariant check now catches the criterion it was built from.** It needed both a
  nondeterminism word and a named deterministic surface; SSPN-31's own criterion is phrased
  entirely as output shape, naming the JSON key `"regressions"` and never the `regression`
  command, so no surface matched and the gate returned `PROCEED`. Surface names now match a
  trailing `s`/`es` and a possessive. The corpus fixture asserts the verbatim criterion
  rather than a reworded one that names the command — a fixture easier than the real case
  makes a check read as working while doing less.

## 3.14.0 — The pipeline can be trusted about its own output

### Added

- **The validity gate refuses a criterion that contradicts a documented invariant.** The run
  that started SSPN-31 produced a criterion requiring an ISO-8601 `meta.generated_at`
  timestamp in a comprehension command's JSON — nobody asked for it, and it breaks CLAUDE.md
  invariant 2 (`understand` / `state` are deterministic; never a clock, an LLM call, or
  randomness). An agent building to it would have shipped non-diffable output and passed its
  own tests. `assess()` now returns `CRITERIA_WRONG` with evidence naming the invariant.
  Deterministic string matching, no LLM call — an LLM asked whether an LLM invented a
  requirement is not an answer. Proposed criteria are checked too, since an invented
  criterion is the kind most likely to break a rule. A timestamp in a log line, an HTTP
  header or a tracker comment is untouched: determinism is a property of specific outputs,
  not a ban on the word.

- **`--spec` on `sdlc autorun` and `sdlc feature`** — implement a spec you wrote instead of
  one derived from the source. Intake is skipped entirely and the `[intake]` line reports
  `skipped`, so a run summary never implies a source document was read when none was. For a
  settled spec (a remediation, something agreed in review) — or when intake itself is what
  the ticket is about, where letting a defective stage specify its own repair is circular.
  The file is JSON validated against `FeatureSpec` rather than markdown: a misspelled
  `acceptance-criteria` is an error naming the valid fields, not a run that proceeds with no
  criteria and passes by default. An empty criteria list is refused for the same reason.

### Changed

- **`FeatureSpec.acceptance_criteria` has narrowed to the criteria the source
  *stated*.** Criteria the spec writer infers now go to `FeatureSpec.proposed_criteria`
  instead of being concatenated into `acceptance_criteria`, so anything reading that
  field sees **fewer entries than before**. The acceptance judge
  (`orchestrator.sdlc.review.SemanticReviewAdapter`) verifies only the stated set — a
  change can no longer be rejected for failing a criterion nobody asked for — while the
  codegen SPEC banner (`orchestrator.sdlc.feature_runner.run_feature`) and every human
  render surface (`intake.service.spec_to_issue_request`, `intake.report`,
  `intake.web.app`) show the proposed set under its own label rather than dropping it.
  Specs with no proposed criteria render exactly as before.

## Unreleased

- **Codegen's source-context budget goes from 40 KB to 200 KB.** 40 KB is ~10k tokens —
  about 1% of the default model's window, and a leftover rather than a limit. It shaped
  work instead of bounding it: a change spanning five files totalled 113 KB, so every file
  was excerpted and codegen quoted an edit anchor from a region it had only partly seen.
  Failure output stays at 40 KB under its own constant — a pytest dump is not source, and
  refine re-sends context every attempt.

### Fixed

- **A failure kind is now advised about the thing it was charged for.** `_failure_kind`
  ordered syntax before anchors; `_corrective_suffix` ordered anchors before syntax. An
  attempt producing both — a stray `</content>` in a new file *and* an edit whose anchor
  missed — was recorded as `syntax` while the model was handed the anchor-repair block, so
  the syntax kind's one correction was spent on advice about something else and the next
  failure had nothing left. The same shape as the shared-retry-pool bug the per-kind budget
  replaced, arriving through misclassification instead. A test now fails if the two
  orderings drift again.

- **A run branches from the branch it will open a PR into.** A clone with no `--branch`
  checks out the remote's *default* branch, so a run targeting `develop` still built on
  `main` — the generated change was written against a tree predating everything merged to
  develop since the last release, and could revert it with nothing noticing. Found when a
  run silently reverted a fix merged two hours earlier. The base branch is now part of the
  cached base repo's identity too, so a base cloned for `main` is rebuilt rather than reused
  for a `develop` run. With no target given, the default branch is still used — this is not
  a silent switch.

- **Intake constrains its output the way codegen and the judge do.** Intent extraction and
  spec writing both asked for JSON with `json_object=True`. `response_format` is
  OpenAI/Ollama-only — Anthropic drops it, and the default model is `claude-opus-5`, so both
  stages ran completely unconstrained and relied on brace-scanning salvage. 3.13.0 fixed
  exactly this for codegen and the acceptance judge; intake was never converted. Both now
  emit through a forced tool call. The text path remains as the degradation route, since a
  forced call constrains shape but a provider can still answer in prose.

- **A server that declares a structured argument as a JSON string now gets one.**
  `mcp-atlassian` types `jira_create_issue.additional_fields` as `string` rather than
  `object`, so every caller had to `json.dumps` it first and a governed create encoded
  twice — once for the field, once for the transport. `MCPRegistry.call` reads the tool's
  declared type and encodes on the caller's behalf. Narrow on purpose: only when the
  declared type is exactly `string` (or `string|null`) *and* the value is an object or
  array. A declared `object`, an undeclared argument, or an unreadable schema passes
  through untouched, and a caller that already encoded still works. The schema round-trip
  is skipped entirely when every argument is a scalar, and a discovery failure falls back
  to the raw call rather than becoming a new way to fail.

## 3.13.0 — A green suite is not a working change

Found by running one ticket end to end a dozen times. Every stage of the build loop was
working from an input it could not see, and reporting the gap as a defect in the work
rather than in its own view. Three separate runs committed code that did nothing: a helper
appended and never called, a file missing its `typing` import, a command wired through an
attribute its class does not have. All three had green tests.

**Behaviour change:** a run is now stopped by things that previously passed. Type errors on
changed lines, a changed file no test exercises, and an acceptance verdict that cannot be
read all block a commit where they used to be silent. Runs that used to finish with a
useless change now fail with a reason.

### Added

- **`orchestrator sdlc autorun --review`** — prints the full diff and asks before the run's
  first write. The last gate before anything is committed or pushed, and the only one that
  is a person rather than a model. Fails closed when there is no terminal to ask on, so an
  unattended run declines rather than assuming yes.
- **The repo's own type checker runs inside the test loop.** Scoped to the lines the change
  touched, so pre-existing errors elsewhere don't block a run, and its output goes back to
  the refine loop exactly like a test failure. Typing-hygiene codes (`unused-ignore`,
  `no-untyped-def`, `import-*`) are ignored — in a generated worktree those report the
  environment, not the change.
- **Per-file coverage proof.** The existing check reverts the whole change at once, which
  only proves the tests depend on *some* part of it. Each changed file is now reverted on
  its own; anything that leaves the suite green is a file nothing tests, and goes back to
  the test author by name.
- **`sdlc autorun` is documented** in `CLI_REFERENCE.md`, including what each bracketed
  stage line means. It was the primary entry point and had no reference entry.
- **`orchestrator mcp contracts` labels every argument with its declared type**, read from
  the server's own JSON Schema at display time — `string`, `string|null` for a union, and
  `any` when the schema gives no top-level type (`anyOf`, `$ref`, or no schema at all). A
  parallel `input_types` map carries the same labels keyed by name. Display-only: nothing is
  stored and `mcp call` is unaffected. **Note:** the existing `inputs` field changed shape
  from `["name"]` to `["name (type)"]` — if you parse that output, this is a break.
- **`orchestrator models`** — every model the pipeline can be pointed at, with context
  window, price per million tokens, and whether it supports tool calling, plus what
  each stage is resolving to right now. Read from the installed LiteLLM's own catalog
  rather than a list maintained here, so upgrading the client brings new models with
  no code change and the table can't drift from what is actually making the calls.
- **A model per stage.** `SDLC_JUDGE_MODEL` and a global `ORCHESTRATOR_MODEL` join the
  existing `SDLC_CODEGEN_MODEL` / `ORCHESTRATOR_INTAKE_MODEL`. Three of the four
  stages — the acceptance judge, the intent extractor and the spec writer — were
  hardcoded constants that no environment variable could move, so a "model choice"
  only ever applied to codegen.

### Changed

- **The acceptance judge can ask for a correction, not only refuse.** A rejection used to
  end the run, which made any criterion the generator was never told to satisfy an
  unwinnable ticket — most visibly a documentation criterion, which every codegen prompt
  forbids and the judge then failed the run for missing. Blockers now go back for revision,
  bounded; a revision that breaks the suite is repaired rather than fatal.
- **The judge reads documentation and config, not just `.py`.** A run wrote the
  `USER_GUIDE.md` its ticket demanded, twice, and was told both times that no documentation
  was present — true of its input, and unfixable by any change. The same hole meant a Java
  or Go change was judged on its Python files, of which there are none.
- **Codegen is shown the files it is changing.** Paths came only from the spec text, so a
  ticket phrased in behaviour named none and codegen received zero bytes of existing source.
  Paths now come from the spec *and* the design, which knew them all along.
- **A file too large for the prompt budget is excerpted, not dropped.** It used to vanish in
  silence, so a repair prompt could say "copy this snippet verbatim from the content below"
  and then include nothing. Windows are placed by anchor and sized by what is left, and
  whatever is not shown is named.
- **Codegen and the judge both emit through a forced tool call.** `response_format`
  is an OpenAI/Ollama concept that Anthropic drops, so on Anthropic models nothing
  constrained the output and replies arrived wrapped in prose.
- **`--max-refine` now defaults to 5** (was 3) on `sdlc autorun` and `sdlc feature`: tests
  and type errors draw on the same budget.
- **The default model moves to `claude-opus-5`** (from `claude-sonnet-4-6`, a
  previous-generation Sonnet that every stage was pinned to). This costs more per
  token — $5/$25 per Mtok against $3/$15 — so it is a deliberate change rather than a
  silent one; set `ORCHESTRATOR_MODEL` to pin any stage back, and `orchestrator models`
  lists the alternatives with prices. Codegen's output cap also rises 16k → 32k
  tokens: on the current Claude models thinking is on unless disabled and shares that
  budget, so a cap sized around the JSON payload alone truncates mid-object.
- **Generated Python is parsed before it is written.** A file that does not compile used to
  reach disk and surface three stages later as an opaque pytest `rc=2`, with the real
  `SyntaxError` buried in an importlib traceback. It is now refused at the write with the
  file, line, and offending text, and takes a corrective retry.
- **Each kind of failure gets its own corrective attempt.** Unparseable JSON, a failed edit
  anchor, an empty submission and unparseable Python previously shared a single retry, so
  whichever failed first consumed it. A kind that fails twice still stops.
- **No context builder drops a file silently.** Four separate loops — the files fed to
  refine, the acceptance judge's reader, the anchor-repair block, and PKG grounding — stopped
  at the first item too large for the budget and dropped everything after it. A 91 KB
  `cli.py` could hide a 1.8 KB module the model then edited blind and could not repair. All
  four now allocate fairly and name what they omit.
- **Editing the repo's own docs is not inventing a path.** The layout guidance banned every
  file outside the source and test directories, which made any documentation criterion
  impossible to satisfy. New code and tests are still confined; changing a file the repo
  already has — README, USER_GUIDE, CHANGELOG, pyproject.toml — is allowed.
- **A criterion the judge cannot verify no longer ships.** An `uncertain` verdict stays a
  comment, but now carries a blocker and enters the revision loop like any other, instead of
  passing straight through because it was not `request_changes`.
- **The type check no longer discards codes the target repo enforces.** Only `import-not-found`
  and `import-untyped` are filtered, since a generated worktree carries runtime deps only.
  Everything else is the repo's own mypy policy — filtering it made "clean" mean less than
  CI clean.
- **A safe-mode rehearsal no longer counts as a duplicate.** The check refused any second
  run on a ticket whose first run finished, so a ticket became unworkable after its first
  dry run. Only a run that reached a PR blocks another.

### Known limitations

- A run parked at the validity gate holds its ticket until its approval is decided; there is
  no reaper for parked runs.
- `--resume` re-runs its stages and builds a fresh worktree rather than continuing the
  previous one, so approve-then-resume regenerates the change instead of committing the diff
  that was approved.

## 3.12.0 — Read your tracker through a server, not a token

**Behaviour change:** where an MCP server is onboarded that can serve them, `jira://` and
`confluence://` now route through it instead of direct REST credentials. If you have an
`mcp.json` and expected REST, this changes which path your ingest takes. `mcp-jira://` and
`mcp-confluence://` still force MCP; deleting the server from `mcp.json` restores REST.

The case for it is not ideology. Credentials stay with the operator's server rather than
spreading through this process's environment, calls are allow-listed and audited, and the
server tracks upstream API changes that a hand-rolled client does not — which stopped being
hypothetical this cycle. Atlassian removed `GET /rest/api/3/search`; it answers 410 Gone on
Jira Cloud, and every read through our Jira REST adapter died with it, including the
`parent = <key>` traversal that walks an epic to its stories. That is repaired here too, but
the lesson is that the MCP server absorbs this class of breakage on our behalf.

Resolution is config-only and deterministic — building a source never launches a server to
ask what it exposes. An explicit `$MCP_JIRA_SERVER` / `$MCP_CONFLUENCE_SERVER` wins; failing
that, the first enabled server whose `allow` list names the tool; failing that, a lone server
with no allow-list. With several unrestricted servers Spine declines to guess and falls back
to REST, rather than routing your ticket through whichever name sorted first.

### Added

- `orchestrator mcp list` now reports **why** a server produced no tools. Previously every
  cause — a missing `mcp` extra, an unpulled image, a typo in `command`, rejected
  credentials, a genuinely dead server — produced the same empty list and a log line nobody
  reads. Failures are now classified `config` (permanent; you must change something) or
  `unreachable` (may resolve on its own), carry a remedy where one is known, print to stderr,
  and exit non-zero when every configured server failed.

### Fixed

- Jira REST reads use `/search/jql`. The endpoint that replaced it returns no `total`, so
  truncation is now taken from `isLast`/`nextPageToken` — reading a missing `total` as zero
  would have quietly claimed every result set was complete.
- Jira issues read over MCP are parsed correctly. `mcp-atlassian` returns issue attributes
  flattened at the top level and spells the type `issue_type`, where Jira REST nests them
  under `fields.issuetype` — so the fix released in 3.11.1 never actually fired against a
  real server. Also: documents are keyed by issue key rather than the opaque numeric id, the
  project key populates `space`, the server-supplied `browse_url` populates `url`, and an
  issue with no description no longer ships its entire JSON payload as the body.

### Documentation

- `USER_GUIDE.md` step 9 covers running MCP servers under Docker, configuring several at
  once, and the traps: `mcp.json` and `.mcp.json` are one character apart and point in
  opposite directions; the old example put raw API tokens in a file that was not gitignored;
  `--env-file` needs an absolute path and does not strip inline comments.

## 3.11.1 — A Bug should read like a Bug

An epic, story or bug read through an MCP server arrived **untyped**, while the same issue
fetched over REST arrived typed. Whether the extractor could tell a Bug from a Story, or
done from open, depended on which transport happened to fetch it.

The mechanism is easy to miss, and worth writing down. `SourceDocument` has no field for
issue type — the REST adapter encodes it as a header prepended to the body
(`Bug · status: Open · priority: High`), precisely because a Bug reads differently from a
Story. The MCP parser built its own document and never produced that header, so the
information was not stored elsewhere; it was dropped. The fix puts the header in one
function both adapters call, and the test asserts the two produce *equal* documents for
identical input rather than checking fields one at a time — which is what stops them
drifting apart again.

> **Note on 3.11.0.** It was tagged and released on GitHub but never published to PyPI;
> this release supersedes it and contains everything it did. Publishing 3.11.0 after this
> fix landed would have put an artifact on PyPI that did not match its own tag.

### Fixed

- Jira issue type, status and priority now survive ingestion over MCP, matching the REST
  adapter. MCP documents also set `space` to the project key. They still carry no `url` —
  an MCP server abstracts the host away, so there is no base to build a browse link from.
  Confluence pages read through the same parser are unaffected.

## 3.11.0 — Take the graph somewhere else

The visualization gap was never really about our own renderer. A user who wanted to explore
the graph in Gephi, yEd, Cytoscape or Obsidian couldn't: the only projection was a
kind-per-table SQLite file. `pkg export` now writes GraphML, DOT, JSON and an Obsidian
vault, and the honest conclusion of building it is that we should stop there — Gephi
already does filtering, search, clustering and click-through-to-source on our own export,
and it does them better than a UI with no build step ever will.

Exports are **complete, never truncated**, which is the opposite of what the built-in
visuals do and deliberately so: a diagram with 9,000 nodes communicates nothing, but a
silently truncated GraphML lets a reader draw conclusions from a subset without knowing it
is one. They are also byte-identical for an identical commit, so a committed export diffs
cleanly — asserted by a test that exports twice and compares bytes, because "deterministic"
that nothing checks stops being true quietly.

The architecture diagram now groups by structural community rather than by name prefix.
Grouping by name answers "what did someone call this"; the coupling graph answers "what
actually clusters", and on a single-namespace project the first answer is one box around
everything. Both the report SVG and the committed `episteme/` diagram use the same
partition, so they can't drift apart.

### Added

- `orchestrator pkg export --format graphml|dot|json|obsidian --out <path>`. GraphML and DOT
  open in Gephi, yEd, Cytoscape and Graphviz; JSON carries nodes **and edges**, unlike
  `pkg extract --json`; `obsidian` writes a vault — a copy of the repo's `episteme/` with
  `[[wikilink]]` syntax, never editing it in place. The existing `--db` keeps working as a
  deprecated alias, and combining it with a non-SQLite format is rejected rather than
  silently ignored.
- Deterministic community detection over the coupling graph
  (`orchestrator.knowledge.clustering`), used to band the architecture diagram. Label
  propagation with sorted iteration, seeded labels, stable tie-breaks and communities
  renumbered by first member — so adding one unrelated area cannot renumber everything and
  make an unchanged architecture look like it moved. Partition quality (modularity) is
  reported in the diagram's `aria-label`.

### Fixed

- Graph exports now include `Doc` nodes and `MENTIONS` edges. `pkg export` ran raw
  extraction, but documentation enters the graph through a post-pass — so exports were
  missing 920 `Doc` nodes and 1,576 `MENTIONS` edges on this repo, and with them every
  media transcript, which reuses `Doc`.
- `media --help` said image OCR and "local", omitting that `--asr api` uploads audio
  off-machine. The consent gate was always enforced in code; the summary was narrower than
  the behaviour, which is the wrong way round for a privacy claim.

### Documentation

- `CLI_REFERENCE.md` documents the export formats, and warns that a naive read of `IMPORTS`
  loses 29% of the dependency graph: 2,746 of 5,895 import edges target a `Type` or
  `Function` rather than a `Module`, so filtering for module-to-module edges yields 3,033
  dependencies where resolving through `CONTAINS` yields 4,287. It fails in the direction
  that looks plausible — a tidier architecture than the real one — so the recipe is spelled
  out.

## 3.10.0 — Diagrams and recordings become facts

A codebase's knowledge was never only in its code and its prose. Architecture diagrams,
screenshots of a dashboard, a recorded design review where the one person who remembers why
explains it — Spine could read none of it. Media ingestion closes that: images go through
OCR, audio and video through speech-to-text, and both land as reviewable artifacts under
`.spine-media/`.

The split is deliberate. `media extract` is the only thing that runs a model; the
deterministic graph build *reads* the committed artifacts and never produces them. So
`understand` and `state` stay no-LLM and reproducible — same commit in, same graph out —
while the slow, non-deterministic part is an explicit, reviewable step you run and commit.
An artifact you can read and diff is also an artifact you can correct when OCR mangles a
label.

Transcription is the first path in Spine that can leave your machine, so it carries a
structural consent gate rather than a warning in the docs. A backend advertises whether it
is off-machine; a remote one refuses to run without per-run `--allow-remote`. The default
backend is local, the default consent is absent, and the API key is read from the
environment rather than a flag that would land in shell history.

### Added

- `orchestrator media extract` — OCR for images (the `[media]` extra: pytesseract,
  Pillow) and speech-to-text for audio and video (the `[asr]` extra: Whisper, kept
  separate because it pulls a full ML stack including torch). Output goes to
  `.spine-media/` as reviewable, committable artifacts keyed by content hash;
  re-extraction is skipped when an artifact is already current unless `--force`.
- A consent gate on off-machine transcription. `--asr local` (the default) and image OCR
  run entirely on this machine; `--asr api` uploads audio and refuses to run without
  `--allow-remote`. Oversized files are skipped rather than truncated silently.
- `docs/specs/` and `docs/evals/` are tracked in the repo again — the design records that
  say *why* a subsystem is shaped the way it is. This also removes a class of CI failure:
  doc ingestion reads markdown from disk whether or not git tracks it, so docs that existed
  locally but not in the repo made a contributor's `episteme/` describe pages CI could not
  see, and `understand --check` failed on a diff nobody could reproduce.

### Changed

- CodeQL query selection moved into `.github/codeql/codeql-config.yml`. The full
  `security-and-quality` suite still runs; two quality queries that misread idiomatic typed
  Python are excluded — `py/ineffectual-statement` fired on `...` as a `Protocol` method-stub
  body (PEP 544), and `py/unused-global-variable` on constants consumed only through a
  function-local import. Between them they accounted for 78 open alerts, none real, and
  because CodeQL posts alerts as review *threads* they blocked merges on a branch ruleset
  that requires thread resolution.

## 3.9.3 — Endpoints that are actually endpoints

A REST client is not a REST server, but the Java front-end couldn't tell them apart. It
matched annotations by their final segment, so Retrofit's `retrofit2.http.GET` read as
JAX-RS's `jakarta.ws.rs.GET` and every client method in a file collapsed into one
`java:endpoint:GET /` — a confident, wrong fact in a graph whose whole claim is that it
only asserts what it can ground.

Annotations now resolve through the file's imports, the way Java itself resolves names.
The second half is less visible and mattered just as much: the fact cache is keyed on the
*analyzed repo's* HEAD, not on Spine's version, so a corrected extractor doesn't reach a
repository that hasn't moved. Fixing the extractor without bumping the cache format would
have left the bad endpoints in place for exactly the users who had already run Spine.

### Fixed

- Java endpoint extraction now resolves unqualified HTTP verb and `@Path`
  annotations through explicit or wildcard `javax.ws.rs` / `jakarta.ws.rs`
  imports, with explicit non-JAX-RS imports taking precedence over JAX-RS
  wildcards as Java name resolution does. Fully qualified annotations still work
  without an import. Annotations from client frameworks such as Retrofit are no
  longer misclassified as server endpoints.
- The fact cache format is now v3, so the corrected endpoints reach repositories
  that haven't moved since they were last extracted. The cache is keyed on the
  analyzed repo's HEAD rather than on Spine's version, so without the bump an
  unchanged tree would keep serving the misclassified endpoints after an upgrade
  — and a false endpoint is indistinguishable from a real one.

### Notes

- An unqualified annotation with no resolving import is skipped rather than
  guessed at. In compilable Java the import is always present, so this only
  affects fragments.
- Known gap: a JAX-RS verb combined with a *non*-JAX-RS `@Path` in the same file
  drops the path rather than skipping the endpoint. Uncommon, and not a
  regression — tracked as a follow-up.

Thanks to [@pritam0802](https://github.com/pritam0802) for the fix
([synaptixs/spine#60](https://github.com/synaptixs/spine/pull/60)).

## 3.9.2 — Prose that survives contact with C

The graph was right; the sentences wrapped around it weren't. Running `understand` on a
large C codebase (open5gs, ~8.6k `.c`/`.h` files) exposed three places where the rendered
prose carried Python-shaped assumptions — the same class of confident-but-wrong claim
3.9.0 set out to remove, but only visible on a non-Python repo.

### Fixed

- **An un-imported area is no longer called "the safer place to change."** The fact was
  correct — nothing else depended on it — but the conclusion wasn't: that is exactly what
  an application entry point looks like. It fired on 14 of 25 open5gs areas, including a
  390-function 5G network function. Being un-imported bounds what a change reaches
  *outward*; it says nothing about how much lives inside.
- **The public/internal split now uses each language's own rule.** Applying Python's
  leading-underscore convention everywhere reported *19,212 public · 32 internal* on C —
  a number that looks computed and means nothing. C and C++ now use `static` (internal
  linkage, which the front-end already encodes), Go uses the upper-case initial, and
  Python/TypeScript/JavaScript keep the underscore. Java and C# express visibility with
  keywords the graph doesn't record, so those symbols are excluded from both counts
  rather than defaulted into "public", and the page names the rule it applied.
- **Import-cycle severity is language-aware.** "A hazard for import order" is true for
  Python and overstated for C, where include guards make mutual `#include` compile
  cleanly — a design smell, not a defect.
- **Possibly-unused candidates never include symbols of unknown visibility.** With
  `is_public` gaining a third state, an unreadable verdict would otherwise have read as
  "internal", putting real Java/C# API on a possibly-unused list.

### Changed

- Public sync commits no longer carry a hardcoded assistant co-author trailer.

## 3.9.1 — Java REST endpoints in the graph

Java joins C# in having its web framework understood, not just its classes. JAX-RS /
Jakarta REST resource methods are lifted into the same `Endpoint` nodes and `EXPOSES`
edges the C# front-end already emits — so "what does this service expose, and which
method handles it?" is now answerable for a Java codebase, and the API surface shows up
in `understand` and `state` without a new concept to learn.

Precision-first, like the rest of the Java front-end: a route is only emitted when it can
be grounded exactly. Deterministic and LLM-free, as ever.

### Added

- **JAX-RS / Jakarta REST endpoint extraction** (`pkg/java_extractor.py`) — `@GET`,
  `@POST`, `@PUT`, `@DELETE`, `@PATCH`, `@HEAD` and `@OPTIONS` become `Endpoint` nodes
  with `EXPOSES` edges to the handler method, carrying the handler's provenance. Class-
  and method-level `@Path` values are joined into one absolute route, preserving templates
  like `{id}`. Annotations are matched on their final name segment, so both `javax.ws.rs`
  and `jakarta.ws.rs` — plain or fully qualified — are recognized.
- Endpoints flow through every surface that already renders them: the `Endpoint`/`EXPOSES`
  vocabulary, RDF projection, and the API-surface section of the rendered reports were
  already language-neutral, so nothing downstream needed a new case.

### Notes

- A `@Path` with a non-literal value, and a `@Path` with no HTTP verb (a sub-resource
  locator), are deliberately skipped — a guessed route poisons grounding.
- `@Produces` / `@Consumes` and cross-file `@ApplicationPath` resolution are out of scope.
- The fact cache is keyed on the repo's HEAD commit, so a clean tree that hasn't moved
  since the last extraction will keep its cached facts. Commit, or clear the cache dir, to
  see the new endpoints on an unchanged repo.

Thanks to [@pritam0802](https://github.com/pritam0802) for the contribution
([#55](https://github.com/synaptixs/spine/pull/55), implementing
[discussion #54](https://github.com/synaptixs/spine/discussions/54)).

## 3.9.0 — Comprehension you can trust

A six-phase overhaul of the understanding layer, driven by an assessment against a real
public repo. The headline: **the dependency graph stopped lying** — relative and
intra-package imports never resolved, so the graph saw almost no internal dependencies
and confidently called a codebase's most central module "a leaf, so it's the safer place
to change". That is fixed in every language front-end at once. On `pallets/click`, import
edges naming a submodule went from 27/232 joined to **321/321**, and
`impact_across("Context")` from **0 symbols to 61**.

Built on that, the committed knowledge base went from 4 sections to 18, gained a
provenance stamp and a CI gate that **proves** it still matches the code, and turned each
module page into a pre-change briefing. Spine now commits its own `episteme/` and fails
its own CI if that knowledge base degrades.

Everything here stays deterministic and LLM-free: same commit in, byte-identical output.

### The import graph stops lying

Phase 0 of the [`understand` enhancement plan](docs/specs/understand-enhancement-spec.md):
relative and intra-package imports now join the modules they denote, in every language
front-end at once — and a standing invariant check makes sure the bug class can't return
unnoticed. Measured on `pallets/click`: import edges naming a click submodule went from
27/232 joined (all from tests) to **321/321**; `impact_across(Context)` from **0 symbols to
61**; the `click.core` area page from *"it's a leaf, so it's the safer place to change"* to
*"it sits in the middle of the graph: 10 areas below it, 8 above."*

#### Fixed

- **Python front-end reads `stmt.level`** — `from .types import X` resolves against the
  module's own package (`py:click.types.X`), so it can no longer be conflated with the
  stdlib `types`. An import that climbs past the scanned tree keeps its dots and never
  falsely joins.
- **`link_imports` post-pass** (`pkg/import_link.py`) — a whole-repo join that repoints
  `IMPORTS` edges at the first-party modules they denote and drops the orphaned phantom
  nodes. One shared resolver; per-language matchers only: dotted-prefix walk (Python /
  Java / C#), relative-specifier resolution (TypeScript), `go.mod` module-path matching
  (Go), unique path-suffix matching for `-I`-style includes (C / C++). Runs inside
  `RepoCodeExtractor.extract`, so every consumer — `understand`, `state`, grounding,
  `pkg export`, the MCP tools — gets resolved imports with no extra wiring.
- Fact-cache format bumped to v2: pre-fix caches would silently reintroduce the dangling
  imports, so they re-extract.

#### Added

- **`orchestrator pkg verify`** — Tier-1 graph invariants, no oracle needed: every edge
  endpoint exists, every grounded provenance resolves to a real `file:line`, per-language
  orphan-rate and external-ratio tripwires (the completeness failures a does-it-run test
  can't see), and phantom-basename warnings. Non-zero exit on error, so it can stand guard
  in CI. Per-language regression fixtures pin the join: a repo using relative imports must
  show non-zero importers for the imported module.

### Episteme can prove it's current

Phase 1 of the [`understand` enhancement plan](docs/specs/understand-enhancement-spec.md):
a committed knowledge base whose entire value is being *code-true* could not tell a reader
whether it described HEAD or a commit from six months ago. Now it says where it came from,
and CI can prove whether it still holds.

#### Added

- **A provenance stamp on `episteme/README.md`** — which commit the bank was generated
  from, whether that tree was dirty, and which Spine rendered it. Deliberately carries **no
  timestamp**: invariant #2 requires the same code to produce byte-identical output, and a
  date would break the property the artifact is trusted for.
- **`orchestrator understand --check`** — writes nothing, re-renders, and diffs against the
  committed bank, exiting non-zero when they disagree. It reports what to look at: pages out
  of date, missing, or still describing code that's gone. The comparison ignores the fenced
  stamp, because committing the episteme itself creates a new commit — content, not the
  stamp, is what proves currency.

#### Fixed

- **`understand` no longer reads its own output.** `episteme/` and the legacy `memory-bank/`
  join the ignored directories. A committed bank was being ingested as the repo's own
  documentation: on a small fixture it turned 6 grounded nodes into 32, all 26 `Doc` nodes
  coming from Spine's own prose. Worse, it made the artifact unable to ever be self-
  consistent — writing the bank changed the graph that rendered it, so no bank could
  describe its own repo twice the same way, and `--check` could never pass.

#### Changed

- `build_memory_bank` is now a thin writer over a new `render_memory_bank`, so the build and
  the check share one rendering path and cannot drift apart.

### One analysis layer, two renderings

Phase 2 of the [`understand` enhancement plan](docs/specs/understand-enhancement-spec.md):
`state` computed sixteen sections and printed them, while the *committed* episteme rendered
four — the ephemeral report was richer than the knowledge base a team reads and an AI tool
grounds on. Both surfaces now read one analysis. On `pallets/click`, episteme goes from 4
top-level sections to 13.

#### Added

- **`knowledge/analysis.py`** — the single pipeline (extract → migrations → data layer →
  docs → profile → metrics) that both `understand` and `state` go through. What differs
  downstream is only rendering.
- **`architecture.md`** gains the **system-architecture diagram** and the strongest
  component dependencies (drawn from `current_state`'s own bounded `architecture_graph`, so
  the two surfaces can't disagree), **layers**, and **test coverage**.
- **`tech-context.md`** gains **infrastructure & runtime**, **entry points**, and
  **most-used external imports** — it was a six-row table, mostly `—`.
- **`progress.md`** leads with computed **suggested next steps** instead of only pointing at
  a `BACKLOG.md` that doesn't exist unless Spine built the repo.

#### Fixed

- **Test coverage measured what it claimed.** An area counted as tested if it *contained* a
  type with "test" in the name — which answers "which areas are tests", not "which areas
  have tests", and reported `click.core`, the most-tested module in click, as untested.
  Coverage is now test→source imports, a lookup that only became possible once Phase 0 made
  intra-package imports resolve. click reads 13 of 27 components exercised, and the untested
  list is now genuinely untested code (`click._winconsole`, the `examples/` trees).
- **Entry points exclude tests.** `main()` inside a test file is a fixture, not how the
  system starts; click's entry-point list was two test functions ahead of the real one.

#### Note

Git-history metrics stay out of the committed bank on purpose. `state`'s "Recent activity"
reads the last ~60 commits, so its value moves on every commit — including the one that
lands the bank — which would make episteme stale the moment it was committed and
`understand --check` fail forever after.

### The module page becomes a briefing

Phase 3 of the [`understand` enhancement plan](docs/specs/understand-enhancement-spec.md):
a module page told you what the code *is*. It now answers what depends on it, what breaks
if it changes, what isn't tested, what it inherits from, and which docs describe it.
Findings 3, 8 and 9 — all of it graph computation, no LLM.

#### Added

- **"Changing this safely" on every module page** — who tests the module, the symbols other
  code most depends on with their blast radius, and which of those have no visible test
  path. This was the product's most persuasive answer and it was only reachable by running
  `regression` against a symbol you had already chosen to change.
- **`store.implementors_of()` / `store.implements_of()`** and inheritance rendered from both
  ends. click's 42 `IMPLEMENTS` edges rendered nowhere; `click.exceptions` now shows all 12
  exception subclasses, and `Parameter` → `Argument`/`Option` is walkable in either direction.
- **"Documented in …"** on modules and symbols, from `MENTIONS`, plus a repo-level
  **Documentation** section with coverage and drift. Four releases of doc ingestion had
  reduced, in the committed bank, to a single `Doc: 264` line; click now reports 6% doc
  coverage and 250 potential drift where only `state` used to.
- **`api-surface.md`** — every route and the code behind it, keyed on `Endpoint`/`EXPOSES`.
  Written only for repos that have routes.
- **`CoverageIndex`** (`sdlc/coverage.py`) — whole-repo test reachability and blast radius
  indexed once. `build_regression_plan` rebuilds a predecessor index per call, which is
  quadratic when every module page needs it; `understand` on click stays at ~1.4s.

#### Note on honesty

The first cut of the safety block reported "16 of 20 symbols have no test" for `click.core`,
naming `Context` — one of the most tested classes in the Python ecosystem. Call resolution is
precision-first (ambiguous `obj.method()` chains are skipped rather than guessed), so an
invisible test path is not an absent one. It now flags only the actionable intersection —
depended upon **and** no visible path — says plainly that invisible ≠ absent, and takes
module-level "tested by" from test **imports**, which are complete in a way call edges aren't.

### No page is a stub or a directory listing

Phase 4 of the [`understand` enhancement plan](docs/specs/understand-enhancement-spec.md) —
Findings 4, 5 and 6 — plus the CI dogfooding left open since Phase 0.

#### Changed

- **`domain-model.md` is ranked, not alphabetised.** With no database it listed 40+ classes
  A–Z and called them *prominent* without computing anything; `Abort` led click's page
  because it starts with A. Now ranked by subtypes, production call-sites, members and doc
  mentions — click's page opens with `Context`, `ProgressBar`, `Command`, `Parameter` — and
  each row says *why* it matters. Retitled too: on a repo with no database, "domain model"
  promises a schema that isn't there.
- **`glossary.md` no longer promises definitions it can't write.** It was 60 alphabetical
  lines of `**Abort** — _TODO: definition_`. Each term now links to where it's defined and
  to the doc that explains it (from `MENTIONS`); private types are excluded.
- **The "Node kinds" dump is gone from `architecture.md`.** `Function: 1938, Field: 400` was
  database statistics in a section of its own. The counts survive as scale on the graph-size
  line, and **Complexity** (size distribution + largest types) takes the slot.
- **`conventions.md`** gains counted naming conventions, test layout, and the error idiom —
  it was four sampled rules and a lint config. **`tech-context.md`** gains the declared
  version and language floor.
- **Production and test call-sites are counted apart** (Finding 6). `echo`'s
  "most-depended-upon" callers were `test_echo`, `test_echo_color_flag`,
  `test_echo_custom_file`. Rankings now use production call-sites only — being called by
  thirty tests makes a symbol well covered, not central — while both numbers are displayed,
  and caller lists lead with production.

#### Fixed

- **Unresolved base classes are recorded instead of dropped.** The Python front-end emitted
  an `IMPLEMENTS` edge only when a base resolved to an import or a local definition, so a
  class extending a *builtin* had no base at all in the graph: `class Abort(RuntimeError)`
  and even `class ClickException(Exception)` answered "extends nothing", and anything
  walking a hierarchy under-counted it. Bare-name bases now emit an external node, exactly
  as unresolved bare *calls* already did. Click's exception hierarchy reads 12 types rooted
  at `ClickException`, matching the source; the name-matching approach found 4.
- A symbol with no edges rendered as a heading followed by silence. It now says so.

#### Added

- **CI runs `orchestrator pkg verify .` and `orchestrator understand . --check`**, and Spine
  commits its own `episteme/`. The product's flagship claim is detecting when docs drift
  from code; until now its own knowledge base could drift silently.

### Answers to questions nobody was asking yet

Phase 5, the last of the [`understand` enhancement plan](docs/specs/understand-enhancement-spec.md).
Finding 10's opportunistic list: findings that need no new facts, only questions aimed at
the *reader* ("where do I start?", "what can I ignore?", "what's tangled?") rather than at
the extractor.

#### Added

- **Onboarding path** on the index — "New here? Read these first": where execution starts,
  then what the most code depends on, each step saying why it's there.
- **Public surface split** — "400 public · 306 internal, of 706". The same codebase,
  reframed as approachable, with the most-depended-upon public symbols listed.
- **Import cycles** — strongly-connected components of the module graph. Undetectable
  before Phase 0, because the graph had almost no first-party import edges to form a cycle
  with; click turns out to have an 11-module core cycle. Iterative Tarjan, because a real
  dependency chain would blow the recursion limit.
- **Possibly-unused candidates** — internal symbols with no caller, subclass or doc
  reference. Restricted to *internal* on purpose: a public function with no in-repo caller
  is what an API looks like. Labelled candidates, not verdicts.
- **`symbol-index.md`** — every first-party symbol A–Z with the page that describes it, so
  the bank is searchable by name without grep.

#### Not done, deliberately

**Churn per module** is the one item from Finding 10 left out. It reads the last ~60
commits, so its value changes on *every* commit — including the one that lands the
knowledge base — which would make the bank stale the moment it was written and
`understand --check` fail permanently. It stays in the ephemeral `state` report.
Tests→module shipped earlier, as Phase 3's "Tested by".

## 3.8.4 — The architecture diagram now explains itself

The 3.8.3 diagram named its components but didn't say what they *do* — boxes read
`CLI · cli.py`, which tells you a module exists, not why it's there. Redrawn so every box
answers "what is this for?", and every layer carries a plain-English line describing what
happens there. Documentation only; no code change.

### Changed

- **Every box now has a purpose line** — `Command line · 41 commands · the main surface`,
  `Hand out credentials · only at the moment of use`. Package paths (`plugin/`, `runtime/`)
  drop to a dimmed third line: useful to a contributor, noise to everyone else. No box is
  labelled with a filename any more.
- **Each layer is narrated.** A sentence under every layer heading says what is happening —
  *"Before writing anything, Spine reads."* — and both gates now read **"Stop."**, spelling out
  that nothing has been written before gate one and nothing pushed before gate two.
- Plainer names over internal jargon: *Read the requirement* rather than `Intake`, *The plan,
  typed* rather than `GraphIR`.
- The image is **72% smaller** (1.3 MB → 0.37 MB) at the same resolution.

## 3.8.3 — Architecture diagram

Adds a full **architecture diagram** and an [ARCHITECTURE.md](ARCHITECTURE.md) that walks the whole
platform end to end — the six layers, every component, the two human gates, and the Product
Knowledge Graph they all read from. Documentation only; no code or behaviour change.

### Added

- **[ARCHITECTURE.md](ARCHITECTURE.md)** — how Spine fits together, layer by layer, with a diagram
  that renders on GitHub and in Spine's own web UI.
- A **static architecture image** (`assets/spine-architecture.png`), shown in the README.

## 3.8.2 — Doc ingestion reaches HTML and Office

3.8.1 folded Markdown, reST, plain text and PDF into the graph. This release adds the
remaining **file** formats teams actually keep specs in — **HTML** and **Word/Excel** — so a
`.docx` architecture doc or an exported HTML spec sitting in your repo becomes `Doc` nodes
`MENTIONS`-linked to the code it describes, exactly like a README.

Still deterministic, still no LLM, still a no-op on a repo with no docs.

### Added

- **HTML ingestion** (`.html`/`.htm`) — no extra needed. `<h1>`…`<h6>` become section
  boundaries (so an HTML doc sections exactly like markdown), and inline `<code>` is
  preserved as a code claim, so the symbols a doc names actually bind. `<script>`/`<style>`
  bodies are ignored; malformed markup is skipped rather than fatal.
- **Word & Excel ingestion** behind a new **`[office]`** extra
  (`pip install 'synaptixs-spine[office]'`). `.docx` maps Word's heading styles to sections
  and treats monospace runs as code claims — the Word equivalent of backticks — and keeps
  table text, which is where spec documents put API and field lists. `.xlsx` gives one
  section per sheet and keeps string cells only (numbers and formula results are data, not
  prose about code). Encrypted or corrupt documents are skipped.
- **Markdown front matter** is now read as prose: the *values* of a `---` block (`title:`,
  `module:`, `tags:`) bind like the text they stand for, while the keys and fences no longer
  leak into the graph as noise.

### Changed

- Documentation formats are now **registered readers** rather than hard-coded branches, so
  adding a format touches no existing one. Behaviour for existing formats is unchanged.
- Standalone `.yaml`/`.yml` files are **deliberately not ingested**. A repo's YAML is
  overwhelmingly configuration, and treating it as documentation would inflate the doc
  coverage `state` reports and flood doc-drift with config values that were never prose.
  YAML's documentary case — front matter — is covered above.

## 3.8.1 — Doc & PDF ingestion: your docs become code-linked facts

Spine now reads a repository's **documentation** — Markdown, reStructuredText, plain text,
and **PDF** — into the Product Knowledge Graph as first-class **`Doc` nodes**, each
**`MENTIONS`**-linked to the code symbol it describes. So comprehension can answer *"which
docs describe `X`?"*, *"how documented is this?"*, and *"do the docs still match the code?"* —
all deterministic, no LLM. This is the *knowledge-doc* half of Spine's doc story; the
*structured-doc* half (OpenSpec `openspec://` → intents) already shipped. It closes the
biggest remaining reach gap vs. doc-graph tools.

Nothing to configure: docs are folded in automatically when you run `orchestrator understand`
or `orchestrator state`. A repo with no docs behaves exactly as before.

### Added

- **Doc ingestion** — `understand`/`state` now emit `Doc` nodes + `MENTIONS` edges. Binding is
  **precision-first**: a mention becomes an edge only when it resolves to exactly one symbol.
  Reuses the deterministic doc→symbol binder already in `pkg/docs.py`.
- **PDF support** behind a new **`[docs]`** extra (`pip install 'synaptixs-spine[docs]'`, lazy
  `pypdf`). The base install stays stdlib-only; malformed or scanned (image-only) PDFs are
  skipped, never fatal — no OCR.
- **`state` Documentation section** — doc count, **symbol coverage %** (how much of the code the
  docs describe), and top **doc drift** (doc claims about code the graph can't resolve —
  renamed/removed symbols), filtered to real symbols so paths/URLs/filenames don't drown it.
- **`docs_for` `/spine` MCP tool** — with a `symbol`, the docs that describe it; with no symbol,
  a doc-coverage summary + top drift. Joins the read-only comprehension tool set; documented in
  the Claude/Codex guides and the `understand-codebase` skill.
- **Section-granular `Doc` nodes** — Markdown is split by heading into `doc:README.md#usage`
  nodes (bounded), so a `MENTIONS` edge points at the *section* that names a symbol, with
  provenance at the heading line.
- **Doc-grounded codegen** — `sdlc feature` grounding now folds a reused symbol's documenting
  prose into the codegen context, so generated code sees not just an API but what it's for.
- **Doc-drift review finding** — `GroundingVerifier.doc_findings` surfaces stale-doc symbol
  claims as an informational, source-anchored finding.

## 3.8.0 — The `/spine` comprehension skill

Spine's read-only comprehension is now a **drop-in skill** any assistant can call — Codex
(plugin) and Claude Code (an `understand-codebase` Agent Skill) — so you can ask about a
codebase in plain language and get engineering *decisions*, not just a map: what a change
breaks, what's untested, and where a ticket or bug lands, each grounded to `file:line`.

### Added

- **Comprehension MCP tools** on the Spine plugin server, all read-only, deterministic
  (no LLM), and needing no credentials: `map_repo` (structure, call-hotspots, coverage
  gaps, recommendations), `blast_radius` ("what breaks if I change X" — callers +
  cross-layer reach), `explain_symbol`, `investigate` (where a ticket lands), `localize`
  (stack trace → fault site), and `regression_gaps` (blast-radius symbols with no covering
  test). Each returns structured fields **plus** a `markdown` rendering. They join
  `read_memory_bank` (a repo's committed `episteme/`).
- **`root_cause`** — a grounded root-cause report (fault site, ranked hypotheses with
  evidence, regression surface, fix approach). Deterministic by default; `use_llm=true`
  opts into LLM-enriched hypotheses.
- **`understand-codebase` Agent Skill** bundled with the Claude Code plugin — tells Claude
  which tool to reach for, so you just ask in plain language.
- **git-URL support** across the comprehension tools — point them at a local path *or* a
  git URL (shallow-cloned behind the same host allow-list as the CLI). Serve them to a
  remote host over HTTP with `orchestrator-mcp --http`.

## 3.7.0 — Go: the 8th PKG language

Go is now a first-class language across the whole stack — comprehension, the call and
interface graph, and greenfield **and** brownfield codegen — so `understand`, `state`,
`design`, `investigate`, `localize`, `rca`, `regression`, grounding, and
`sdlc feature --language go` all work on Go repos. Install with the `go` extra
(`pip install 'synaptixs-spine[go]'`); codegen needs the `go` toolchain on PATH.

### Added

- **Go comprehension** (`go` extra, tree-sitter-go) — `Module`/`Type`/`Function`/`Field` +
  `IMPORTS`/`CONTAINS`. Go's module unit is the **package = its directory**, so every `.go`
  file in a dir merges into one component (the first front-end where that holds).
- **Go call + data + interface graph** — `CALLS` (same-file package functions and
  receiver-method calls), `REFERENCES` (same-package struct-field types), and the Go
  highlight, **`IMPLEMENTS` by method-set matching**: because Go has no `implements` keyword,
  a concrete type is linked to each in-repo interface it structurally satisfies (matched by
  method name + arity over value **and** pointer receivers). So blast-radius, `design`,
  `rca`, and `regression` light up on Go.
- **Go codegen** (`sdlc feature --language go`) — scaffolds/extends a module and builds +
  tests it with `go build ./...` / `go test ./...`, with co-located `_test.go` tests. It is
  **multi-module aware**: the runner builds and tests the module(s) a change actually
  touches (not just the repo root), so code generated into a sub-module is never a false
  green.

### Changed

- **`sdlc feature --language` is now validated** against the supported set — an unknown value
  errors instead of silently scaffolding a Python project.

## 3.6.1 — Shareable codebase-intelligence report

`orchestrator state . --out report.html` now emits a single **self-contained HTML file** you
open in a browser and forward to your team — the engineering-decision counterpart to a
concept-map `graph.html`. Deterministic, no LLM, nothing fetched. It packages the analysis
`state` already computes, so this is rendering, not new comprehension.

### Added

- **Shareable HTML report** — `orchestrator state . --out report.html` writes one
  self-contained, theme-aware (light/dark) file with a provenance header, plain-language
  overview, architecture diagram, blast-radius hotspots, risk & health, test-coverage gaps,
  security surface, recent activity, and prioritized recommendations. `--out *.html` selects
  HTML; any other extension keeps today's markdown. `--no-timestamp` gives byte-stable output
  for CI diffs. The `--lens stakeholder` view drops the jargon-heavy sections.
- **Deterministic architecture diagram** — an inline SVG (components grouped into zones,
  weighted dependency arrows) laid out seeded-in-Python, so the same commit renders the same
  picture; it grid-wraps large zones to stay legible and themes with the page (no mermaid, no
  external assets).
- **Graph-quantified blast radius** — the spotlight quantifies the cross-layer impact of the
  top hotspot via `impact_across` ("changing X → N dependents across M files") and lists
  blast-radius symbols with no covering test via the regression plan (`build_regression_plan`).
- **In-browser filter** — a client-side search box hides non-matching rows, dims non-matching
  architecture components, and collapses emptied sections; vanilla JS, no build step, still one
  self-contained file.

## 3.6.0 — Knowledge-graph-grounded design & RCA

A suite of new, deterministic-first CLI commands that ground engineering work — design,
debugging, and root-cause analysis — in the Product Knowledge Graph, plus the call-graph
extraction that makes them work across languages. Every command is inspectable and states
its own limits rather than implying certainty.

### Added

- **`orchestrator design`** — spec × knowledge graph → a grounded design with a **blast
  radius** (which modules a change touches, who imports them, the call hotspots) and an
  **unverified-references** flag for named paths absent from the graph. Deterministic by
  default; `--llm` writes the prose.
- **`orchestrator investigate`** — research a ticket against the codebase before designing:
  where it lands in the code (real symbols with `file:line` + caller counts) and the relevant
  committed `episteme/` knowledge. Ticket from a source URI or inline.
- **`orchestrator localize`** — parse a stack trace / pytest failure and resolve each frame to
  the repo symbol it names, pointing at the likely fault site and its callers.
- **`orchestrator rca`** — a gated root-cause report: fault site, ranked root-cause
  *hypotheses* with evidence (exception priors, recent git churn, call sites), the regression
  surface a fix must cover, and a scoped fix approach. Stops at analysis — no autonomous code.
- **`orchestrator regression`** — blast-radius regression coverage: split the call-graph
  impact of a change into tests that already exercise it vs production code with no covering
  test (the gaps).
- **Jira as a read source** (`jira://PROJ-123` / `jira://PROJ` / `jira://jql/…`) — ingest
  existing issues as requirements, the read counterpart to the Jira issue-tracker sink.
- **Generalized MCP-backed sources** — `mcp-jira` and `mcp-confluence` presets plus a generic
  `mcp` escape hatch, so any onboarded MCP server can back intake (route access through a
  governed server instead of spreading REST tokens).

### Changed

- **Call graphs across the stack:** the Java and TypeScript front-ends now extract `CALLS`
  edges (precision-first; TypeScript resolves relative imports to the definition, so
  cross-file call graphs connect). Impact, RCA, and regression coverage now work on Python,
  C, C++, C#, Java, and TypeScript.
- **`FactStore.impact_across`** — composed transitive blast radius over CALLS + IMPORTS +
  REFERENCES, so impact traces across the code, module, and data layers.
- The README banner now shows the platform's full capability map rather than a single pipeline.

## 3.5.0 — Security hardening

This release is the output of a security baseline of Spine's own source tree. Nothing
here is a claim that the codebase is "secure" — it is a description, verifiable against
this repository, of the checks we now run and the issues we found and fixed.

### 🔒 Security

- **Continuous checks in CI, on every pull request:**
  - **CodeQL** dataflow analysis for Python and JavaScript.
  - **`pip-audit`** over the resolved lockfile (not the ambient environment — bare
    `pip-audit` in a uv checkout audits the wrong thing and false-passes).
  - **`bandit`-class static analysis** via ruff's flake8-bandit (`S`) rules, wired
    into the existing lint gate.
  - **Dependabot** for weekly dependency and GitHub-Actions updates.
- **A multi-model adversarial self-review** across the full source tree: 863 candidate
  findings were triaged by one model, then independently verified by a stronger model
  instructed to *refute* each one. 174 of the high-severity candidates were refuted as
  safe-by-design; **7 confirmed issues were fixed, each with a regression test.**
- **All patchable dependency CVEs resolved** — 17 of 18 known advisories fixed by
  version bumps (aiohttp, starlette, cryptography, langsmith, langgraph, pydantic-
  settings). The one remaining (`click`'s `click.edit()` command injection) is
  unreachable — Spine never calls that function — and is documented rather than
  force-fixed, because the fix would regress the `semgrep` scanner by ~2 years.
- Coordinated disclosure via [SECURITY.md](SECURITY.md).

### Fixed

Security fixes from the review above, described at the level of *what class of issue*
rather than a reproduction:

- **Path traversal** in the knowledge-base reader and the `memory-bank` capability
  endpoint — an untrusted section name or a symlink committed in a cloned repo could
  read files outside the intended directory. Reads are now confined to the bank dir.
- **Stored XSS** in the operator web UI — the shared HTML escaper escaped `&<>` but not
  quotes, so an untrusted value (e.g. a cloned-repo file name) placed in a quoted HTML
  attribute could break out. The escaper now escapes quotes across all web UI files.
- **SSRF backstop** for remote-repo cloning — the internal-host guard missed obfuscated
  IPv4 encodings (integer, hex, octal, short-form) that resolve to loopback. These are
  now normalized and blocked. (The guard was already robust under its default
  restrictive host allow-list; this hardens the opt-in `*` mode.)
- **Prompt-injection hardening** in the codegen/design/review pipeline — untrusted
  cloned-repo content fed into LLM prompts is now fenced and marked as data, and the
  review judge is instructed to ignore injected verdicts. This is defense-in-depth; the
  human merge approval remains the authoritative gate.

### Added

- `SECURITY.md` disclosure policy surfaced in the README.
- Security review plan and methodology in `docs/specs/security-review-plan.md`.
