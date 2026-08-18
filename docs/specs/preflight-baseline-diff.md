# Preflight baseline-diff — making `mergeable` portable

**Status:** Proposed — awaiting approval. **Written 2026-08-16 against 3.18.1.**
**Owner:** _unassigned_
**Branch:** `bench/external-repo`

**One-liner:** `mergeable` must mean *"this change is clean"*, not *"this repo was already
clean."* Today the gate fails on a repository's pre-existing errors before the model writes a
line, which makes the metric unusable anywhere except a pristine codebase.

---

## 1. Why — found by trying to run the external test

[`codegen-model-comparison-results.md`](codegen-model-comparison-results.md) measured the PKG's
contribution on **this** repository. The obvious next step was an external repo, and
`synaptixs/ontomesh` was assessed as the target. It is a good target in every respect but one:

| check | result |
|---|---|
| Public, Python, active | ✅ |
| Spine extracts it well | ✅ **5,167 grounded nodes · 413 external · 13,784 edges** (`CALLS` 5,746, `EXPOSES` 87, `IMPLEMENTS` 28) |
| Real test suite | ✅ 78 test files |
| Sizeable, real structure | ✅ 246 Python files |
| **Passes its own strict bar** | ❌ **518 `mypy --strict` errors across 67 files; 1,656 `ruff` errors** |

`SubprocessPreflightRunner.run` (`sdlc/preflight.py:70-83`) executes `ruff check`,
`ruff format --check` and `mypy` against **the whole worktree**, not the changed files. On
ontomesh, preflight therefore fails on every ticket in every arm before the model contributes
anything. `mergeable` would read 0/50 grounded *and* 0/50 ungrounded — a run that measures
nothing while costing ~$25.

### The finding is bigger than the blocker

**The `mergeable` gate only works on repositories that already pass their own strict bar.**
Spine does, which is exactly why this never surfaced: the home-field advantage was not only
the graph, it was the *gate*. Most real codebases carry a backlog of lint and type findings,
so as written the metric is not portable — which turns
[`gap6-benchmarks-roadmap.md`](gap6-benchmarks-roadmap.md)'s portability caveat from
theoretical into concrete, and undercuts `mergeable` as the leadership axis proposed in
[`codegen-benchmark-roadmap.md`](codegen-benchmark-roadmap.md).

---

## 2. Files changed

| file | change |
|---|---|
| `src/orchestrator/sdlc/preflight.py` | add `capture_baseline()`; `run()` takes an optional `baseline`; report only *new* findings |
| `scripts/codegen_benchmark.py` | capture the baseline once per repo at startup; pass it into each ticket's preflight; print baseline size in the provenance block |
| `tests/sdlc/test_preflight.py` | the cases in §5 |

**`sdlc/worker.py` is deliberately not touched.** `baseline=None` preserves today's behaviour
exactly — fail on any finding — so the production SDLC path is unchanged by this work.

---

## 3. How the diff works

Each tool's output is parsed into findings normalised to **`(relative_path, rule_code)`** and
counted as a multiset. A finding is **new** only when its count for that pair *increased*
against the baseline.

**Line numbers are deliberately excluded from the key.** Inserting a function shifts every
line beneath it; keying on line would report an entire file as new on any insertion. Keying on
`(path, code)` with counts catches *"this file gained another `attr-defined`"* while ignoring
*"the same error moved down twelve lines"*.

**Fixing a pre-existing error is never penalised** — counts decreasing is always fine, and a
change that reduces the backlog should not be failed for it.

**`ruff format --check` emits no rule codes**, so it degrades to per-file: a file already
unformatted stays tolerated, a newly unformatted file fails.

**Baseline is captured once per repository, not per ticket.** Every ticket runs in a fresh
worktree at the same commit, so the baseline is identical for all of them; capturing per
ticket would multiply a full `mypy` run across the corpus for no additional signal.

---

## 4. Critical failures stop the run, loudly

Each condition below **halts with a non-zero exit**. None may degrade to a silent pass — the
failure mode that produced two sets of real-looking-but-meaningless numbers on 2026-08-15.

| condition | why it must stop |
|---|---|
| **Baseline capture fails** (tool crash, timeout) | no baseline ⇒ no delta ⇒ every `mergeable` figure is meaningless |
| **`ruff` / `mypy` not importable in the target env** | the gate silently becomes "tests only" and `mergeable` quietly means something weaker |
| **No `pyproject.toml`** | `run()` currently returns **`passed=True`** with "preflight skipped" — a silent pass that would inflate every arm |
| **Baseline output unparseable** | new findings cannot be distinguished from known ones |

Message shape, matching the abort guard already in the harness:

```
*** STOPPING — preflight baseline could not be captured for <repo>: <reason>.
    Without a baseline, `mergeable` cannot distinguish the model's errors from the
    repo's own. This is not a result. Fix the cause and re-run.
```

**Non-critical but reported:** when the baseline is large (ontomesh: 518 + 1,656), the gate is
weaker there than on a pristine repo. The count is printed in the provenance block beside the
acceptance-gate line, so no reader can quote the number without seeing it.

---

## 5. Validation — before any paid run

1. **Unit.** Synthetic baseline: a change adding one finding is caught; a change that only
   shifts line numbers is not; a change that *removes* a finding passes.
2. **Regression on Spine.** Baseline is 0 findings, so *new == all*. One ticket must behave
   identically to today — this is what proves production is unaffected.
3. **Integration on ontomesh.** One ticket: preflight must stop failing on the 518 pre-existing
   errors and judge only the change.

Steps 1–3 cost nothing. **Only after all three pass do the arms run.**

---

## 6. Risks accepted, and why

- **Cross-file errors count as new.** A change in `a.py` that introduces an error in `b.py` is
  reported. Correct — that is breakage the change caused — and worth stating because it means
  "new" is not limited to touched files.
- **Same code, different instance.** If a model fixes one pre-existing `attr-defined` in a file
  and introduces another, counts net to zero and the new one is masked. Rare, and the
  alternative (line-keyed matching) is far noisier under insertions.
- **Baseline drift.** If a ticket legitimately modifies an existing file, the baseline for that
  file still applies. Intended: the pre-existing findings were not the model's doing.

---

## 7. Cost

| | |
|---|---|
| Implement + validate | ~half a day |
| ontomesh arms | 10 tickets × 4 arms × 3 passes ≈ **$25, ~2h** |
| Ticket authoring for ontomesh | the larger and riskier half — see below |

**Ticket quality is the real risk, not the gate.** The `create` tickets must genuinely require
importing existing ontomesh symbols, or the run measures nothing regardless of how clean the
harness is. If the codebase does not support such tickets, that gets reported rather than
papered over with weak ones.

---

## 8. What this unblocks

- The external-repo test, and with it the removal of *"measured on this repository"* from the
  claim now carried in `README.md`.
- `mergeable` as a portable metric — the axis
  [`codegen-benchmark-roadmap.md`](codegen-benchmark-roadmap.md) proposes Spine should lead on.
  Without this it only works on repositories that are already clean, which is a small and
  unrepresentative set.
