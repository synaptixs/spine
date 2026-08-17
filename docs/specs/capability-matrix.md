# Capability matrix — Spine against the field

**Generated 2026-08-16 against 3.18.1.**

> ## Read this before quoting any cell
>
> **Spine's column is verified against source.** Every other column is **public-documentation
> only**, so **➖ means "not found in public docs", not "proven absent"**. A competitor may well
> ship something their docs do not describe.
>
> **Do not put a competitor column in front of a customer without re-verifying it against that
> vendor's own product.** `evals/agent_corpus.py` exists because a hand-authored capability
> matrix in this project was once **22% wrong with nothing failing** — a matrix is the format
> most likely to be quoted after its caveats have been stripped off.
>
> Companion: [`competitive-landscape.md`](competitive-landscape.md) (narrative + strategy),
> [`codegen-model-comparison-results.md`](codegen-model-comparison-results.md) and
> [`external-repo-grounding-results.md`](external-repo-grounding-results.md) (the measurements
> behind the four rows marked ⁴).

**Legend:** ✅ verified in source · 🟡 partial or opt-in · ❌ absent · ➖ not found in public
docs · **n/a** out of category

---

| Capability | **Spine 3.18.1** | CodeGraph | Graphify | GitNexus | Serena | Joern | Sourcegraph | OpenHands | Devin |
|---|:--:|:--:|:--:|:--:|:--:|:--:|:--:|:--:|:--:|
| **LAYER A — CODE INTELLIGENCE** |
| Deterministic, no-LLM extraction | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ➖ | ➖ |
| Real parser, never regex | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ➖ | ➖ |
| `file:line` provenance on every fact | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ➖ | ➖ |
| **Published precision/recall of its own graph** | **✅** | ➖ | ➖ | ➖ | ➖ | ➖ | ➖ | ➖ | ➖ |
| **Invention detection (fabricated edges)** | **🟡 py** | ➖ | ➖ | ➖ | ➖ | ➖ | ➖ | ➖ | ➖ |
| **Runtime oracle — recall from executing tests** | **🟡 py** | ➖ | ➖ | ➖ | ➖ | ➖ | ➖ | ➖ | ➖ |
| **Declared-vs-extracted parity check** | **✅** | ➖ | ➖ | ➖ | ➖ | ➖ | ➖ | ➖ | ➖ |
| **CI-checkable currency (`--check`)** | **✅** | ➖ | ➖ | ➖ | ➖ | ➖ | ➖ | ➖ | ➖ |
| **Accuracy regression gate in CI** | **✅** | ➖ | ➖ | ➖ | ➖ | ➖ | ➖ | ➖ | ➖ |
| Byte-stable report output | 🟡 ¹ | ➖ | ➖ | ➖ | ➖ | ✅ | ➖ | n/a | n/a |
| Blast-radius / impact analysis | ✅ | ✅ | ✅ | ✅ | 🟡 | ✅ | 🟡 | ➖ | ➖ |
| Doc ingestion into the graph | ✅ | ➖ | ✅ | ➖ | ➖ | ➖ | ➖ | ➖ | ➖ |
| Media (OCR / ASR) ingestion | ✅ ² | ➖ | ✅ | ➖ | ➖ | ➖ | ➖ | ➖ | ➖ |
| SQL data-layer semantics | ✅ | ➖ | ✅ | ➖ | ➖ | 🟡 | ➖ | ➖ | ➖ |
| Interactive graph exploration | ✅ ³ | ➖ | ✅ | ✅ | ➖ | 🟡 | ✅ | n/a | n/a |
| Cross-repo graph | ➖ | ➖ | 🟡 | ✅ | ➖ | 🟡 | ✅ | ➖ | ➖ |
| Languages | **8** | 21 | ~40 | TS | 40+ | multi | many | n/a | n/a |
| **LAYER B — DELIVERY** |
| Grounded code generation | ✅ | n/a | n/a | n/a | 🟡 | n/a | 🟡 | ✅ | ✅ |
| **Grounded in a deterministic graph** | **✅** | n/a | n/a | n/a | ➖ | n/a | ➖ | ➖ | ➖ |
| **Published causal contribution of the context layer (controlled A/B)** | **✅ ⁴** | ➖ | ➖ | ➖ | ➖ | ➖ | ➖ | ➖ | ➖ |
| **Replicated on an unrelated external codebase** | **✅ ⁴** | ➖ | ➖ | ➖ | ➖ | ➖ | ➖ | ➖ | ➖ |
| **Held-out grading (model never authors its grader)** | **✅ ⁴** | n/a | n/a | n/a | n/a | n/a | n/a | ➖ | ➖ |
| **Portable acceptance gate (baseline-diff)** | **✅ ⁴** | n/a | n/a | n/a | n/a | n/a | n/a | ➖ | ➖ |
| Test generation + refine loop | ✅ | n/a | n/a | n/a | ➖ | n/a | ➖ | ✅ | ✅ |
| **CI-parity preflight before PR** | **✅** | n/a | n/a | n/a | ➖ | n/a | ➖ | ➖ | ➖ |
| Opens real PRs | ✅ | n/a | n/a | n/a | ➖ | n/a | ➖ | ✅ | ✅ |
| Autonomous merge on green | ✅ | n/a | n/a | n/a | ➖ | n/a | ➖ | ➖ | 🟡 |
| Responds to human PR review comments | ✅ | n/a | n/a | n/a | ➖ | n/a | ➖ | 🟡 | 🟡 |
| Published acceptance benchmark | ✅ 9/10 | n/a | n/a | n/a | n/a | n/a | n/a | ✅ 72% | ✅ |
| **Comparable public benchmark (SWE-bench)** | **❌ ⁷** | n/a | n/a | n/a | n/a | n/a | n/a | ✅ | ✅ |
| **LAYER C — GOVERNANCE** |
| Explicit human approval gates | **✅** | n/a | n/a | n/a | n/a | n/a | n/a | 🟡 | 🟡 |
| Append-only audit log (DB-backed) | **✅** | ➖ | ➖ | ➖ | ➖ | ➖ | ➖ | ➖ | 🟡 |
| Per-run spend cap, enforced | **✅** | n/a | n/a | n/a | n/a | n/a | n/a | ➖ | 🟡 |
| Policy / evidence verifiers per step | **✅** | n/a | n/a | n/a | n/a | n/a | n/a | ➖ | ➖ |
| Security verifier in the delivery path | ❌ ⁵ | n/a | n/a | n/a | n/a | ✅ | ➖ | ➖ | ➖ |
| Confidence-calibrated escalation | ✅ | n/a | n/a | n/a | n/a | n/a | n/a | ➖ | ➖ |
| OpenTelemetry tracing end-to-end | ✅ | ➖ | ➖ | ➖ | ➖ | ➖ | ➖ | ➖ | ➖ |
| **RBAC enforcement** | **🟡 ⁶** | n/a | n/a | n/a | n/a | n/a | ✅ | ➖ | ✅ |
| **Multi-tenancy (isolation, not a label)** | **✅ ⁶** | n/a | n/a | n/a | n/a | n/a | ✅ | ➖ | ✅ |
| Secrets vault beyond `.env` | ❌ | n/a | n/a | n/a | n/a | n/a | ✅ | ➖ | ✅ |

---

## Footnotes

**¹ Byte-stable output — partial, and the exception matters.** `understand` is byte-identical
run to run. **`state` is not**: components that tie on their symbol counts sort out of a `set`
with a non-total key, and Python randomises string hashing per process, so five identical runs
produced three different outputs. `PYTHONHASHSEED=0` is the workaround
([`current_state.py:647`](../../src/orchestrator/knowledge/current_state.py)).

**² Media ingestion** shipped in **3.10.0** (G3) — images via OCR, audio/video via ASR, both
through a committed `.spine-media/` artifact so no model runs in the deterministic build. Its
own roadmap said *"Not started"* for months after it shipped.

**³ Interactive exploration is delivered by delegation, not omission.** `pkg export` writes
GraphML, DOT, JSON and an Obsidian vault. G5 shipped in **3.11.0** and its Phase 3 — a built-in
renderer — was **deliberately dropped**: *"Gephi already does filtering, search, clustering and
click-through-to-source on our own export."*

**⁴ New in August 2026.** The four rows nobody else in the field fills. Every published
benchmark found in the code-intelligence category is an *efficiency* metric — "58–70% fewer
tool calls", "74% token savings", "~70% token reduction" — which answers *how cheap*, not *is
it right* or *is it worth anything*. Spine's numbers: **47/68 new modules integrated with the
graph, 3/68 without**, with an `edit`-ticket control at **122/124 either arm** ruling out a
generic more-context effect, replicated on a second unrelated codebase.

**⁵ No security verifier in the delivery path.** Semgrep lives in `evals/` only; the runtime
verifier chain is `base, chain, confidence, evidence, glossary, policy`. Security scanning
exists at CI level and via the `audit` persona, but not as a per-step verifier.

**⁶ Corrected twice — read the split, because the two halves differ.** First marked ❌ from a
G-scorecard entry reading *10%*, then ✅ once the code was read. An audit on 2026-08-17 shows
that ✅ was right for tenancy and **too generous for roles**, so they are now scored separately.

- **Multi-tenancy ✅.** `Principal(id, tenant_id, roles)` reaches **16 of 23 API route
  modules** via `PrincipalDep`, there are **23 `principal.tenant_id` scoping sites**, a
  cross-tenant read returns **404** rather than 403 (no id leakage), and `tenant_id` is a real
  column on four tables. That is isolation, not a label.
- **RBAC 🟡.** Identity and roles are modelled and plumbed everywhere, but the *role check*
  itself — `has_role` — is called at **exactly one site**: the approval decision in
  `approvals.py`. Every other route is authenticated and tenant-scoped, not role-gated.

Defensible as a design — the approval gate is the highest-risk action and the right place to
start — but a buyer asking "can I restrict who does X?" will find one X. Scoring it ✅ would be
the matrix overstating what they would actually find, which is what this document exists to
avoid.

**⁷ There is no SWE-bench number — none has been run.** ❌ here means *absent*, not *low*.
Spine's `9/10` and the 200-run rates are measured on **self-authored tickets against its own
repository**; SWE-bench Verified is 500 real GitHub issues graded by human-written
`FAIL_TO_PASS` tests. Quoting one beside the other would mislead. Declining the benchmark is a
position rather than an omission: the leaderboard is saturated at **95–96%**, and scaffold
choice moves the same model further than model choice does (one reported case: ~80% on a vendor
scaffold vs 61.5% top score on a standardised harness). See
[`codegen-benchmark-roadmap.md`](codegen-benchmark-roadmap.md), Phase 1, `Not started`.

---

## Summary

**11 rows where Spine stands alone.** Four are new, and they are the strongest: they are about
whether the product's central claim is *true*, not about what it can do.

**3 rows where Spine is ❌** — SWE-bench comparability, an in-path security verifier, and a
secrets vault; plus RBAC at 🟡 (see ⁶). Down from six earlier in the month: two were **my own errors** from stale status
docs (⁶), and one (interactive visualization) turned out to be a **deliberate design choice**
rather than a gap (³).

**Still genuinely behind:** language breadth (8 against 21–40) and adoption, by orders of
magnitude. Neither is disputed, and neither is what this matrix is for.
