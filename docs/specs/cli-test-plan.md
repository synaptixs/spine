# CLI test plan — every command, with acceptance criteria

**Status:** living document · 50 commands · written 2026-08-10 against 3.16.0

A manual sweep of all 48 CLI commands is what found SSPN-49. This is that sweep, made
repeatable: every command Spine exposes, what it must do, and what counts as passing.

**Tiers are ordered by blast radius, and you should work outward.** A tester who runs
Tier 0 first learns the tool without touching anything; a tester who starts at Tier 4
files Jira issues and spends money. Each tier states what it needs and what it can break.

| Tier | Needs | Touches | Spends |
|---|---|---|---|
| 0 | nothing | nothing | no |
| 1 | a repo | writes under `.spine/` or a named `--out` | no |
| 2 | the registry API running | the local API's store | no |
| 3 | a source (Jira / Confluence / MCP) | reads only, unless stated | intake only |
| 4 | credentials + a target repo | branches, PRs, tracker issues | **yes** |

---

## Cross-cutting criteria

These apply to **every** command in the plan. A command can pass its own criteria and
still fail here.

1. **`--help` works and describes the command** without importing a provider or touching
   the network.
2. **An expected failure prints one line, not a traceback.** A missing file, an
   unreachable service, a malformed argument — all are expected conditions.
   **⚠ Known failing:** `template *` and `contract *` print ~40 lines of Rich-formatted
   stack frames when the registry API is down (SSPN-49, still open).
3. **Exit codes are meaningful**: `0` success, `2` bad usage or missing config, non-zero
   and specific otherwise. Never `0` on a failure.
4. **Read-only means read-only.** Nothing in Tiers 0–1 mutates a tracker, a remote, or the
   working tree outside `.spine/` and explicit `--out` paths.
5. **Every bounded output says what it elided** — "top N of M", never a clipped list that
   reads as complete.
6. **`--json` (where offered) parses**, and carries the same facts as the human render.

---

## Recording results

**A test plan nobody records against is a checklist.** Copy the results table at the end
of this document into a new file per sweep and fill it in as you go:

```
docs/specs/cli-test-results/<version>-<YYYY-MM-DD>-<who>.md
```

One file per sweep, never edited in place afterwards. Two sweeps of the same version on
different machines are two files, because "it passed for me" is the thing this is meant to
settle.

### The five verdicts

| verdict | means | needs |
|---|---|---|
| **pass** | every criterion for that command held | the evidence column filled |
| **fail** | a criterion did not hold | what you saw, and the exit code |
| **partial** | some criteria held, at least one did not | which ones, by number |
| **blocked** | could not run — missing config, service down, no credentials | what was missing |
| **skipped** | chose not to run it | **why** — cost, external writes, no target repo |

**A skipped test is not a passed test**, and a summary that adds them together is the
failure this document exists to prevent. Report them in separate columns.

**A known ⚠ failure recorded as `fail` is the correct result**, not a broken sweep. Note
its id (e.g. SSPN-49) in the notes column so a reader can tell an old defect from a new one.

### Evidence, not assertion

The evidence column takes the **one line that decides it** — an exit code, the summary
line, the count that was wrong. Not a full transcript, not "looks right". If a criterion
turns on a file, name the path.

```
| 1 | `pkg extract` | pass | `nodes: 12,481  edges: 31,073`, edges_CONSUMES: 120 | |
| 2 | `template list` | fail | 41-line traceback, exit 1, API down | SSPN-49, known |
```

### The header every sweep carries

```markdown
# CLI sweep — <version> — <who> — <YYYY-MM-DD>

**Commit:** `<sha>`  ·  **Machine:** <os / arch>  ·  **Model configured:** <id or none>
**Tiers run:** 0–1  ·  **Not run:** 2–4 (no registry API, no credentials)

**Result:** N pass · N fail · N partial · N blocked · N skipped  (of 50)

**New defects found:** <links or "none">
```

That block is the shareable summary. Paste it into a PR or a ticket and the reader knows
what was covered, what was not, and on what — without opening the file.

---

## Tier 0 — no configuration, nothing touched

Run these first on any machine. They need no keys, no repo, no server.

### `orchestrator doctor`
```bash
orchestrator doctor
```
**Prereq:** Nothing. Run it in a directory with no `.env` too — that is a valid case.
- Reports every check with `[OK]`, `[SKIP]` or a failure, and a reason for each.
- Names the variables that are missing rather than saying "not configured".
- Exits `0` when the local CLI is usable even if Mode B is unconfigured — a skipped
  optional block is not a failure.
- Says how many variables it loaded from `.env`.

### `orchestrator models`
```bash
orchestrator models --provider openai
orchestrator models --all
```
**Prereq:** Nothing. A provider key is *not* required: the catalog is local to LiteLLM.
- Lists ids with context window, `$/Mtok` in/out, and tool-calling support.
- Marks what each stage currently resolves to, so an override can be confirmed.
- `--tools-only` (default) hides models that cannot do a forced tool call; `--all` shows
  them. The distinction is stated, because codegen and the judge silently degrade on those.
- `--provider` with an unknown vendor returns an empty list and says so, not an error.

### `orchestrator pkg capabilities`
```bash
orchestrator pkg capabilities
orchestrator pkg capabilities --format json
```
**Prereq:** Nothing — no repo needed. It reads the front-ends' own source.
- Prints the node/edge matrix per language front-end, read from the front-ends' source.
- `python` shows ✓ for `CONSUMES`; every other front-end shows `·`.
- Output is byte-identical to the block committed in `KNOWLEDGE_GRAPH.md` — this is what
  the capability-matrix test asserts, so a drift here is a stale doc.
- `--format json` parses and carries the same matrix.

### `orchestrator catalog list`
```bash
orchestrator catalog list
```
**Prereq:** Nothing.
- Lists assemblable capabilities; needs no project and no network.

### `orchestrator init`
```bash
orchestrator init --path /tmp/spine-init-test
```
**Prereq:** An empty directory you can throw away. **Never run it in a repo whose `.env` you care about** — verify the target is empty first: `ls -a /tmp/spine-init-test`
- Creates `.env` from the template in a **new** directory.
- **Refuses to overwrite an existing `.env`** and says so — a test that silently replaced
  a real one would cost credentials.
- Prints the next step rather than leaving the user at a prompt.

---

## Tier 1 — read-only against a repo

**Tier 1 baseline.** A checked-out repo with source the extractor understands, on a clean
tree. Verify before starting, because a repo the graph cannot see turns every command below
into a false pass — they will all cheerfully report nothing:

```bash
git status --porcelain          # empty: a dirty tree stamps documents "-dirty"
orchestrator pkg extract --path .
```

Non-zero nodes **and** non-zero edges. Zero edges with non-zero nodes means the call graph
collapsed, and `regression`, `rca` and section 5 of `sdlc plan` will all under-report
without saying so.

Nothing external is contacted at this tier. Outputs land under `.spine/` or a `--out` you
name.

### `orchestrator pkg extract`
```bash
orchestrator pkg extract --path . --json
```
**Prereq:** Tier 1 baseline.
- Prints node and edge totals, and **an `edges_<kind>` count for every kind** — a kind with
  no edges reports `0` rather than being omitted.
- Counts are stable across two runs on an unchanged tree.
- `--json` parses. **Known limitation:** it carries nodes and the summary, not edges.

### `orchestrator pkg verify`
```bash
orchestrator pkg verify --path .
```
**Prereq:** Tier 1 baseline.
- Reports Tier-1 invariants: dangling edges, missing provenance, unjoined imports.
- A clean repo exits `0`; each violation names the node or edge, not just a count.

### `orchestrator pkg export`
```bash
orchestrator pkg export --path . --format json --out /tmp/g.json
orchestrator pkg export --path . --format graphml --out /tmp/g.graphml
```
**Prereq:** Tier 1 baseline, plus a writable `--out` path.
- Every offered format writes a file another tool can open (JSON parses, GraphML is
  well-formed XML, SQLite opens).
- Node and edge counts match `pkg extract` on the same commit.

### `orchestrator pkg docs`
```bash
orchestrator pkg docs --path .
```
**Prereq:** Tier 1 baseline, plus at least one markdown/rst/txt file in the tree — otherwise there is nothing to reconcile and a pass proves nothing.
- Reconciles doc claims against the graph and reports drift with `file:line`.
- Says which docs it read; a repo with no docs says so rather than reporting zero drift.

### `orchestrator profile`
```bash
orchestrator profile --path .
```
**Prereq:** Tier 1 baseline.
- Detects languages, framework, DB and test setup, and says how confident it is.
- **Detection and extraction are independent** — a language detected here may still yield
  zero graph nodes, and that must not be presented as coverage.

### `orchestrator understand`
```bash
orchestrator understand --path . --out /tmp/episteme-test
orchestrator understand --path . --check
```
**Prereq:** Tier 1 baseline. For `--check`, a repo that already has a committed `episteme/` — on a repo without one the check has nothing to compare against.
- Writes the `episteme/` knowledge base; **no LLM call anywhere in the path**.
- **Deterministic:** two runs on the same commit produce byte-identical output.
- `--check` exits non-zero when the committed bank differs from a fresh generation, and
  names the files that differ.

### `orchestrator state`
```bash
orchestrator state --path .
```
**Prereq:** Tier 1 baseline.
- Renders the two lenses plus the Documentation section.
- Groups by owning module, never by symbol id — C/C++ ids are symbols, and id-grouping
  makes every function its own component.
- Deterministic for a given commit.

### `orchestrator investigate`
```bash
orchestrator investigate --path . --problem "CLI crashes when the registry API is down"
```
**Prereq:** Tier 1 baseline, plus a problem statement in the ticket's own words.
- Returns ranked landing symbols with `file:line` and caller counts.
- **Retrieval is lexical**, and the output must not imply otherwise — a brief that names
  none of the files a ticket will change is the SSPN-49 failure.
- Deterministic; no LLM.

### `orchestrator localize`
```bash
orchestrator localize --trace /tmp/traceback.txt
```
**Prereq:** A file containing a real traceback, and the repo it came from. Generate one: `python -c 'raise ValueError("x")' 2> /tmp/traceback.txt`
- Resolves trace frames to repo symbols; external frames drop out rather than mis-resolve.
- **Reads prose, not just tracebacks**: an exception named mid-sentence is picked up, and
  file paths the text names are resolved against the graph.
- A named file is reported as a *file*, never as a fault site.

### `orchestrator rca`
```bash
orchestrator rca --problem "ConnectError: [Errno 61] Connection refused in cli.py"
```
**Prereq:** Tier 1 baseline. Test it twice — once with a traceback, once with prose naming an exception and a file — because those are two different code paths.
- Produces ranked hypotheses **with evidence**, never an asserted cause.
- Deterministic with no `--model`; identical input gives identical output.
- The recently-changed banner and the ranked list agree with each other.
  **⚠ Known weakness:** "recently changed" reads the last 40 commits repo-wide, so an
  actively-developed file scores `[high]` regardless of relevance to the ticket.

### `orchestrator regression`
```bash
orchestrator regression --path . --symbol _client
```
**Prereq:** Tier 1 baseline, plus a symbol name that exists in the graph. Confirm one first: `orchestrator pkg extract --path . --json | head`
- Names the tests that cover the symbol and the production dependents a change reaches.
- "Covered" means *reached by a test through the call graph* — exercised, not asserted
  correct — and the output says so.
- A language with no call graph reports unknown, not zero.

### `orchestrator design`
```bash
orchestrator design --spec ./SSPN-49.json --path .
```
**Prereq:** Tier 1 baseline, plus a spec JSON. No model key needed — the heuristic path runs.
- Prefers the paths the spec **states** over anything inferred from its words.
- Attaches blast radius and flags design-named paths absent from the graph.
- Runs without a model configured (heuristic path) and says which path produced it.

### `orchestrator backlog`
```bash
orchestrator backlog --source jira://SSPN-49
```
**Prereq:** A source that has been ingested at least once. With no cache this tests the *empty* path, which is also worth running — just record which one you ran.
- Renders the **cached** backlog; contacts nothing.
- With no cache, says which source has none and how to populate it — no traceback.

### `orchestrator catalog plan`
```bash
orchestrator catalog plan --path .
```
**Prereq:** Tier 1 baseline.
- Shows the capability plan for the profiled project, with why each was chosen.

### `orchestrator sdlc plan` ⭐
```bash
orchestrator sdlc plan --spec ./SSPN-49.json --path .
```
**Prereq:** Tier 1 baseline, plus a spec JSON — `docs/specs/build-documents/SSPN-49.json` is a worked one. Run it against **both a bug and a feature**: section 3 and section 12's denominator differ between them, and a sweep of only one hides half the behaviour. No model key needed with `--spec`.
- Renders **twelve sections in fixed order**, each with a provenance label.
- **No model call with `--spec`** — same commit and spec produce a byte-identical body.
- Section 3 is **omitted** when nothing localizes; it is never padded.
- Section 4 states whether the investigation brief can be trusted for this ticket.
- Section 5 carries a mermaid diagram that renders in `md.js`'s subset, then *Reading it*,
  *Containment*, *Caveat*, *Evidence*.
- Section 8 shows all three criteria states, and an already-met criterion keeps its place.
- Section 10's payload manifest lists only sections that exist.
- Section 12 gives **two bands, never one number**, and a check that cannot apply reads
  `n/a` rather than scoring zero.
- Writes to `.spine/plans/<INTENT>-build.md`; a changed document snapshots the old one to
  `history/` keyed by commit; an unchanged one writes no snapshot.
- Verify the diagram: `node scripts/check-mermaid.js .spine/plans/<INTENT>-build.md`

### `orchestrator sdlc approve`
```bash
orchestrator sdlc approve SSPN-49 --note "why"
orchestrator sdlc approve SSPN-49 --reject --note "why not"
```
**Prereq:** A plan already rendered for that intent — run `sdlc plan` first, or the command correctly refuses and you have tested only the error path. Also needs `git config user.name`, or pass `--by`.
- Records who, when, why, the derivation commit, and a digest of the document body.
- `--by` defaults to `git config user.name`; with neither, it refuses rather than guessing.
- Re-running `sdlc plan` shows the decision in the header under a `human` label.
- **Editing a file the plan names makes it read `stale`** on the next render.
- Approving a plan that does not exist exits `2` and names the path.

### `orchestrator sdlc runs`
```bash
orchestrator sdlc runs list
```
**Prereq:** Nothing, but a previous `autorun` gives it something to list.
- Lists running, parked and abandoned runs with their phase and spend.
- A parked run names what it is waiting on.

---

## Tier 2 — needs the registry API

**Tier 2 baseline.** The registry API running and reachable, with `ORCHESTRATOR_API_URL`
and `ORCHESTRATOR_API_KEY` matching what the server started with:

```bash
orchestrator up          # or: docker compose -f docker-compose.dev.yml up
curl -fsS "${ORCHESTRATOR_API_URL:-http://localhost:8000}/healthz" && echo " reachable"
```

**Run one command with the API deliberately down before you bring it up.** That is the
SSPN-49 case, it is the only way to test cross-cutting criterion 2, and it takes ten
seconds while the server is not yet started.

A mismatched `ORCHESTRATOR_API_KEY` gives 401s that read like a broken command — check it
before filing anything.

### `orchestrator template {register,list,show,publish,deprecate}`
```bash
orchestrator template register ./template.json
orchestrator template list --tag x --status published
orchestrator template show my-template --version 1.0.0
orchestrator template publish my-template 1.0.0
orchestrator template deprecate my-template 1.0.0
```
**Prereq:** Tier 2 baseline, plus a template JSON **and** a YAML one — both formats are claimed.
- Register accepts JSON **and** YAML; a malformed file is rejected naming the problem.
- List filters by `--tag` and `--status`; an empty result says so rather than printing
  nothing.
- Show without `--version` returns the latest **published** version.
- Publish promotes a draft; publishing an already-published version is refused, not
  silently re-applied.
- Deprecate marks it and leaves it retrievable.
- **⚠ With the API down, all five must print one actionable line naming the URL and how to
  start the server. They currently print a traceback (SSPN-49).**

### `orchestrator contract {register,list,show,publish,deprecate}`
```bash
orchestrator contract register ./contract.json
orchestrator contract list --tag x --status published
orchestrator contract show my-contract --version 1.0.0
orchestrator contract publish my-contract 1.0.0
orchestrator contract deprecate my-contract 1.0.0
```
**Prereq:** Tier 2 baseline, plus a contract JSON and a YAML one.
- The same five criteria as `template`, against tool contracts — and the same known
  SSPN-49 failure with the API down.
- A contract registered here is what `mcp contracts` renders for an MCP tool, so the two
  views must agree on argument types for the same contract.

### `orchestrator task submit`
```bash
orchestrator task submit ./task.json
```
**Prereq:** Tier 2 baseline, plus a task JSON the server will accept.
- Submits and prints the **final** state, not the accepted state.
- A task that fails server-side reports the server's own message.

---

## Tier 3 — sources and MCP

**Tier 3 baseline.** Either MCP or REST credentials for the source you are testing —
Spine prefers MCP wherever a capable server is onboarded, so **confirm which path is
actually being taken** before recording a result against the wrong transport:

```bash
orchestrator mcp list                    # does a server expose jira_get_issue?
orchestrator doctor                      # or are the REST variables set?
```

Two things to settle before you start:

- **`JIRA_DRY_RUN=true`** unless you intend to file real issues. Check it, do not assume it.
- **MCP covers reads only.** Issue creation, transitions and worklogs go over REST
  regardless of what is onboarded, so an MCP-only setup reads tickets fine and cannot
  write — record that as configuration, not as a defect.

### `orchestrator mcp list`
```bash
orchestrator mcp list
ORCHESTRATOR_MCP_CONFIG=./mcp_server.json orchestrator mcp list
```
**Prereq:** An `mcp.json` (or `ORCHESTRATOR_MCP_CONFIG`) naming at least one server, and whatever that server needs to start. Test a **broken** server too — an unreachable one must be distinguishable from a misconfigured one.
- Lists allow-listed tools per server, with `read_only` per tool.
- A server that cannot start appears under `unavailable` **with the reason** — config vs
  unreachable is the distinction that matters.
- Honours `ORCHESTRATOR_MCP_CONFIG`.

### `orchestrator mcp contracts`
```bash
orchestrator mcp contracts
```
**Prereq:** Tier 3 baseline, plus a server whose tools expose an input schema.
- Shows the `ToolContract` derived per tool, including argument types.
- A tool whose schema cannot be read is reported, not skipped.

### `orchestrator mcp call`
```bash
orchestrator mcp call atlassian:jira_get_issue --args '{"issue_key":"SSPN-49"}'
```
**Prereq:** Tier 3 baseline, plus a real tool name and valid arguments. To test the governance refusal you need a **write** tool on a server that is not `write_enabled`.
- Invokes one tool and prints the result.
- **A write tool is refused unless the server is `write_enabled`** — governance, not
  convenience.
- Malformed `--args` JSON is rejected before the call.

### `orchestrator mcp ingest-db`
```bash
orchestrator mcp ingest-db --server <db-server>
```
**Prereq:** A database MCP server onboarded, with a schema it can introspect.
- Introspects schema into `Entity`/`Field` facts and reports counts per table.

### `orchestrator ingest`
```bash
orchestrator ingest --source jira://SSPN-49 --dry-run
orchestrator ingest --source file://./spec.md --dry-run
```
**Prereq:** Credentials or MCP for the source. **Keep `JIRA_DRY_RUN=true`** unless you intend to file issues. `file://` needs nothing — start there.
- `--dry-run` (default) **creates nothing** and prints the would-be issues.
- Prefers an onboarded MCP server over REST credentials, and says which it used.
- `--refresh` re-extracts; without it the cached plan is reused and says so.
- Gaps gate issue creation; `--force` is required to proceed past them.
- `file://` needs no credentials.

### `orchestrator openspec draft`
```bash
orchestrator openspec draft --source confluence://<id>
```
**Prereq:** A source to draft from, and a writable `openspec/` directory.
- Writes change proposals under `openspec/changes/`, reviewable before use.

### `orchestrator media extract`
```bash
orchestrator media extract --path ./assets
```
**Prereq:** Image, audio or video files to process, plus the optional extras installed — without them the command should say what is missing rather than fail.
- OCR and transcripts land under `.spine-media/` as reviewable artifacts.
- A file it cannot process is named and skipped, not fatal.

---

## Tier 4 — writes externally, or spends money

**Do not run these to "see what happens".** Each one either costs money, writes to a
tracker, or pushes a branch. `--safe` and `--dry-run` are the versions to test first.

**Tier 4 baseline**, and every item is a way to lose money or write somewhere real:

```bash
orchestrator models                      # confirm which model each stage resolves to
echo "$JIRA_DRY_RUN"                     # want: true
git -C <target-repo> status --porcelain  # want: empty
```

- **A model key is configured and you know which model.** Section 11 of a build document
  for the same ticket will price the run before you start it — read that first.
- **A cost cap on every run.** `--max-cost` on `autorun`, or `SDLC_RUN_BUDGET_USD`.
- **A target repo you are willing to have branches pushed to.** Not this one, unless you
  mean it.
- **`--safe` for the first pass of anything.** It makes no external write at all, so the
  only thing at risk is tokens.
- **Record the spend** in the evidence column. It is the number nobody has, and the one
  section 11's estimate is eventually judged against.

### `orchestrator sdlc autorun`
```bash
orchestrator sdlc autorun --source file://./spec.md --spec ./SSPN-49.json --path . --safe
```
**Prereq:** **A model key (this spends money), a target repo, and an approved plan.** Run `sdlc plan` then `sdlc approve` first, or you are testing only the refusal. Use `--safe` for the first pass and `--max-cost` always. `--source` is required even with `--spec`.
- **Refuses without an approved plan** and parks rather than fails (`--no-plan-gate` skips,
  and says so out loud).
- A plan that changed since approval is refused, naming the approver and the commit.
- `--safe` makes **no external write**: local branch and commit, dry-run tracker, no push.
- Stages run in order and each records itself, including `skipped` with a reason.
- Each stage appends to `.spine/plans/<INTENT>-journey.jsonl`; **no stage rewrites an
  earlier one**.
- Implement touching files the design did not name is journalled as a disagreement.
- `--max-cost` parks the run rather than shipping half a change.
- **⚠ `--source` is required even when `--spec` supplies the spec**, and is then unused.

### `orchestrator sdlc feature`
```bash
orchestrator sdlc feature --source file://./spec.md --safe
```
**Prereq:** **A model key (spends money)** and a target repo. `--safe` first, then `--review`.
- The linear pipeline for one intent; `--safe` writes nothing external.
- Every guard fires and is visible: `[validity]`, `[typecheck]`, `[proof]`, `[coverage]`,
  `[judge]`.
- `--review` shows the full diff and asks once, before the first write; **it fails closed**
  with no terminal.

### `orchestrator sdlc run`
```bash
orchestrator sdlc run --source <uri>
```
**Prereq:** A running Temporal stack and worker on the `sdlc-tasks` queue, plus a model key.
- Starts the Block-C workflow on the `sdlc-tasks` queue; requires Temporal.

### `orchestrator sdlc address-review`
```bash
orchestrator sdlc address-review --pr <url>
```
**Prereq:** A real PR with human review comments, GitHub auth, **and a model key**.
- Reads human review comments only, revises, and pushes.
- Comments it could not action are reported rather than dropped.

### `orchestrator sdlc complete`
```bash
orchestrator sdlc complete --pr <url>
```
**Prereq:** A **merged** PR and Jira write credentials. Also test it against an unmerged PR — it must refuse.
- Moves the Jira issue to Done for a **merged** PR; refuses on an unmerged one.

### `orchestrator sdlc remediate`
```bash
orchestrator sdlc remediate --report drift.json
```
**Prereq:** A drift report JSON, a target repo, and a model key.
- One governed run per affected entity, each independently reviewable.

### `orchestrator sdlc baseline`
```bash
orchestrator sdlc baseline --corpus <path>
```
**Prereq:** An eval corpus with known answers, and a model key. **This spends the most of anything here.**
- Scores the agent against tickets with known answers; reports per-ticket, not just a mean.

### `orchestrator audit`
```bash
orchestrator audit --path .
```
**Prereq:** A repo and a model key. Writes nothing, still costs tokens.
- Read-only agentic audit → findings report. **Costs model tokens** despite writing nothing.

### `orchestrator up`
```bash
orchestrator up
```
**Prereq:** Docker running, and ports 8000/5433/7233 free. Check first: `lsof -i :8000`
- Brings the local stack up and opens the inbox; a port already in use is reported rather
  than left as a failed connection later.

### `orchestrator tui`
```bash
orchestrator tui
```
**Prereq:** An interactive terminal, and ideally a run in flight to watch.
- Watches runs and clears gates. **It must not be the only way to clear one** — every gate
  it can clear has a non-interactive equivalent, or an unattended run cannot proceed.

---

## What this plan does not cover

- **Concurrency.** Two runs against one ticket race for the same branch; the guard exists
  but is not exercised here.
- **Recovery paths.** `--resume`, reaping abandoned runs, and the refine loop's retry
  allowance each need their own plan.
- **Non-Python front-ends.** Every Tier-1 criterion above was written against a Python
  repo; Java, TypeScript, C#, C, C++, Go and SQL each emit a different subset (see
  `pkg capabilities`) and a plan that assumes Python coverage will report false passes.

---

## Results table — copy this per sweep

Verdict is one of **pass · fail · partial · blocked · skipped**. Evidence is the one line
that decides it. 50 commands.

| Tier | Command | Verdict | Evidence | Notes |
|---|---|---|---|---|
| 0 | `doctor` | | | |
| 0 | `models` | | | |
| 0 | `pkg capabilities` | | | |
| 0 | `catalog list` | | | |
| 0 | `init` | | | |
| 1 | `pkg extract` | | | |
| 1 | `pkg verify` | | | |
| 1 | `pkg export` | | | |
| 1 | `pkg docs` | | | |
| 1 | `profile` | | | |
| 1 | `understand` | | | |
| 1 | `state` | | | |
| 1 | `investigate` | | | |
| 1 | `localize` | | | |
| 1 | `rca` | | | |
| 1 | `regression` | | | |
| 1 | `design` | | | |
| 1 | `backlog` | | | |
| 1 | `catalog plan` | | | |
| 1 | `sdlc plan` | | | |
| 1 | `sdlc approve` | | | |
| 1 | `sdlc runs` | | | |
| 2 | `template register` | | | |
| 2 | `template list` | | | |
| 2 | `template show` | | | |
| 2 | `template publish` | | | |
| 2 | `template deprecate` | | | |
| 2 | `contract register` | | | |
| 2 | `contract list` | | | |
| 2 | `contract show` | | | |
| 2 | `contract publish` | | | |
| 2 | `contract deprecate` | | | |
| 2 | `task submit` | | | |
| 3 | `mcp list` | | | |
| 3 | `mcp contracts` | | | |
| 3 | `mcp call` | | | |
| 3 | `mcp ingest-db` | | | |
| 3 | `ingest` | | | |
| 3 | `openspec draft` | | | |
| 3 | `media extract` | | | |
| 4 | `sdlc autorun` | | | |
| 4 | `sdlc feature` | | | |
| 4 | `sdlc run` | | | |
| 4 | `sdlc address-review` | | | |
| 4 | `sdlc complete` | | | |
| 4 | `sdlc remediate` | | | |
| 4 | `sdlc baseline` | | | |
| 4 | `audit` | | | |
| 4 | `up` | | | |
| 4 | `tui` | | | |

**Totals:** __ pass · __ fail · __ partial · __ blocked · __ skipped  (of 50)
