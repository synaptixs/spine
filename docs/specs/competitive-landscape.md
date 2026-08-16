# Competitive landscape — where Spine stands

**Audience:** internal · **Date:** 2026-08-15 · **Spine version:** 3.18.1

**Method and its limits, stated first.** Spine's cells are read from source — `scoreboard.json`,
`codegen_benchmark.py`, `docs/specs/README.md`, the CLI. **Competitor cells are from public
documentation only**, and are therefore lower-confidence than Spine's: a blank means *not found
in public docs*, not *proven absent*. Any cell used externally should be re-verified against
the competitor's own product first.

That caveat is not boilerplate. `evals/agent_corpus.py` exists because a hand-authored
capability matrix in this project was once **22% wrong with nothing failing**. A matrix is a
claim surface, and this is the format most likely to be quoted without its caveats.

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

## 2. Capability matrix

✅ shipped · 🟡 partial or opt-in · ➖ not found in public docs · **n/a** out of category

| Capability | **Spine** | CodeGraph | Graphify | Serena | Joern | OpenHands | Devin |
|---|:--:|:--:|:--:|:--:|:--:|:--:|:--:|
| **Layer A — the graph** |
| Deterministic, no-LLM extraction | ✅ | ✅ | ✅ | ✅ | ✅ | ➖ | ➖ |
| Real parser, never regex | ✅ | ✅ | ✅ | ✅ | ✅ | ➖ | ➖ |
| `file:line` provenance on every fact | ✅ | ✅ | ✅ | ✅ | ✅ | ➖ | ➖ |
| **Published precision/recall of the graph** | **✅** | ➖ | ➖ | ➖ | ➖ | ➖ | ➖ |
| **Invention detection (fabricated edges)** | **✅** | ➖ | ➖ | ➖ | ➖ | ➖ | ➖ |
| **Runtime oracle — recall from executing tests** | **🟡 py** | ➖ | ➖ | ➖ | ➖ | ➖ | ➖ |
| **Declared-vs-extracted parity check** | **✅** | ➖ | ➖ | ➖ | ➖ | ➖ | ➖ |
| **CI-checkable currency (`--check`)** | **✅** | ➖ | ➖ | ➖ | ➖ | ➖ | ➖ |
| Accuracy regression gate in CI | **✅** | ➖ | ➖ | ➖ | ➖ | ➖ | ➖ |
| Blast radius / impact analysis | ✅ | ✅ | ✅ | 🟡 | ✅ | ➖ | ➖ |
| Doc ingestion into the graph | ✅ | ➖ | ✅ | ➖ | ➖ | ➖ | ➖ |
| Media (OCR / ASR) ingestion | ✅ | ➖ | ✅ | ➖ | ➖ | ➖ | ➖ |
| Data-layer semantics (SQL, FKs, migrations) | ✅ | ➖ | ✅ | ➖ | 🟡 | ➖ | ➖ |
| Interactive visualization | 🟡 | ➖ | ✅ | ➖ | 🟡 | n/a | n/a |
| Languages | 8 | 21 | ~40 | 40+ | multi | n/a | n/a |
| **Layer B — delivery** |
| Grounded code generation | ✅ | n/a | n/a | 🟡 | n/a | ✅ | ✅ |
| Grounded in a *deterministic graph* | **✅** | n/a | n/a | ➖ | n/a | ➖ | ➖ |
| Test generation + refine loop | ✅ | n/a | n/a | ➖ | n/a | ✅ | ✅ |
| CI-parity preflight before PR | **✅** | n/a | n/a | ➖ | n/a | ➖ | ➖ |
| Opens real PRs | ✅ | n/a | n/a | ➖ | n/a | ✅ | ✅ |
| Autonomous merge on green | **✅** | n/a | n/a | ➖ | n/a | ➖ | 🟡 |
| Responds to human PR review comments | ✅ | n/a | n/a | ➖ | n/a | 🟡 | 🟡 |
| **Published acceptance benchmark** | **✅ 9/10** | n/a | n/a | n/a | n/a | **✅ 72% SWE-bench** | ✅ |
| **Comparable public benchmark (SWE-bench)** | ❌ | n/a | n/a | n/a | n/a | ✅ | ✅ |
| **Layer C — governance** |
| Explicit human approval gates | **✅** | n/a | n/a | n/a | n/a | 🟡 | 🟡 |
| Append-only audit log (every tool call) | **✅** | ➖ | ➖ | ➖ | ➖ | ➖ | 🟡 |
| Per-run spend cap, enforced | **✅** | n/a | n/a | n/a | n/a | ➖ | 🟡 |
| Policy / evidence verifiers per step | **✅** | n/a | n/a | n/a | n/a | ➖ | ➖ |
| Confidence-calibrated escalation | ✅ | n/a | n/a | n/a | n/a | ➖ | ➖ |
| OpenTelemetry tracing end-to-end | ✅ | ➖ | ➖ | ➖ | ➖ | ➖ | ➖ |
| RBAC / multi-tenancy | ❌ 10% | n/a | n/a | n/a | n/a | ➖ | ✅ |
| Secrets vault beyond `.env` | ❌ 10% | n/a | n/a | n/a | n/a | ➖ | ✅ |

---

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
| Real-repo invention (3.18.1, this session) | **0** on flask, httpx, libuv |

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

---

## 5. Where Spine is genuinely behind

1. **No comparable public benchmark.** This is *not* "unmeasured" — 9/10 acceptance on a strict
   definition is a real result. The gap is **comparability**: 9/10 on this repo's own tickets
   cannot be set beside 72% on SWE-bench, and buyers will try. Closing it is a positioning
   need, not a measurement need. [`gap6-benchmarks-roadmap.md`](gap6-benchmarks-roadmap.md)
   remains *Not started*.
2. **RBAC/multi-tenancy (10%) and secrets vault (10%).** The weakest cells in the layer Spine
   otherwise leads, and exactly what an enterprise buyer probes after the governance pitch.
3. **Language breadth: 8.** A growth curve rather than a ceiling — but today it disqualifies
   Spine as a general code-intelligence layer for a polyglot shop (no Rust, Ruby, Kotlin, Swift,
   PHP).
4. **Two of four oracles are Python-only** (`runtime` via PEP 669, `invention` via Python `ast`).
   On a non-Python repo `invention` reports `0` meaning *not measured*, not clean.
5. **Small self-inflicted credibility nicks:** `--intents` ships with no reader, and `state` is
   not byte-stable (found 2026-08-14). Minor in themselves, but they sit directly under the
   determinism claim that is a headline strength.

---

## 6. The strategic read

Spine's defensible position is the **conjunction**, and no competitor found holds both ends: a
code graph whose correctness is *measured and gated*, feeding a delivery pipeline whose actions
are *governed and audited*. The graph leaders do not act on code; the agent leaders do not
publish the correctness of what they act on.

The highest-value next moves, in order:

1. **A comparable public benchmark** — the one number that makes the delivery half legible to
   an outside buyer.
2. **RBAC + secrets** — the two cells that undercut the governance pitch on contact.
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
