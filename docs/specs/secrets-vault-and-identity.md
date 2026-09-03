# Secrets, and the identity work still open

**Status:** **Scoped 2026-09-02 against 3.28.0. Phases 1 and 2 shipped 2026-09-03.** D1, D2, D3 decided; D4 stays *named, not scoped*. Phase 3 waits on an operator who wants direct fetch.
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

### 3.1 — Which provider first, and the one that was rejected

**Decided 2026-09-03: HashiCorp Vault, with OpenBao covered by the same client, behind the
`[vault]` extra.** Environment stays the default. Cloud-specific managers — AWS Secrets Manager,
Azure Key Vault, Google Secret Manager, OCI Vault — come **one named customer each**, never
speculatively, each behind its own extra.

Why that one, against an agnostic charter:

- **It is the only cloud-neutral option.** Each cloud manager picks a winner the customer may not
  be in; Vault runs on-prem and in all four.
- **It is the standard the others converge on.** OpenBao is API-compatible (the Linux Foundation
  fork, if a customer's licence review needs it), and Akeyless, Conjur and the cloud managers all
  carry Vault-shaped migration paths. One client covers the largest share of *bring your own*.
- **It is the implementation that actually tests the seam.** A real vault needs an auth method
  (token, AppRole, Kubernetes), path addressing, and lease/TTL handling. The environment provider
  can exercise none of that, so it cannot by itself prove `get_secret` is an abstraction rather
  than a wrapper around one case. Vault is the second implementation that does.
- **It also covers the injection pattern.** Vault Agent renders secrets into the environment for
  operators who prefer that, and the default provider handles it with no code at all.

**Rejected: a file provider as the "safe, dependency-free" second implementation.** It looked
attractive — every platform can mount a secret as a file, and it costs nothing — and it is not
safe. A plaintext secret on disk is a static secret with a new attack surface: readable by any
process with the right uid, baked into an image by accident, swept into a backup, with no rotation
and no audit trail. It is precisely what a security review flags, and it advances the vault story
not at all. Recorded because it will be proposed again for the same reason it was proposed here.

**Effect on the plan:** phase 3 is no longer *"which vault?"*. It is Vault/OpenBao, still gated on
an operator who wants **direct fetch** rather than injection — because for operators on ESO, ECS
task secrets, Container Apps references or Vault Agent, the environment default already covers
them and there is no phase 3 at all.

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
| **1 — The invariant test** ✅ | `tests/test_read_path_needs_no_configuration.py` — `state`, `understand`, `investigate` and `pkg extract` each run in a process holding only `PATH` and `HOME`, via the console script. A fifth test proves the harness observes a non-zero exit, so it cannot pass by construction. **Shipped 2026-09-03.** | 0.5 d | CI fails if any read-only command ever needs configuration |
| **2 — The secrets seam** ✅ | `core/secrets.py`: `get_secret(name)` with `EnvSecretProvider` as the default, selected by `ORCHESTRATOR_SECRETS_PROVIDER`, and `register_provider` for anything behind an extra. `Settings` reads its four credential fields through a pydantic source ordered *after* init kwargs and *before* env; Temporal, MCP auth and the object store call `get_secret` directly. **Shipped 2026-09-03.** | 1 d | Behaviour byte-identical under the default; a fake provider registered in-test is consulted by `Settings` and the object store, proving the seam is an abstraction; a fresh interpreter importing `core.secrets` loads no client |
| **3 — HashiCorp Vault / OpenBao** | One client behind the `[vault]` extra — token, AppRole and Kubernetes auth, path addressing, lease/TTL. Built when an operator wants **direct fetch** rather than injection; for injection the phase-2 default already suffices. | ~3–5 d | An operator configures it and nothing else changes; the default install still imports no client |
| **4 — The remaining identity work** | Quorum, then the untenanted tables, then JWT/OIDC — in that order, each against a named buyer. | unscoped | — |

**Phase 1 is worth doing whatever happens to the rest**, including if item 4 stays parked: it costs
half a day and defends the property the entire adoption story rests on.

**Phase 4 is deliberately unscoped.** JWT/OIDC is a large surface, and the RBAC spec already says
these arrive *"when a buyer needs them"*. Scoping them without one is how a roadmap acquires work
nobody asked for.

## 6. Decisions

| | Decision | Recommendation |
|---|---|---|
| **D1** | Build the seam before any vault exists? | ✅ **Decided 2026-09-03: yes.** A refactor with no behaviour change, and what makes the vault a plug rather than a rewrite |
| **D2** | Which vault first? | ✅ **Decided 2026-09-03: HashiCorp Vault / OpenBao**, behind `[vault]` — the only cloud-neutral option and the one that actually exercises the seam (§3.1). Cloud-specific managers follow one named customer each. A file provider was considered as the dependency-free alternative and **rejected** as a static secret with a new attack surface |
| **D3** | Should the seam cover the CLI's LLM key too, or only the service? | ✅ **Decided 2026-09-03: service only.** The CLI's key is the user's own, in their own environment; routing it through a provider adds a concept for no gain — and keeps the read path untouched, which phase 1 now enforces |
| **D4** | Do quorum and OIDC belong in this spec? | **Named, not scoped.** They are the RBAC spec's follow-ups and belong to it; this spec exists to separate them from the secrets work they were bundled with |

## 7. Non-goals

- **A user store, or an identity provider of our own.** OIDC delegates that; anything else rebuilds it.
- **Encrypting the graph, or secrets *in* the graph.** The PKG records structure, not credentials.
- **Making RBAC mandatory.** Its opt-in default is the reason a developer can run the service at all.

## 8. Open questions

1. **Is there an operator who wants direct fetch rather than injection?** That, not *"which vault"*, is what gates phase 3 now — for an operator whose platform already injects into the environment, phase 2 is the whole story. Nothing else in this spec is blocked by the answer.
2. ~~**Does anything outside `registry/api` read a credential?**~~ **Measured 2026-09-03, before
   phase 2 was built.** Four service-side sites, now all behind the seam: `Settings` (four fields —
   `database_url`, `api_key`, `principals`, `session_secret`), `temporal/config.py`
   (`TEMPORAL_API_KEY`), `plugin/auth.py` (`ORCHESTRATOR_MCP_TOKEN`,
   `ORCHESTRATOR_MCP_INTROSPECTION_CLIENT_SECRET`) and `storage/client.py` (the object-store key
   pair). **Deliberately left alone under D3:** `cli/_common.py`, which reads the *client's* key to
   call the service, and `cli/start.py`, the launcher that seeds the service's environment — both
   are the user's own environment, and the read-only path phase 1 pins must stay untouched.
