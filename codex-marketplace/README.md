# Spine — Codex plugin marketplace

A one-plugin [Codex](https://openai.com/codex) marketplace that packages the Spine
(agent-orchestrator) MCP server as an installable **plugin**, so you can enable it from
Codex's plugin list instead of hand-editing `~/.codex/config.toml`.

## Install

```bash
# 1. make the server available on PATH
pip install 'synaptixs-spine[all]'        # provides the `orchestrator-mcp` command

# 2. add this marketplace + install the plugin
codex plugin marketplace add synaptixs/spine        # or a local path to this folder
codex plugin add spine@spine
```

Restart Codex; **Spine** appears in the plugin list.

> **Use `[all]`, not `[mcp]`.** `[mcp]` installs the server and nothing else, so the graph
> is **Python-only** — and silently: a Java or Go repo yields zero nodes rather than an
> error, which reads as "nothing here" instead of "nothing parsed". `[all]` adds every
> language front-end, doc ingestion and the MCP server. `[languages]` is the front-ends
> alone.

## What it exposes

**Comprehension — read-only, deterministic, no credentials.** `map_repo`, `blast_radius`
("what breaks if I change X"), `explain_symbol`, `investigate` (where a ticket lands),
`localize` (stack trace → fault site), `regression_gaps` (blast radius with no covering
test), `root_cause`, `docs_for`, plus `read_memory_bank` (a repo's committed `episteme/`),
`pkg_grounding` and `doctor`. Each takes a local path **or a git URL**, and returns typed
fields **plus** a `markdown` rendering.

**Across several repositories.** Declare your services in a `.spine/repos.yaml` and pass it
as `repos=` to `blast_radius` or `investigate`: a handler with zero callers in its own
source then names the service that depends on it. `pkg_joins` proposes or checks that
topology — read-only, it never writes a config.

**Plan and decide.** `sdlc_plan` renders a twelve-section build document from the graph, git
and the tree (no model, no credentials); `sdlc_approve` records the decision on it. Both
write only under `.spine/`.

**Gated codegen.** `sdlc_feature` (greenfield via `layout=new`, brownfield via
`layout=existing`) and the run-control set `sdlc_start_run` / `…_status` / `…_decide_gate` /
`…_result`. These spend real money, and `live=true` additionally requires `confirm=true`.

## Credentials

The server reads provider/source/tracker creds from a `.env`. Either run Codex from a
project that has one, or point the server at an absolute path by setting
`ORCHESTRATOR_DOTENV=/abs/path/to/.env` in the plugin's environment (Codex
`[mcp_servers.spine.env]` in `~/.codex/config.toml`). Read-only tools
(`pkg_grounding`, `doctor`) work without creds.

## Layout

```
codex-marketplace/
  .agents/plugins/marketplace.json   # marketplace manifest (the plugin list)
  plugins/spine/
    .codex-plugin/plugin.json        # plugin manifest (branding + mcpServers ref)
    .mcp.json                        # declares the `orchestrator-mcp` MCP server
```
