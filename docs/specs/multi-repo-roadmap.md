# Multi-repo comprehension — one graph across several repositories

**Status:** **Complete 2026-08-25 — all four phases.** Repositories merge into one scoped
graph, three joiners cross the boundary, and the comprehension surfaces read them: a ticket
landing in one repository now reports the dependents it has in another. Multi-repo *delivery*
remains an explicit non-goal.
**Written:** 2026-08-25 against 3.22.0. **Owner:** _unassigned_.
**Scope:** comprehension only. **Multi-repo *delivery* is an explicit non-goal** — see below.
**Index entry:** E2 in [`enhancement-index.md`](enhancement-index.md).

## Progress

| Phase | What it delivers | Status | Started | Ended |
|---|---|---|---|---|
| **1** | Identity — repo scope, the parse contract pinned | ✅ **Complete** | 2026-08-25 | 2026-08-25 |
| **2** | The merged graph — declared repos, per-repo caches | ✅ **Complete** | 2026-08-25 | 2026-08-25 |
| **3a** | The HTTP joiner — declared topology, proposal, `--check` | ✅ **Complete** | 2026-08-25 | 2026-08-25 |
| **3b** | The data and package joiners | ✅ **Complete** | 2026-08-25 | 2026-08-25 |
| **4** | The surfaces — investigate reads the joins | ✅ **Complete** | 2026-08-25 | 2026-08-25 |
| — | Multi-repo **delivery** | ❌ **Explicit non-goal** | — | — |

**Where that leaves it.** A merged graph answers *"what breaks in the other repo"* in all three
shapes — an HTTP call, a shared table, an imported library. `pkg joins --check` reports what was
placed and what was not; `--propose` derives the topology from evidence rather than asking
anyone to write it.

| Joiner | Precision | Recall | The miss |
|---|---|---|---|
| http | **1.00** | 0.67 | A path built from an f-string is collected as no call at all |
| data | **1.00** | **1.00** | — |
| package | **1.00** | **1.00** | — |

**Precision is 1.00 by construction, not by luck** — nothing can join to a repository nobody
declared. So recall is the number worth watching, and `--check` yields it for free.

**The last mile landed in Phase 4.** `investigate --repos` researches a ticket against the
merged graph, and each landing site reports the repository it is in and **how many dependents it
has elsewhere**:

```
- **billing** · `create_order` (Function, 0 caller(s), **1 dependent(s) in other repos**) — billing:app/routes.py:7
```

That row is the whole programme in one line. `0 caller(s)` is true — nothing in the source calls
an HTTP handler — and on its own it is the most dangerous answer the graph can give.

**Why 3 was split.** The three joiners are not equal risk. HTTP carries all of it — path
templating, prefixes, the resolution judgement — and needs every piece of machinery: retained
unmatched calls, proposal, `--check`, scoring, a corpus case. Data and package joins reuse all
of that with far easier matching. Shipping HTTP alone proves the design end to end, including
whether declared topology really does collapse the resolution risk. It does: **precision is
1.00 because nothing can join to an undeclared repository.**

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

## Where it was blocked, and what still is

Four places were single-repo **by construction** rather than by oversight. Two have moved:

| Surface | Constraint | Now |
|---|---|---|
| `RepoCodeExtractor.extract(root)` | Takes one root | **Still true, deliberately.** `load_or_extract_repos` composes it per repo rather than changing it — which is what keeps single-repo extraction byte-identical |
| `pkg/persistence.py` | Cache keyed to **one** repo's HEAD SHA, clean trees only | ✅ **Phase 2.** Per-repo caches merged on read; `MergedFacts.cache_key()` is the tuple, and empty when any repo is untrusted |
| `WorkspaceManager(root, repo_url, base_branch)` | Clones one repo, one worktree per issue | **Still true.** Only matters for delivery, which is a non-goal |
| `sdlc/autorun.py` | Ends in one PR; `episteme/` lands in one repo | **Still true.** Same reason |

**The blocking one was none of those. It was identity** — closed in Phase 1, and recorded here
because the reasoning is what the rest of the design rests on.

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

**No new `NodeKind`, no new `EdgeKind`, no `facts.py` vocabulary change** — and the claim held
all the way through Phase 3. Across four phases `facts.py` gained exactly one field, `repo` on
`Provenance`. Every cross-repo edge that now exists is a `CONSUMES`, `READS`, `WRITES`,
`IMPORTS` or `REFERENCES` that was already in the vocabulary; what was missing was only the
post-pass, and it landed in the shape of `import_link.py` as predicted.

## Phase 1 — the identity decision *(complete)*

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

## Phase 3 — the joiners *(complete)*

Deterministic post-passes, in the shape of `import_link.py`.

**These are resolution, not structure — and that is the whole risk.** The tree tells you a client
issued `POST /v1/orders`; it does not tell you which service serves it. Path templating
(`/v1/orders/{id}`), version prefixes, gateway rewrites and base-path config all make it a
judgement, and judgements can be wrong.

### Declare the topology, infer the edges

**The topology is declared in `.spine/repos.yaml` alongside the repos.** Same principle as D1
and D2, and it removes most of the resolution risk: a call cannot be joined to a repository
nobody declared as its provider.

```yaml
repos:
  billing: ../billing
  web:     ../storefront

joins:
  - kind: http
    consumer: web
    provider: billing
    base: /v1              # the provider mounts its routes under this
  - kind: data
    repos: [billing, reporting]
    schema: analytics
```

**But declaring a join does not create the edge — it narrows the search.** *"web talks to
billing over HTTP under /v1"* is a topology fact. Matching `POST /v1/orders/42` against
`POST /v1/orders/{id}` is still a judgement; the declaration only decides **which** set of
`EXPOSES` to search, not **which member** of it matches. The split is deliberate:

| Declared by a human | Inferred from facts |
|---|---|
| Which repos join, and by which mechanism | Which call matches which endpoint |
| Base paths, prefixes, gateway rewrites | Path-template matching inside that narrowed set |
| Shared schema names | Which symbols read or write which table |

Topology is small and stable — a few lines that change when a service is added. Edges are
thousands and change every commit. **Declaring topology is maintainable; declaring edges is a
second codebase.**

### Nobody authors this from scratch — it is proposed

The join set is itself a small graph, so it gets the treatment every other graph here gets:
**derived from evidence, ratified by a human.** Nobody hand-writes `episteme/` either.

```bash
orchestrator pkg joins --propose    # a draft, with the evidence inline
orchestrator pkg joins --check      # what is still unjoined
```

**`--render` was proposed and not built.** It is listed in open question 4, not here, because a
spec that advertises a flag the CLI does not have is a spec a reader cannot trust about the
flags it does have.

**Four sources, by strength:**

| Source | Evidence | Strength |
|---|---|---|
| **Unmatched client calls × another repo's endpoints** | `web` has 142 unmatched `POST /v1/orders…`; `billing` exposes `POST /v1/orders/{id}` | Strongest — both sides are extracted facts carrying `file:line` |
| **Package manifests between declared repos** | `billing/go.mod` requires the module `shared-lib` declares | Near-certain |
| **Shared table names** in `READS`/`WRITES` | Both repos write `invoices` | Strong; occasionally coincidence |
| **Service URLs in config or env** | `BILLING_URL=http://billing` | Weak — propose, never assume |

**Manifest discovery is safe here even though it was declined for `repos:`.** There it had to
guess *which of forty dependencies are ours*, which is a judgement. Here the repositories are
already declared, so it only has to find relationships **among a known set** — a much smaller
problem with nothing to guess.

**Every proposal carries the number of edges it would create.** A join producing 0 is noise and
must not be offered; one producing 142 is real. This is the trigger-count-at-proposal-time idea
from [`constitution-roadmap.md`](constitution-roadmap.md) — except the evidence here is
deterministic path overlap rather than prose read by a model, so it actually works.

### The one prerequisite: stop discarding the evidence

`pkg/python_client.py` resolves a call by exact endpoint name and **drops it when there is no
match**:

```python
endpoint_id = endpoints.get(f"{call.verb} {call.path}")
if endpoint_id is None:
    continue        # ← the cross-repo candidate, discarded
```

Correct for one repository, and it throws away precisely the facts a proposal needs. **Retaining
unmatched calls is the first change Phase 3 makes**, before any joiner exists. Same for the other
front-ends that emit `CONSUMES`.

### `--check`, because a missing join is quiet

For `repos:`, a forgotten entry is **loud** — no nodes, a visibly narrower graph. **A forgotten
`joins:` entry is not.** Missing cross-repo edges look exactly like *"these services are not
coupled"*, which reads as health — the constitution's failure mode, appearing here.

So the joiner reports what it could **not** join: *"142 calls in `web` matched no declared
provider."* An unmatched call becomes a visible number instead of an absence. That is the
*bound honestly* invariant applied to a join — say what was elided, never let a clipped view
imply completeness.

### What this does to the measurement

**Precision becomes ~1.00 by construction** — nothing can join to an undeclared repository. So
the interesting number flips to **recall**: of the calls that should have joined, how many did?
That is the more honest metric anyway, and `--check` yields it for free.

The joins are still held to the `CALLS` standard, not the structure standard:

- **Precision first, skip rather than guess.** A missing cross-repo edge sends a human looking; a
  fabricated one asserts that a service calls an endpoint nobody serves.
- **Scored on their own** in `scoreboard.json`, never folded into an average that hides them.
- **A corpus case per joiner**, written before the joiner and failing before it works — the order
  that made `corpus/*/shadowed_calls` worth having. Each now tests both halves: a declared join
  that matches, and a declared join whose path shape defeats the matcher.
- Expect recall well under 1.00 and **publish it that way.**

### Exit — 3a met *(3b's own results are in the section below)*

| Criterion | Evidence |
|---|---|
| Unmatched calls **retained as a side-channel**, not as facts | `ClientState.unmatched`, surfaced as `RepoCodeExtractor.unresolved_calls`. Guarded: no phantom endpoint node, no phantom edge |
| `joins --propose` carries evidence and an edge count per entry | A candidate producing 0 edges is never offered |
| A joiner and a corpus case, **written before it worked** | `corpus/multirepo/http_join` — scored **CONSUMES precision `None`, recall 0.00** with the joiner disabled, 1.00 / 0.67 with it |
| Precision **1.00**, recall **stated honestly** | 0.67. The missing third is a **known gap**, labelled: an f-string path is collected as no call at all |
| `--check` reports the unjoined | By reason, and **per declared join**, so a stale one shows `** placed nothing **` instead of hiding in a healthy total |
| **Blast radius crosses a repository boundary** | `py:web@app.client.order -CONSUMES-> py:billing@endpoint:POST /v1/orders` |
| **The digest check** | `develop` vs branch on leveldb, gin, zod, Newtonsoft.Json **and `spine/src`** — the Python path Phase 3 actually modified. Identical on all five |

Gate: `mypy src tests` clean · `ruff` clean · **3,080 tests passing** · `pkg accuracy --check` OK.

**Three things Phase 3a found that the spec did not predict.**

1. **The side-channel does not survive the cache.** `load_or_extract` never runs the extractor on
   a warm hit, so `unresolved_calls` came back empty and the joiner placed nothing — which looks
   exactly like two uncoupled services. Fixed with a **sidecar** file beside the cached facts,
   deliberately not a key inside them: these are not facts and must not travel in the graph.
2. **The corpus format assumed one fixture per case.** `expected.json` now accepts `roots:` (a
   mapping of repo key to path) alongside `root:`, and a cross-repo case is scored through the
   *real* multi-repo path — scoping, merging, declared joins — or it would measure an assembly
   nothing ships.
3. **`language` was doing two jobs.** It named both what a case *measures* and which front-end
   must be *installed*, which are the same for every single-language case and differ for this
   one. A `requires:` field now names the second, so the case is neither skipped forever nor
   filed under a front-end it is not testing.

**The digest check, as an exit criterion rather than a habit.** Neither Phase 3 invariant is safe
as a comment: both describe a change producing **no error, no failing test and no dangling
edge** — just a different graph. A digest is the only check that notices, and it moves for a
reason nobody can argue with. It has now run at Phases 1, 2 and 3a and been identical every
time.

### 3b — data and package *(complete)*

Both **repoint and rebuild** rather than adding edges, which HTTP does not — and that turned out
to be the design question, not the matching.

**Data.** Two repositories writing `invoices` produce two `Entity` nodes for one physical table,
so *"who writes this table"* answers per repository and silently under-reports: a schema change
looks safe because half its writers are in a graph nobody merged. The declaration names the
schema owner and the consumer's node is **collapsed onto it** — the same move `data_layer_link`
makes when an ORM entity and a real SQL table describe the same thing.

**Package.** A repository importing a library that is another declared repository. `import_link`
already repoints an external placeholder at what it denotes *within* a repo; across a boundary
the placeholder denotes a symbol in another declared one, and the same two rules apply — exact
id first, then longest dotted prefix naming a provider module.

**Two things that had to be got right, and both are the same shape.**

1. **Every edge into a collapsed node is dropped, not kept.** Forced rather than chosen: the
   node is going away, so a surviving edge would dangle. It is also true on its own terms —
   once `reporting`'s `invoices` collapses onto `billing`'s, `reporting`'s module does not
   contain an `invoices` entity, and repointing that `CONTAINS` would assert that billing's
   module contains a node declared in another repository.
2. **`CALLS` moves with `IMPORTS`, or the join destroys knowledge.** The placeholder carries
   both — `from shared.money import to_cents` makes the import, `to_cents()` makes the call.
   Moving only the import would drop a real call edge on the floor when the placeholder is
   removed, turning a join that adds knowledge into one that quietly loses it.

**Each has a control in its corpus case, and the controls are the point.** `ledger` is a table
`reporting` owns and `billing` has never heard of; `json` is an external import nobody declares.
A joiner that collapsed *every* entity, or repointed *every* external import, would pass the
positive test and destroy the control.

| Case | Result | Failed first at |
|---|---|---|
| `multirepo/data_join` | Entity **1.00/1.00**, READS **1.00/1.00**, WRITES **1.00/1.00** | Entity P 0.67, READS 0.67/0.67 |
| `multirepo/package_join` | IMPORTS **1.00/1.00**, CALLS **1.00/1.00** | IMPORTS 0.50/0.50, CALLS 0.50/0.50 |

Recall is 1.00 on both, against 0.67 for HTTP — as expected: a table name is a string and an
import is a declaration, neither of which needs the path-template judgement HTTP does.

Gate: `mypy src tests` clean · `ruff` clean · **3,088 tests passing** · `pkg accuracy --check`
OK · digest identical against `develop` on leveldb, gin, zod, Newtonsoft.Json and `spine/src`.

## Phase 4 — the surfaces *(complete)*

### Most of the traversal was already there

`FactStore.impact_of` follows `CALLS`, then `EXPOSES`, then `CONSUMES` — written for the
single-repo case where a repo ships both a client and the service it calls. Given a merged graph
it crosses a repository with no change at all:

```
impact_of(py:billing@app.routes.create_order)
   1 hop:  py:billing@endpoint:POST /v1/orders
   2 hops: py:web@app.client.place_order
```

**So Phase 4 was never about traversal. It was about whether anyone is told.** The graph knew;
no surface said so.

### What the brief now carries

| | Why it is not decoration |
|---|---|
| `Landing.repo` | Module **names** are not scoped — only ids are — so two services with `app.models` produce two landings both reading `app.models`, and `where` does not disambiguate either: both say `app/models.py:14` |
| `Landing.cross_repo` | `callers` counts inbound `CALLS` and nothing else. Right for a function, **catastrophic for an HTTP handler**: nothing in the source calls one, so it reports *0 callers* while another service depends on it entirely |
| Repo-qualified `areas` | Unqualified, two services' `app.models` collapse into one area and the brief claims the change is narrower than it is |
| `Investigation.elided` | *"Top N of M"*, never a clipped list implying completeness (invariant 7). One extra symbol is retrieved so *"these are all of them"* and *"this is the top N"* are distinguishable |

`cross_repo` is reported **beside** the caller count, not folded into it: they are different
facts, and a handler with 0 callers and 3 dependents in another service is the row a reader must
not skim past.

### `episteme/` is omitted rather than guessed

A merged brief passes `root=None`. `episteme/` belongs to one repository and a merged graph has
no single owner for it; filling the section from an arbitrary repo would present one service's
domain model as the system's. The brief is built to be silent where it has nothing grounded, so
it is silent here.

### Exit — met

| Criterion | Evidence |
|---|---|
| *"A ticket landing in repo A shows the repo-B caller that will break"* | `test_a_handler_reports_its_dependent_in_another_repo` — `create_order`, **0 callers, 1 cross-repo dependent** |
| Attributable to the join and nothing else | The counterfactual: same fixture, no join declared → `cross_repo == 0` |
| Bounded honestly | `elided` renders *"N further match(es) not listed"*, and does not when the list is complete |
| Single-repo unchanged | No repo, no cross-repo count, neither line rendered — and the digest identical against `develop` on leveldb, gin, zod and `spine/src` |

Gate: `mypy src tests` clean · `ruff` clean · **3,095 tests passing** · `pkg accuracy --check` OK.

### Not done, and named rather than implied

- **`state` and `understand` do not render repo as a dimension.** A merged bank has no owner —
  the same problem as `episteme/` above — and it wants its own decision rather than a default.
- **`pkg joins --render`** was proposed in Phase 3 and never built (open question 4).
- **Only `investigate` reads the joins.** `design`, `criteria_binding` and the SDLC pipeline
  still run against one repository, which is consistent with delivery being a non-goal but is
  worth stating rather than leaving to be discovered.

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

> **The one idea underneath the first three.** Single-repo extraction has precision 1.00 and a
> `strict` gate. The joiners will publish recall well under 1.00 *by design*. **New uncertainty
> must not leak into the surface that is already trusted** — anything that lets the second
> contaminate the first is a regression wearing a feature's clothes.

- **Single-repo behaviour does not change.** Byte-identical extraction, same cache, same
  scoreboard, same corpus scores.

- **Retained unmatched calls are a side-channel, never facts.** *(held — `ClientState.unmatched`, guarded by `test_an_unmatched_http_call_emits_no_fact`)* The natural way to
  keep a call whose endpoint is not in this repository is to emit it: a node for the endpoint,
  an edge from the caller. **That edge is an invention.** It asserts *"this function calls
  `POST /v1/orders`"* about an endpoint nothing in scope is known to serve — and it is the
  self-consistent kind, because creating the phantom endpoint node alongside it makes
  `pkg verify` report **zero dangling**, exactly as it did through the 497 Python inventions and
  the 46 in C++.

  **The `invention` gate would not catch it.** That oracle detects *shadowing* and says so in its
  own docstring. This would be a new invention class in a system that has just finished proving
  it has no detector for classes nobody wrote a detector for.

  Carry them as a list on the extractor — the shape `RepoCodeExtractor.skipped` already uses for
  unparseable files. Same information available to the proposer, and the graph asserts nothing
  new.

- **The joiners run only from the multi-repo path.** *(held — `link_joins` is called from `load_or_extract_repos` alone, asserted on the source by `test_the_joiner_never_runs_on_a_single_repo_extraction`)* Never from
  `RepoCodeExtractor.extract`, and never as a repo-wide post-pass beside `import_link` and
  `doc_link`, which run on every extraction.

  Mechanically: with one repository there is no cross-repo work to do, but a joiner would still
  attempt fuzzy path matching *within* it and create edges the exact-match client
  **deliberately declined** to create — the same invention risk through a back door.

  Structurally, and this is the real reason: the joiner is the least trustworthy code here. It is
  resolution rather than structure, it assumes a human declared the topology, and a single-repo
  user declared nothing. Letting it run on their graph gives them uncertainty they never opted
  into, with no `joins:` block and no `--check` output to notice it by. **`--repos` is the
  opt-in to a graph with different guarantees, and the two must stay distinguishable.**

- **Deterministic, no LLM.** The joiners are graph queries. A model deciding whether two paths
  are the same endpoint would put a model call in the one path that has none.
- **No vocabulary change.** `facts.py` is untouched: the edge kinds already exist. If a join
  genuinely needs a new kind, that is a separate decision and a separate spec.
- **Bound honestly**, and **skip rather than guess** — the join is where guessing is tempting.

## Open questions

1. ~~**What is a repo key?**~~ **Closed across Phases 1–2.** The *shape* is fixed —
   `^[A-Za-z0-9][A-Za-z0-9._-]*$`, validated by `validate_repo_key`, refused loudly rather than
   sanitised because a key silently rewritten differs between the machine that wrote the cache
   and the one that reads it. **Where it comes from is closed too:** a human writes it in
   `.spine/repos.yaml` (D1). Nothing derives it, because nothing derivable — a directory name, a
   remote URL in either of its two spellings — is stable across a laptop and CI, and the key is
   baked into every scoped id and every cache entry.
2. ~~**How are the repos declared?**~~ **Closed in Phase 2:** `.spine/repos.yaml`, explicit,
   with `--repos` to point at one elsewhere. Manifest discovery declined — see D2.
3. **Does `episteme/` become multi-repo?** It lands in one repo today. A merged bank has no
   obvious owner, and writing one repo's bank from another repo's facts is a currency problem
   nobody has thought about.
4. **Where does the topology picture belong?** **Open, and now homeless** — `joins --render`
   was proposed in Phase 3, not built there, and Phase 4 closed without it. The graph is small
   enough that a deterministic seeded layout is trivial, and *"show me how our services connect,
   from evidence rather than from a wiki page last edited in 2024"* is arguably a deliverable in
   its own right rather than a review aid. It needs a decision to belong to something, or it
   will keep being the thing each phase assumes the next one will do.
5. **How does the doc binder behave?** `doc_link` binds docs to symbols within a repo. A doc in
   repo A describing repo B's API is real and common, and cross-repo `MENTIONS` may be the
   cheapest useful join of all — or the noisiest.

## What "complete" means here, and what it does not

The roadmap's **scope** is done: every phase met its exit criteria, and the exit criteria were
written before the phases. That is not the same as the feature being finished, and the two are
worth separating so nobody reads the progress table as the whole story.

**Out of scope by decision, not by omission:**

- **Multi-repo delivery** — N PRs that must land together is ordering, atomicity and rollback,
  not a graph problem. Argued in full above.
- **Remote repositories** in `.spine/repos.yaml` — cloning is `WorkspaceManager`'s job.

**In scope for a follow-on, and named rather than left to be discovered:**

| | |
|---|---|
| Only `investigate` reads the joins | `design`, `criteria_binding` and the SDLC pipeline still run against one repository |
| `state` / `understand` ignore repo | A merged bank has no owner — open question 3 |
| `joins --render` | Open question 4, and now homeless |
| Cross-repo `MENTIONS` | Open question 5; possibly the cheapest useful join of all |
| Only Python emits `CONSUMES` | So the HTTP joiner is Python-client → any-language-endpoint. Java, C# and Go expose routes but have no client-side extraction |

**And the measurement that has not been taken** — see below.

## What would make this obviously worth building

**Someone asks "what breaks if I change this?" and the answer names a caller in a different
repository, correctly, with a `file:line` they can open.**

**That now happens** — on the corpus fixtures and on a two-repo scratch system, and it is a test
rather than an anecdote. What has *not* happened is the same thing on a real pair of production
repositories, where the paths are templated, the prefixes are rewritten by a gateway and the
declaration is written by someone who did not build this. Recall is 0.67 on HTTP against a
fixture designed to be joinable; the number that matters is the one from a system nobody tuned
it against, and it has not been taken.
