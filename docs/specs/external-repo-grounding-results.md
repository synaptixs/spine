# External-repo results — does the grounding effect survive off Spine's own code?

**Run 2026-08-16 against 3.18.1** · target `synaptixs/ontomesh` @ `342e209` · 5 tickets ×
2 frontier models × grounded/ungrounded × 3 passes = **12 arms, 60 ticket-runs** ·
**0 aborts, 0 incomplete logs** · **$8.91**

**The question.** [`codegen-model-comparison-results.md`](codegen-model-comparison-results.md)
measured the PKG's contribution on **Spine's own repository**, with tickets Spine wrote and
grounding tuned here. That is the most favourable setup that will ever exist, and the honest
reading of it was *a ceiling, not a capability*. This run asks the only question that reading
leaves open: does the effect exist on a codebase Spine did not write?

---

## 1. Verdict

**Yes — the mechanism replicates, and it replicates in the same shape.**

| | grounded | ungrounded | 95% CI (Wilson) |
|---|---|---|---|
| **`edit` tickets** | **12 / 12** | **12 / 12** | both [0.76, 1.00] — identical |
| **`create` tickets** | **18 / 18** | **3 / 18** | [0.82, 1.00] vs [0.06, 0.39] — **no overlap** |

Per model, grounded arms were perfect on every pass — `claude-opus-5` 5/5, 5/5, 5/5 and
`gpt-5.6-sol` 5/5, 5/5, 5/5. Ungrounded: 0.60 and 0.40.

**The asymmetry is the finding, not the rate.** Grounding was worth *nothing* where the
ticket named its target file, and decisive where the model had to discover what already
existed. That is the same shape Spine produced (98/100 `edit` either way; 29/50 vs 0/50 on
`create`) — now on a repository with different authors, different layout, and types Spine has
never seen.

**It held under a harder condition.** ontomesh is public, so the models carry partial memory
of it; the ungrounded arm should therefore do *better* here than on Spine's private-ish code,
and it did — 3/18 versus 0/50. The gap narrowed and did not close.

---

## 2. How the ungrounded arm failed — the mechanism, visible

**All 15 ungrounded `create` failures failed on `fit`. Every one.** Not on tests, not on lint:
on *integration*.

| ungrounded `create` outcome | count |
|---|---|
| tests **PASS**, preflight **PASS**, `fit` **no** | 5 |
| tests **PASS**, preflight FAIL, `fit` **no** | 6 |
| tests FAIL, preflight FAIL, `fit` **no** | 4 |

**11 of 15 produced code whose own tests passed.** It worked. It just reinvented
`ColumnModel` / `TableModel` / `Template` instead of importing ontomesh's, or placed the
module outside the package. That is precisely the *"parallel schema"* failure
`scripts/codegen_ab.py:160` predicted, reproduced on a foreign codebase — and it is invisible
to any gate that only asks "do the tests pass".

---

## 3. What can now be claimed, and what still cannot

**Now supported:**

- The grounding effect **replicates across two unrelated codebases and two frontier models**,
  concentrated entirely in work that must integrate with existing code.
- *"Measured on this repository"* is no longer the necessary qualifier on the mechanism claim.
  The claim is about grounding, and grounding was tested off Spine.

**Still not supported:**

- **Absolute rates.** The `1.00` grounded figures are 5 tickets over 3 passes with **mypy
  excluded** — ontomesh has no `[tool.mypy]`, so acceptance was tests + ruff + fit, a weaker
  bar than Spine's three-tool gate. Perfect scores on 15 attempts under a reduced gate are not
  a capability number and must never be quoted as one.
- **Model comparison.** Not attempted; n is far too small.
- **Generalisation beyond two repositories.** Two is better than one. It is not a population.

**Held-out grading agrees with the gate** rather than contradicting it — grounded 15/15 for
both models, ungrounded 10/15 and 8/15. Worth stating because the suites were authored here:
had they disagreed with the acceptance gate, the gate would be the more trustworthy of the two.

---

## 4. Two harness defects found by running this, both the same shape

Neither was a model failure. Both were **checks that are correct on Spine and silently
vacuous anywhere else** — the home-field advantage was never only the graph, it was every
acceptance criterion.

**1. The preflight bar assumed a clean repository.** `SubprocessPreflightRunner` ran
`ruff`/`mypy` across the whole worktree, so ontomesh's **3,378 pre-existing findings** failed
every ticket before the model contributed a line. `mergeable` would have read 0/50 in *both*
arms. Fixed by baseline-diffing — only *new* findings fail
([`preflight-baseline-diff.md`](preflight-baseline-diff.md), commit `8c3fa2e`). **Caught
before spending**, by assessing the target repo first.

**2. The `fit` check asserted Spine's own layout.** It required `src/orchestrator/` and an
`from orchestrator.` import. ontomesh uses flat `src/` modules and `from db_introspector
import …`, so **every `create` ticket failed `fit` in both arms** while its tests and
preflight passed. Grounded and ungrounded scored identically and the run read as *"the
grounding effect does not reproduce on an external repo"*. **Caught after 4 arms and $3.20**,
by reading the per-ticket rows rather than the summary line.

That second one is the cautionary result of this whole exercise. It would have produced a
serious, quotable, and completely false conclusion — and the summary line looked entirely
normal while it happened.

---

## 5. Method notes

- **Tickets** (`scripts/bench_tickets_ontomesh.py`): 2 `edit` naming a target file, 3 `create`
  that must import a real ontomesh type. `causality_dag` was rejected as ticket material
  despite being richer — it needs `numpy`, absent from the benchmark interpreter, so its
  graders could never import.
- **Held-out suites validated against reference implementations before the run**, so a failure
  is the model's rather than a bug in the grader. The `create` suites locate the function by
  scanning the src path; pinning a module would hand the model the answer to the only question
  grounding exists to answer.
- **Grounding verified to produce real context on foreign code before spending** — the
  grounder surfaced `_levenshtein` for the ticket that says to reuse it, and
  `ColumnModel`/`TableModel`/`Template` for the renderers. Had it returned nothing, the
  grounded arm would have been silently identical to the control.
- **Variance:** 3 passes. Temperature cannot be pinned on this model set, so single-pass
  results are not reproducible; intervals are reported rather than counts.

## Reproducing

```bash
BENCH_REPO=<ontomesh> EVAL_TASKSET=ontomesh SDLC_CODEGEN_MODEL=claude-opus-5 \
    uv run python scripts/codegen_benchmark.py                     # grounded
BENCH_REPO=<ontomesh> EVAL_TASKSET=ontomesh SDLC_CODEGEN_MODEL=claude-opus-5 \
    BENCH_NO_GROUNDING=1 uv run python scripts/codegen_benchmark.py # control
uv run python scripts/bench_aggregate.py <log-dir>
```

Per-arm logs are not committed (they contain generated source); the tables above are the
result.
