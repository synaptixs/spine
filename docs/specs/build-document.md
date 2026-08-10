# The build document — plan before code

**Status:** Phases 1–2 built (`orchestrator sdlc plan`) · Phases 3–6 proposed
**Supersedes nothing.** Changes what `sdlc autorun` *is*.

Today a run spends roughly $1.19 before anyone sees anything, and its first
output is either a PR or a traceback. This record proposes that every ticket —
bug, feature, requirement — first produces a **build document**: a reviewable
artifact assembled from the sources of truth, gated by a human, and only then
handed to codegen.

The motivation is measured, not theoretical. Over six live runs on one ticket
(SSPN-49) the pipeline produced usable source most times and completed zero
times, and every failure was a decision made silently: a design that named the
wrong files, a prompt rule that forbade the shape the spec asked for, two
acceptance criteria that described behaviour the code already had. All three
were visible in a document that cost nothing to produce.

---

## 1. Provenance — the distinction that makes it trustworthy

Every field carries a label. A reader must be able to tell a fact from an
inference without asking.

| label | means | reproducible |
|---|---|---|
| **stated** | Verbatim from the requirement, ticket or spec. Nobody inferred it. | yes — it is quoted |
| **derived · deterministic** | Computed from the PKG, git, or the filesystem. Same input, same output. | yes — re-run it |
| **derived · model** | An LLM inferred it. May be wrong; must be reviewable. | no |
| **human** | A choice someone made, recorded with who and when. | n/a |

This is the load-bearing idea. A document that mixes quoted requirements with
model inference and does not say which is which is worse than no document —
it lends the authority of the first to the second.

---

## 2. Section map — where each part comes from

Mapped against the SSPN-49 document. **Only two sections are stated; everything
else is derived.**

| # | Section | Provenance | Source | Exists today |
|---|---|---|---|---|
| 01 | Requirement | stated | ticket body | yes — intake |
| 02 | Intent | model | `intake/intents.py` | yes |
| 03 | Root cause | model | `orchestrator rca` | partly — not wired to plans |
| 04 | PKG facts | deterministic | `FactStore` | yes |
| 05 | Blast radius | deterministic | `sdlc/impact.py` | yes — `render_md` already emits it into `design.md` |
| 05b | Endpoints crossed | deterministic | `EXPOSES` edges | **no** — `/v1` unlinked |
| 05b | Coverage today | deterministic | `sdlc/coverage.py` | yes — not surfaced |
| 05b | Regression surface | deterministic | `IMPORTS` from tests | yes |
| 05b | Convention to match | model | precedent search | **no** |
| 05b | Recent history | deterministic | git log | **no** |
| 05b | Docs affected | deterministic | `MENTIONS` | needs `understand` first |
| 06 | Design | deterministic | `sdlc/design.py` | yes |
| 07 | Files | deterministic | spec paths + design | yes |
| 08 | Acceptance criteria | stated **+ model** | spec JSON, reconciled against the code | partly — see §4 |
| 09 | Facts the generator needs | model | reading the source | **no** |
| 10 | Codegen prompt | deterministic | prompt assembly | yes — not exposed |
| 11 | Token usage & cost | deterministic | catalog + worklog | partly |
| 12 | Confidence | model + human | judgement | **no** |
| — | Decisions | human | the review | **no** |

**The uncomfortable read:** the two most valuable sections — root cause (03) and
confidence (12) — are model-derived with no deterministic backing, and are the
two a reviewer is most likely to accept without checking. Label them loudly, and
treat root cause as a hypothesis until the fix proves it.

---

## 3. The template — the sections, fixed

[`build-documents/SSPN-49-build.md`](build-documents/SSPN-49-build.md) is the
template. It was assembled by hand; its shape is now the contract.

**Twelve sections, these titles, this order.** A section is added, renamed or
reordered for *every* ticket or not at all. A reviewer must be able to find
section 9 without reading sections 1–8, and a renderer can only be built against
a stable shape.

| # | Title | Required structure | Omitted when |
|---|---|---|---|
| 1 | Requirement | The symptom as filed, including the verbatim error text in a fenced block. How it was found. | never |
| 2 | Intent | One paragraph: what should happen instead. | never |
| 3 | Root cause | Names the function and `file:line`. Ends with a **Consequence:** line stating what is therefore *out* of scope. | ticket is not a bug — omitted, not padded |
| 4 | PKG | Module + byte size. Importers **by name**. Imports counted, first-party listed. Symbols ranked by callers, as a table. Ends with a verdict on whether the investigation brief is trustworthy for this ticket. | graph unavailable for the language |
| 5 | Blast radius | A mermaid `flowchart TD`, then four blocks in order — **Reading it**, **Containment**, **Caveat**, **Evidence**. Evidence carries the five deterministic findings the diagram cannot draw: coverage today, endpoints crossed, regression surface, recent history, docs affected. | never |
| 6 | Design | The chosen shape as a code snippet. The rejected alternative in italics beneath it. | never |
| 7 | Files | Two tables. **Changed:** file, scope with line numbers. **Created:** file, contents, size. | never |
| 8 | Acceptance criteria | Numbered, independently checkable, each traceable to the spec — see §4. | never |
| 9 | Facts the generator needs | The anti-duplication warnings first. Env vars with defaults. Exact constructor signatures. The existing test to copy, by node id. | never |
| 10 | Codegen prompt | System prompt by name. User payload as a list of *which sections of this document*. Context budget in bytes **and** percent of the window. | never |
| 11 | Token usage & cost | Measured token stats, then the per-model table, then the flat statement that a failed run costs what a successful one costs. | no measured history — state that, do not estimate silently |
| 12 | Confidence | **Two numbers, never one** — is the analysis right, and will the pipeline complete — each with a basis table. | never |

**The mermaid in section 5 must render.** `md.js` implements a ~90-line subset;
anything outside it falls back to `<pre>` in our own UI while still looking fine
on GitHub, so a broken diagram is invisible until someone opens Spine. Declare
nodes first, then edges, and verify — do not eyeball:

```bash
node scripts/check-mermaid.js docs/specs/build-documents/*.md
```

### Labelling — how provenance appears on the page

§1 defines the four labels. This is where they go, and without this the idea is
decorative.

**Each section heading carries its label and its source**, as an italic line
directly beneath:

```markdown
## 5. Blast radius
*derived · deterministic — `sdlc/impact.py` @ `3ad6d93`*
```

**A section's label is the weakest of its parts.** A section that is mostly
quoted but contains one inferred sentence is `derived · model`. Mixing without
saying so is the failure §1 exists to prevent.

**A field that differs from its section's label says so inline**, in the same
italic form: `*(model)*` after the claim, not in a footnote.

**`human` sections name a person and a time** — "approved by @falcon,
2026-08-08" — or they are not `human`, they are unattributed.

---

## 4. Acceptance criteria have three states, not one

Section 8 cannot be labelled `stated` and left there. SSPN-49 proves why: the
spec filed six criteria, the built document carries four, and the two that
vanished did so because `_check()` already satisfied them. That deletion is the
single most valuable finding on the page, and it survived only in a side
document.

Every criterion carries one of three states:

| state | means | provenance |
|---|---|---|
| **stated** | Filed on the ticket, not yet met by the code. | stated |
| **stated · already met** | Filed on the ticket, and the code already does it. Names the function and line that satisfies it. | stated claim, **model** judgement |
| **proposed** | Nobody filed it; the spec writer inferred it. | derived · model |

**Nothing is deleted.** An already-met criterion stays on the page with its
evidence, because a run that reports it met having changed nothing is exactly the
failure this document exists to catch.

`FeatureSpec` already separates two of these — `acceptance_criteria` versus
`proposed_criteria` ([`intake/specs.py`](../../src/orchestrator/intake/specs.py)).
The third state has no home in the model yet; adding it is part of Phase 1, not a
later refinement.

---

## 5. Document identity — name, place, and the commit it was derived at

**Name:** `<INTENT_ID>-build.md`. One document per ticket, overwritten in place
as stages append — never `-v2`.

**Place:** written to the run artifacts directory while a run is live, and
committed to `docs/specs/build-documents/` when approved. Those are different
directories on purpose, and both are load-bearing:

- `default_artifacts_dir()` writes **outside the repo** ([`sdlc/autorun.py`](../../src/orchestrator/sdlc/autorun.py))
  because `understand` ingests markdown from disk regardless of git — a document
  written into the working tree becomes a `Doc` node and changes the graph the
  next stage reads.
- An approved document is evidence and must outlive the run. The SSPN-49
  template sat in a `/tmp` session scratchpad for two days; that is not storage.

**Header block**, first thing after the title:

```markdown
**Spec:** `SSPN-49.json` · **Derived at:** `3ad6d93` · **Status:** proposed
```

**Derived at is not decoration.** Every deterministic section is computed from
one commit. A plan approved at X and built at Y is a document that was true. The
PKG cache is already commit-keyed ([`pkg/persistence.py`](../../src/orchestrator/pkg/persistence.py)),
so stamping this in Phase 1 is nearly free; retrofitting it once approvals exist
is not. **`build` warns when the stamp does not match `HEAD`, and refuses when
any file named in section 7 has changed since.**

---

## 6. What changes about a run

| | today | proposed |
|---|---|---|
| first output | a PR, or a failure | a document |
| first spend | ~$1.19 before anyone sees anything | ~$0 |
| human gate | after the diff exists | before code is written |
| a failed run | loses the analysis | keeps it |
| re-run | re-derives everything | starts from an approved plan |
| record | a scrolling log | a document that accretes |

```
today      intake → investigate → validity → design → implement → tests → judge → PR
proposed   intake → investigate → validity → design → RCA → BUILD DOCUMENT → review
                 → implement → tests → judge → PR   (each stage appending)
```

**`autorun`'s stage list already draws the line.** `STAGES` is
`("intake", "investigate", "validity", "design", "implement", "review")` — the
first four already write artifacts to the run directory and cost almost nothing;
the last two do the work and carry the risk. The split is latent in the design.

### Command shape

`sdlc plan` and `sdlc build` become real commands; `autorun` stays as the
composed path so nothing in use today breaks.

- **Two gates, not one.** `--review` gates the *diff* before commit and push.
  The new gate is before code exists. They catch different failures and both stay.
- **`--live` narrows.** Plan is safe by construction; `--live` qualifies build only.
- **The ticket stops thrashing.** `autorun` currently moves an issue to In Progress
  on start and back to To Do on failure — SSPN-49 bounced six times. A plan does not
  touch the tracker; the ticket moves when work begins.
- **`--max-cost` becomes meaningful.** It caps the build, approved against an estimate.

---

## 7. Build phases

### Phase 1 — `orchestrator sdlc plan` — **built**

Runs intake → investigate → validity → design, renders the document, **stops**. No
worktree, no codegen, nothing spent, and the tracker untouched. Lives in
[`sdlc/builddoc.py`](../../src/orchestrator/sdlc/builddoc.py); `autorun.py` was not
modified.

**Shipped:** sections 1, 2, 4, 5, 6, 7, 8 and 10, with the labels of §3, the header
block of §5 and the three criteria states of §4. Sections 3, 9, 11 and 12 render as
headings naming what would establish them.

Three things worth recording, because they were not obvious when this was written:

- **The builders are called directly, not through `autorun`'s `_stage_*` wrappers.**
  Those carry run records, checkpoints, approval parking and Jira worklogs. A plan
  must touch none of that, and reusing them would have meant either gutting the path
  that works or passing it a fake context.
- **Section 4's verdict on the brief turned out to be deterministic.** Whether the
  investigation names any file the design will change is a set intersection. It fires
  on SSPN-49 today, which is the failure that made this record necessary.
- **`--spec` makes the whole path LLM-free**, so the same commit and spec produce a
  byte-identical document. That is what makes the kept history readable as a diff.

**Where the plans live:** `.spine/plans/<INTENT_ID>-build.md`, with a replaced
document snapshotted under `history/` keyed by the commit it was derived at. The
dot-directory is load-bearing — `doc_source` skips dirnames starting with ".", so a
live plan never becomes a `Doc` node in the graph the next stage reads.

### Phase 2 — close the deterministic gaps — **built**

All five land in section 5's **Evidence** block, deterministic.

- **The client/server join now exists.** A new `CONSUMES` edge kind —
  [`pkg/python_client.py`](../../src/orchestrator/pkg/python_client.py) — joins a
  caller to the endpoint it calls, and `impact_of` follows it, so changing a handler
  reaches the code that calls it.
- **Coverage today** per symbol in the changed files, from `CoverageIndex`.
- **Regression surface** — the test modules importing what is changing.
- **Recent history** on the touched files, from git.
- **Docs affected** — `MENTIONS`, via `store.docs_for`.

**This record's own stated fact was wrong, and measuring it changed the work.** It
said *"77 `EXPOSES` edges, none under `/v1`."* Measured: 71 Endpoint nodes, 77
`EXPOSES` edges, **39 of them under `/v1`** — and the actual gap was worse than
described. Zero edges pointed *at* an endpoint at all: `EXPOSES` runs endpoint→handler,
the server half, and nothing represented a client. So this was not a linking bug to
fix but a fact that did not exist, which per invariant 1 is a change to `facts.py` and
a front-end, not to a renderer.

**What the join does not reach, by construction.** Paths are resolved only when
literal. `cli.py`'s template/contract group builds them as `f"/v1/{entity}"`, so five
of its six call sites emit nothing — the repo yields 120 `CONSUMES` edges and one of
them is from `cli.py`. Resolving an f-string against its call sites would be
inter-procedural constant propagation, which is a guess, and a wrong edge is worse
than an absent one. The Evidence block says "silence rather than absence" for exactly
this reason.

### Phase 3 — wire RCA into the plan *(~2 days)*

`orchestrator rca` already produces a grounded root-cause report with ranked
hypotheses. Wire it in for bug-type tickets, labelled *model*. For features the
section is omitted rather than padded.

**Ships:** section 3.

### Phase 4 — approval gate and handoff contract *(~3 days)*

- A plan is *approved*, recorded with who and when, as a *human* section.
- `autorun` refuses to build without an approved plan for that spec.
- Implement reads section 10 as its payload, not the whole document.

**Open question:** does `build` take the plan, or the spec plus an approval
record? Prefer the second — the spec stays the contract, the plan is the reviewed
evidence that the contract is sound. Otherwise the plan becomes a second source
of truth and the two can disagree.

### Phase 5 — the journey *(~3 days)*

Each stage appends its own section, stamped with stage and time. **No stage
rewrites an earlier one.** When implement disagrees with the design, that
disagreement is the most valuable thing on the page.

**Ships:** run outcome, diffs, judge verdict, cost actuals against estimate.

### Phase 6 — cost and confidence *(~2 days)*

Estimate from the catalog and measured history; score confidence from what the
plan could and could not establish. Both model-flavoured — present as a range
with its basis, never a bare number.

---

## 8. Sequencing, and what to resist

**Phase 1 alone delivers most of the value.** It is the cheapest phase, touches
none of the fragile code, and turns a $1.19 gamble into a free document.
Everything after it is refinement.

**Resist** building phases 5 and 6 before 1–4 are in use. A journey nobody reads
and a confidence score nobody trusts are both easy to build and hard to remove.
Ship the plan, use it on ten tickets, then decide what the journey needs to hold.

**What this does not fix.** Every failure of the last week was *downstream* of
everything a plan covers — in implement and the recovery machinery. This makes
decisions better and failures cheaper. It does not make codegen more likely to
succeed, and expecting otherwise will make the first failed handoff feel like
the approach did not work.

---

## 9. Worked example

`SSPN-49` — *CLI commands crash with a traceback when the registry API is down* —
was taken through this document by hand before any code was written. The result
is [`build-documents/SSPN-49-build.md`](build-documents/SSPN-49-build.md), and it
is the template §3 describes. It found:

- Two of six acceptance criteria described behaviour `_check()` already had.
  A run would have reported them met having changed nothing.
- The investigation brief named eight registry **server** modules and never
  `cli.py`, because the retrieval is lexical and the ticket says "registry API".
- Ten of the eleven affected commands have no test at all — a materially larger
  delivery than the spec implied.

None of these were visible in six runs. All three were visible in the document.

**Two caveats the template inherits, and Phase 1 must fix.** The hand-written
document carries no provenance labels at all — the idea of §1 with none of the
notation of §3. And it silently narrowed six criteria to four; the reconciliation
lives in [`SSPN-49-plan.md`](build-documents/SSPN-49-plan.md), a document a
reviewer of the build document would never see. §4 exists because of that.
