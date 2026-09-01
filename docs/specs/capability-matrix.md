# Capability matrix — Spine against the field

**Generated 2026-08-16 against 3.18.1; refreshed 2026-08-23.** Phases 1, 2a and 2b of the
GraphIR programme shipped in **3.20.0** and Phase 3 in **3.21.0** — both marked ⁸. **Phase 4
delivered nothing** and adds no row here: both halves were measured and declined
([`graphir-sdlc-workflow.md`](graphir-sdlc-workflow.md)).

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

| Capability | **Spine 3.27.0** | CodeGraph | Graphify | GitNexus | Serena | Joern | Sourcegraph | OpenHands | Devin |
|---|:--:|:--:|:--:|:--:|:--:|:--:|:--:|:--:|:--:|
| **LAYER A — CODE INTELLIGENCE** |
| Deterministic, no-LLM extraction | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ➖ | ➖ |
| Real parser, never regex | ✅ ¹⁰ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ➖ | ➖ |
| `file:line` provenance on every fact | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ➖ | ➖ |
| **Published precision/recall of its own graph** | **✅** | ➖ | ➖ | ➖ | ➖ | ➖ | ➖ | ➖ | ➖ |
| **Invention detection (fabricated edges)** | **🟡 py** | ➖ | ➖ | ➖ | ➖ | ➖ | ➖ | ➖ | ➖ |
| **Runtime oracle — recall from executing tests** | **🟡 py** | ➖ | ➖ | ➖ | ➖ | ➖ | ➖ | ➖ | ➖ |
| **Declared-vs-extracted parity check** | **✅** | ➖ | ➖ | ➖ | ➖ | ➖ | ➖ | ➖ | ➖ |
| **CI-checkable currency (`--check`)** | **✅** | ➖ | ➖ | ➖ | ➖ | ➖ | ➖ | ➖ | ➖ |
| **Accuracy regression gate in CI** | **✅** | ➖ | ➖ | ➖ | ➖ | ➖ | ➖ | ➖ | ➖ |
| Byte-stable report output | ✅ ¹ | ➖ | ➖ | ➖ | ➖ | ✅ | ➖ | n/a | n/a |
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
| **Deterministic research artifact per ticket** (landing symbols + RCA + blast radius, no model) | **✅ ⁸** | ➖ | ➖ | ➖ | ➖ | ➖ | ➖ | ➖ | ➖ |
| **Acceptance criteria bound to `file:line`, or the ticket refused** | **✅ ⁸** | n/a | n/a | n/a | n/a | n/a | n/a | ➖ | ➖ |
| **Impact analysis keyed off where the ticket lands, not the plan's own guess** | **✅ ⁸** | ➖ | ➖ | ➖ | ➖ | ➖ | ➖ | ➖ | ➖ |
| **Deterministic validator on a model stage's output** | **✅ ⁸** | n/a | n/a | n/a | n/a | n/a | n/a | ➖ | ➖ |
| **A published measurement that *declined* a feature** | **✅ ⁸** | ➖ | ➖ | ➖ | ➖ | ➖ | ➖ | ➖ | ➖ |
| **Research pipeline shaped by issue type, as a file the repo can carry** | **✅ ⁸** | n/a | n/a | n/a | n/a | n/a | n/a | ➖ | ➖ |
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

**¹ Byte-stable output — fixed in 3.19.0.** Both `understand` and `state` are byte-identical
run to run. `state` was not, until 3.19.0: its area list sorted out of a `set` on score alone,
and Python randomises string hashing per process, so five identical runs produced three
different outputs. The sort key is now total, and a regression test renders under five
`PYTHONHASHSEED` values in subprocesses — the only way to see the bug, since the seed is fixed
for the life of a process.

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

**⁸ Shipped, in two releases** — [`graphir-sdlc-workflow.md`](graphir-sdlc-workflow.md).
Phases 1, 2a and 2b in **3.20.0** (2026-08-18/19); **Phase 3 in 3.21.0** (2026-08-19), which is
the issue-type row: three profiles ship, the ticket's issue type chooses one by **deterministic
lookup rather than a model** — a model choosing would make the Evidence unreproducible at a
commit — and a repo may carry its own in `.spine/workflows/`, where the same name wins. These
were marked unreleased while they sat on `develop`; the marker stays only to say when they
arrived.

**The *declined a feature* row is the odd one, and it is deliberate.** It now has **two
instances**, which is what turns a one-off into a method:

1. Spine ran a 100-run controlled A/B on whether its design stage should call a model, found no
   gain it could resolve and a held-out rate that went the wrong way, and **published the result
   and did not ship the feature**
   ([`design-promotion-ab-results.md`](design-promotion-ab-results.md)).
2. Phase 4 proposed concurrent pipeline nodes and a bounded replan. Timing showed the fan-out
   was worth **~30ms**; the replan was built, then **reverted when the trigger was probed and
   found unreachable** — `validate_design` refused 0 of 6 real specs, because a deterministic
   design cannot fabricate. **The phase shipped neither half and the record says so**
   ([`graphir-sdlc-workflow.md`](graphir-sdlc-workflow.md), Phase 4).

No competitor in this category was found publishing a measurement that declined their own
capability. It is a claim about method rather than function, which is why it sits last.

**¹⁰ "Never regex" is three cases too strong, and they are named rather than defended.** All
eight front-ends parse with a real parser and read structure off the tree. Three narrow regex
fallbacks exist: `java`/`csharp` recover the one-line `package`/`namespace` declaration to *name*
a module the parser already found, and `sql` recovers `CALL`/`PERFORM proc()` — which sqlglot
collapses into opaque `Command` nodes — as **real `CALLS` edges**. Only the third emits facts,
and its corpus evidence is **one labelled call edge**. The row stays ✅ because the claim it is
scored against is "structure is parsed, not pattern-matched", which holds; the asterisk is here
so the cell is not quoted as more absolute than the source supports. Detail:
[`STATE-OF-SPINE.md`](STATE-OF-SPINE.md) §3 and
[`parsing-and-the-pkg.md`](parsing-and-the-pkg.md).

---

## Summary

**21 rows where Spine stands alone**, out of 46. Six arrived with the GraphIR programme (⁸). Of the rest, four are the strongest: they are about whether the
product's central claim is *true*, not about what it can do.

> **The count is derived, not maintained.** `python scripts/matrix-count.py` reads this table and
> prints the numbers in this section; CI fails if they drift from the prose. It exists because
> this section read **16** and [`STATE-OF-SPINE.md`](STATE-OF-SPINE.md) read **11** for the same
> table on the same day, and both were wrong — the exact failure `evals/agent_corpus.py` was
> built for, reproduced in the document that warns about it.

**3 rows where Spine is ❌** — SWE-bench comparability, an in-path security verifier, and a
secrets vault; plus three at 🟡 (invention detection and the runtime oracle are Python-only;
RBAC, see ⁶). Down from six earlier in the month: two were **my own errors** from stale status
docs (⁶), and one (interactive visualization) turned out to be a **deliberate design choice**
rather than a gap (³).

**Still genuinely behind:** language breadth (8 against 21–40) and adoption, by orders of
magnitude. Neither is disputed, and neither is what this matrix is for.
