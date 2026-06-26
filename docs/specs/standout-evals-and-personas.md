# Standing out: a measured, general-purpose agentic layer

> Status: proposed (under review). Strategy bets **1 (prove it works — evals)**
> and **4 (multi-persona)**, to be built together; bets **2 (trust spine)** and
> **3 (comprehension moat)** follow. The pairing is deliberate: a *measured* and
> *general* agentic layer is a claim almost no competitor can make.

## Thesis

Raw codegen is commoditizing — we won't out-generate Cursor/Devin/Claude Code on
frontier models. The differentiator is a **trustworthy autonomous layer**:
durable + gated + audited + comprehension-grounded. Two moves make that
*believable* rather than asserted:

1. **Measure it** — publish honest, reproducible acceptance numbers from the
   agentic loop on real tickets. The field is demoware; numbers stand out.
2. **Generalize it** — run a *second, non-SWE persona* on the same catalog + loop
   machinery, proving it's a platform, not a coding bot.

These compose: design the eval harness **persona-agnostic**, and the same
harness that proves the SWE loop works also proves the new persona works.

---

## Bet 1 — Prove it works (eval harness)

Build on `scripts/codegen_benchmark.py` (already: 10 tickets, throwaway
worktrees, cost via `RecordingLLMClient`, acceptance = tests + preflight + fit).
Extend it into a reusable, persona-agnostic harness.

**What it measures, per task (honest metrics, not just pass/fail):**
- **acceptance rate** — did the produced artifact meet its acceptance criteria?
- **cost/task** ($) and **wall-clock**;
- **convergence** — loop steps / refine iterations to acceptance;
- **human-intervention rate** — would a gate have needed a human edit?
- **variance** — run each task K times, report mean ± range (LLM nondeterminism is real; hiding it is dishonest).

**Arms to compare:** single-shot codegen vs. the **agentic loop** (`SDLC_AGENTIC_CODEGEN`),
so the eval *justifies* the loop (or shows where it doesn't help).

**Honesty + repro:** pin the model; record runs (`RecordingLLMClient`) for
replay; every run writes to throwaway worktrees (no repo mutation); report
failures and their failure mode (parse / anchor / test / fit). Emit a
**scorecard** (JSON + a short markdown table) checked into `docs/evals/`.

**Dataset:** start with the existing 10 on this repo; add a small brownfield set
(a couple of real OSS repos + real tickets) so numbers aren't self-graded on the
tool's own codebase.

**Exit:** `uv run python scripts/agentic_eval.py` prints a scorecard;
single-shot vs agentic acceptance/cost/convergence are side by side; a dated
scorecard lands in `docs/evals/`.

---

## Bet 4 — A second persona on the same machinery

Prove persona-agnosticism. A **persona** on this stack is four things, all
already modeled — only the *content* is SWE today:
- a **catalog** of capabilities (skills / MCP servers / workflow params),
- a **workflow template** (the stage sequence + gates),
- a **tool set** the agentic loop is given,
- an **acceptance definition** (what "done" means, for the eval harness).

**The abstraction work:** today the workflow stages are named for SWE
(`intake → spec → codegen → tests → review → merge`). Generalize to a
persona-parameterized shape — `gather → plan → ⟨gate⟩ → act-loop → verify →
⟨gate⟩ → deliver` — where a persona supplies the loop's tools, the verify step,
and the delivery artifact. Keep SWE as persona #1 unchanged behind this seam.

**Candidate second personas (recommend one at review):**
- **Codebase auditor (recommended)** — security/dependency/architecture audit →
  a findings report (or PR of fixes). *Reuses the existing read tools (PKG,
  read_file, MCP) + gates + comprehension with almost no new write semantics;
  the loop already supports it; strongly proves "not just codegen."*
- **Migration-at-scale** — mechanical change across many files (the catalog
  already has a `migration-fanout` param). Still SWE-adjacent.
- **Data/analysis** — query a warehouse via MCP → a report. Furthest from SWE
  (best generality proof) but needs the most new tooling + acceptance shaping.

**Exit:** a second persona runs end to end through the same loop + gates,
produces its artifact, and is **scored by the same eval harness** (Bet 1).

---

## Why 1 + 4 together

The harness from Bet 1 is built to score *any* (task → artifact) pair against an
acceptance definition. So when Bet 4's persona lands, it plugs into the same
harness for free — one credibility artifact proving **both** "the loop works"
and "it generalizes." That combined claim — *a measured, governed, general
agentic layer* — is the standout.

## Phased plan

1. **1a — harness core**: generalize `codegen_benchmark.py` → a persona-agnostic
   runner + metrics + scorecard; SWE single-shot arm (baseline).
2. **1b — agentic arm + brownfield set**: add the loop arm + a small external-repo
   dataset; publish the first dated scorecard in `docs/evals/`.
3. **4a — persona seam**: parameterize the workflow template + catalog by persona;
   SWE stays persona #1 behind it.
4. **4b — second persona**: implement the chosen persona (catalog + tools +
   verify + delivery); run it through the loop + gates.
5. **4c — eval the persona**: score it on the Bet-1 harness; combined scorecard.

## Then next (bets 2 + 3)

- **2 — trust spine**: in-loop per-tool-call approval escalation, run replay +
  audit export, policy-as-code, RBAC/multi-tenancy (G11). The enterprise moat.
- **3 — comprehension moat**: PKG FK/`READS`-`WRITES` edges, semantic retrieval,
  blast-radius the agent uses to decide what to change (G1 → deeper). Lifts loop
  quality, so it compounds with the eval numbers from Bet 1.

## Decisions (locked)

1. **Second persona = codebase auditor.** Cheapest path, reuses the read tools /
   PKG / gates with near-zero new write semantics, strongly proves "not just
   codegen." Data/analysis is a later persona.
2. **Eval cadence = one-shot scorecard, on demand.** No standing LLM cost; runs
   are triggered manually and dated scorecards committed to `docs/evals/`. A
   nightly/CI job can come later if the signal proves worth the spend.
3. *(default)* **Brownfield dataset** — seed with the existing 10 on-repo tickets
   for 1a/1b; add 2–3 external OSS repos where a merged fix exists as ground
   truth (the issue's accepted PR defines acceptance). Exact repos chosen at 1b.
4. *(default)* **Workflow abstraction** — parameterize the *existing*
   `SDLCWorkflow` by persona rather than forking a second workflow; SWE stays
   persona #1 behind the seam (one durable code path, lower risk — promote to a
   separate template only if the auditor's shape genuinely diverges).

The codebase-auditor persona, concretely: a **read-only act-loop** (PKG queries,
read_file, governed MCP) whose `verify` step is "findings are grounded in
file:line provenance" and whose `deliver` artifact is a findings report (later: a
PR of fixes). It exercises comprehension + loop + gates with no new write path —
acceptance for the eval = findings resolve to real locations and match a known
seeded issue set.
