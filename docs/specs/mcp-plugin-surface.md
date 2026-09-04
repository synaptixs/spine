# The MCP plugin surface — what it is, what it lacks, and the order to extend it

**Status:** **Phase 1 COMPLETE** — all six steps shipped, all six gaps closed. Phase 2 in progress — steps 1 (prompts + resources) and 2 (progress) shipped. **Written 2026-09-04 against 3.30.0**,
after #316 removed the terminal UI and left the plugin as the only non-browser operator face.
**Owner:** _unassigned_

**One-liner:** Spine already *is* an MCP server with 20 tools in three tiers (24 after Phase 1); the work is not a
new server but closing six known gaps in this one, in an order that retires the gaps before
adding any surface.

> **Read this before adding a tool.** A tool without a tier does not register (`_TIER` in
> `plugin/server.py`), and the tier decides the annotations a host sees. Decide what the tool can
> cost first; the code refuses to let you skip that.

---

## 1. What is true at 3.30.0

Verified by reading `src/orchestrator/plugin/`, `plugins/spine/`, `tests/plugin/`, and by building
the server from the checkout and listing its tools.

Two packages carry the letters "mcp", and they are inverses:

| Package | Role |
|---|---|
| `orchestrator/plugin/` | Spine **is** the server. This record. |
| `orchestrator/mcp/` | Spine **uses** servers: onboarding, allow-lists; `agentic/mcp_tools.py` bridges them into the codegen loop. |

The server is a façade over the real engine. Tool implementations are module-level functions in
`plugin/server.py`, testable without the `mcp` extra; `build_server()` lazy-imports the SDK
(`mcp.server.MCPServer`, SDK v2) and registers the `_TOOLS` tuple.

**Transports.** stdio by default (a desktop host launches `orchestrator-mcp` as a subprocess;
`.mcp.json` and the plugin manifest both point at it with no args). `--http` serves
streamable-http for hosted clients, with `--host/--port/--path/--stateless` and
`ORCHESTRATOR_MCP_HOST/PORT/PATH` fallbacks. `ORCHESTRATOR_DOTENV` names an absolute `.env`,
because a host's cwd is not the repo.

**Auth (HTTP only).** An OAuth 2.1 resource server: it verifies bearer tokens and never mints
them. Introspection (`ORCHESTRATOR_MCP_INTROSPECTION_URL` + client id/secret, issuer and resource
URLs) or a static shared secret (`ORCHESTRATOR_MCP_TOKEN`, constant-time compare). A non-loopback
bind with neither configured is refused unless `--allow-unauthenticated`.

**Scopes follow the tiers (Phase 1 step 5).** `spine:read` for comprehension and observing a
run, `spine:plan` for the build document and its approval, `spine:run` for anything that spends
money or writes where it cannot be taken back. The SDK checks scopes once, server-wide, and only
for the floor (`spine:read`); each registered tool is wrapped in a guard that reads the verified
token at call time and refuses — naming the scope it needed and the scopes the token has — when
the tier's scope is missing. Over stdio there is no token and the guard passes.
`ORCHESTRATOR_MCP_REQUIRED_SCOPES` is what the **static token carries** (default: all three), so
a read-only token is that variable set to `spine:read`. The legacy `sdlc` scope expands to all
three for one release.

**Packaging.** A Claude Code plugin at `plugins/spine/` bundling the `understand-codebase` skill;
marketplace entry in `.claude-plugin/marketplace.json`; `pip install 'synaptixs-spine[all]'`.

## 2. The 32 tools, in three tiers plus an operator set

The tiers are separated by what a tool can cost you if it is wrong. An assistant works **down**
them: comprehend, then plan and get the plan approved, then build.

| Tier | Tools | Cost |
|---|---|---|
| **1 · comprehend** | `doctor`, `map_repo`, `blast_radius`, `explain_symbol`, `investigate`, `localize`, `regression_gaps`, `root_cause`, `docs_for`, `pkg_joins`, `read_memory_bank`, `pkg_grounding`, `ingest_preview` | No credentials, no model, deterministic. `root_cause(use_llm=true)` is the one opt-in model call, and it still never changes code. `repo_path` is a local path or a git URL; `blast_radius` and `investigate` answer across repositories via `repos` (`.spine/repos.yaml`). |
| **2 · plan** | `sdlc_plan`, `sdlc_approve` | Writes only under `.spine/`. Still no model, no credentials — which is what lets a host with its own model drive Spine on a machine where Spine has neither. |
| **the free back half** | `understand_repo`, `profile_repo`, `design_change`, `sdlc_baseline` | Deterministic, no credentials (`design_change(use_llm=true)` is the opt-in model call). `understand_repo` is the one write — under `episteme/` or `out` — so it carries plan scope; the rest are tier 1. |
| **the gated back half** | `sdlc_address_review`, `sdlc_complete`, `sdlc_remediate`, `audit_repo` | Each spends money or writes outside the repo. The first two have no local mode and need `confirm=true` on every call; `remediate` gates `live` like `sdlc_feature`; `audit_repo` writes nothing but runs a model — read-only for the host, run scope for the token. |
| **operate** | `registry_runs`, `registry_approvals`, `registry_trace`, `registry_decide` | Over HTTP to the registry (`orchestrator up`); needs only the API URL and key. Observing is read-only; `registry_decide` is destructive because a rejection ends a run. |
| **3 · run** | `sdlc_feature`, `sdlc_start_run`, `sdlc_run_status`, `sdlc_decide_gate`, `sdlc_run_result` | Spends tokens. `live=true` (or `create_jira=true`) writes where it cannot be taken back and needs `confirm=true` on top of the host's own confirmation. The run set needs the Temporal backend. |

### The tiers as annotations (Phase 1)

Prose tiers are read by people. MCP `ToolAnnotations` are read by hosts, which use them to decide
what to confirm. From Phase 1 every registration carries the four hints, derived from `_TIER`:

| Tools | read-only | destructive | idempotent | open-world |
|---|---|---|---|---|
| Tier 1 | yes | no | yes | yes — `repo_path` may be a URL, a `source` may be remote |
| `doctor`, `pkg_joins` | yes | no | yes | no — local only |
| `sdlc_plan`, `sdlc_approve` | no | no | yes — re-running rewrites the same document | no |
| `understand_repo` | no | no | yes | yes — `repo_path` may be a URL; the write stays under `episteme/` |
| `sdlc_run_status`, `sdlc_run_result` | yes | no | yes | yes — they read Temporal |
| `registry_runs`, `registry_approvals`, `registry_trace` | yes | no | yes | yes — they read the registry |
| `registry_decide` | no | yes | no | yes |
| `sdlc_feature`, `sdlc_start_run`, `sdlc_decide_gate`, `sdlc_address_review`, `sdlc_complete`, `sdlc_remediate` | no | yes | no | yes |
| `audit_repo` | yes | no | no — a model ran | yes |

Deciding a gate is destructive because a rejection ends a run. `_TIER` is total by construction:
`_register_tools` raises for a tool it does not list, and `tests/plugin` asserts the table and
`_TOOLS` name the same set, so a new tool cannot reach a host with its cost unstated.

## 3. The gaps

Numbered, because §5 retires them by number.

1. **No operator-ops tools.** Nothing lists registry runs or pending approvals generically;
   `sdlc_decide_gate` covers only an sdlc run's own gates. This is what the removed TUI did.
   *(Closed in Phase 1 step 4: `registry_runs`, `registry_approvals`, `registry_trace`,
   `registry_decide` — §2, operator tools.)*
2. **`__all__` drifted from `_TOOLS`.** Four registered tools were not exported. *(Closed in
   Phase 1, with a test.)*
3. **The tiers were prose only.** A host could not tell a read-only tool from a money-spending
   one. *(Closed in Phase 1 — §2.)*
4. **One scope for everything.** A token that may call `map_repo` may call `sdlc_feature`.
   *(Closed in Phase 1 step 5: per-tier scopes, checked per call on HTTP — §1.)*
5. **The back half of the pipeline is CLI-only.** `sdlc address-review`, `sdlc complete`,
   `sdlc remediate`, `sdlc baseline`, `design`, `audit`, `profile`, `understand`, `state` exist
   as commands and not as tools. An assistant can open the PR but cannot drive what follows.
   *(Closed. 6a — the free, deterministic half: `understand_repo`, `profile_repo`,
   `design_change`, `sdlc_baseline`; `state` turned out to be `map_repo` already. 6b — the
   gated half: `sdlc_address_review`, `sdlc_complete`, `sdlc_remediate`, `audit_repo`. The
   clone-and-checkout and the merge→Done logic moved from the CLI into the engine —
   `checkout_pr_worktree`, `sdlc/complete.py` — so the CLI and the plugin share one
   implementation.)*
6. **A stale install was invisible.** The `orchestrator-mcp` on one machine's PATH pointed at
   another checkout's venv (Spine 3.9.3, no `mcp` module); the host saw "Connection closed" and
   nothing said why. *(Closed in Phase 1: `doctor` reports version, interpreter, SDK and extras,
   as a tool and as a CLI header.)*

## 4. Proposals

Each is scoped to one PR. The number is a name, not an order — §5 is the order.

- **4.1 Annotations and typed output.** Annotations: Phase 1. Typed output schemas (a model per
  tool instead of `dict[str, Any]`) are deferred to their own PR: the SDK already advertises an
  open-object schema, and real models touch twenty return shapes and their tests.
- **4.2 Operator-ops tools.** *(Shipped.)* `registry_runs`, `registry_approvals`,
  `registry_trace`, `registry_decide` over the registry `/v1` API with `X-API-Key`, through
  `plugin/registry_client.py` (the removed TUI client, reinstated and extended). Config via
  `ORCHESTRATOR_API_URL` / `ORCHESTRATOR_API_KEY`. The `registry_` prefix mirrors `sdlc_`: it
  names the surface, and says the tool needs the server up. Over HTTP rather than in-process
  because the plugin process then needs no database or Temporal credentials, the registry
  enforces tenant scoping, and the audit log records the key's principal as the actor.
  `registry_trace` is bounded — the newest `tail` entries plus a `truncated` count.
- **4.3 Per-tier scopes on HTTP.** *(Shipped.)* `spine:read`, `spine:plan`, `spine:run`, each
  tier naming its scope beside its annotations, enforced by a call-time guard around every
  registered tool — the SDK only checks scopes server-wide, and a request does not name its tool
  until the protocol layer has parsed it. Stdio is untouched. `sdlc` expands to all three for one
  release, then goes.
- **4.4 `understand_repo` and `current_state` as tools.** *(Shipped in 6a — `understand_repo`
  builds or checks the bank; `current_state` was already `map_repo`.)* `understand_repo` refuses
  a build on a git URL unless `out` is absolute: the clone vanishes, and a bank written into it
  with it. It returns the three entry pages and the counts, not every path.
- **4.5 The post-PR loop.** *(Shipped, in two halves.)* 6a: `profile_repo`, `design_change`,
  `sdlc_baseline` — deterministic, free, tier 1. 6b: `sdlc_address_review`, `sdlc_complete`,
  `sdlc_remediate` as tier-3 tools under the same `confirm` gate as `sdlc_feature(live=true)`
  (the first two on every call — they have no local mode), and `audit_repo`, which spends
  tokens on a persona loop — read-only in annotations, run scope, its own tier row.
- **4.6 Resources.** *(Shipped in Phase 2 step 1 — for the default repository, `SPINE_REPO_ROOT` or the cwd, because a URI segment cannot carry a path; `spine://bank`, `spine://bank/{section}`, `spine://plans`, `spine://plan/{intent_id}`, `spine://state`.)* Originally sketched as `spine://episteme/{repo}/{section}`, `spine://plan/{repo}/{intent}`,
  `spine://state/{repo}` as MCP resources — listable, subscribable, attachable as context.
- **4.7 Prompts.** *(Shipped in Phase 2 step 1 — five prompts in `plugin/prompts.py`, the source of the workflow because the skill is not in the wheel; a test holds the skill to the same tools.)* The `understand-codebase` skill's guidance registered as MCP prompts so Codex,
  Desktop and claude.ai get the "which tool, in which order" workflow through the protocol.
- **4.8 Progress.** *(Shipped in Phase 2 step 2.)* The five long tools receive the SDK
  `Context` through an optional parameter the scope wrapper passes through untouched and the
  schema never shows. The phases come from the engines' existing log hooks — the feature
  runner's bracketed prefixes, mapped to ordered steps in `plugin/progress.py` — not from new
  instrumentation, so the CLI and the plugin describe the same stages. Monotonic (a high-water
  mark); an unknown line rides on the current step as its message, because MCP's separate
  logging capability is deprecated (SEP-2577). `audit_repo` is start/done only: its loop has no
  per-step hook. `root_cause(use_llm)` and `design_change(use_llm)` are single model calls and
  stay as they are.
- **4.9 Self-describing `doctor`.** Phase 1.
- **4.10 Multi-repo parity.** `explain_symbol`, `regression_gaps`, `localize`, `docs_for` take
  `repos` like `blast_radius` and `investigate` do.
- **4.11 Per-principal audit on HTTP.** Tier-3 invocations recorded against the token principal,
  in the registry's existing audit log.

## 5. Order — gaps first

### Phase 1 — retire the gaps

| Step | Gap | Work | State |
|---|---|---|---|
| 1 | 6 | Fix the stale install; ship 4.9. | **this PR** |
| 2 | 2 | Sync `__all__`; a test holds it. | **this PR** |
| 3 | 3 | 4.1 annotations (typed output deferred). | **this PR** |
| 4 | 1 | 4.2 operator-ops tools. | **shipped** |
| 5 | 4 | 4.3 scopes — before step 4 is served over HTTP to anyone else. | **shipped** |
| 6a | 5 | The free half: `understand_repo`, `profile_repo`, `design_change`, `sdlc_baseline`. | **shipped** |
| 6b | 5 | The gated half: `sdlc_address_review`, `sdlc_complete`, `sdlc_remediate`, `audit_repo`. | **shipped** |

### Phase 2 — extensions (§3 is empty as of 6b)

| Step | Proposals | State |
|---|---|---|
| 1 | 4.7 prompts + 4.6 resources — protocol parity for non-Claude hosts | **shipped** |
| 2 | 4.8 progress notifications from the long tools | **shipped** |
| 3 | 4.10 multi-repo parity for `explain_symbol`, `regression_gaps`, `localize`, `docs_for` | next |
| 4 | 4.11 per-principal audit on HTTP | |
| 5 | typed output (the deferred half of 4.1) | last — every return shape |
| — | retire the `sdlc` scope alias | the release after 3.31.0 |

## Invariants

- **A tool without a tier does not register.** The refusal lives in `_register_tools`, not in a
  review checklist.
- **A tool's scope is its tier's scope.** `tool_scope(name)` reads the same `_TIER` row as
  `tool_annotations(name)`; the guard is applied at registration to every tool, so a tool cannot
  be reachable on HTTP without a scope check any more than without a tier.
- **Annotations are derived, never hand-copied.** `tool_annotations(name)` is the one source;
  the protocol-level test compares what a host sees against it field for field.
- **Tier 1 and 2 stay model-free and credential-free.** That is the property the keyless
  roadmap ([codex-plugin-keyless-roadmap](codex-plugin-keyless-roadmap.md)) depends on.
- **The tool implementations stay importable without the `mcp` extra.** Everything that needs
  the SDK is inside `build_server` / `_register_tools`.

## Non-goals

- A second MCP server. There is one; it grows.
- A terminal UI. Removed in #316 for adding no capability the other faces lacked; 4.2 is its
  successor where an assistant is the operator.
- Deploy or rollback tooling. The pipeline ends at the merge.
