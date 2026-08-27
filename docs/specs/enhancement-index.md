# Planned enhancements — directory

**Date:** 2026-08-25 · spine 3.22.0 (released; `main` and `develop` in sync, on PyPI)

**What this page is.** The enhancements decided or discussed in the 2026-08-24/25 working
session, in one place, with an honest status on each. Several are **conversation, not spec** —
that is marked, because the difference matters when someone picks one up.

**What it is not, and this matters.** It is **not** a third backlog. Two lists already exist and
this page does not restate either:

- [`STATE-OF-SPINE` §8](STATE-OF-SPINE.md) — *"Outstanding, everything else"*: the standing list
  of open items across the whole product. **That is the authority.** Anything here that
  survives review belongs there too.
- [`gap-roadmap-index`](gap-roadmap-index.md) — the competitive-gap programme (G4, G6, CB, KL).

If this page and either of those disagree, **they win** — they are maintained at release
cadence; this one is a snapshot of a conversation.

---

## The index

| # | Enhancement | State | Size | What gates it |
|---|---|---|---|---|
| **E1** | [Project constitution](#e1--project-constitution) | **Spec written, needs rewrite** — [`constitution-roadmap.md`](constitution-roadmap.md), branch `docs/constitution-spec` | Unknown until Phase 0 | A blocking trigger probe that may close it unbuilt |
| **E2** | [Multi-repo comprehension](#e2--multi-repo-comprehension) | ✅ **Complete — all four phases** — [`multi-repo-roadmap.md`](multi-repo-roadmap.md), branch `feat/multi-repo-identity` | — | Unmeasured on real repos; delivery still a non-goal |
| **E3** | [G6 — comprehension benchmark](#e3--g6--comprehension-benchmark) | **Spec exists, not started.** Branch `feat/g6-comprehension-metrics` cut, empty | ~3 days if descoped | **Four open decisions** — corpus scope, metric set, repo mix, gate tier |
| **E4** | [Per-run caching: temperature + MCP](#e4--per-run-caching-temperature-refusal-and-mcp-sessions) | **Observed in the field.** No ticket | Small | Nothing |
| **E5** | [Oracle coverage gaps](#e5--oracle-coverage-gaps) | **Named in `STATE-OF-SPINE` §3** | Medium | Nothing |
| **E6** | [Stale `## Unreleased` in the changelog](#e6--stale-unreleased-heading) | **Known defect** | Trivial | Someone who knows which release those entries shipped in |

**Branch state.** The four documentation branches were consolidated into `docs/session-specs`
on 2026-08-25 — they each added a row to `SPEC-INDEX.md` and conflicted pairwise, which is the
predictable cost of one branch per document. `feat/g6-comprehension-metrics` is separate and
empty: E3 awaits the four decisions below.

---

## E1 — Project constitution

**Spine helps a project build *its own* constitution from grounded sources.** Not a Spine
constitution, and not a hand-written YAML file — the framing changed twice during the session
and the current spec has not caught up.

Spec-kit ships `/speckit.constitution` as prompt text, where the only thing checking the model
honoured a principle is the same model. Spine has a graph, so a well-chosen principle can be a
**deterministic query** instead of a hope.

**Build sources, by how grounded they are:**

| Tier | Source | Normative | Derivation risk |
|---|---|---|---|
| 1 | Enforcement config already in the repo — `import-linter` contracts, ArchUnit tests, eslint boundaries, `CODEOWNERS`, pre-commit, CI gates | Yes, already ratified | **None** — machine-readable, someone said yes to it |
| 2 | Architecture docs and ADRs | Yes, stated intent | Model reads prose; output falsifiable against the graph |
| 3 | The PKG | **No — descriptive** | None, but it can freeze accidents into policy |
| 4 | Git history — reverts, boundary crossings undone | Revealed policy | Medium |

**Tier 1 is the headline.** A team with `import-linter` contracts has already written their
constitution; Spine should read it rather than ask them to restate it. Nothing in the codebase
reads any of these today — [`doc_source.py`](../../src/orchestrator/pkg/doc_source.py) ingests
markdown/rst/txt/PDF through a **reader registry** (`register_reader`), which is the seam.

**Ground before proposing.** Every candidate compiles to a query and is *run* before it is
offered, so it arrives with *does it hold, how many violations, where*. That is what removes the
Phase 4 risk structurally rather than by discipline.

**Amendment is human-ratified, pattern-triggered, and can say no.** Five outcomes — narrow,
relax, strengthen, retire, **reaffirm**. Reaffirm is the load-bearing one: without it the process
only ratchets one way and accumulated violations argue themselves into legitimacy. Distinguishing
*"the rule is wrong"* from *"the team was undisciplined"* is deterministic from git and the
graph — many authors over a long window and a wide module spread says the rule is misplaced; one
author, short window, hotfix messages says the code is.

**Drift, four kinds:**

| Drift | Detection |
|---|---|
| Code drifts from the rule | Run the rule — the ordinary case |
| Docs drift from the code | **Already exists** — `GroundingVerifier.stale_findings`; the rule becomes *suspect*, not *violated* |
| Rule drifts from reality | The pattern analysis above |
| **Constitution drifts from its sources** | A currency gate modelled on `understand --check`: each rule records its source's content hash, and `constitution --check` marks it **unverified** when the source moves |

Status per rule is `verified` / `unverified` / `unratified` — the same three-state discipline the
invention oracle adopted, for the same reason: "0 violations" means nothing unless someone
checked.

**The honest problem:** tier 2 needs a model to read prose, and no deterministic validator can
say *"this rule faithfully represents that paragraph."* You can check it compiles and what it
does, not that it means what the author meant. Mitigation is human ratification with the source
paragraph in view — but the artifact must show which rules are *grounded* and which are
*grounded and interpreted*.

> **The rewrite this spec needs:** derivation as Phase 1, the amendment ledger as a first-class
> artifact, thresholds calibrated from Phase 0 data rather than chosen, and Phase 0 reduced to
> running the derivation here and on two external repos.

**Phase 0 already partly ran, and the result is not encouraging.** Three of `CLAUDE.md`'s eight
invariants compile to PKG queries; the determinism one works (210 modules reachable from
`understand`/`state`, **0** LLM modules among them). All three have fired **zero** times across
this project's entire history. That is a fact about *this* repo — the people who wrote the
invariants were never going to break them — but *"it will fire on customer repos"* is exactly
the untested assumption that let Phase 4 ship. Probe it elsewhere before building.

## E2 — Multi-repo comprehension

**Not supported today, by construction.** `RepoCodeExtractor.extract(root)` takes one root; the
cache is keyed to one repo's HEAD SHA on a clean tree; `WorkspaceManager` clones one repo;
`autorun` ends in one PR.

The blocking issue is **identity**: node ids are language-prefixed, not repo-prefixed, so
`py:shop.cart.Cart` in two repos is the same id. Merging graphs today produces silent collisions
— internally consistent, externally false.

**Most of the vocabulary is already there.** `EXPOSES`, `CONSUMES`, `SERVES`, `READS`, `WRITES`
are the cross-service edge kinds and they exist. What is missing is the joining pass: A
`CONSUMES` a `(verb, path)` that B `EXPOSES`; both `WRITE` the same table; B publishes the
package A `IMPORTS`. Three deterministic joins, **no new edge kinds**.

**But that join is resolution, not structure** — same class as `CALLS`, and it will be the
weakest cell. Path templating, versioned prefixes and gateways make it a judgement. Score it on
its own, precision-first, skip rather than guess.

**Determinism survives:** the cache key becomes a tuple of SHAs. Same commits in, same bytes out.

**Split it.** Ship the read-only half first — merged graph for comprehension, blast radius and
grounding, **no multi-repo delivery**. That is most of the value and avoids the atomicity
problem entirely. N PRs that must land together is ordering and rollback, not a graph problem,
and preflight has to run per repo with that repo's toolchain. Do not build the pipeline on an
unmeasured join.

**Spec written 2026-08-25:** [`multi-repo-roadmap.md`](multi-repo-roadmap.md).

Writing it surfaced a hazard that reshaped the design, recorded here because it looks like a
one-line improvement. The obvious way to address a fact across repos is to make
`Provenance.__str__` return `repo:file:line` — but **three production call sites parse that
string back** with `split(":", 1)[0]` to recover a file path (`design.py:130`,
`autorun.py:788`, `builddoc.py:953/1163`). All three would silently return the **repo name**
where a path is expected: no exception, a blast radius computed against a path that does not
exist, and a build document listing it as a landing file. Two further sites parse node ids the
same way for area grouping.

**Phase 1 completed 2026-08-25** — decision C taken, scoping applied **at merge time, not
extract time**, so single-repo extraction is byte-identical (proved by SHA-256 over the full
node and edge set on leveldb, gin and zod, extracted by `develop` and by the branch).

**Phase 2 completed the same day.** Repositories are **declared** in `.spine/repos.yaml`, never
derived — a key baked into every scoped id and cache entry must be identical on a laptop and in
CI, and nothing derivable is. Caches stay **per repo, merged on read**, so changing one of four
re-extracts one and reuses three. `MergedFacts` carries a per-repo sha/dirty/cached state,
because a merged graph looks identical whether or not one input is dirty — and one dirty repo
makes the whole graph unreproducible. `pkg extract --repos` says so in as many words.

Phase 2's original exit criterion had to be corrected: it asked for blast radius crossing a
repository boundary, which cannot happen before the Phase 3 joiners create any cross-repo edges.
Moved to Phase 3.

**Phase 3a completed the same day — the HTTP joiner, so a merged graph now carries edges that
cross a repository.** Topology is declared in `.spine/repos.yaml` and *proposed* from evidence
by `pkg joins --propose`, because the join set is itself a small graph and nobody should author
one by hand. Corpus: **precision 1.00, recall 0.67** — the missing third is a path built from an
f-string, which the extractor never collects as a call at all, labelled as a known gap rather
than hidden. `--check` reports what went unplaced *and per declared join*, so a stale join reads
`** placed nothing **` instead of disappearing into a healthy total.

**Phase 3b completed the same day — the data and package joiners.** Both *repoint and rebuild*
rather than adding edges, which turned out to be the design question rather than the matching:
every edge into a collapsed node must be **dropped** (the node is going, so anything else
dangles), and `CALLS` must move with `IMPORTS` or removing a placeholder silently destroys a
real call edge. Each case carries a control — a table only one repo has, an import nobody
declares — because a joiner that collapsed everything would pass the positive test and destroy
them. Recall **1.00** on both, against 0.67 for HTTP, which is the expected shape: a table name
is a string and an import is a declaration.

Three things the spec did not predict, all found by building it: the side-channel **did not
survive the cache** (a warm hit skips the extractor, so the joiner saw no candidates — fixed
with a sidecar, deliberately not a key in the fact cache); the corpus format assumed **one
fixture per case**; and `language` was doing **two jobs** — naming what a case measures and
which front-end must be installed, which only differ for a cross-repo case.

Phase 1 also re-measured the blast radius and the first draft was wrong: **six provenance parse
sites, not three, and fourteen id sites, not two.** The two missed were `evidence.py:73` and
`criteria_binding.py:224` — the Evidence artifact's file accessor, and the code that decides
whether an acceptance criterion is bound. A criterion binding against a repo name binds against
nothing and *passes*, which is the guarantee `ticket-to-landing-sites.md` §7 describes. Pinned
by `tests/pkg/test_identity_contract.py`, proved by breaking `__str__` and watching two guards
fail.

`tri-repo-integration.md` is **not** this — that is three products interoperating through a
shared ontology key.

## E3 — G6 — comprehension benchmark

Spec exists and is unchanged; what moved is the argument for it.

Everything gated today answers *"is the graph correct?"* on fixtures we wrote —
`scoreboard.json` has exactly three keys: `corpus`, `invention`, `parity`. Nothing answers
*"does Spine give the right answer to a real engineering question on a real repository?"*

**The value is not a better claim — it is a cheaper one.** The grounding A/B (47/68 vs 3/68,
with a control) is a *stronger* result and always will be. But it costs ~$50 and 200 runs, and
cannot run in CI. G6 is deterministic and free, so it can **gate**. You currently have no way to
notice `investigate` getting worse.

This week made that concrete: four front-ends were fabricating edges while `pkg verify`, corpus
precision and the invention oracle all reported clean, because they were all looking at the same
altitude. **G6 is a different altitude.**

**Descope it.** The spec leans *"both — a curated gold set of ~50 for headline numbers, mined PRs
for volume."* Do the gold set only for v1, 30–50 hand-labelled issues, and drop the PR mining:
the spec already admits mined PRs are a dirty denominator, and a small hand-labelled set is the
discipline that has now worked twice in this repo. Collapses Phase 1 from ~5–7 days to ~3.

Directly relevant to [`ticket-to-landing-sites.md`](ticket-to-landing-sites.md): how often the
true fix site lands in the top *k* is **unmeasured**, and a `0` there would not distinguish *bad*
from *never measured*.

**Four decisions block the work, all recommended, none taken:**

| | Decision | Recommendation |
|---|---|---|
| D1 | Gold set only, or also mined PRs? | **Gold set only** — 30–50 hand-labelled. The spec admits mined PRs are a dirty denominator, so a bad number could not be attributed. Cuts Phase 1 to ~3 days |
| D2 | Which of the five metrics ship in v1? | **Two** — top-k localization and provenance validity. The first is the claim that matters; the second is nearly free (`stale_findings`). Impact recall depends on D1; fault-site top-1 needs a traceback corpus that does not exist |
| D3 | Which repos? | **Reuse the eleven pinned for the invention work** — already SHA-pinned, already exercised against 3.22.0, and the language mix is right (the spec is explicit the corpus must not be Python-heavy) |
| D4 | Gate tier? | **Ratchet** — but not for the spec's stated reason. A SHA-pinned corpus does not churn; the real reason is that this is a *ratio*, not a defect count, so unlike `invention` there is no correct value to hold at zero |

**One stale line in the spec:** open question 3 asks whether to publish before or after G3/G5
land. Both landed — 3.10.0 and 3.11.0 — so it resolves to *publish now* and should be struck.

## E4 — Per-run caching: temperature refusal and MCP sessions

Two findings from a real `sdlc plan --source jira://…` run. Same shape: a **per-call cost that
should be per-session**. Neither breaks correctness.

1. **The temperature refusal is never remembered.**
   [`litellm_client.py:226`](../../src/orchestrator/core/llm/litellm_client.py) pops
   `temperature` from a **local** `params` dict, so every call to a model that rejects
   `temperature=0.0` fails once, warns, and retries. Two calls → two wasted round-trips and two
   warnings. A per-model refusal cache halves the request count on those models.
2. **The MCP server is spawned per operation.**
   [`mcp/client.py:53`](../../src/orchestrator/mcp/client.py) `_session()` is an async context
   manager that starts a fresh `stdio_client` and tears it down on exit. No pooling, so
   `list_tools` + `call_tool` = two process spawns and two full MCP startups.

**Not in scope:** the determinism warning itself is correct and should stay. A model that refuses
`temperature=0.0` makes intake non-deterministic, and saying so is the system working.

## E5 — Oracle coverage gaps

Recorded in [`STATE-OF-SPINE` §3](STATE-OF-SPINE.md) after the 3.22.0 work; listed here so they
are not lost.

- **`runtime` is still Python-only** (PEP 669) — now the *only* oracle with that limit, since
  `invention` was widened to six front-ends. On a non-Python repo it reports nothing, and that
  is *not measured*, not clean.
- **The invention oracle only knows about shadowing.** Other classes — `CONSUMES` matched on
  `(verb, path)`, `EXPOSES` composed from mount prefixes, ORM `REFERENCES` guessing a class name
  — have **no detector at all** and rest on `sample_edges` plus a human reading source. A new
  invention class needs a fixture as well as a detector.

## E6 — Stale `## Unreleased` heading

`CHANGELOG.md:957` carries a second `## Unreleased` between the 3.14-era entries and
`## 3.13.0`. Pre-existing, cosmetic, and it needs someone who knows which release those entries
actually shipped in — which is why it was flagged during the 3.22.0 cut rather than guessed at.

---

## Declined, recorded so it does not come back

**spec-kit** — evaluated 2026-08-25 and declined:
[`spec-kit-integration-analysis.md`](spec-kit-integration-analysis.md) (branch
`docs/spec-kit-declined`). LLM-driven end to end with no validator on any output edge, nothing
deterministic, and it never reads the codebase it is about to change. **Revisit condition:** it
ships a step that reads the target codebase deterministically. More templates, agents or
adoption do not qualify.

E1 is the one idea taken from it.

## If you are sequencing these

Not a queue, but the argument as it stands:

1. **E4** — small, real, and found in the field. Cheap to clear.
2. **E3 (descoped)** — the only one that adds a *standing check* where none exists, and this
   week showed what a missing check costs.
3. **E1 Phase 0** — a probe, not a build. It is allowed to close the spec, and that is a
   successful outcome.
4. **E5**, then **E2**, which needs a spec before it needs a decision.

**E6** whenever someone with the history is passing.
