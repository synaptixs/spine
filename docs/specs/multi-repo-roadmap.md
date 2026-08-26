# Multi-repo comprehension — one graph across several repositories

**Status:** **Spec only. Nothing built.** Phase 1 is a **blocking identity decision** — there is
more than one defensible answer and the wrong one is expensive to reverse.
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
and **three production call sites parse it back**:

```
sdlc/design.py:130        landing.where.split(":", 1)[0]
sdlc/autorun.py:788       str(getattr(land, "where", "")).split(":", 1)[0]
sdlc/builddoc.py:953,1163 str(getattr(land, "where", "")).split(":", 1)[0]
```

Making `__str__` return `repo:file:line` makes every one of those return **the repo name where a
file path is expected**, silently, with no exception raised. Design would compute a blast radius
against a path that does not exist; the build document would list it as a landing file. This is
recorded here because it is the kind of change that looks like a one-line improvement.

Two ids are similarly parsed — `areas.py:83` and `current_state.py:248` both do
`node.id.split(":", 1)[-1]` to recover the name for area grouping — so a scope placed *before*
the language prefix breaks area grouping in both.

### The options

| | Approach | Cost |
|---|---|---|
| **A** | Repo scope **inside** the id, after the language prefix — `py:svc-a::shop.cart.Cart` | Every id changes shape in multi-repo mode; area grouping needs to learn the separator |
| **B** | `Provenance` gains a `repo` field; `__str__` **unchanged**; a new `qualified()` renders `repo:file:line` for display | Ids still collide — solves addressing, not identity. **Insufficient alone** |
| **C** | **A + B, applied at merge time only** | Two code paths, but see below |

**Recommendation: C, and the "at merge time" half is the important part.** A new
`merge_repos({repo_key: FactBatch})` applies the scope while merging; `RepoCodeExtractor` is
untouched. **Single-repo extraction stays byte-identical** — which protects the commit-keyed
cache, the committed `scoreboard.json`, every corpus fixture, and `understand --check`, none of
which should move because a feature nobody enabled was added.

**Exit:** the decision is written down, the three `split(":", 1)[0]` sites carry a test that
fails if `Provenance.__str__` ever changes shape, and a single-repo extraction is proved
byte-identical before and after.

## Phase 2 — the merged graph

A tuple cache key — `((repo, sha), …)` — instead of one SHA. Determinism survives intact: same
commits in, same bytes out. `understand --check` still works; the key just gets wider.

Dirty-tree handling stays as-is per repo: **any** dirty repo makes the whole merged graph
untrusted, because a cache that is right about three of four inputs is not a cache.

**Exit:** two repos extract, merge without collision, and `pkg verify` reports zero dangling
edges across the merged graph. Blast radius crosses the boundary in at least one direction.

## Phase 3 — the three joiners

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

**Exit:** three joiners, three corpus cases, precision 1.00 on each, recall stated honestly.

## Phase 4 — the surfaces

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

1. **What is a repo key?** Remote URL, a name in config, or a content hash? It appears in every
   scoped id, so it must be stable across clones and machines — a local directory name is not.
2. **How are the repos declared?** A `.spine/repos.yaml`, a CLI flag, or discovered from
   manifests (`go.mod`, `package.json`, `pyproject.toml` dependencies)? Discovery is nicer and is
   a second resolution problem.
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
