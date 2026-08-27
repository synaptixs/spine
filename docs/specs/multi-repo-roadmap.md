# Multi-repo comprehension — one graph across several repositories

**Status:** **Phases 1–2 complete 2026-08-25.** Identity decided and pinned; several
repositories now extract and merge into one scoped graph, cached per repo. Phases 3–4 not
started — there are still no cross-repo *edges*, so a merged graph is two islands.
**Written:** 2026-08-25 against 3.22.0. **Owner:** _unassigned_.
**Scope:** comprehension only. **Multi-repo *delivery* is an explicit non-goal** — see below.
**Index entry:** E2 in [`enhancement-index.md`](enhancement-index.md).

> **This is not [`tri-repo-integration.md`](tri-repo-integration.md).** That design spans three
> *products* — ontomesh, agent-orchestrator, infodrift — joined by a shared ontology key. This is
> one *change* landing in several repositories of one system, which is an ordinary situation with
> no ontology in it.

---

## The problem

A change spans a service and the library it depends on, or two services either side of an HTTP
boundary. Today Spine can see one of them. `investigate` lands the ticket in whichever repo it
was pointed at, blast radius stops at that repo's edge, and the caller in the other repo — the
thing that will actually break — is not in the graph at all.

The failure is **silence**, which is the right direction but is still a failure: the brief looks
complete and is bounded by an accident of which directory the command ran in.

## Where it is blocked today, precisely

Four places, all single-repo **by construction** rather than by oversight:

| Surface | Constraint |
|---|---|
| `RepoCodeExtractor.extract(root)` | Takes one root |
| `pkg/persistence.py` | Cache keyed to **one** repo's HEAD SHA, trusted only on a clean tree |
| `WorkspaceManager(root, repo_url, base_branch)` | Clones one repo, one worktree per issue |
| `sdlc/autorun.py` | Ends in one PR; `episteme/` lands in one repo |

**The blocking one is none of those. It is identity.**

Node ids are language-prefixed, not repo-prefixed. `py:shop.cart.Cart` in two repositories is
**the same id**, and `FactBatch.merge` is `add_node` in a loop — so merging two graphs today
silently collapses distinct classes into one node, and their edges onto each other. Internally
consistent, externally false: the exact failure mode `STATE-OF-SPINE` §9 keeps describing, and
`pkg verify` would report zero dangling edges throughout.

## The good news: the vocabulary is already there

The edge kinds that cross a repository boundary **exist and are already extracted**:

| Join | Edges | What it connects |
|---|---|---|
| **HTTP** | `CONSUMES` ↔ `EXPOSES` | A client call in repo A to an endpoint repo B serves |
| **Data** | `READS` / `WRITES` | Two repos touching the same table |
| **Package** | `IMPORTS` ↔ module | A publishes what B imports |

**No new `NodeKind`, no new `EdgeKind`, no `facts.py` vocabulary change.** What is missing is a
post-pass that performs the join — the same shape as `import_link.py` and `doc_link.py`, which
already exist and already run repo-wide.

## Phase 1 — the identity decision (blocking)

**The constraint that rules out the obvious answer.** `Provenance.__str__` returns `f"{file}:{line}"`,
and production code parses it back to recover the file path.

**The first draft of this spec said three call sites. Re-measured in Phase 1, it is six** — and
the two that were missed are the two that matter:

```
sdlc/design.py:130            landing.where.split(":", 1)[0]
sdlc/autorun.py:788           str(getattr(land, "where", "")).split(":", 1)[0]
sdlc/builddoc.py:953, 1163    str(getattr(land, "where", "")).split(":", 1)[0]
sdlc/evidence.py:73           self.where.split(":", 1)[0]            ← the Evidence artifact
sdlc/criteria_binding.py:224  in_evidence=where.split(":", 1)[0] in landing
```

Making `__str__` return `repo:file:line` makes every one of those return **the repo name where a
file path is expected** — silently, with no exception raised. Demonstrated rather than asserted:
breaking `__str__` and re-running, `criteria_binding.py:224` computes `in_evidence` against
`'billing'` instead of `payments/retry.py`.

**That last site is the one to understand.** It decides whether an acceptance criterion is bound
to a landing site, and a criterion binding against a repo name binds against nothing and
**passes** — which is precisely the guarantee described in
[`ticket-to-landing-sites.md`](ticket-to-landing-sites.md) §7, removed by a change that looks
like a one-line improvement.

**Node ids are parsed the same way at fourteen sites**, not two:
`knowledge/{areas,insights,current_state}`, `pkg/{docs,verify,cpp_extractor}`, three in
`pkg/invention.py` and **five in `pkg/import_link.py`**. So a scope placed *before* the language
prefix breaks all of them.

### The options

| | Approach | Cost |
|---|---|---|
| **A** | Repo scope **inside** the id, after the language prefix — `py:svc-a::shop.cart.Cart` | Every id changes shape in multi-repo mode; area grouping needs to learn the separator |
| **B** | `Provenance` gains a `repo` field; `__str__` **unchanged**; a new `qualified()` renders `repo:file:line` for display | Ids still collide — solves addressing, not identity. **Insufficient alone** |
| **C** | **A + B, applied at merge time only** | Two code paths, but see below |

**Decided: C**, and the *at merge time* half is the important part.
[`pkg/scoping.py`](../../src/orchestrator/pkg/scoping.py) applies the scope while merging;
`RepoCodeExtractor` is untouched, so **single-repo extraction stays byte-identical**.

### The id form, and the one collision in it

The scope goes **after** the language prefix, terminated by `@`:

| Before | After |
|---|---|
| `py:shop.cart.Cart` | `py:svc-a@shop.cart.Cart` |
| `cpp:Namespace::func` | `cpp:svc-b@Namespace::func` |
| `ts:app/handler.Handler` | `ts:web@app/handler.Handler` |

`@` because `.`, `::` and `/` are all taken — module paths, C++ qualified names, TypeScript
module paths — and no id body in this repository contains `@` at all.

**Except one shape does.** npm-scoped TypeScript packages already produce
`ts:@vue/runtime-core:h`, where `@` is the *first* character of the body. `unscope_id` therefore
requires the text before the first `@` to be a non-empty valid repo key, so an unscoped npm id
reads as unscoped rather than as scoped-to-nothing — which would have silently stripped the
package from the id.

### External nodes are deliberately not scoped

`py:ValueError` and `ts:react:useState` name the same thing in every repository. Scoping them
makes two copies of one fact; merging them is what lets a merged graph answer *"which of our
services depend on this package"* for free. They are ungrounded by definition, so nothing that
reports on a repository's own symbols is affected. **The cost, stated:** two repos pinning
different versions of one package share a node, because the graph does not hold versions.

### Exit — met

| Criterion | Evidence |
|---|---|
| The decision is written down | Option C, above, and the preamble of `pkg/scoping.py` |
| The parse sites are pinned | [`tests/pkg/test_identity_contract.py`](../../tests/pkg/test_identity_contract.py) — 28 tests. **Proved by breaking it:** with a repo added to `__str__`, two guards fail |
| Single-repo extraction is byte-identical | SHA-256 of the full node+edge set on three real repos, extracted by `develop` and by this branch: **identical on all three** (leveldb 1,611/7,791 · gin 1,898/3,391 · zod 4,610/7,381) |
| Nothing else moved | `pkg accuracy --check` OK · 3,040 tests passing · mypy and ruff clean |

## Phase 2 — the merged graph *(complete)*

### The three decisions, taken

**D1 — the repo key is declared, never derived.** It is baked into every scoped id and every
cache entry, so it must be identical on a laptop and in CI. A directory name differs per
checkout; a remote URL differs between SSH and HTTPS forms. A drifting key silently invalidates
caches and makes two runs incomparable.

**D2 — declared in `.spine/repos.yaml`**, beside `.spine/workflows/`, which already establishes
repo-carried configuration. **Not discovered from manifests:** guessing which of forty entries
in a lockfile are "our" repositories is a judgement the team owns, and a second resolution
problem this package does not need.

> **Why a hand-maintained file is acceptable here, when it would not be for the constitution.**
> Both depend on somebody filling something in. The difference is the failure mode: a repo
> nobody listed produces no nodes and a visibly narrower graph — you notice on the first run.
> An empty rules file produces *"no violations found"*, which reads as health.

**D3 — per-repo caches, merged on read. There is no merged cache entry.** Change one of four
repositories and one re-extracts while three are served from cache; a single entry keyed on the
whole tuple would invalidate everything on any change. Merging is cheap and extraction is not,
so the composition runs every time and the expensive half is reused.

The tuple cache key from the original draft therefore became **carried metadata** rather than a
filename: `MergedFacts.cache_key()` returns `((repo, sha), …)` in key order, and returns
**empty** when any repo is untrusted so it can never name a graph containing uncommitted work.

### Trust is a property of the whole graph

`MergedFacts` carries a `RepoState` per repository — sha, dirty, and whether it came from cache
— because **a merged graph looks identical whether or not one of its inputs is dirty**. One
untrusted repo makes the whole thing untrusted: it cannot back a currency gate, cannot be
reproduced at a commit, and must not be quoted as a measurement. Not a per-repo verdict, because
the graph is one artifact and a join crossing into the dirty repo is wrong wherever it lands.

`pkg extract --repos <config>` prints the per-repo state and says so in as many words when the
graph is not reproducible — placed after the counts, where the eye stops.

### Scope calls

- **A minimal CLI now, not in Phase 4.** A capability nobody can invoke is inert, which is what
  GraphIR Phase 4 was reverted for. `--repos` commits to no UX beyond a summary.
- **Local paths only.** Cloning is `WorkspaceManager`'s job; pulling it in here would drag auth,
  shallow-clone policy and workspace layout into a phase whose point is that merging works.

### Exit — met, and corrected from the draft

**The original exit criterion was unachievable.** It required *"blast radius crosses the
boundary in at least one direction"* — but cross-repo edges do not exist until the Phase 3
joiners, so a merged graph is two islands and no radius can cross. Moved to Phase 3.

| Criterion | Evidence |
|---|---|
| Two repos merge with **zero id collisions** | `test_two_repos_defining_the_same_symbols_stay_two_nodes` — both `py:svc-a@shop.Cart` and `py:svc-b@shop.Cart` survive |
| **No dangling edges** across the merged graph | `test_the_merged_graph_has_no_dangling_edges`; and an intra-repo call stays in its own repo rather than binding to the identically-named symbol next door |
| An unchanged repo **reuses its cache** | `test_a_repo_that_did_not_change_is_served_from_cache` — cold/warm/one-commit, asserted on `RepoState.cached` rather than on timing |
| **Deterministic** regardless of declaration order | Same nodes, same edges, same `cache_key()` from reversed YAML |
| A dirty repo is **visible** | `trusted` False, `untrusted_keys == ("svc-b",)`, `cache_key() == ()` |
| Single-repo path still byte-identical | The Phase 1 guards, re-run |

Gate: `mypy src tests` clean · `ruff` clean · **3,062 tests passing** · `pkg accuracy --check` OK.

## Phase 3 — the three joiners *(not started)*

Deterministic post-passes, in the shape of `import_link.py`.

**These are resolution, not structure — and that is the whole risk.** The tree tells you a client
issued `POST /v1/orders`; it does not tell you which service serves it. Path templating
(`/v1/orders/{id}`), version prefixes, gateway rewrites and base-path config all make it a
judgement, and judgements can be wrong.

So the joins are held to the `CALLS` standard, not the structure standard:

- **Precision first, skip rather than guess.** A missing cross-repo edge sends a human looking; a
  fabricated one asserts that a service calls an endpoint nobody serves.
- **Scored on their own** in `scoreboard.json`, never folded into an average that hides them.
- **A corpus case per joiner**, written before the joiner, failing before it works — the order
  that made `corpus/*/shadowed_calls` worth having.
- Expect recall well under 1.00 and **publish it that way.**

**Exit:** three joiners, three corpus cases, precision 1.00 on each, recall stated honestly —
**and blast radius crossing a repository boundary in at least one direction**, moved here from
Phase 2 where it could not be met.

## Phase 4 — the surfaces *(not started)*

`investigate` ranks across the merged graph; blast radius crosses boundaries; `state` and
`understand` render repo as a dimension. Bound honestly — a merged overview must say *"top N of
M across R repos"*, never imply completeness.

**Exit:** a ticket landing in repo A shows the repo-B caller that will break.

## Explicit non-goal: multi-repo delivery

N worktrees is easy. **N PRs that must land together is not a graph problem** — it is ordering,
atomicity and rollback, and no platform solves it well. Preflight compounds it: Spine shells out
to *the repo's own* `ruff`/`mypy`/`go test`, so a cross-repo run needs each repo's toolchain
present and green independently.

**Ship the read-only half and stop.** A change spanning three repos then gets *planned* and
*reviewed* correctly, with a human opening three PRs. That is most of the value at a fraction of
the risk.

Building delivery on an unmeasured join would be blast radius computed from a proposal again —
verification-shaped, and fiction.

## Invariants

- **Single-repo behaviour does not change.** Byte-identical extraction, same cache, same
  scoreboard, same corpus scores.
- **Deterministic, no LLM.** The joiners are graph queries. A model deciding whether two paths
  are the same endpoint would put a model call in the one path that has none.
- **No vocabulary change.** `facts.py` is untouched: the edge kinds already exist. If a join
  genuinely needs a new kind, that is a separate decision and a separate spec.
- **Bound honestly**, and **skip rather than guess** — the join is where guessing is tempting.

## Open questions

1. ~~**What is a repo key?**~~ **Closed across Phases 1–2.** The *shape* is fixed —
   `^[A-Za-z0-9][A-Za-z0-9._-]*$`, validated by `validate_repo_key`, refused loudly rather than
   sanitised because a key silently rewritten differs between the machine that wrote the cache
   and the one that reads it. **Where the key comes from is still open** (remote URL, config
   name, content hash); the constraint is that it must be stable across clones and machines, so
   a local directory name is not a candidate.
2. ~~**How are the repos declared?**~~ **Closed in Phase 2:** `.spine/repos.yaml`, explicit,
   with `--repos` to point at one elsewhere. Manifest discovery declined — see D2.
3. **Does `episteme/` become multi-repo?** It lands in one repo today. A merged bank has no
   obvious owner, and writing one repo's bank from another repo's facts is a currency problem
   nobody has thought about.
4. **How does the doc binder behave?** `doc_link` binds docs to symbols within a repo. A doc in
   repo A describing repo B's API is real and common, and cross-repo `MENTIONS` may be the
   cheapest useful join of all — or the noisiest.

## What would make this obviously worth building

**Someone asks "what breaks if I change this?" and the answer names a caller in a different
repository, correctly, with a `file:line` they can open.** Until that has happened once against
a real pair of repos, this is a design with a good argument and no evidence — the same standing
the constitution spec has, and recorded the same way.
