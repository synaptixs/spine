# Design: persona-skill eval measurement — do the candidate skills actually help?

**Status:** **Implemented, P0–P3 (the one remaining step is running the P2 A/B `--live`, which
spends).** Completes the persona-skill system's "measure the winners" step: decide, with evidence,
whether the three authored SWE candidate skills (`test-strategy`, `security-aware-coding`,
`convention-digest`) improve generated code, so the ones that do are promoted into the capability
catalog and the rest stay candidates. The full pipeline is built and unit-tested without spend —
phase-aware conditioning (P0), 18 signal-bearing tickets with held-out graders (P1), the
provider-selectable baseline-vs-treatment runner (P2), and the bar-applying promoter (P3); the only
thing that costs money is one `scripts/skill_ab.py --live` run, after which
`scripts/skill_promote.py` decides and promotes. See each phase below for the shipped detail.

**Thesis (the discipline this enforces):** *a skill that doesn't move a metric doesn't ship.* The
vetting gate (`catalog/vetting.py`) already refuses to select an un-measured skill; this spec is the
measurement that produces the scores it gates on.

## Where we are, and why the first run was inconclusive
- The 3 candidate skills are authored as native `Skill`s but **deliberately out of `_SEED`**, so the
  planner never selects them (zero run-behavior change) until measured.
- `scripts/codegen_benchmark.py` (10 create+edit tickets against this repo) has an `EVAL_SKILL=<id>`
  A/B hook; `orchestrator/evals/` scores runs into a `Scorecard` (`acceptance_rate`, `flaky`,
  `intervention_rate`, `mean_iterations`, cost, failure-mode tally).
- A first bounded A/B (`convention-digest`, 2 edit tickets, sonnet) was **inconclusive — a ceiling
  effect**: baseline already hit 2/2, so there was no headroom for the skill to show signal.

That run exposed the **three problems any credible measurement must solve**:

1. **Ceiling / no headroom.** The stock tickets are well-specified; the baseline already passes,
   so the skill can't improve the number. → We need **signal-bearing tasks** (harder /
   under-specified, where the naive baseline fails).
2. **Self-grading circularity.** Acceptance today = *the model's own generated tests pass* +
   preflight + fit. For `test-strategy` especially the model both writes the tests *and* is graded
   by them — a better test strategy can't change a pass/fail it controls. → We need **independent
   grading** the model doesn't author.
3. **Wrong phase.** Skill guidance currently conditions only the **implement** prompt
   (`codegen._condition_system`). `test-strategy` is about the **tests**, which are written in the
   *author_tests* phase — so today it never reaches the phase it targets. → Conditioning must be
   **phase-aware**.

## Design

### 1. Phase-aware skill conditioning (prerequisite)
Extend `codegen` so a skill declares *which phase(s)* it conditions, and `_condition_system` applies
each skill to its phase:
- `test-strategy` → **author_tests** (+ refine), where the suite is written.
- `security-aware-coding`, `convention-digest` → **implement** (+ refine).

This is a real product-behavior change (and what we actually want to measure), so add a small
`phases` field to the `Skill` artifact (default `("implement",)` — preserves today's behavior for
the existing conventions/grounding skills). `author_tests`/`refine` gain the same
persona/skill-conditioning seam `implement` already has.

### 2. Independent grading (the crux)
Replace "the model's own tests pass" with graders the model doesn't author, per skill:

- **Held-out reference tests.** Each measurement ticket ships a hidden reference test suite (not
  shown to the model) that judges the *implementation* — including the edge/error/boundary cases a
  thin solution misses. The independent acceptance = held-out tests pass. This is the headroom that
  lets `test-strategy` (more refine cycles toward a correct impl) and any skill show up.
- **Static security grader (`security-aware-coding`).** Run **semgrep** (the existing `security`
  extra) over the generated diff; metric = security findings (lower is better) + a held-out
  security-assertion suite (injection/validation/secret-leak cases).
- **Convention-conformance grader (`convention-digest`).** The benchmark's preflight (ruff +
  format + mypy-strict, repo config) already enforces much; add a check that the change **reused
  existing symbols** (via the PKG / grep) rather than introducing a parallel pattern, and didn't
  create an unrelated module. Metric = preflight pass + reuse/no-parallel-module + fewer refine
  cycles.
- **Rubric judge (supporting, not sole).** An LLM judge scoring test-suite thoroughness (each
  acceptance criterion → ≥1 assertion; error paths + boundaries present). Used as a secondary
  signal, never the only gate (a judge is itself an LLM — bias-prone).

### 3. The A/B harness
Reuse `evals.run_eval` + `codegen_benchmark.run_ticket`, extended:
- **Two arms** per skill: *baseline* (`EVAL_SKILL` unset) vs *treatment* (`EVAL_SKILL=<id>`,
  conditioning its declared phase). Same tickets, same seeds/repeats.
- **K repeats per ticket** (variance is real — the `Scorecard` already surfaces `flaky`); report
  mean ± spread, not a single run.
- **Per-skill task set** of N **signal-bearing** tickets (designed with headroom + a held-out
  grader), authored from real failure modes — not by peeking at the skill text (avoid
  teaching-to-the-test).
- Output: dated scorecards + a baseline-vs-treatment comparison to `docs/evals/` (the existing
  `render_comparison`), with the independent-acceptance delta as the headline.

### 4. Metrics + the promotion bar
Headline = **independent-acceptance delta** (treatment − baseline) on held-out graders. Supporting:
refine cycles, intervention rate, security findings, convention conformance, **cost delta** (skills
add prompt tokens). Promote a skill into `_SEED` only when it clears a pre-registered bar:

- a **meaningful margin** on its primary independent metric over baseline (e.g. ≥ +10 percentage
  points held-out acceptance, or a clear security-findings / refine-cycle reduction), across the full
  task set × K repeats, **not within run-to-run noise**; and
- an acceptable **cost delta** (no material regression for no gain).

A skill that clears → added to `_SEED` (planner can select it) and its `SkillEval`/min_score recorded
so the vetting gate reflects the evidence. A skill that doesn't clear **stays a candidate** with the
honest numbers written down — not silently dropped.

### 5. What this is *not*
- Not a leaderboard — it's a go/no-go per skill against a pre-registered bar.
- Not model-universal — a skill that helps a weaker model may not help a stronger one. Measure on the
  **codegen default (sonnet) first**; treat the promotion decision as per-model-tier, and re-measure
  if the default model changes.

## Phasing (each shippable)
- **P0 — Measurement infra** (no live run): phase-aware skill conditioning (`Skill.phases` +
  author_tests/refine seam); the held-out-tests runner, semgrep grader, and convention/reuse check in
  the benchmark; the per-phase `EVAL_SKILL` hook. Deterministic, unit-testable.
  **✅ SHIPPED.** `Skill.phases` added (default `("implement",)`; `test-strategy` →
  `("author_tests", "refine")`, `security-aware-coding`/`convention-digest` → `("implement",
  "refine")`); `codegen._condition_system(..., phase=...)` filters skills by phase and now conditions
  author_tests + refine, not just implement; independent graders live in
  `orchestrator/evals/graders.py` (held-out reference-test runner, semgrep finding counter,
  symbol-reuse check), wired into `scripts/codegen_benchmark.py` via `Ticket.held_out_tests` +
  independent-acceptance reporting. The `EVAL_SKILL` hook is now phase-aware automatically (a skill
  reaches whatever phase it declares). Full unit coverage; mypy/ruff/pytest green.
- **P1 — Per-skill task sets**: author N signal-bearing tickets per candidate (with held-out
  graders), from real failure modes.
  **✅ SHIPPED.** `scripts/codegen_benchmark.py` now carries `SKILL_TASKSETS` — 6 tickets per
  candidate (18 total), each a create ticket pinned to an importable `orchestrator.bench_<x>` module
  with a hidden held-out suite (`Ticket.held_out_tests`): `test-strategy` = edge-heavy pure functions
  (duration/int-list/truncate/percentile/intervals/slug — empty, zero, negative, boundary,
  non-mutation, error paths); `security-aware-coding` = untrusted-input handlers (path-join, HTML
  escape, secret-mask, identifier allow-list, open-redirect, shell-arg) judged on safe behavior +
  semgrep; `convention-digest` = features with an existing repo helper to reuse (diff_utils,
  verifiers ×2, pkg.stats, llm.recording) judged on held-out behavior + the symbol-reuse grader.
  Authored from failure modes, not from skill text (no teaching-to-the-test). `EVAL_TASKSET=<id>`
  selects a set; pair with `EVAL_SKILL=<id>` for the treatment arm. Validated two ways:
  `tests/evals/test_skill_tasksets.py` locks shape (parseable, pinned import target, unique ids,
  reuse target named), and all 51 held-out assertions pass against reference implementations (the
  graders are correct — a correct impl passes). mypy/ruff/pytest green (1257 passed).
- **P2 — Run the A/Bs**: baseline vs treatment, K repeats, on the default model → scorecards +
  comparison in `docs/evals/`. (The one real cost: N×K×2×3 ticket-runs.)
  **✅ HARNESS BUILT (live run = one `--live` command, which spends).** `scripts/skill_ab.py`
  runs each skill's task set through a baseline arm (no skill) and a treatment arm (skill on) via
  `evals.run_eval`, scoring each ticket-run by its **independent (held-out) acceptance**
  (`evals.skill_ab.outcome_from_result`), then writes per-arm scorecards + `render_comparison` + the
  promotion `Verdict` to `docs/evals/`. **Model is `--provider`-controlled** — `local`
  (ollama/openllama, via OLLAMA_API_BASE), `openai` (gpt-4o), `claude` (claude-sonnet-4-6) — with
  `--model`/per-provider-env override (`evals.skill_ab.resolve_model`). The pre-registered bar
  (+10pp held-out acceptance, `PROMOTION_MARGIN`) is applied by `promotion_verdict`. Dry-runs by
  default (prints the arms×tickets×repeats call budget); `--live` actually runs. `run_ticket` now
  takes explicit `model`/`eval_skill` so the runner controls the arm without env mutation. Pure
  core unit-tested (no spend); mypy/ruff/pytest green (1269 passed). **To execute:**
  `uv run python scripts/skill_ab.py --provider <claude|openai|local> --repeats 3 --live`.
- **P3 — Decide + promote**: apply the bar; promote winners into `_SEED` with their `SkillEval`;
  write the losers' numbers honestly.
  **✅ MACHINERY BUILT (waits on P2 numbers to actually promote anything).**
  `orchestrator/evals/promotion.py` turns a P2 A/B JSON into a `PromotionDecision` (re-applying the
  bar — a float-noise-safe `round(Δ,9) >= margin`), mints the promoted `Capability` with its measured
  score recorded in `payload["eval"]` (id/min_score=baseline+margin/achieved/model), renders an
  honest `PROMOTIONS.md` decisions log (winners **and** held candidates, with numbers), and provides
  a pure, idempotent `apply_to_catalog_source` that injects winners into a new machine-managed
  `_PROMOTED` overlay in `catalog.py` (kept separate from hand-curated `_SEED`; included by
  `default_catalog`/`from_sources`, empty until earned). `scripts/skill_promote.py` reads
  `docs/evals/*-skill-ab-*.json`, writes the log, prints the `_PROMOTED` snippets, and with `--apply`
  edits the overlay in place (review-then-gate). Verified end-to-end against the real catalog source
  (injected skill becomes selectable, held skill stays out, re-apply is a no-op); pure core
  unit-tested. mypy/ruff/pytest green (1282 passed). **To run after P2:**
  `uv run python scripts/skill_promote.py [--apply]`.

  *Promotion vs the vetting gate:* the three candidates are `NATIVE` skills, which the vetting gate
  approves by origin — so the recorded `eval` evidence lives on the **catalog entry** (`payload`), not
  by mutating the native `Skill.evals` tuple (which stays `()`). The `SkillEval`/min_score machinery
  still gates an *imported* re-pin of the same capability.

## Decisions to confirm
1. **Independent grading** = held-out reference tests + semgrep + a rubric judge (recommended) vs a
   lighter subset. The held-out tests are the load-bearing part.
2. **Promotion bar** — the exact margin + cost ceiling (recommend ≥ +10pp held-out acceptance or a
   clear sub-metric win, within a small cost delta; pre-register before running so it isn't
   rationalized after).
3. **Task-set size / repeats** — recommend ~8–12 tickets per skill × 3 repeats (powered enough to
   beat the noise that sank the first run, bounded on cost).
4. **Model(s)** — sonnet (the codegen default) first; optionally gpt-4o as a second tier later.
5. **Phase-conditioning rollout** — land phase-aware conditioning as real codegen behavior (so we
   measure the product, not a harness-only path), default-preserving for the existing skills.

## Honest risks / limits
- **Self-grading is the headline risk** — independent graders fix it but add complexity and their own
  bias (semgrep false-positives; an LLM judge's noise). Lean on held-out *executable* tests over the
  judge.
- **Teaching-to-the-test.** If tickets are written to favor the skill, the result is circular. Author
  them from real failure modes, hold out the graders, and keep the skill text away from the
  ticket-authoring.
- **Cost + time.** A powered live A/B over multiple skills × repeats is real money; P2 is the spend.
  Bounded sizing (decision 3) keeps it sane.
- **Model-dependence.** The verdict is per-model-tier; revisit when the default model moves.
- **Null results are likely and fine.** Some candidates may not clear — that's the discipline
  working, not a failure. The deliverable is an *honest* decision, not three promotions.

## First step
**P0:** make skill conditioning **phase-aware** (`Skill.phases`; condition author_tests/refine, not
just implement — so `test-strategy` reaches the test phase) and add the **held-out reference-test
runner** to `codegen_benchmark.py`. Those two unlock a measurement that *can* show signal — without
them the A/B is structurally blind (exactly why the first run was inconclusive). Then author the
per-skill task sets (P1) and run the powered A/B (P2).
