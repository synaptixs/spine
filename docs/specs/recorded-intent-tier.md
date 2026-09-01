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
| **1 — reach the export** | `--intents` on `pkg export`, applying `link_intents` beside `link_docs` for non-sqlite formats. No exporter change: they already emit every kind | ~2 h | `pkg export . --format json --intents` yields non-zero `Intent`/`SERVES`; a test asserts it |
| **2 — a query seam** | `intents_for(symbol)` / `symbols_serving(intent)` on `FactStore`, mirroring `docs_for` / `mentions_of`. An MCP tool only if Phase 3 wants one | ~0.5 d | The question is answerable in-process and from a script |
| **3 — one real consumer** | `investigate`'s landing report names the tickets a landing symbol serves, bounded and labelled *last changed for* | ~1 d | A ticket landing on an attributed symbol reports it; one landing on an unattributed symbol says nothing, and says nothing *loudly* — never a blank that reads as "no prior work" |
| **4 — the "built for" half** | `git log -L` per symbol to recover the introducing commit | ~1–2 d | Deferred on purpose. One subprocess per symbol against 11,022 symbols needs its own measurement before anyone commits to it |

**Phase 1 is the one that removes "no reader".** Everything after it is about *which* reader.

## 5. Decisions

| | Decision | Recommendation |
|---|---|---|
| **D1** | Keep the tier, or delete it? | **Keep.** The hard half — a deterministic, correctly-directed join — is built, documented and measured. The missing half is a flag and a query. Deleting working code that does something no competitor does, to close a row, is the wrong trade |
| **D2** | Opt-in, or on by default? | **Stays opt-in.** It costs 3.0s here against 2.8s for the whole extraction — it roughly *doubles* comprehension time, and on a repository with no issue keys it buys nothing. A default that doubles the cost of the cheap path for a repo-dependent payoff is the wrong default |
| **D3** | Which consumer first? | **`investigate`.** It already reads the graph, already renders per-symbol context, and "what was this last changed for" is the question a ticket-landing report exists to answer |
| **D4** | Add an `intent` table to the sqlite export? | **No.** That schema is kind-per-table and is a contract with the ontomesh consumer; `link_docs` is already excluded from it for the same reason. Revisit only if ontomesh asks |

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
