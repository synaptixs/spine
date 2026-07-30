# Bet 2c-ii — RBAC + multi-tenancy (G11)

> Status: **implemented.** The design below is as-built; see "As built" at the
> end for where it firmed up. Turns the two unenforced halves of the approval
> plane into real controls: **(a)** the role on an `Approver` is *enforced* — only
> a caller holding a required role may decide an approval; **(b)** runs, approvals,
> and audit rows are **tenant-scoped** so one tenant can neither see nor decide
> another's. The last slice of the trust spine ([bet2-trust-spine.md](bet2-trust-spine.md)).

## Today (what exists, what's unenforced)

- **Auth** is a single static API key (`Settings.api_key`); `require_api_key`
  returns the raw key **string** ([deps.py:21](src/orchestrator/registry/api/deps.py)).
  No identity, no roles, no tenant.
- **Approver roles flow but are never checked.** The workflow raises an approval
  with `approvers=[Approver(role=…)]` (from the IR's `approver_roles`), persisted
  in `approvers_json`. The decide path (`_decide` → `repo.decide`) sets
  `decided_by = actor` (the API-key string) **without verifying** the caller holds
  any required role. The module docstring says as much: *"free-form string today…
  RBAC lands later."*
- **No tenant anywhere.** Grep confirms `tenant_id`/`org_id` don't exist as
  columns or fields — only forward-looking comments. `GET /v1/approvals` and
  `GET /v1/runs` return **everything** to any authenticated caller.

## Design

Two independent but co-delivered pieces. Keep it minimal and real — per the
locked risk note, *don't over-build RBAC before a buyer needs it*: no JWT/OIDC
stack, no user store. Extend the auth the project already has.

### A. Identity: API-key → Principal map

Replace the bare-string auth with a `Principal` carrying tenant + roles, resolved
from config — the smallest change that makes enforcement *real* rather than
theatrical.

```python
@dataclass(frozen=True)
class Principal:
    id: str                 # stable subject id (for decided_by / audit actor)
    tenant_id: str
    roles: frozenset[str]   # e.g. {"approver", "admin"}

    def has_role(self, *needed: str) -> bool:
        return "*" in self.roles or any(r in self.roles for r in needed)
```

- `Settings` gains an optional `principals` map (a JSON env var
  `ORCHESTRATOR_PRINCIPALS`): `key → {id, tenant_id, roles}`. `require_api_key`
  becomes `require_principal(...) -> Principal`, matching the presented
  `X-API-Key` against the map.
- **Backward compatible.** When `principals` is unset, the existing single
  `api_key` resolves to a **default principal** — `tenant_id="default"`,
  `roles={"*"}` (the wildcard satisfies every role check). So a single-tenant
  self-host behaves exactly as today: one key, every caller can approve. RBAC +
  tenant isolation switch on only when an operator configures principals.
- `ApiKeyDep` (returns `str`) → `PrincipalDep` (returns `Principal`). The
  `_actor` parameters that just need a string use `principal.id`.

> *Alternative considered:* trusting gateway-injected headers (`X-Tenant-Id`,
> `X-Roles`) behind a reverse proxy. Rejected as the default because it's only
> safe behind a correctly-configured proxy; the key→principal map is
> self-contained and safe by default. (Could be added later as an opt-in
> `identity_source=headers` mode.)

### B. Tenant scoping

- **Schema:** add `tenant_id String(64) NOT NULL DEFAULT 'default'` (indexed) to
  `approval_requests` and `audit_log` ([db/models.py](src/orchestrator/registry/db/models.py)).
  `DEFAULT 'default'` backfills existing rows and keeps single-tenant installs
  working. New alembic migration `0005_add_tenant_id`.
- **Model:** `ApprovalRequest` gains `tenant_id: str = "default"`;
  `ApprovalRequestRepo.create` persists it, `_to_model` reads it.
- **Threading (run → approval):** `tenant_id` rides the workflow inputs
  (`SDLCWorkflowInput`, `FeatureWorkflowInput`, `TaskWorkflowInput`) set from the
  submitting principal at the kickoff endpoints (`/v1/tasks`, the SDLC start
  path), into the `raise_approval_request` payload (both
  `temporal/activities.py` and `sdlc/activities.py`), onto the row.
- **Filtering:** `list_pending`, `get`, and `decide` take a `tenant_id`;
  `GET /v1/approvals` and `GET /v1/runs` filter by the caller's tenant. A
  cross-tenant `get`/`decide` returns **404** (not 403 — don't reveal another
  tenant's ids).

### Enforcement at the decide path

`_decide` ([approvals.py:134](src/orchestrator/registry/api/approvals.py)) gains, before `repo.decide`:

1. **Tenant match** — the row's `tenant_id` must equal `principal.tenant_id`
   (enforced by scoping the lookup to the tenant → 404 otherwise).
2. **Role check** — the principal must hold at least one of the approval's
   required roles: `principal.has_role(*[a.role for a in approval.approvers])`.
   The sentinel role `"any"` (today's default when the IR names no roles) is
   satisfied by any authenticated principal — preserving current behavior for
   approvals that don't specify roles. A mismatch → **403**.
3. `decided_by = principal.id` (stable subject), not the raw key. The audit row
   records `actor=principal.id` and the satisfied role(s).

`GET /v1/approvals` additionally hides approvals the caller couldn't action
(optional, behind the same role check) — *lean: filter to actionable* so the
queue is meaningful, but keep it simple (tenant filter is the must-have; role
filter on the list is a nicety).

## Scope boundaries

- **In:** `Principal` + key→principal map (backward-compatible default), enforced
  role check at decide, tenant column + threading + list/decide filtering, the
  migration, deterministic tests.
- **Out — `min_required` quorum (N-of-M).** Today the workflow signals on the
  *first* decision; true 2-of-3 needs the decide path to collect N approvals
  before signaling. Enforcing *that a decider holds a role* is this PR; enforcing
  *how many* is a documented follow-up (it changes the workflow signal contract).
  `min_required` stays in the model, unenforced, with a clear note.
- **Out:** JWT/OIDC, a user database, per-tenant policy files, tenant-scoping the
  *other* registry tables (templates/glossary/calibration) — add when a buyer
  needs them.

## Migration plan

`migrations/versions/0005_add_tenant_id.py` (down_revision `0004`): `add_column`
`tenant_id` (String(64), nullable=False, server_default `'default'`) on
`approval_requests` and `audit_log`, plus an index on each. Reversible `downgrade`
drops them. (Per project memory: migrations aren't packaged for pip — source
installs run alembic; note in the PR.)

## Test plan (no live services, no Temporal)

1. **Principal resolution:** map hit → correct tenant/roles; unknown key → 401;
   no map configured → default principal (`tenant=default`, `roles={"*"}`).
2. **Role enforcement:** principal with a required role → approve succeeds;
   principal lacking it → 403; `"any"`-role approval → any principal succeeds;
   wildcard `"*"` principal → succeeds.
3. **Tenant isolation:** principal in tenant B decides a tenant-A approval → 404;
   `list_pending` returns only the caller's tenant; `decided_by` == principal.id.
4. **Backward compat:** existing approval tests pass unchanged under the default
   principal (single key, wildcard role, `tenant=default`).
5. **Repo/migration:** `create`+`list_pending`+`get` round-trip `tenant_id`;
   migration upgrade/downgrade against sqlite + the existing test DB.

## Decisions (resolved)

1. **Identity mechanism** — **API-key→Principal map** (`ORCHESTRATOR_PRINCIPALS`
   JSON). Self-contained, safe by default, backward-compatible.
2. **Quorum (`min_required`)** — **deferred.** This PR enforces that a decider
   *holds* a required role; enforcing the *count* (N-of-M) stays a follow-up.

## As built (notes)

- **Identity** lives in `registry/api/deps.py`: `Principal(id, tenant_id, roles)`
  + `require_principal` (two modes). `require_api_key` is kept as a back-compat
  shim returning `principal.id`, so endpoints that only needed the actor string
  are unchanged. `Settings.principals` parses a JSON object (or dict).
- **Enforcement** is in `approvals.py::_decide`: tenant-scoped lookup (cross-tenant
  → 404), then `principal.has_role(*required)` (→ 403). `"any"` (the SDLC default)
  and the wildcard `"*"` (single-key default) both pass — so single-tenant installs
  behave exactly as before. `decided_by` is now the principal id; the audit row
  records the satisfied role(s) + `tenant_id`.
- **Tenant threading**: both workflow paths. SDLC — `SDLCWorkflowInput` /
  `FeatureWorkflowInput`, parent gates + child in-loop gate (2c-i) + all
  `_audit` calls; set at kickoff (`run_control.start_run(tenant_id=…)`,
  `/v1/tasks` from the principal, CLI from `ORCHESTRATOR_TENANT_ID`). Orchestrator —
  `TaskWorkflowInput`, the gate `raise_approval_request` payload, `record_audit`.
  The orchestrator path already passed `approver_roles` from the IR, so role
  enforcement lit up there for free.
- **Lists**: `GET /v1/approvals` and `GET /v1/runs` filter by the caller's tenant.
- **Timeout sweep** (`list_timed_out` → `decide`) is a system process, not a
  tenant caller, so it calls `decide` with no `tenant_id` (the default `None` =
  unscoped) and keeps working across all tenants.
- **Migration** `0005_add_tenant_id` adds the column (server_default `'default'`)
  + index to `approval_requests` and `audit_log`; exercised by the integration
  conftest's migrate step. (Migrations aren't packaged for pip — note for source
  installs.)
- **Tests:** `tests/registry/test_principal.py` (identity, no DB) + RBAC/tenant
  cases in `tests/integration/test_approvals_api.py` (role 403/200, cross-tenant
  404, tenant-scoped list, repo round-trip). Full suite + integration green;
  mypy + ruff clean.

## Out of scope (follow-ups)

- **Quorum** (`min_required` N-of-M) — needs the decide path to collect N approvals
  before the workflow signal; changes the signal contract.
- **Tenant-scoping the other registry tables** (templates / glossary / calibration)
  and the **synchronous (non-Temporal) task path's** audit/approval — add when a
  buyer needs them; this PR covers the approval plane + SDLC/orchestrator runs.
- **JWT/OIDC** identity and a user store.
