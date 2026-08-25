# Competitive landscape — where Spine stands

**Audience:** internal · **Date:** 2026-08-21 · **Spine version:** 3.21.0 released, Phase 4 unreleased on `develop`

**Method and its limits, stated first.** Spine's cells are read from source — `scoreboard.json`,
`codegen_benchmark.py`, `docs/specs/README.md`, the CLI. **Competitor cells are from public
documentation only**, and are therefore lower-confidence than Spine's: a blank means *not found
in public docs*, not *proven absent*. Any cell used externally should be re-verified against
the competitor's own product first.

That caveat is not boilerplate. `evals/agent_corpus.py` exists because a hand-authored
capability matrix in this project was once **22% wrong with nothing failing**. A matrix is a
claim surface, and this is the format most likely to be quoted without its caveats.

**This document is the narrative. [`capability-matrix.md`](capability-matrix.md) is the
matrix** — it is scored per row, footnoted, and its counts are checked by
`python scripts/matrix-count.py --check`. Until 2026-08-21 this file carried a *second* matrix
of its own, and the two had drifted into contradiction on four cells — including RBAC, which
§5 below still described with a number the matrix documents as corrected twice. Two claim
surfaces for the same claim is one more than can be kept true, so the duplicate is gone.

Companion to [`graphify-vs-spine-comparison.md`](graphify-vs-spine-comparison.md) (one
competitor, in depth, at v3.7.0 — now stale).

---

## 1. Compete on capability, not adoption

Star counts and funding are not capability, and Spine should not be positioned against them.
Language breadth is a **growth curve, not a ceiling** — front-ends are additive against a fixed
universal schema (`facts.py`), which is why Go and four others landed without reworking the
model. This document therefore ranks capability and treats breadth as a roadmap line.

Spine spans three layers usually sold as separate products:

| Layer | Spine ships | Others here |
|---|---|---|
| **A. Code intelligence** | The PKG — deterministic, no-LLM, `file:line` | CodeGraph, Graphify, Serena, GitNexus, Joern, Sourcegraph |
| **B. Autonomous delivery** | `sdlc` — requirements → tested, reviewed, merged PR | OpenHands, Devin, Cursor, Copilot |
| **C. Governance** | Gates, audit log, verifiers, spend caps | Frameworks, not products |

---

## 2. Capability matrix — see [`capability-matrix.md`](capability-matrix.md)

**The matrix lives in one file, and this is not it.** A scored copy sat here until 2026-08-21
and had drifted from the real one on four cells:

| Cell | This file said | The matrix says |
|---|---|---|
| RBAC / multi-tenancy | ❌ 10% — one row | **Multi-tenancy ✅, RBAC 🟡** — split, because they differ |
| Invention detection | ✅ | **🟡** — Python-only; on other languages `0` means *not measured* |
| Interactive visualization | 🟡 | **✅** — delegated to `pkg export` by decision, not missing |
| Secrets vault | ❌ 10% | ❌ — the `10%` was from a G-scorecard entry that was never true |

Three of the four made Spine look **worse** than the source supports, which is the direction
that costs a deal quietly. The RBAC cell is the one the matrix's own footnote ⁶ records as
*corrected twice* — it was wrong here for a third time because nothing connected the two
documents.

The matrix carries **46 scored rows across three layers**, of which **21 are rows no
competitor's public documentation fills**. Its counts are derived rather than typed:

```
python scripts/matrix-count.py --check
```

Read the caveats at the top of that file before quoting any cell. In particular ➖ means *not
found in public docs*, never *proven absent*.


## 3. What Spine is measured on — the record

This is the part most easily understated, so it is itemised.

**The graph (Layer A):**

| Measure | Result |
|---|---|
| Corpus precision | **1.00** on every node and edge kind, all 8 languages |
| Corpus recall | 1.00 on every kind except `CALLS` |
| `CALLS` recall | 1.00 (c, sql) · 0.73 (python) · 0.67 (cpp, csharp, go, java) · 0.50 (typescript) |
| Invention | **0** across 15,212 call edges |
| Parity shortfall | 0 |
| Real-repo invention (measured at 3.18.1) | **0** on flask, httpx, libuv |

The 19-fixture corpus is only the *labelled* half. Three oracles measure real repositories:
`parity` and `invention` read real source, and `runtime` **executes the repo's own test suite**.

**Delivery (Layer B):**

| Measure | Result |
|---|---|
| Codegen acceptance benchmark | **9/10** (edit 5/5, create 4/5), $0.68 total |
| Acceptance definition | tests pass **AND** `ruff`+format+`mypy --strict` preflight **AND** change fits |
| Autonomous features merged as real PRs | 6 (#16, #18, #20, #21, …) |
| Real PR → real CI green → autonomous merge | Proven (PR #16) |
| External **private** repo, live run | Proven (AEO-27, GitHub App) |
| Grounded-codegen A/B | 3/3 |
| Agent validity gate | `sdlc baseline` vs known-answer tickets; false and missed refusals counted **separately** |

That last row is a design choice worth quoting: a single accuracy number would let a false
refusal hide behind a missed one, and they cost very different things.

---

## 4. Where Spine genuinely leads

1. **Correctness of the graph is measured and published.** Every competitor benchmark found in
   this category is an *efficiency* metric — "58–70% fewer tool calls", "74% token savings",
   "~70% token reduction". They answer *how cheap*. None answers *is it right*. Spine is alone
   in the second question, and it is the question that decides whether an agent can trust the
   graph.
2. **Invention detection has no counterpart anywhere found.** Spine hunts for *fiction* — edges
   asserting a callee that does not exist — where every other check hunts for absence. This is
   the failure mode an agent cannot detect for itself.
3. **The graph is CI-verifiable.** `understand --check` makes the knowledge base *provably*
   current rather than hopefully current. No competitor found offers a currency gate.
4. **Delivery is governed, not just autonomous.** Human gates, per-step policy/evidence
   verifiers, append-only audit log, enforced per-run spend cap, calibrated escalation, OTel
   tracing. **EU AI Act Annex III obligations took effect 2 August 2026** — this is now a
   procurement gate, and it is Spine's least-contested ground.
5. **CI-parity preflight before the PR opens.** Acceptance requires `mypy --strict` to pass, not
   merely that tests are green — a stricter bar than "the agent thinks it is done".
6. **The delivery pipeline researches before it designs, deterministically.** Completed
   2026-08-18/21 ([`graphir-sdlc-workflow.md`](graphir-sdlc-workflow.md)). Every run writes an
   **Evidence** artifact — landing symbols with `file:line`, root-cause analysis, and a blast
   radius keyed off *where the ticket lands* rather than off the plan's own guess — with **no
   model in the path**, so it is reproducible at a commit. Each acceptance criterion is then
   **bound to a `file:line` or the ticket is refused**. The competitive point is not that the
   pipeline has a planning step; it is that the step is checkable, and that a wrong plan is
   caught by evidence rather than by the plan agreeing with itself.
7. **Measurements that decline features, published.** Twice now: the design stage was **not**
   promoted to a model call after a 100-run A/B found no gain it could resolve, and Phase 4's
   parallel fan-out was **not** built after timing showed ~30ms was the entire available
   saving. No competitor found publishes a measurement that killed their own feature. It is the
   least imitable row in the matrix, because imitating it costs a shipped capability.

---

## 5. Where Spine is genuinely behind

1. **No comparable public benchmark.** This is *not* "unmeasured" — 9/10 acceptance on a strict
   definition is a real result. The gap is **comparability**: 9/10 on this repo's own tickets
   cannot be set beside 72% on SWE-bench, and buyers will try. Closing it is a positioning
   need, not a measurement need. [`gap6-benchmarks-roadmap.md`](gap6-benchmarks-roadmap.md)
   remains *Not started*.
2. **RBAC role-gating, and a secrets vault.** State this precisely, because it was overstated
   here for months as *"RBAC/multi-tenancy (10%)"* from a G-scorecard entry that was never
   true. **Multi-tenancy is real**: `Principal(id, tenant_id, roles)` reaches 16 of 23 API
   route modules, there are 23 tenant-scoping sites, and a cross-tenant read returns **404**
   rather than 403 so no id leaks. **Role-gating is the thin part** — `has_role` is called at
   exactly one site, the approval decision. Defensible as a starting point, since that is the
   highest-risk action, but a buyer asking *"can I restrict who does X?"* will find one X.
   The secrets vault is genuinely absent. Both are what an enterprise buyer probes right after
   the governance pitch.
3. **Language breadth: 8.** A growth curve rather than a ceiling — but today it disqualifies
   Spine as a general code-intelligence layer for a polyglot shop (no Rust, Ruby, Kotlin, Swift,
   PHP).
4. **Two of four oracles are Python-only** (`runtime` via PEP 669, `invention` via Python `ast`).
   On a non-Python repo `invention` reports `0` meaning *not measured*, not clean.
5. **`--intents` ships with no reader** — it costs ~3× CPU and nothing renders its facts. Minor
   in itself, but it sits under the determinism-and-measurement claim that is a headline
   strength. (`state` was also not byte-stable when this was written; **fixed in 3.19.0**.)

---

## 6. The strategic read

Spine's defensible position is the **conjunction**, and no competitor found holds both ends: a
code graph whose correctness is *measured and gated*, feeding a delivery pipeline whose actions
are *governed and audited*. The graph leaders do not act on code; the agent leaders do not
publish the correctness of what they act on.

The highest-value next moves, in order:

1. **A comparable public benchmark** — the one number that makes the delivery half legible to
   an outside buyer.
2. **RBAC role-gating + a secrets vault** — the two cells that undercut the governance pitch on
   contact. Not multi-tenancy, which is already there; see §5.
3. **Language breadth** — real, but additive against a fixed schema, and the least urgent.

---

## Sources

Competitor facts below are public-documentation only; see the method note at the top.

- [Code Intelligence Tools for AI Agents Compared — Ry Walker](https://rywalker.com/research/code-intelligence-tools)
- [OpenHands — Devin alternatives comparison](https://www.openhands.dev/blog/devin-ai-alternatives)
- [CodeGraph overview — ToKnow.ai](https://toknow.ai/posts/codegraph-knowledge-graph-ai-coding-agents-fewer-tokens/)
- [Code Property Graph — Joern](https://cpg.joern.io/)
- [SCIP code indexing format — Sourcegraph](https://sourcegraph.com/blog/announcing-scip)
- [AI Agent Governance and Compliance 2026 — Zylos Research](https://zylos.ai/research/2026-05-01-ai-agent-governance-compliance-2026/)
