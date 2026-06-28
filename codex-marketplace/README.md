# Spine — Codex plugin marketplace

A one-plugin [Codex](https://openai.com/codex) marketplace that packages the Spine
(agent-orchestrator) MCP server as an installable **plugin**, so you can enable it from
Codex's plugin list instead of hand-editing `~/.codex/config.toml`.

## Install

```bash
# 1. make the server available on PATH
pip install 'synaptixs-spine[mcp]'        # provides the `orchestrator-mcp` command

# 2. add this marketplace + install the plugin
codex plugin marketplace add synaptixs/spine        # or a local path to this folder
codex plugin add spine@spine
```

Restart Codex; **Spine** appears in the plugin list. It exposes the orchestrator's MCP
tools — `doctor`, `ingest_preview`, `pkg_grounding`, `read_memory_bank`, `sdlc_feature`
(greenfield via `layout=new`, brownfield via `layout=existing`), and the gated
`sdlc_start_run` / `…_status` / `…_decide_gate` / `…_result`.

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
