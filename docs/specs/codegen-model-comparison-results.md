# Codegen model comparison — results

**Run 2, 2026-08-16 against 3.18.1** · `scripts/codegen_benchmark.py` · 10 tickets × 2 models ×
grounded/ungrounded × **5 passes** = **20 arms, 200 ticket-runs** · **0 aborts** · **$40.36**

Aggregated with [`scripts/bench_aggregate.py`](../../scripts/bench_aggregate.py) (Wilson score
intervals). Run 1 (2026-08-15) is superseded — it was measured before held-out grading, before
abort detection, and before the failure-classification fix; its numbers are not comparable and
should not be quoted.

**Every ticket reached the model in every arm.** That is stated first because two Run-1 attempts
produced figures that looked like results and were not.

---

## 1. Headline — grounding is decisive, and only where theory says it should be

### Pooled rates (all 10 tickets)

| model | arm | per-pass | rate | 95% CI |
|---|---|---|---|---|
| `claude-opus-5` | **grounded** | 7,10,9,10,9 | **0.90** | [0.79, 0.96] |
| `claude-opus-5` | ungrounded | 4,5,5,5,5 | 0.48 | [0.35, 0.61] |
| `gpt-5.6-sol` | **grounded** | 7,6,7,5,8 | **0.66** | [0.52, 0.78] |
| `gpt-5.6-sol` | ungrounded | 5,5,5,5,5 | 0.50 | [0.37, 0.63] |

Claude's intervals do not overlap. **gpt's do** — and that nearly produced the wrong conclusion.

### Split by ticket kind — the pooled number is diluted

The corpus is half `edit` and half `create`, and **grounding does nothing for `edit`**. Averaging
a ceiling effect against a real effect halves the measured contrast.

| arm | `edit` | 95% CI | `create` | 95% CI |
|---|---|---|---|---|
| claude grounded | 24/25 (0.96) | [0.80, 0.99] | **21/25 (0.84)** | [0.65, 0.94] |
| claude ungrounded | 24/25 (0.96) | [0.80, 0.99] | **0/25 (0.00)** | [0.00, 0.13] |
| gpt grounded | 25/25 (1.00) | [0.87, 1.00] | **8/25 (0.32)** | [0.17, 0.52] |
| gpt ungrounded | 25/25 (1.00) | [0.87, 1.00] | **0/25 (0.00)** | [0.00, 0.13] |

**On `create` tickets the intervals do not overlap for either model.** Grounding is significant
for both — the pooled view hid it for gpt. On `edit` tickets all four intervals overlap
completely: **98/100 accepted regardless of grounding.**

> **`0/50` ungrounded `create` tickets were accepted** — both models, five passes each, zero
> successes in fifty attempts. Against 29/50 grounded.

**Why this shape is the point.** An `edit` ticket names its target file, so the model needs no map
and withholding one costs nothing. A `create` ticket must import what already exists and land in
the right place; ungrounded, the model invents a parallel structure that imports nothing real —
the failure `scripts/codegen_ab.py:160` predicted. A flat "grounding helps by 42 points" would be
both weaker and less true than "grounding is worth nothing when the file is named and decisive
when it is not."

---

## 2. `resolved` → `mergeable`

Now measured against **held-out tests the model never sees** (all 10 tickets carry suites; in
Run 1 nine did not, making `resolved` self-graded).

| arm | `resolved` (tests pass) | `mergeable` (+ `mypy --strict` + fit) | Δ |
|---|---|---|---|
| claude grounded | **50/50** | 45/50 | 5 |
| claude ungrounded | 28/50 | 24/50 | 4 |
| gpt grounded | **50/50** | **33/50** | **17 (34%)** |
| gpt ungrounded | 30/50 | 25/50 | 5 |
| **total** | **158/200** | **127/200** | **31 (15.5%)** |

**Both grounded arms resolved 100% of tickets.** A benchmark scoring `resolved` alone would record
a perfect 50/50 for each — while a third of gpt's output would not survive CI. That single row is
the case for the `mergeable` axis, and it is far stronger than Run 1's 2/40.

**The delta is a property of the model:** 10% for Claude grounded, **34%** for gpt grounded, on
identical tickets under an identical gate.

**Held-out acceptance** (independent grading): claude grounded 40/50, gpt grounded 35/50. For
Claude it is *stricter* than the acceptance gate (40 < 45) — five changes passed tests, types and
placement yet failed the hidden suite. For gpt it is *looser* (35 > 33) — some output was
functionally correct but wrongly placed or typed. The two signals disagree in both directions,
which is why both are reported.

---

## 3. Cost per **usable** change

| arm | cost | mergeable | $/mergeable |
|---|---|---|---|
| claude grounded | $12.34 | 45 | **$0.27** |
| claude ungrounded | $14.07 | 24 | $0.59 |
| gpt grounded | $7.38 | 33 | **$0.22** |
| gpt ungrounded | $6.57 | 25 | $0.26 |

**Grounding more than halves Claude's cost per usable change and costs less in absolute terms**
($12.34 vs $14.07) — the ungrounded arm burns its refine budget failing. `$/mergeable` is the
procurement number: `$/resolved` would flatter gpt by a third.

---

## 4. What this does **not** support

- **No model ranking.** Claude 0.90 vs gpt 0.66 looks decisive, but the corpus is Spine's own code
  and the grounding was tuned here; that advantage is unquantified and may not be symmetric.
- **No portable score.** `mergeable` folds in *this* repo's `mypy --strict` config.
- **No generalisation to SWE-bench.** These tickets are smaller and friendlier than real instances.
- **Variance is real and irreducible.** Claude grounded ranged 7–10 on identical inputs. Temperature
  cannot be pinned (`claude-opus-5` rejects a pinned value), so single-pass results are not
  reproducible — which is why five passes and intervals, not counts.

## 5. Defects found by running this

Four, all in `LiteLLMClient` or the harness, **none findable by reading code**:

1. **Reasoning models reject tools unless the level is named** — every `gpt-5.6-sol` codegen call
   failed. The error recommends `reasoning_effort='none'`, which disables the reasoning; `low`,
   `medium` and `high` all work.
2. **`json_object` + tools is rejected** on the Responses API path; adding "json" to a system
   message does not help. It was always redundant alongside a forced tool.
3. **The request timeout was not enforced** on that path — one arm sat **4h16m at 0% CPU** on a
   single call. In production an `sdlc` run would hang for hours with the budget never tripping,
   because a call that never returns never bills. Now wrapped in `asyncio.wait_for`.
4. **Model failures were being counted as "not measured"** — `CodegenError` (unusable output) was
   conflated with `LLMError` (no response). Every instance landed in the *ungrounded* arms, so the
   bug would have flattered the control and understated the grounding effect.

## Reproducing

```bash
SDLC_CODEGEN_MODEL=claude-opus-5 uv run python scripts/codegen_benchmark.py                      # grounded
SDLC_CODEGEN_MODEL=claude-opus-5 BENCH_NO_GROUNDING=1 uv run python scripts/codegen_benchmark.py # control
uv run python scripts/bench_aggregate.py <log-dir>                                               # intervals
```

Per-arm logs are not committed (they contain generated source); the tables above are the result.
