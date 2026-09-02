# `Endpoint` nodes for TypeScript and Go

**Status:** **Scoped 2026-09-02 against 3.28.0.** Not built. Decisions D1–D3 open.
**Owner:** _unassigned_

`STATE-OF-SPINE` §8 carries this as *"Express endpoint extraction (TypeScript), unscheduled"*.
Measuring first widened it and raised its priority.

---

## 1. It is not one language, and it silently limits a feature that shipped

**Only Java and C# emit `Endpoint` among the tree-sitter front-ends.** Python emits them
through [`python_routes.py`](../../src/orchestrator/pkg/python_routes.py). **Go, TypeScript, C
and C++ emit none.**

That is not merely a missing fact. The multi-repo `http` joiner matches a consumer's
unresolved calls against the **provider's `Endpoint` nodes** — so **a Node or Go service cannot
be a provider in a cross-repo join.** Multi-repo comprehension shipped in 3.22.0 and the leak
fix in 3.28.0; both work only when the provider is Java, C# or Python. The two most common
backend stacks are excluded, and nothing says so.

The single-repo cost is the one `python_routes.py` already names: *"nothing in Python **calls**
an HTTP handler — the framework does, at runtime"*. So a route handler has zero callers, and
`impact_of` answers **"safe to refactor"** for a public endpoint whose blast radius is every
client of `GET /v1/orders`. That wrong answer is live for every Go and TypeScript service.

## 2. The design is settled by precedent, not invention

`python_routes.py` is the reference and its decisions carry over unchanged:

| | |
|---|---|
| Node id | `ts:endpoint:GET /v1/orders`, `go:endpoint:GET /v1/orders` — the same scheme as `py:`/`cs:`, so every renderer composes for free |
| Edge | `EXPOSES`, from the handler to the endpoint |
| When | **Deferred to `finalize`**, because a mount (`app.use("/v1", router)`, `r.Group("/v1")`) lives in a different file from the route it re-mounts. `FactBatch` is add-only, so an endpoint emitted at the wrong path could not be corrected, only duplicated |
| Precision | **Literal or nothing.** A path built from a template literal or held in a variable yields no endpoint |
| The one exception | An **unresolved prefix** still emits at the local path. Dropping it restores exactly the false negative this exists to kill |

Nothing here needs a new dependency: both front-ends already parse a tree-sitter CST, and this
reads route declarations from it the way `_typed_locals` reads annotations.

## 3. What has to be read

**TypeScript — Express.** `app.get("/x", h)`, `router.post("/x", h)`, `app.use("/v1", router)`,
`express.Router()`. The verb is the method name; the path is the first string argument.

**Go — Gin and `net/http`.** `r.GET("/x", h)`, `v1 := r.Group("/v1")`, `mux.HandleFunc("/x", h)`,
`http.HandleFunc("/x", h)`. Gin's `Group` is the mount; `net/http` has no verb at the
registration site, which is D2.

## 4. Decisions

| | Decision | Recommendation |
|---|---|---|
| **D1** | Which frameworks in the first pass? | **Express and Gin only.** They dominate their ecosystems, and each additional framework is a separate matcher with its own corpus case. Fastify, Koa, chi, echo and `net/http` follow if measured demand justifies them |
| **D2** | `net/http`'s `HandleFunc` registers a path with **no verb** — emit what? | **Emit nothing in the first pass.** An endpoint whose verb is unknown cannot be matched by the joiner, which requires verb equality, and inventing `ANY` would create a node that joins to everything. Revisit with a measured count of how much Go traffic it represents |
| **D3** | Both languages in one change, or TypeScript first? | **One spec, two changes.** The vocabulary and the `finalize` deferral are shared, so specifying once is right; shipping separately keeps each diff reviewable and each corpus case honest |

## 5. How it will be measured

A corpus case per language under `corpus/typescript/` and `corpus/go/`, each with a deliberate
**control** — a route built from a template literal, which must yield nothing — so a matcher
that resolves everything fails. `Endpoint` then scores under the `corpus` gate at `strict`, the
same tier that holds `invention` at zero.

**And a multi-repo case**, because the single-repo number is not the point: a `corpus/multirepo`
fixture with a TypeScript provider proves the joiner can reach it, which is the gap §1 names.

## 6. Non-goals

- **RPC, GraphQL, gRPC.** `Endpoint` means an HTTP route here.
- **Middleware chains.** The handler is the last argument; middleware is not the endpoint.
- **Any change to the four front-ends that already emit endpoints.**
- **Raising recall by guessing a computed path.** A wrong path is presented as grounded by every
  surface downstream, which is worse than an absent one.

## 7. Open questions

1. **How many real TypeScript/Go services declare routes in a shape this reads?** Unmeasured.
   The pinned corpus is no help — vue-core is a client library and gin is the framework itself,
   not a user of it. This wants a repository nobody here controls before recall is quoted.
2. **Does `EXPOSES` need to reach `blast_radius`'s output**, or does the existing renderer
   already compose it from the node kind? Verify before adding a surface.
