# Many repositories, one graph — a walkthrough

**Written 2026-09-01 against 3.27.0.** High-level by design: the mechanism, not the design
record. [`multi-repo-roadmap.md`](multi-repo-roadmap.md) is the *why*, the four phases and the
identity decision; this page is *what happens when you point Spine at three repositories*.

---

## Executive summary

A product is rarely one repository. The storefront calls the billing service, both read the
same `orders` table, and a shared library sits under each. Ask any single-repo tool *"what
breaks if I change this?"* and it answers confidently about a third of the system.

**You declare the repositories; Spine builds one graph across all of them, and draws the edges
that cross the boundary.** A ticket landing in `web` then reports the dependents it has in
`billing`. Three commands:

```bash
orchestrator pkg joins --propose          # read the code, suggest the topology
orchestrator pkg extract --repos .spine/repos.yaml
orchestrator investigate "<ticket>" --repos .spine/repos.yaml
```

**It stays deterministic and model-free**, and it keeps the rule the rest of the graph keeps:
*where the answer is ambiguous, draw nothing*. Comprehension only — multi-repo **delivery** is
an explicit non-goal.

---

## Step 1 — You declare the repositories

One file, `.spine/repos.yaml`. Everything Spine knows about your topology, you told it.

```yaml
repos:                       # required — a key you choose -> a local path
  web:     ../storefront
  billing: ../billing-service
joins:                       # optional — declared, not guessed
  - kind: http               # http | data | package
    consumer: web
    provider: billing
    base: /v1                # http only
```

That is the whole format. **Local paths only** — no URL, branch or clone; Spine reads what is
already on disk. Keys are validated (`^[A-Za-z0-9][A-Za-z0-9._-]*$`) and a bad one is **refused,
never sanitised**; two keys may not point at the same directory; a path that does not exist is
an error naming the file.

Repos and joins are both **sorted** before use, so two spellings of the same file produce the
same graph.

**You do not have to write the `joins:` block yourself.** `pkg joins --propose` reads the code
and prints one you can paste, with the evidence as comments — and it only proposes a join that
would actually create edges.

## Step 2 — Each repository is extracted on its own

Every repo goes through the ordinary single-repo path, unchanged, and is cached under its own
commit. Nothing about extraction knows that other repositories exist.

A repo is only cached when it is a git checkout with a **clean tree**. **Trust is
whole-graph:** one repository with uncommitted work marks the merged result untrusted, and the
command says so loudly, because a graph you cannot reproduce should not be quoted.

## Step 3 — The repositories are merged, and identity stays unambiguous

Two repositories can both define `shop.cart.Cart`. Merging without care silently fuses them.

At merge time — and **only** at merge time — every id gains its repo key after the language
prefix:

```
py:shop.cart.Cart      ->   py:web@shop.cart.Cart
                            py:billing@shop.cart.Cart
```

Two consequences worth knowing:

- **Third-party nodes are deliberately *not* scoped.** `py:httpx` stays `py:httpx`, so when both
  repositories import it, the graph shows one library with two dependents — which is the true
  answer.
- **Single-repo extraction is byte-identical.** Scoping is applied by the merge, so nothing
  changes for anyone using one repository.

Provenance also gains the repo, so every fact still says where it came from.

## Step 4 — The joiners cross the boundary

Three joiners, all deterministic, and **none of them will join to a repository you did not
declare.** That is where precision comes from: the topology is your claim, not Spine's guess.

| Join | What it looks at | What it does |
|---|---|---|
| **`http`** | calls in `web` that matched **no endpoint in `web`**, against `billing`'s `Endpoint` nodes | draws a `CONSUMES` edge |
| **`data`** | `Entity` nodes with the same name in both | collapses them into one, repointing reads and writes |
| **`package`** | a placeholder in `web` for something `billing` actually defines | repoints the import — and the calls through it — at the real node |

**HTTP matching is exact or nothing.** The verb must match, then the route, either literally or
through a template where `{param}` matches exactly one path segment and never across a `/`. If
**two** provider routes match, no edge is drawn and the call is recorded as *ambiguous*. This is
the same *skip rather than guess* rule that holds `CALLS` precision at 1.00 — a fabricated
cross-repo edge would send a reader into a service that never receives the request.

**What did not join is reported, not hidden.** `pkg joins --check` prints how many candidate
calls were placed, groups the rest by reason (`no-declared-provider`, `no-matching-endpoint`,
`ambiguous`), and **flags a declared join that placed nothing** — which is how you find a
topology entry that has gone stale.

## Step 5 — What reads it

| Surface | What it does with the merged graph |
|---|---|
| `investigate --repos` | a ticket's landing sites, plus **"N dependent(s) in other repos"** |
| `pkg extract --repos` | the merged graph, per-repo cache status, per-edge-kind counts |
| `pkg joins --check` / `--propose` | placement rate; a pasteable topology block |
| MCP `blast_radius`, `investigate`, `pkg_joins` | the same answers to an assistant, each carrying whether the graph is reproducible |

## The impact on the PKG

**No new node kinds and no new edge kinds.** `CONSUMES` already existed for client/server pairs
inside one repository; multi-repo reuses the vocabulary rather than extending it. That is why
every existing surface reads a merged graph without being taught to.

**Two of the three joiners make the graph *smaller*.** `data` and `package` collapse a
duplicate — the entity the consumer thought it owned, the placeholder standing in for a real
module — so the merged graph has fewer nodes than the sum of its parts, and they are the right
ones. Only `http` adds edges.

**Measured, on the fixtures:** cross-repo `CONSUMES` scores **recall 0.67, precision 1.00**, and
is gated — a drop fails the build. The one miss is known and named: an f-string route
(`f"/v1/orders/{order_id}"`) that the client reader cannot resolve to a literal path.

**The number from a real pair of production repositories has not been taken.** 0.67 is against a
fixture built to be joinable, and should not be quoted as what your repositories will score.

## What this does not do

- **Multi-repo delivery.** Comprehension only; a change still lands in one repository.
- **Remote repositories.** Local paths on disk.
- **`--intents` with `--repos`** — refused, because `git blame` is per checkout.
- **Cross-repo `MENTIONS`**, and repo as a dimension in `state` / `understand` — not built.
- **A bound on repository count.** There is no cap; nothing has been measured at scale.

> **One known defect, found while writing this page (2026-09-01).** `pkg extract --repos` and
> `investigate --repos` pass a single extractor for every repository, and the front-end's
> unmatched-call list survives between repositories. On a cold cache the second repo's join
> candidates therefore include the first repo's calls, which can place a `CONSUMES` edge whose
> source node does not exist in the graph. `pkg joins` is unaffected — it builds a fresh
> extractor per repo — which means **the joins report and the extracted graph can disagree**.
> Reproduced against 3.27.0; not yet fixed.
