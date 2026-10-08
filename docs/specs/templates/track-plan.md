# Design + Plan: `<TRACK>` — `<one line on what it delivers>`

*Template — the skeleton every development plan in this repository starts from, whatever the
subject: a language front-end, a documentation consolidation, a refactor, an integration.
Copy it, replace every `<...>` placeholder, and delete this italic line and the bracketed
guidance notes as you fill each section in.*

*Two things make this file worth copying rather than re-deriving: **§5 is generic** — the
housekeeping every plan in this repository owes, identical across tracks, refilled only in its
Evidence column — and **§6.1 exists at all**, because the recurring way a plan goes wrong here
is a rename landing before the tooling that hard-codes the old name.*

*A language front-end has a more specific skeleton: [language-track.md](language-track.md),
which specialises this one with fact mapping, corpus cases, packaging and the front-end
checklist. Start there for a language; start here for anything else.*

**Where this file lives, and where your copy does.** This template is tracked. **Your plan is
not** — copy it to a scratchpad or notes directory outside the checkout. A plan is not a design
record: tracking one adds a row to `SPEC-INDEX.md`, a prose count and an `ls … wc -l` line
beside it, a spec count in `STATE-OF-SPINE.md`, and an indexing check in
`scripts/roadmap-status.py`, for a document no user reads. Point the gate at it instead:

```bash
python scripts/roadmap-status.py --check ~/plans/<track>.md
```

That applies the same currency checks an in-tree roadmap gets — a DONE row with no evidence, a
Finished date before its Started date, a header contradicting its own table — to a file the
repository never sees. Relative links to repository files resolve the way an in-tree roadmap
writes them.

---

**Status:** Proposed — plan for review, no code written. **Date:** `<YYYY-MM-DD>` · spine
`<vX.Y.Z>`.
**Branch:** `<type>/<slug>` off `develop` at `<base-sha>`. **Delivery: one MR** to `develop`
when every phase in §4 is DONE and tested. No intermediate PRs: the §4 table is updated in the
same commit as the work, so the single MR arrives with a complete, receipted history.

`<Two or three sentences: what changes, measured where possible, and what the reader-facing and
maintainer-facing wins are. Numbers beat adjectives — "84% identical, measured" says more than
"heavily duplicated".>`

> `<One blockquote: the thing that is genuinely hard or risky about this track — not a summary
> of the work. Every track has one or two real design decisions and a long tail of routine; name
> which is which. If the risk is that the change touches a contract with code, say which code.>`

## Roadmap currency — the rule this document follows

Every phase row in §4 — and every generic task in §5 — carries **Status · Started · Finished ·
Evidence**, updated in the same commit as the work. A phase is DONE only when its Evidence
column links a commit, a test name, or a pasted command result. Status vocabulary:
📋 planned · 🔨 in progress · ✅ DONE · ⛔ blocked.

---

## 0. Decisions surfaced up front

*One row per genuinely open choice — not every implementation detail. A row needs real options
considered and a recommendation with a reason, not a recommendation with no alternative shown.
Anything the reviewer would otherwise discover mid-diff belongs here.*

| # | Decision | Options | Recommendation and why |
|---|---|---|---|
| **D1** | `<the choice that keys everything else>` | (a) … (b) … | `<pick one, say why, and say what it costs>` |
| **D2** | `<…>` | (a) … (b) … | `<…>` |

---

## 1. Where things are today — measured

*Facts with a source, not impressions. If you cannot measure it, say so rather than estimating.*

| Fact | Measurement | Consequence |
|---|---|---|
| `<what is true now>` | `<the command that showed it, and its output>` | `<why it matters>` |

---

## 2. Why this is cheaper than it looks, and where it is not

```
CHEAP
· <what already exists and is reused verbatim>
NOT CHEAP
· <the part that is genuinely new, and the invariant it must not break>
```

---

## 3. Design — the target state

*What the world looks like when this lands. A table beats prose when the change is
file-by-file or component-by-component.*

---

## 4. Phases — the living table

*Phases are sequential unless stated. Each is independently reviewable even though only one MR
opens at the end.*

| Phase | Work | Effort | Exit criteria | Status | Started | Finished | Evidence |
|---|---|---|---|---|---|---|---|
| **P0 Baseline** | Record the pre-existing state of every gate this track will re-run, so a regression is distinguishable from something that was already failing. Agree §0's decisions | `<~0.5 d>` | a recorded baseline in §10; decisions decided | 📋 | | | |
| **P1 `<name>`** | `<work>` | `<effort>` | `<what proves it>` | 📋 | | | |
| **P<n> Gate, audit, MR** | every §5 generic task still open, plus a final rebase | `<~1 d>` | gate green; audit clean; MR open against `develop` | 📋 | | | |

---

## 5. Generic tasks — the housekeeping every plan carries

**These are not specific to this track.** They are the standing obligations any plan in this
repository inherits from [CONTRIBUTING.md](../../../CONTRIBUTING.md) and
[CLAUDE.md](../../../CLAUDE.md). Copy the block as-is; refill only Status and Evidence.

`Per` says when each one runs: **once** at the start, **each phase** (same commit as the work),
or **pre-MR**.

| # | Generic task | Per | How | Status | Evidence |
|---|---|---|---|---|---|
| **G1** | Branch cut from `develop`, never `main` | once | `main` only moves through a release promotion, so a branch cut from it is already behind | 📋 | |
| **G2** | Baseline captured before the first edit | once | every gate command below, run on the base commit, recorded in §10 | 📋 | |
| **G3** | Conventional Commits | each phase | `fix(planner): handle empty claims list` | 📋 | |
| **G4** | Roadmap currency | each phase | §4 row's Status · Started · Finished · Evidence updated **in the same commit** as the work; `python scripts/roadmap-status.py --check <this file>` | 📋 | |
| **G5** | Quality gate green locally | each phase | `mypy src tests` (**not** just `src` — typing `src` alone passes here and fails CI), `ruff check .`, `ruff format --check .` | 📋 | |
| **G6** | The four generated-artifact `--check` gates | each phase | `render_architecture_svg.py --check`, `render_knowledge_foundation_svg.py --check`, `matrix-count.py --check`, `state-numbers.py --check` — CI runs every one; running only the first is how a release PR failed on a diagram nobody had re-rendered | 📋 | |
| **G7** | Accuracy gate and pipeline shapes | pre-MR | `uv run orchestrator pkg accuracy --check`, `python scripts/sdlc_shapes.py` | 📋 | |
| **G8** | Tests, `pkg verify`, `understand .` | pre-MR | report from the pytest summary line, never an exit code; distinguish environmental failures from real ones — sync the extras `ci.yml` syncs, not a guess | 📋 | |
| **G9** | Documentation audit and matrix walk | each phase | `uv run --frozen python scripts/docs_audit.py --base develop --head HEAD`, then walk [docs-matrix.md](../../reviewing/docs-matrix.md) | 📋 | |
| **G10** | `CHANGELOG.md` entry | pre-MR | one entry naming the command or surface, linking the record | 📋 | |
| **G11** | `episteme/` never staged | each commit | `git checkout origin/develop -- episteme/`; CI fails a PR that carries it, and only a rebase clears a stale bank | 📋 | |
| **G12** | Pre-commit hooks installed | once | `pre-commit install` — the only way the secret scan runs on your machine | 📋 | |
| **G13** | Rebase, never re-run | as needed | `git fetch origin && git rebase origin/develop && git push --force-with-lease` — a re-run replays the stale merge ref and reports the same failure | 📋 | |
| **G14** | Maintainer review pass | pre-MR | the reviewer checklist: gate with CI's extras, fan-out review, docs audit, front-end smoke test if one changed | 📋 | |
| **G15** | One MR to `develop` | pre-MR | opened against `develop` (the default branch; never `main`); §4's table pasted into the description | 📋 | |

### 5.1 Track-specific checks on top of the generic block

*Only what this track needs that the block above does not cover. Keep it short — if it is long,
it probably belongs in §4's exit criteria.*

| Check | Command | Why this track needs it |
|---|---|---|
| `<…>` | `<…>` | `<…>` |

---

## 6. Files to change

`<The list. Group by package or by document set.>`

### 6.1 Tooling that hard-codes what this track renames — assigned to a phase, same commit

*The section that exists because of how plans go wrong here. A rename that lands before the
tooling naming the old value breaks the gate for everyone until the catch-up lands — so there
is no catch-up phase: each site belongs to the phase that renames it, in the same commit. Grep
for every literal you are about to change before writing the table.*

| Site | What it hard-codes | Phase that must carry it |
|---|---|---|
| `<path:line>` | `<the literal>` | `<P…>` |

### 6.2 Deliberately untouched

`<Named, so the reviewer knows it was a decision and not an oversight.>`

---

## 7. Blast radius — measured

*From the PKG where the change is code (`orchestrator blast-radius`), from `grep -rl` where it
is documentation. A count, and what it covers.*

---

## 8. Shared infrastructure this track builds

*Distinct from §5: new reusable pieces this track happens to build, which later tracks inherit.
Say which phase builds each. If this track builds none, delete the section — inventing one is
worse than admitting it is all track-specific.*

---

## 9. Packaging

`<What reaches a user: extras, manifests, the PyPI long description, workflow tags. Delete if
nothing does — but check before deleting.>`

---

## 10. Validation — the P0 baseline and what each phase adds

| Command | Result on `<base-sha>` |
|---|---|
| `<gate command>` | *(P0)* |

*Each later phase appends its own line, so the MR can show the change with receipts rather than
a claim.*

---

## 11. Out of scope

`<Named explicitly. The list that stops the branch growing.>`

---

## 12. Risks and gotchas

| Risk | Mitigation |
|---|---|
| `<what could go wrong>` | `<what makes it not>` |

---

## 13. Sequence

```
0. Decide §0                                    — review
1. P0 baseline recorded in §10
2. P1 <…>
…
n. P<n> close out §5 (G7/G8/G10/G14/G15)        — one MR to develop

§5's G1-G15 run alongside, not after: G4/G5/G6/G9/G11 in every phase commit.
```
