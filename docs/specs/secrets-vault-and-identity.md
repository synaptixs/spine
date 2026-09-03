# Secrets, and the identity work still open

**Status:** **Scoped 2026-09-02 against 3.28.0.** Not built. D1–D4 open.
**Owner:** _unassigned_

`STATE-OF-SPINE` §8 carries *"RBAC role-gating beyond the approval decision + secrets vault —
parked"*. Reading it, most people conclude RBAC is unbuilt. **It is built.** This spec separates
what shipped from what did not, and scopes the two things that did not.

---

## 1. What is already built, verified 2026-09-02

[`bet2c-rbac-multitenancy.md`](bet2c-rbac-multitenancy.md) reads *"Status: implemented"*, and the
source agrees:

- `Principal` carries `id`, `tenant_id` and `roles` ([`deps.py`](../../src/orchestrator/registry/api/deps.py)).
- A role on an `Approver` is **enforced** — only a caller holding it may decide.
- Runs, approvals and audit rows are **tenant-scoped**.

**And it is already opt-in, in exactly the shape this spec proposes for secrets.**
`resolve_principal_from_key` has two modes: with a `principals` map configured, per-key tenant and
roles; **without one, the single `api_key` resolves to a wildcard principal in tenant `default`** —
today's single-tenant, everyone-can-approve behaviour, unchanged and requiring no configuration.

That precedent is the whole design below. It is not a new pattern; it is the one already here.

## 2. What is actually outstanding

From the RBAC spec's own follow-ups, plus the piece no spec covers:

| | |
|---|---|
| **A secrets seam** | Not started, and **not mentioned in any spec.** Credentials come from `Settings` (pydantic, `ORCHESTRATOR_` env vars). There is no `get_secret`, no provider interface, nothing to plug a vault into |
| Quorum (`min_required`, N-of-M) | changes the signal contract |
| Tenant-scoping the remaining registry tables | templates, glossary, calibration, and the synchronous task path |
| JWT/OIDC identity + a user store | the single largest of the four |

## 3. The secrets seam

**One interface, whose default implementation is today's behaviour.**

```
get_secret("llm_api_key")     ->  env var, exactly as Settings reads it now
```

A vault is a *second* implementation, selected by configuration. Nobody who does not configure one
ever learns it exists — the same two-mode shape as `resolve_principal_from_key`.

**Two rules make "optional" real rather than nominal**, and both are cheap:

1. **The default path must not import a vault client.** It lives behind an extra
   (`pip install synaptixs-spine[vault]`), the way every language parser already does. Otherwise
   "optional" still costs every developer a dependency and an import.
2. **A test runs the read-only path with an empty environment.** This is the rule that stops
   *optional* quietly becoming *required* eighteen months later, which is the real failure mode —
   not the initial design, which nobody gets wrong.

## 4. The invariant, and why it is testable today

> **No read-only surface gains a credential, a tenant, or a configuration requirement.**

G4 states this as an adoption invariant — *"Read-only stays read-only and credential-free. The
frictionless path's whole value is that it is safe to try."* This spec makes it enforceable.

**Measured 2026-09-02:** `orchestrator state` on a scratch repository, in a process whose
environment holds only `PATH` and `HOME`, exits **0** with empty stderr. The property is true now;
it simply has nothing defending it. A test asserting it costs one function.

**Why it matters commercially, stated plainly:** the free path and the governed path are different
code paths over one graph. A developer runs comprehension with no key, no account and no service; an
enterprise runs the write path with roles, tenants and a vault. Enterprise features are *additive*
until something on the read path starts requiring configuration — at which point the 60-second
first touch dies and the adoption argument dies with it.

## 5. Plan

| Phase | Work | Effort | Exit |
|---|---|---|---|
| **1 — The invariant test** | Assert the read-only path runs with an empty environment. No feature work. | ~0.5 d | CI fails if `state`/`understand` ever needs configuration |
| **2 — The secrets seam** | `get_secret` with the env-var implementation as default; `Settings` reads through it. **No vault yet.** | ~1–2 d | Behaviour byte-identical; nothing new is required or installed |
| **3 — One vault provider** | A single implementation behind the `[vault]` extra, chosen against a named operator's actual vault. | ~3–5 d | An operator configures it and nothing else changes |
| **4 — The remaining identity work** | Quorum, then the untenanted tables, then JWT/OIDC — in that order, each against a named buyer. | unscoped | — |

**Phase 1 is worth doing whatever happens to the rest**, including if item 4 stays parked: it costs
half a day and defends the property the entire adoption story rests on.

**Phase 4 is deliberately unscoped.** JWT/OIDC is a large surface, and the RBAC spec already says
these arrive *"when a buyer needs them"*. Scoping them without one is how a roadmap acquires work
nobody asked for.

## 6. Decisions

| | Decision | Recommendation |
|---|---|---|
| **D1** | Build the seam before any vault exists? | **Yes.** It is a refactor with no behaviour change, and it is what makes the vault a plug rather than a rewrite |
| **D2** | Which vault first? | **None, until a named operator says which one they run.** Building for a vault nobody uses is a maintenance burden with a version number |
| **D3** | Should the seam cover the CLI's LLM key too, or only the service? | **Service only.** The CLI's key is the user's own, in their own environment; routing it through a provider adds a concept for no gain |
| **D4** | Do quorum and OIDC belong in this spec? | **Named, not scoped.** They are the RBAC spec's follow-ups and belong to it; this spec exists to separate them from the secrets work they were bundled with |

## 7. Non-goals

- **A user store, or an identity provider of our own.** OIDC delegates that; anything else rebuilds it.
- **Encrypting the graph, or secrets *in* the graph.** The PKG records structure, not credentials.
- **Making RBAC mandatory.** Its opt-in default is the reason a developer can run the service at all.

## 8. Open questions

1. **Is there a named operator for phase 3?** D2 turns on it, and nothing else in this spec is blocked by the answer.
2. **Does anything outside `registry/api` read a credential?** Unmeasured. The seam's blast radius is whatever that answer is, and it should be counted before phase 2 rather than discovered during it.
