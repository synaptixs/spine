# Capability matrix — Spine against the field

**Generated 2026-08-16 against 3.18.1; refreshed 2026-08-23; Spine's column re-read in full
against 3.59.0 source on 2026-10-15 (SSPN-189 / B90).** The competitor columns were *not*
re-checked in that pass and are as of August. Phases 1, 2a and 2b of the
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

| Capability | **Spine 3.60.1** | CodeGraph | Graphify | GitNexus | Serena | Joern | Sourcegraph | OpenHands | Devin |
|---|:--:|:--:|:--:|:--:|:--:|:--:|:--:|:--:|:--:|
| **LAYER A — CODE INTELLIGENCE** |
| Deterministic, no-LLM extraction | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ➖ | ➖ |
| Real parser, never regex | ✅ ¹⁰ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ➖ | ➖ |
| `file:line` provenance on every fact | ✅ ¹² | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ➖ | ➖ |
| **Published precision/recall of its own graph** | **✅** | ➖ | ➖ | ➖ | ➖ | ➖ | ➖ | ➖ | ➖ |
| **Invention detection (fabricated edges)** | **🟡 ¹³** | ➖ | ➖ | ➖ | ➖ | ➖ | ➖ | ➖ | ➖ |
| **Runtime oracle — recall from executing tests** | **🟡 py ¹³** | ➖ | ➖ | ➖ | ➖ | ➖ | ➖ | ➖ | ➖ |
| **Declared-vs-extracted parity check** | **✅** | ➖ | ➖ | ➖ | ➖ | ➖ | ➖ | ➖ | ➖ |
| **CI-checkable currency (`--check`)** | **✅** | ➖ | ➖ | ➖ | ➖ | ➖ | ➖ | ➖ | ➖ |
| **Accuracy regression gate in CI** | **✅** | ➖ | ➖ | ➖ | ➖ | ➖ | ➖ | ➖ | ➖ |
| Byte-stable report output | ✅ ¹ | ➖ | ➖ | ➖ | ➖ | ✅ | ➖ | n/a | n/a |
| Blast-radius / impact analysis | ✅ | ✅ | ✅ | ✅ | 🟡 | ✅ | 🟡 | ➖ | ➖ |
| Doc ingestion into the graph | ✅ | ➖ | ✅ | ➖ | ➖ | ➖ | ➖ | ➖ | ➖ |
| Media (OCR / ASR) ingestion | ✅ ² | ➖ | ✅ | ➖ | ➖ | ➖ | ➖ | ➖ | ➖ |
| SQL data-layer semantics | ✅ | ➖ | ✅ | ➖ | ➖ | 🟡 | ➖ | ➖ | ➖ |
| Interactive graph exploration | 🟡 ³ | ➖ | ✅ | ✅ | ➖ | 🟡 | ✅ | n/a | n/a |
| Cross-repo graph | ✅ ¹⁴ | ➖ | 🟡 | ✅ | ➖ | 🟡 | ✅ | ➖ | ➖ |
| Languages | **14** ¹⁵ | 21 | ~40 | TS | 40+ | multi | many | n/a | n/a |
| **LAYER B — DELIVERY** |
| Grounded code generation | ✅ | n/a | n/a | n/a | 🟡 | n/a | 🟡 | ✅ | ✅ |
| **Grounded in a deterministic graph** | **✅** | n/a | n/a | n/a | ➖ | n/a | ➖ | ➖ | ➖ |
| **Published causal contribution of the context layer (controlled A/B)** | **✅ ⁴** | ➖ | ➖ | ➖ | ➖ | ➖ | ➖ | ➖ | ➖ |
| **Replicated on an unrelated external codebase** | **✅ ⁴** | ➖ | ➖ | ➖ | ➖ | ➖ | ➖ | ➖ | ➖ |
| **Held-out grading (model never authors its grader)** | **✅ ⁴** | n/a | n/a | n/a | n/a | n/a | n/a | ➖ | ➖ |
| **Portable acceptance gate (baseline-diff)** | **✅ ⁴** | n/a | n/a | n/a | n/a | n/a | n/a | ➖ | ➖ |
| Test generation + refine loop | ✅ | n/a | n/a | n/a | ➖ | n/a | ➖ | ✅ | ✅ |
| **CI-parity preflight before PR** | **✅ ¹¹** | n/a | n/a | n/a | ➖ | n/a | ➖ | ➖ | ➖ |
| **Deterministic research artifact per ticket** (landing symbols + RCA + blast radius, no model) | **✅ ⁸** | ➖ | ➖ | ➖ | ➖ | ➖ | ➖ | ➖ | ➖ |
| **Acceptance criteria bound to `file:line`, or the ticket refused** | **✅ ⁸** | n/a | n/a | n/a | n/a | n/a | n/a | ➖ | ➖ |
| **Impact analysis keyed off where the ticket lands, not the plan's own guess** | **✅ ⁸** | ➖ | ➖ | ➖ | ➖ | ➖ | ➖ | ➖ | ➖ |
| **Deterministic validator on a model stage's output** | **✅ ⁸** | n/a | n/a | n/a | n/a | n/a | n/a | ➖ | ➖ |
| **A published measurement that *declined* a feature** | **✅ ⁸** | ➖ | ➖ | ➖ | ➖ | ➖ | ➖ | ➖ | ➖ |
| **Research pipeline shaped by issue type, as a file the repo can carry** | **✅ ⁸** | n/a | n/a | n/a | n/a | n/a | n/a | ➖ | ➖ |
| Opens real PRs | ✅ | n/a | n/a | n/a | ➖ | n/a | ➖ | ✅ | ✅ |
| Autonomous merge on green | ✅ ¹⁶ | n/a | n/a | n/a | ➖ | n/a | ➖ | ➖ | 🟡 |
| Responds to human PR review comments | ✅ | n/a | n/a | n/a | ➖ | n/a | ➖ | 🟡 | 🟡 |
| Published acceptance benchmark | ✅ 9/10 ¹⁸ | n/a | n/a | n/a | n/a | n/a | n/a | ✅ 72% | ✅ |
| **Comparable public benchmark (SWE-bench)** | **❌ ⁷** | n/a | n/a | n/a | n/a | n/a | n/a | ✅ | ✅ |
| **LAYER C — GOVERNANCE** |
| Explicit human approval gates | **✅** | n/a | n/a | n/a | n/a | n/a | n/a | 🟡 | 🟡 |
| Append-only audit log (DB-backed) | **✅** | ➖ | ➖ | ➖ | ➖ | ➖ | ➖ | ➖ | 🟡 |
| Per-run spend cap, enforced | **✅** | n/a | n/a | n/a | n/a | n/a | n/a | ➖ | 🟡 |
| Policy / evidence verifiers per step | **✅** | n/a | n/a | n/a | n/a | n/a | n/a | ➖ | ➖ |
| Security verifier in the delivery path | 🟡 ⁵ | n/a | n/a | n/a | n/a | ✅ | ➖ | ➖ | ➖ |
| Confidence-calibrated escalation | 🟡 ¹⁷ | n/a | n/a | n/a | n/a | n/a | n/a | ➖ | ➖ |
| OpenTelemetry tracing end-to-end | ✅ | ➖ | ➖ | ➖ | ➖ | ➖ | ➖ | ➖ | ➖ |
| **RBAC enforcement** | **🟡 ⁶** | n/a | n/a | n/a | n/a | n/a | ✅ | ➖ | ✅ |
| **Multi-tenancy (isolation, not a label)** | **✅ ⁶** | n/a | n/a | n/a | n/a | n/a | ✅ | ➖ | ✅ |
| Secrets vault beyond `.env` | ❌ ¹⁹ | n/a | n/a | n/a | n/a | n/a | ✅ | ➖ | ✅ |

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

**³ Interactive exploration is delivered by delegation, and that is a 🟡, not a ✅.** Re-read
2026-10-15: the product ships no interactive graph explorer. `pkg export` writes GraphML, DOT,
JSON, Cypher, SQLite and an Obsidian vault, and the operator UI's `/app/graph` is a *bounded
static overview* (tiles, bar charts and tables of the top modules and edges — `web/static/graph.js`
has no pan, zoom, filter or click-through). G5 shipped in **3.11.0** and its Phase 3 — a built-in
renderer — was **deliberately dropped**: *"Gephi already does filtering, search, clustering and
click-through-to-source on our own export."* Earlier versions of this matrix scored the row ✅
because the choice was deliberate; a deliberate choice is still not the capability, and the legend
reads "verified in source".

**⁴ New in August 2026.** The four rows nobody else in the field fills. Every published
benchmark found in the code-intelligence category is an *efficiency* metric — "58–70% fewer
tool calls", "74% token savings", "~70% token reduction" — which answers *how cheap*, not *is
it right* or *is it worth anything*. Spine's numbers: **47/68 new modules integrated with the
graph, 3/68 without**, with an `edit`-ticket control at **122/124 either arm** ruling out a
generic more-context effect, replicated on a second unrelated codebase.

**⁵ A security scan is in the autorun delivery path; it is a pattern scan, and the AST scanner is
not wired in.** Re-read 2026-10-15 (earlier text: ❌, "Semgrep lives in `evals/` only"). In
`sdlc autorun`, `reviewloop.review_and_fix` runs `codereview.verifiers.run_verifiers` over the
worktree diff *before* the PR is opened, with the default set `SecretsVerifier`, `SecurityVerifier`
and `StyleVerifier`; `BLOCKER` and `WARNING` findings go back to the fixer, bounded by the round
cap. `SecurityVerifier` is **six regexes over added lines** (`eval`/`exec`, `shell=True`, unsafe
`yaml.load`, `pickle.load`, `verify=False`, f-string SQL) on code-suffix files — not data-flow or
taint analysis. `codereview/semgrep.py` (`SemgrepVerifier`, AST-aware, behind the `security`
extra) exists but is not in `default_code_verifiers()` and nothing in `src/` constructs it. The
Temporal feature workflow does not call `run_verifiers`. The runtime agent verifier chain is still
`base, chain, confidence, evidence, glossary, policy`. The same default set runs in the GitHub-App PR reviewer (`codereview/reviewer.py`). Joern's
analysis remains well ahead; the row is 🟡 because something runs, not because it is comparable.

**⁶ Corrected twice — read the split, because the two halves differ.** First marked ❌ from a
G-scorecard entry reading *10%*, then ✅ once the code was read. An audit on 2026-08-17 shows
that ✅ was right for tenancy and **too generous for roles**, so they are now scored separately.

- **Multi-tenancy ✅.** `Principal(id, tenant_id, roles)` reaches **16 of 23 API route
  modules** via `PrincipalDep`, there are **25 `principal.tenant_id` scoping sites** in nine
  modules, a cross-tenant read returns **404** rather than 403 (no id leakage), and `tenant_id` is
  a real column on **three of the seven registry tables** (`approval_requests`, `audit_log`,
  `agent_memory`). The other four — `agent_templates`, `tool_contracts`, `glossary_entries`,
  `calibration_history` — are shared catalogue data with no tenant column, which the
  secrets-and-identity spec lists as Phase 4 ("the untenanted tables"). The earlier text said four
  tables carried the column and 16 of 23 route modules; re-counted 2026-10-15 it is three tables
  and 16 of 23 modules (17 files name `PrincipalDep`, one of which defines it). That is isolation of
  runs, approvals, audit and memory, not of the shared catalogue.
- **RBAC 🟡.** Identity and roles are modelled and plumbed everywhere, but the *role check*
  itself — `has_role` — is called at **exactly one site**: the approval decision in
  `approvals.py`. Every other route is authenticated and tenant-scoped, not role-gated.

Defensible as a design — the approval gate is the highest-risk action and the right place to
start — but a buyer asking "can I restrict who does X?" will find one X. Scoring it ✅ would be
the matrix overstating what they would actually find, which is what this document exists to
avoid.

**⁷ There is no SWE-bench *score*; a pilot was run and failed its own go/no-go.** ❌ here means
*no number Spine can quote*, not *low* and — corrected 2026-10-15 — not *never run*. The earlier
text said none had been run. Between 2026-10-11 and 2026-10-13 the harness
([`codegen-benchmark-roadmap.md`](codegen-benchmark-roadmap.md), SSPN-135) ran three 20-instance
pilots: patch-or-park on **12, 12 and 7 of 20** against a pre-registered bar of 16, so the
programme was stopped on its own decision rule (D9) and no full run was made. Resolved counts
(1, 5 and 1 of 20) are n = 20 each, one run each, and are **not** a result on their own. Spine's
`9/10` and the 200-run rates are measured on **self-authored tickets against its own
repository**; SWE-bench Verified is 500 real GitHub issues graded by human-written
`FAIL_TO_PASS` tests, so quoting one beside the other would mislead. The leaderboard is saturated
at **95–96%**, and scaffold choice moves the same model further than model choice does (one
reported case: ~80% on a vendor scaffold vs 61.5% top score on a standardised harness). The
row stays ❌. (`docs/evals/swebench-harness.md` and `codegen-benchmark-roadmap.md` carried the
same stale "no number" sentence and were corrected in the same pass — SSPN-189.)

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

**¹⁰ "Never regex" is several cases too strong, and they are named rather than defended.** All
fourteen front-ends parse with a real parser (tree-sitter, stdlib `ast` or sqlglot) and read
structure off the tree. Regex survives in these places, re-counted 2026-10-15 (the earlier text
said three): `java`, `csharp`, `kotlin` and `php` recover the one-line `package`/`namespace`
declaration to *name* a module the parser already found; `razor` recognises `@`-directives and
`@code` blocks line by line to rewrite a `.razor` file into synthetic C# that the C# parser then
reads (nothing is emitted from the regex alone); `sql` fingerprints the dialect with regexes,
strips a `GO` batch separator, and recovers `CALL`/`PERFORM proc()` — which sqlglot collapses
into opaque `Command` nodes — as **real `CALLS` edges**. Only that last one emits a fact from a
pattern, and its corpus evidence is **one labelled call edge**. The row stays ✅ because the claim
it is scored against is "structure is parsed, not pattern-matched", which holds; the asterisk is
here so the cell is not quoted as more absolute than the source supports. Detail:
[`STATE-OF-SPINE.md`](STATE-OF-SPINE.md) §3 and
[`parsing-and-the-pkg.md`](parsing-and-the-pkg.md).

**¹¹ True of the Temporal workflow path and the benchmark; not of `sdlc autorun`.** Measured
2026-10-11 on 3.57.0, while planning the SWE-bench programme (SSPN-135); line references
re-located 2026-10-15 on 3.59.0, where the earlier `feature_runner.py` range had drifted. `SubprocessPreflightRunner`
— `ruff check`, `ruff format --check` and `mypy` over the worktree, with baseline-diff — runs in the
test → preflight → refine loop of the Temporal feature workflow (`sdlc/workflows.py:153-154,389-424`;
built at `sdlc/worker.py:212`) and in `scripts/codegen_benchmark.py`. The imperative `sdlc autorun`
path (`sdlc/feature_runner.py`) does not call it: its refine loop is budgeted over tests, a
changed-lines `mypy` check, a per-file coverage probe and the repository's required-behavior
manifest (the per-kind budgets at `feature_runner.py:1290-1393`), and `ruff --fix` / `ruff format` run only as a best-effort
step on the files it created (or any file it writes, when the repository configures ruff;
`apply_files` in `codegen.py:2309`, `_ruff_fix` at `:2582`). The row stays ✅ because the gate exists and is in
production on the path the green end-to-end run used; the asterisk keeps it from being quoted as
covering every entry point.

**¹² "Every fact" means every first-party fact.** `Node.provenance` and `Edge.provenance` are
optional in `facts.py`: external placeholders (an imported third-party symbol, a builtin) are
marked `external=True` and carry none, by design. On this repository's committed scoreboard
16,237 of 16,237 resolved facts are anchored to a `file:line` and 968 externals are excluded from
that count. The row stays ✅ for the claim it is scored against — nothing the graph asserts about
your own code is unlocated — but "every fact" should not be quoted to include `external` nodes.

**¹³ Invention detection finds one class, in more front-ends than "py"; the runtime oracle is still Python-only.**
Re-read 2026-10-15 (the earlier cell read "🟡 py"; STATE-OF-SPINE had already recorded the widening
on 2026-08-24). The detector flags a `CALLS` edge whose target name is *bound inside the calling
function* — a call through a parameter or local mislabelled as a call to a module-level symbol.
It runs on Python (stdlib `ast`) and, through `pkg/scope.py` `WALKERS`, on TypeScript,
JavaScript, Go, Rust, C#, C, C++ and Kotlin. Java, SQL, PHP and Perl are *excluded with a stated
reason* (a local cannot shadow a bare call there), which `status` reports as not-applicable
rather than clean; Gradle emits no `CALLS`. Other invention classes — `CONSUMES` matched on
verb and path, `EXPOSES` composed from mount prefixes — have no detector, which is why the
cell is 🟡 and not ✅. The runtime oracle traces the repository's own test suite with
`sys.monitoring` and maps frames to `py:` ids; on a non-Python repo it reports nothing, and that is
*not measured*, not *clean*.

**¹⁴ Cross-repo graph — the earlier ➖ was wrong, and ➖ was the wrong symbol for Spine anyway**
(➖ means "not found in public docs"; for Spine absent is ❌). Spine ships declared multi-repo
joins: `.spine/repos.yaml` lists repositories and `joins:` of kind `http`, `data` or `package`;
`pkg/join_link.py` `link_joins` adds `CONSUMES` edges that cross repository boundaries (exact
match, then template match, and **nothing** when two provider endpoints are ambiguous);
`orchestrator pkg joins --propose` drafts the block from evidence and `--check` counts the calls
no declared join could place; `investigate --repos` reads the merged graph. The 3.59.0 changelog
records a fix to package joins over re-exports (N18). Two limits belong next to the ✅: the
**topology is declared, not discovered** (a forgotten `joins:` entry produces a visibly narrower
graph, which `--check` reports), and the matcher is precision-first, so its own docstring says
recall is deliberately well under 1.00. I did not find a published recall figure for joins in this
pass.

**¹⁵ Languages: 14 front-ends — 13 languages plus a Gradle reader.** `FRONT_ENDS` in
`pkg/capabilities.py` (also `orchestrator pkg capabilities`) lists Python, Java, TypeScript,
JavaScript, C#, C, C++, Go, Rust, PHP, Perl, Kotlin, SQL and Gradle, which turns `.kts` build
scripts into module facts only. STATE-OF-SPINE §2 counts it the same way (13 languages, 14
front-ends). The cell was 8 until this pass. Counts are not like-for-like across the row:
competitor figures are what each vendor's public docs call a "language", and front-ends differ in
depth (the capabilities table shows which node and edge kinds each can emit). Every non-Python
front-end installs as an extra.

**¹⁶ Merge-on-green is a property of the Temporal feature workflow.** The `sdlc_merge_prs` activity
runs after the human `approve merge` gate, merges only the gate-approved PRs, and is
all-or-nothing. `sdlc autorun` contains no merge call: it stops at an open pull request. The
row stays ✅ for the same reason as ¹¹.

**¹⁷ Escalation is real, deterministic and annotate-only — not confidence-calibrated.** Re-read
2026-10-15 (earlier ✅). `sdlc/escalation.py` weighs three signals: the semantic judge could not
confirm a criterion, refinement took more than 3 test cycles, and a changed symbol has more than
10 cross-file callers (computed over changed `.py` files only). Its own docstring calls the
thresholds "v0 … static"; nothing tunes them from calibration history. The effect in the Temporal
workflow is an audit event and an annotation for the merge approver — it **never blocks**.
`sdlc autorun` parks runs on a file-backed approval (`sdlc/escalate.py`) but does not consult
`EscalationPolicy`. In the runtime agent verifier chain a low-confidence result terminates or
continues with a warning; the `escalate_to_human` action reduces to *continue*, with the intent
written to the audit log (`failure_dispatch._NOT_YET_IMPLEMENTED`).

**¹⁸ The 9/10 is a dated measurement, not re-run for this refresh.** Measured 2026-08 on 3.21.0
(edit 5/5, create 4/5, $0.68 total) and recorded in
[`competitive-landscape.md`](competitive-landscape.md); acceptance means tests pass **and** the
`ruff`/format/`mypy --strict` preflight passes **and** the change fits. Re-running it spends money
and was not part of a documentation refresh. Same caveat as ⁷: self-authored tickets, Spine's own
repository.

**¹⁹ The secrets seam shipped; a vault did not.** `core/secrets.py` (`get_secret`,
`ORCHESTRATOR_SECRETS_PROVIDER`, `register_provider`) lets a provider be registered behind an
extra, with tests, but the only provider in the tree is `env`, and there is no `[vault]` extra. The
HashiCorp Vault / OpenBao client is Phase 3 of
[`secrets-vault-and-identity.md`](secrets-vault-and-identity.md), held until an operator wants
direct fetch. Injection through the environment already works. The row stays ❌.

---

## Summary

**21 rows where Spine stands alone**, out of 46. Six arrived with the GraphIR programme (⁸). Of the rest, four are the strongest: they are about whether the
product's central claim is *true*, not about what it can do.

> **The count is derived, not maintained.** `python scripts/matrix-count.py` reads this table and
> prints the numbers in this section; CI fails if they drift from the prose. It exists because
> this section read **16** and [`STATE-OF-SPINE.md`](STATE-OF-SPINE.md) read **11** for the same
> table on the same day, and both were wrong — the exact failure `evals/agent_corpus.py` was
> built for, reproduced in the document that warns about it.

**2 rows where Spine is ❌** — SWE-bench comparability (⁷) and a secrets vault (¹⁹); plus six at
🟡: invention detection and the runtime oracle (Python-only or one class, ¹³), interactive
exploration (³), a security verifier in the delivery path (⁵), escalation (¹⁷) and RBAC (⁶).
The 2026-10-15 re-read moved three cells: security verifier ❌→🟡 (something runs; it is a
pattern scan), and interactive exploration and escalation ✅→🟡 (the legend says "verified in
source", and the source shows less than the label). The August summary said three ❌ and three
🟡. Two earlier corrections stand: RBAC and tenancy were re-scored from stale status docs (⁶),
and interactive visualization was first read as a deliberate design choice rather than a gap (³);
the choice is real, but it is not the capability.

**Still genuinely behind:** adoption, by orders of magnitude, and language breadth against the
largest graph-only tools (14 front-ends against ~21–40 — see ¹⁵ for what is and is not
comparable). Neither is disputed, and neither is what this matrix is for.
