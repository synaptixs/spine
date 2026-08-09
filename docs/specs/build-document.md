# The build document — plan before code

**Status:** proposed · not built
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
| 05 | Impact neighbourhood | deterministic | `sdlc/impact.py` | yes — no renderer |
| 05b | Endpoints crossed | deterministic | `EXPOSES` edges | **no** — `/v1` unlinked |
| 05b | Coverage today | deterministic | `sdlc/coverage.py` | yes — not surfaced |
| 05b | Regression surface | deterministic | `IMPORTS` from tests | yes |
| 05b | Convention to match | model | precedent search | **no** |
| 05b | Recent history | deterministic | git log | **no** |
| 05b | Docs affected | deterministic | `MENTIONS` | needs `understand` first |
| 06 | Design | deterministic | `sdlc/design.py` | yes |
| 07 | Files changed / created | deterministic | spec paths + design | yes |
| 08 | Acceptance criteria | stated | spec JSON | yes |
| 09 | Facts for the generator | model | reading the source | **no** |
| 10 | Codegen prompt | deterministic | prompt assembly | yes — not exposed |
| 11 | Cost estimate | deterministic | catalog + worklog | partly |
| 12 | Confidence | model + human | judgement | **no** |
| — | Decisions | human | the review | **no** |

**The uncomfortable read:** the two most valuable sections — root cause (03) and
confidence (12) — are model-derived with no deterministic backing, and are the
two a reviewer is most likely to accept without checking. Label them loudly, and
treat root cause as a hypothesis until the fix proves it.

---

## 3. What changes about a run

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

## 4. Build phases

### Phase 1 — `orchestrator sdlc plan` *(~2 days)*

Run intake → investigate → validity → design, render the document, **stop**. No
worktree, no codegen, no cost beyond intake.

Six `render_*_md` functions already exist (`investigate`, `design`, `localize`,
`coverage`, `escalate`, `autorun`). This is assembly plus a document renderer.

**Ships:** sections 01, 02, 04, 05, 06, 07, 08, 10 — with provenance labels.

### Phase 2 — close the deterministic gaps *(~3 days)*

- **Link `/v1` routes to their callers.** 77 `EXPOSES` edges, none under `/v1` —
  the client/server join is missing, and its absence is why `investigate` pointed
  at the registry server on SSPN-49.
- **Surface coverage per changed symbol** from `CoverageIndex`. On SSPN-49 this
  revealed 10 of 11 commands untested, which changes the delivery bar.
- **Recent history** on the touched files, from git.
- **Regression surface** — the test modules importing what is changing.

**Ships:** section 05b, all deterministic.

### Phase 3 — wire RCA into the plan *(~2 days)*

`orchestrator rca` already produces a grounded root-cause report with ranked
hypotheses. Wire it in for bug-type tickets, labelled *model*. For features the
section is omitted rather than padded.

**Ships:** section 03.

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

## 5. Sequencing, and what to resist

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

## 6. Worked example

`SSPN-49` — *CLI commands crash with a traceback when the registry API is down* —
was taken through this document by hand before any code was written. It found:

- Two of six acceptance criteria described behaviour `_check()` already had.
  A run would have reported them met having changed nothing.
- The investigation brief named eight registry **server** modules and never
  `cli.py`, because the retrieval is lexical and the ticket says "registry API".
- Ten of the eleven affected commands have no test at all — a materially larger
  delivery than the spec implied.

None of these were visible in six runs. All three were visible in the document.
