# The MCP plugin surface — what it is, what it lacks, and the order to extend it

**Status:** Phase 1 in progress (this record's first PR). **Written 2026-09-04 against 3.30.0**,
after #316 removed the terminal UI and left the plugin as the only non-browser operator face.
**Owner:** _unassigned_

**One-liner:** Spine already *is* an MCP server with 20 tools in three tiers; the work is not a
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
URLs) or a static shared secret (`ORCHESTRATOR_MCP_TOKEN`, constant-time compare). One scope list
for the whole server (`ORCHESTRATOR_MCP_REQUIRED_SCOPES`, default `sdlc`). A non-loopback bind
with neither configured is refused unless `--allow-unauthenticated`.

**Packaging.** A Claude Code plugin at `plugins/spine/` bundling the `understand-codebase` skill;
marketplace entry in `.claude-plugin/marketplace.json`; `pip install 'synaptixs-spine[all]'`.

## 2. The 20 tools, in three tiers

The tiers are separated by what a tool can cost you if it is wrong. An assistant works **down**
them: comprehend, then plan and get the plan approved, then build.

| Tier | Tools | Cost |
|---|---|---|
| **1 · comprehend** | `doctor`, `map_repo`, `blast_radius`, `explain_symbol`, `investigate`, `localize`, `regression_gaps`, `root_cause`, `docs_for`, `pkg_joins`, `read_memory_bank`, `pkg_grounding`, `ingest_preview` | No credentials, no model, deterministic. `root_cause(use_llm=true)` is the one opt-in model call, and it still never changes code. `repo_path` is a local path or a git URL; `blast_radius` and `investigate` answer across repositories via `repos` (`.spine/repos.yaml`). |
| **2 · plan** | `sdlc_plan`, `sdlc_approve` | Writes only under `.spine/`. Still no model, no credentials — which is what lets a host with its own model drive Spine on a machine where Spine has neither. |
| **3 · run** | `sdlc_feature`, `sdlc_start_run`, `sdlc_run_status`, `sdlc_decide_gate`, `sdlc_run_result` | Spends tokens. `live=true` (or `create_jira=true`) writes where it cannot be taken back and needs `confirm=true` on top of the host's own confirmation. The run set needs the Temporal backend. |

### The tiers as annotations (Phase 1)

Prose tiers are read by people. MCP `ToolAnnotations` are read by hosts, which use them to decide
what to confirm. From Phase 1 every registration carries the four hints, derived from `_TIER`:

| Tools | read-only | destructive | idempotent | open-world |
|---|---|---|---|---|
| Tier 1 | yes | no | yes | yes — `repo_path` may be a URL, a `source` may be remote |
| `doctor`, `pkg_joins` | yes | no | yes | no — local only |
| `sdlc_plan`, `sdlc_approve` | no | no | yes — re-running rewrites the same document | no |
| `sdlc_run_status`, `sdlc_run_result` | yes | no | yes | yes — they read Temporal |
| `sdlc_feature`, `sdlc_start_run`, `sdlc_decide_gate` | no | yes | no | yes |

Deciding a gate is destructive because a rejection ends a run. `_TIER` is total by construction:
`_register_tools` raises for a tool it does not list, and `tests/plugin` asserts the table and
`_TOOLS` name the same set, so a new tool cannot reach a host with its cost unstated.

## 3. The gaps

Numbered, because §5 retires them by number.

1. **No operator-ops tools.** Nothing lists registry runs or pending approvals generically;
   `sdlc_decide_gate` covers only an sdlc run's own gates. This is what the removed TUI did.
2. **`__all__` drifted from `_TOOLS`.** Four registered tools were not exported. *(Closed in
   Phase 1, with a test.)*
3. **The tiers were prose only.** A host could not tell a read-only tool from a money-spending
   one. *(Closed in Phase 1 — §2.)*
4. **One scope for everything.** A token that may call `map_repo` may call `sdlc_feature`.
5. **The back half of the pipeline is CLI-only.** `sdlc address-review`, `sdlc complete`,
   `sdlc remediate`, `sdlc baseline`, `design`, `audit`, `profile`, `understand`, `state` exist
   as commands and not as tools. An assistant can open the PR but cannot drive what follows.
6. **A stale install was invisible.** The `orchestrator-mcp` on one machine's PATH pointed at
   another checkout's venv (Spine 3.9.3, no `mcp` module); the host saw "Connection closed" and
   nothing said why. *(Closed in Phase 1: `doctor` reports version, interpreter, SDK and extras,
   as a tool and as a CLI header.)*

## 4. Proposals

Each is scoped to one PR. The number is a name, not an order — §5 is the order.

- **4.1 Annotations and typed output.** Annotations: Phase 1. Typed output schemas (a model per
  tool instead of `dict[str, Any]`) are deferred to their own PR: the SDK already advertises an
  open-object schema, and real models touch twenty return shapes and their tests.
- **4.2 Operator-ops tools.** `runs_list`, `run_trace`, `approvals_list`, `approval_decide` over
  the registry `/v1` API with `X-API-Key`, reinstating the removed 52-line registry client as
  `plugin/registry_client.py`. Config via `ORCHESTRATOR_API_URL` / `ORCHESTRATOR_API_KEY`.
- **4.3 Per-tier scopes on HTTP.** `spine:read`, `spine:plan`, `spine:run`, checked per tool at
  registration; `sdlc` accepted as an alias for one release.
- **4.4 `understand_repo` and `current_state` as tools.** Both deterministic by invariant, so
  they fit tier 2 without new policy. Today `read_memory_bank` can only read a bank someone
  already committed.
- **4.5 The post-PR loop.** `sdlc_address_review`, `sdlc_complete`, `sdlc_remediate`,
  `sdlc_baseline`, `design` as tier-3 tools under the same `live`/`confirm` gate.
- **4.6 Resources.** `spine://episteme/{repo}/{section}`, `spine://plan/{repo}/{intent}`,
  `spine://state/{repo}` as MCP resources — listable, subscribable, attachable as context.
- **4.7 Prompts.** The `understand-codebase` skill's guidance registered as MCP prompts so Codex,
  Desktop and claude.ai get the "which tool, in which order" workflow through the protocol.
- **4.8 Progress.** Per-phase progress notifications from `sdlc_feature` and
  `root_cause(use_llm=true)`, which run for minutes with no signal today.
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
| 4 | 1 | 4.2 operator-ops tools. | next |
| 5 | 4 | 4.3 scopes — before step 4 is served over HTTP to anyone else. | after 4 |
| 6 | 5 | 4.4, then 4.5. | after 5 |

### Phase 2 — extensions, once §3 is empty

4.7 and 4.6 (protocol parity), then 4.8, 4.10, 4.11 as the run tier sees real use, and the
typed-output half of 4.1 on its own.

## Invariants

- **A tool without a tier does not register.** The refusal lives in `_register_tools`, not in a
  review checklist.
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
