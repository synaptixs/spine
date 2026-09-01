# The recorded-intent tier — what a symbol was last changed *for*

**Status:** **Written 2026-09-01 against 3.26.1.** The producer shipped and works; **nothing
reads it.** This spec exists because the capability was carried in a module docstring and a test
plan, and a feature with no design record is a feature nobody can schedule.
**Owner:** _unassigned_

**The one-line problem.** `orchestrator understand . --intents` computes 37 `Intent` nodes and
1,418 `SERVES` edges on this repository, counts them into a summary line, and throws them away.
There is no supported way to ask *which ticket was this symbol last changed for* — and that
question is the entire point of the tier.

---

## 1. What exists, measured

`pkg/intent_link.py`, reached only from `analyse(..., intents=True)`, which only
`understand` and `state` pass.

**The join is `git blame`, and the direction is the interesting decision.** The obvious
implementation walks `git log` for keyed commits and joins their changed line ranges to symbol
provenance. It is wrong: a commit's line numbers are as-of-that-commit, provenance is against
today's tree, and lines drift with every edit above them. Blame maps *current* lines to the
commits that last touched them, which is the direction the join needs.

Measured on this repository, 2026-09-01:

| | |
|---|---|
| `Intent` nodes | **37** |
| `SERVES` edges | **1,418** |
| Symbols attributed | **1,272 of 11,022 — 11.5%** |
| Commits carrying a key | 92 of 783 |
| Cost | **3.0s**, on top of 2.8s extraction |

Three properties worth keeping:

- **Deterministic and no-LLM.** Commits are identified by hash, no timestamps, no wall-clock.
  Same tree at same commit → same facts, which is what lets it sit in a gated pipeline at all.
- **It says which half it ships.** *"Last changed for"*, not *"built for"*. Recovering the
  commit that **introduced** a symbol needs `git log -L` per symbol — accurate, and one
  subprocess per symbol. §5 keeps that as a phase rather than pretending it is covered.
- **Coverage is reported, not hidden.** `IntentCoverage` carries the denominator on every run,
  because a tier that attributes 12 of 4,000 symbols is working exactly as designed and still
  says almost nothing.

**11.5% is this repository's number, and it is low for a reason that is not the method's fault:**
the history was squashed on import from a private repo, so most surviving lines blame to a
handful of large key-less commits. A repository developed in the open with issue keys in its
commit messages attributes far more. **That is also the tier's central risk — see §6.**

## 2. The gap, exactly

Three surfaces could read this. None does.

### 2.1 The export — one missing post-pass, beside its own precedent

`pkg export` builds a code-only batch and then, for every non-sqlite format, applies
`link_docs`. The comment above that call states the reasoning:

> Doc nodes + MENTIONS come from the `link_docs` post-pass, not raw extraction, so without this
> the doc/media modality is invisible in the export.

**That argument is true of intents word for word, and `link_intents` is not there.** The
exporters do not filter by kind — `export_json` emits every node sorted by id — so the facts are
absent because they were never added, not because anything rejected them. The test plan recorded
`Intent nodes: 0 / SERVES edges: 0` and concluded the facts were unreachable; the narrower truth
is that `pkg export` has no `--intents` flag while `understand` and `state` do.

### 2.2 The comprehension surfaces render a count and nothing else

With `--intents`, the *entire* rendered consequence across `episteme/` is the words `34 intents`
in two graph-size lines. No section, no per-symbol attribution, no per-ticket rollup. Verified in
`comprehension-test-plan.md` by diffing a run against a plain one.

### 2.3 There is no query

`pkg/store.py` gained `docs_for` / `mentions_of` when doc ingestion landed. It has no
`intents_for` / `symbols_serving`, and no MCP tool exposes one. An assistant cannot ask the
question even with the graph in hand.

## 3. Why the tier is worth finishing

**The graph's vocabulary is mechanical.** It says what calls what, never what anything is *for*.
That meaning already exists in the system — in tickets, commit messages, build documents — and
none of it points at a symbol. This is the only tier that joins the two halves **without a
model**, which is what makes it gateable and reproducible.

The concrete payoff is in the surfaces that already exist:

| Where | The question it answers |
|---|---|
| `investigate`'s landing report | "you are about to change something last touched for SSPN-49" — blast radius in *ticket* terms, not just call terms |
| `state` / `episteme` | "this area serves these tickets" — what a subsystem is *for*, per the knowledge base's own purpose |
| The export | any external tool, Gephi included, can colour a graph by the work it came from |

## 4. Phases

| Phase | Work | Effort | Exit |
|---|---|---|---|
| **1 — reach the export** ✅ **2026-09-01** | `--intents` on `pkg export`, applying `link_intents` beside `link_docs` for non-sqlite formats. No exporter change was needed: they already emit every kind | ~2 h | ✅ 37 `Intent` + 1,418 `SERVES` in the JSON export, coverage printed beside it. **And it immediately exposed §6.1** |
| **2 — a query seam** ✅ **2026-09-01** | `intents_for(symbol)` / `symbols_serving(intent)` on `FactStore`, mirroring `docs_for` / `mentions_of`. No MCP tool: Phase 3 did not want one | ~0.5 d | ✅ Both directions, asserted inverse; an unscanned graph answers empty rather than raising |
| **3 — one real consumer** ✅ **2026-09-01** | `investigate --intents` names the tickets each landing was last changed for, bounded at three with `+N more`, and states coverage **once at report level** | ~1 d | A ticket landing on an attributed symbol reports it; one landing on an unattributed symbol says nothing, and says nothing *loudly* — never a blank that reads as "no prior work" |
| **4 — the "built for" half** | `git log -L` per symbol to recover the introducing commit | ~1–2 d | Deferred on purpose. One subprocess per symbol against 11,022 symbols needs its own measurement before anyone commits to it |

**Phase 1 is the one that removes "no reader".** Everything after it is about *which* reader.

## 4.1 — What phases 2 and 3 look like in practice

```
$ orchestrator investigate . --title "autorun should record the run context" --intents
  recorded intent: 31 ticket(s) from SSPN; 1120/11040 symbols (10.1%)

- `RunContext` (Type, 3 caller(s)) … autorun.py:65 — last changed for SSPN-22, SSPN-23, SSPN-24 +5 more
- `record_stage` (Function, 0 caller(s)) … autorun.py:146 — last changed for SSPN-22, SSPN-23
- `_stage_implement` (Function, 1 caller(s)) … autorun.py:1025 — last changed for SSPN-14, SSPN-22, SSPN-23 +3 more
- `needs_context` (Function, 0 caller(s)) … security_verify.py:154

_Recorded intent covers 4 of 10 landing(s). A landing with no ticket was not attributed —
which is not the same as having no prior work._
```

**Four decisions in that output.**

**Bounded at three, with `+N more`.** A symbol edited across a dozen tickets says *this is hot*,
which the count conveys; listing twelve keys would bury the landing site the line exists to name.

**Coverage stated once, at report level, and only when the tier ran.** Per symbol it would be
noise on every line. Omitted entirely, a reader takes the six unattributed landings for symbols
with no prior work — and here that would be wrong for nine landings in ten. A run that never
scanned says *nothing at all* rather than claiming 0%, because 0% and "not measured" are
different facts and this codebase has confused them before.

**The rate goes to stderr, the findings to the brief.** The brief is the artifact a human reads
and a run records; a coverage figure belongs with the scan that produced it.

**Intents cannot become landing sites.** They are ungrounded by construction and
`relevant_symbols` skips ungrounded nodes, so a ticket key can never be returned as a place in
the code. The invariant in §7 was written before this consumer existed and is what makes it safe.

**`--intents` is refused with `--repos`** rather than silently ignored: blame is per checkout and
a merged graph has several. A flag that quietly does nothing is worse than one that says no.

## 5. Decisions

| | Decision | Recommendation |
|---|---|---|
| **D1** | Keep the tier, or delete it? | **Keep.** The hard half — a deterministic, correctly-directed join — is built, documented and measured. The missing half is a flag and a query. Deleting working code that does something no competitor does, to close a row, is the wrong trade |
| **D2** | Opt-in, or on by default? | **Stays opt-in.** It costs 3.0s here against 2.8s for the whole extraction — it roughly *doubles* comprehension time, and on a repository with no issue keys it buys nothing. A default that doubles the cost of the cheap path for a repo-dependent payoff is the wrong default |
| **D3** | Which consumer first? | **`investigate`.** It already reads the graph, already renders per-symbol context, and "what was this last changed for" is the question a ticket-landing report exists to answer |
| **D4** | Add an `intent` table to the sqlite export? | **No.** That schema is kind-per-table and is a contract with the ontomesh consumer; `link_docs` is already excluded from it for the same reason. Revisit only if ontomesh asks |

## 6.1 — Precision: about an eighth of the "intents" were not tickets ✅ **fixed 2026-09-01**

**Found the moment Phase 1 made the facts readable, which is the argument for Phase 1.** The key
pattern is deliberately generic — `\b[A-Z][A-Z0-9]{1,9}-\d+\b`, so that no repository is locked
out by a hard-coded prefix — and it matches things that are not issue keys at all:

| Claimed intent | Symbols | What it actually is |
|---|---|---|
| `SHA-256` | 31 | a hash algorithm named in a commit message |
| `ISO-8601` | 27 | a date standard |
| `CHANGE-2046` | 19 | not an issue key |
| `UTF-16` | 8 | a text encoding |
| `CB-676` | 7 | ambiguous |

**5 of 37 intents (13.5%) and 92 of 1,418 `SERVES` edges (6.5%).** The join itself is correct —
those commit messages really do contain those strings — but reading them as tickets is wrong, and
a `SERVES` edge to `intent:SHA-256` asserts a symbol was changed for a ticket nobody ever filed.

**This was invisible while the tier rendered only a count.** `34 intents` in a size line is as
true of a good tier as a noisy one. Phase 1's whole value is that the facts became legible enough
to be wrong out loud.

**A deterministic discriminator exists, and it is the shape of the data.** A real tracker prefix
appears with *many distinct numbers* — `SSPN-2`, `SSPN-3`, … `SSPN-49`. A standard appears with
*one*: `SHA` only ever `256`, `ISO` only ever `8601`, `UTF` only ever `16`. Counting distinct
numbers per prefix separates them without a list of standards to maintain.

**It is not sufficient alone.** `WI-2` is a legitimate intent from the watch-items work and has
exactly one number, so a naive "≥2 distinct numbers" rule would discard it. Options, none free:

| Approach | Cost |
|---|---|
| Distinct-number threshold | Drops legitimate low-volume prefixes like `WI-2` |
| Configurable prefix allow-list (`--intent-prefix SSPN`) | Correct and explicit; requires the operator to know their own prefix |
| Require a conventional position (`fixes #`, `refs`, message prefix) | Precise; discards keys mentioned mid-sentence, which is most of them here |
| Denylist of standards (`SHA-`, `ISO-`, `UTF-`, `RFC-`, `CVE-`) | Fragile, endless, and wrong the first time someone files `RFC-12` in their tracker |

> **✅ Fixed 2026-09-01, and the measurement changed the answer.** The recommendation above was a
> distinct-number *threshold*. Counting them settled it against that:
>
> | prefix | distinct numbers | mentions |
> |---|---|---|
> | **SSPN** | **45** | 111 |
> | UTF | 2 | 6 |
> | WI | 2 | 11 |
> | SHA · ISO · CHANGE · CB · CVE · PROJ | 1 each | ≤ 8 |
>
> **"≥2" keeps `UTF` (8 and 16); "≥3" discards the legitimate `WI-2`.** Any constant between
> them is the arbitrary tolerance band `GATES` already argues against — one that eventually
> fires on something legitimate and gets widened until it means nothing.
>
> **So: the single dominant prefix, which is a maximum and not a band.** A repository has one
> issue tracker, and here it wins 45 distinct numbers to 2. The one floor is that the winner
> must have **at least two** distinct numbers — a prefix seen with exactly one is
> indistinguishable from a standard, so a repository whose leader has only one gets no intents
> rather than a coin-flip.
>
> `--intent-prefix` (repeatable) overrides it for a repository with two trackers or a history
> too thin to infer from, and **every run reports what it accepted and what it rejected** —
> an inference nobody can inspect is a guess wearing a measurement's clothes.
>
> **Effect here: 37 intents → 31, all genuine `SSPN-N`; 1,418 `SERVES` → 1,263; coverage 11.5%
> → 10.2%.** The cost is `WI-2`'s 63 edges, and that is the right trade: `WI` is a roadmap
> label, not a tracker key, and precision over recall is this graph's standing rule.
>
> One thing found while testing it: `_all_commit_keys` reads only the **first** key in a
> message, so a standard sharing a commit with a real key never surfaced at all. The five that
> did were first in their own commits.

**This blocked Phase 3, not Phase 1.** Noise in an export is inspectable; noise in
`investigate`'s landing report is a tool telling an engineer their change relates to `SHA-256`.

## 6. The risk, stated plainly

**The tier's value is a function of the customer's commit hygiene, and nothing we build changes
that.** A repository whose commits carry issue keys attributes well. One whose commits say
`fix stuff` attributes nothing, and the feature is silently inert — the graph gains no Intent
nodes and every surface correctly shows nothing.

Two consequences that belong in any decision to invest further:

- **Report the rate wherever the tier is surfaced**, never just the findings. `IntentCoverage`
  already carries the denominator; a consumer that renders attributions without the rate lets a
  reader mistake 12-of-4,000 for a complete picture. That is invariant 7 ("bound honestly")
  applied to a modality rather than an aggregation.
- **This repository is a bad demo of it.** 11.5% here is an artefact of a squashed import, so
  measuring the tier's worth on Spine's own history will understate it. If it is ever pitched, it
  should be measured on a repository with real history — the G6 corpus has five of them.

## 7. Invariants

- **Deterministic and no-LLM.** The join is git and a regex. The moment a model is asked to guess
  intent, the tier stops being gateable and belongs in a labelled second tier, the way GraphIR
  Phase 2b's promotion was handled.
- **`Intent` nodes are ungrounded, by construction.** An intent is not a place in a file, so it
  carries no provenance and `pkg verify`'s provenance check skips it. Any consumer that assumes
  every node has a `file:line` will break on this kind — including future ones.
- **Symbols only.** A `Module` is a file; attributing a whole file to whichever ticket last
  touched line 1 would be noise wearing a provenance label.
- **Bound honestly.** Coverage travels with the facts. See §6.

## 8. Non-goals

- **Inferring intent from code with a model.** A different capability with a different trust
  model; it must never be folded into this one silently.
- **Ticket *content*.** This tier records that a symbol serves `SSPN-49`. Fetching what SSPN-49
  says is `intake`'s job and needs a network and credentials.
- **Branch-name keys.** `_issue_key_from_branch` exists and is nearly useless in practice: of 465
  commits here, 102 carry a key in the *message* across 45 intents, while exactly one branch
  matches the SDLC's pattern. Commit messages are the source.

## 9. Open questions

1. **Should an unattributed symbol be visibly unattributed in Phase 3, or absent?** (Lean:
   **visibly**, once, at the report level — "attribution covers 11% of the symbols here" — rather
   than per symbol, which would be noise on every line.)
2. **Is `SERVES` the right edge direction?** It currently reads *symbol → intent*. Consistent with
   the graph's other edges, and it makes `symbols_serving(intent)` the reverse lookup rather than
   the natural one. (Lean: **leave it**; a direction flip is a breaking change to a shipped edge
   kind for a stylistic gain.)
3. **Does Phase 4 survive its own measurement?** One `git log -L` per symbol against 11,022
   symbols may be minutes, not seconds. It is a phase precisely so someone measures before
   building.
