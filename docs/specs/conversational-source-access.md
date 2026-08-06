# Conversational source access — ask for it, don't construct a URI

**Status:** Proposed. Not started.
**Owner:** _unassigned_

**One-liner:** replace "know the URI scheme and the opaque id" with "say what you want, paste
what you have" — an operator should be able to hand Spine a Jira URL, an epic key, or a
sentence, and reach the same governed MCP tools that `jira://` reaches today.

---

## Why

Reading a ticket today requires constructing `jira://CB-676` or `confluence://3329359873`.
Two things are wrong with that.

**The identifier is the hard part, and we ask for the least convenient form.** Nobody has a
Confluence page id to hand; they have the URL in their address bar. Nobody thinks in
`mcp-jira://` schemes; they think "the CB epic". The one artifact a user reliably possesses —
a browser URL — is the one form we do not accept.

**The URI encodes a transport decision the user should not be making.** `jira://` versus
`mcp-jira://` is about *how* Spine reaches Atlassian. 3.12.0 already made that automatic
(`factory.mcp_server_for`), which leaves the scheme prefix as vestigial ceremony.

The same argument extends past Atlassian. A DB MCP server can answer "what tables are in
this schema?" — but only if the operator already knows to run `mcp ingest-db --server X
--query-tool query --sql-arg sql`. That is a lot of prior knowledge for a question the tool
could have asked.

## What already exists (build on this, do not rebuild)

| Piece | Gives us |
|---|---|
| `agentic/loop.py` | Bounded, budgeted think→act→observe loop with a hard step cap, a no-progress detector, `Policy` gates, and **approval pauses that resume from where they stopped** |
| `agentic/mcp_tools.py` | Bridges allow-listed MCP server tools into loop `Tool`s; allow-list and `write_enabled` enforced identically to the gateway |
| `MCPTool.input_schema` | The server's own JSON Schema per tool — what a prompt-for-missing-arguments step needs, with no guessing |
| `MCPRegistry.probe()` | Per-server health with `config`/`unreachable` classification (3.12.0) |
| `factory.mcp_server_for` | Transport already chosen automatically; the scheme prefix is already redundant |
| `catalog/` + `evals/` | Skill registration and the A/B promotion harness — a new skill can be *measured*, not just asserted |

**The engine exists. What is missing is the entry point.** That should shape the estimate:
this is mostly plumbing and parsing, not new capability.

## Invariants this must not break

- **Governance is not bypassable by phrasing.** An allow-list is not advice. A natural-language
  request must reach exactly the tools `mcp list` shows, and `write_enabled: false` must still
  refuse a mutating tool no matter how the user asked. The NL layer selects *among permitted
  tools*; it never widens the set.
- **Writes still pause.** `require_approval` (Bet 2c) must fire identically. "Create the Jira
  issue" typed as prose is still a write.
- **`understand` / `state` stay deterministic and no-LLM** (invariant #2). This work lives in
  the *intake* path, which already uses an LLM. It must not leak into comprehension.
- **Ask rather than guess.** Inferring a page id is worse than requesting one: a confidently
  wrong id reads a real page belonging to someone else. Elicitation is the safe default and
  inference the exception.

## Phases

| Phase | Work | Effort | Exit |
|---|---|---|---|
| **1 — Accept what people have** | Parse Atlassian URLs and bare identifiers wherever a source id is expected: `https://…/browse/CB-676` → `CB-676`; `https://…/wiki/spaces/ENG/pages/3329359873/Title` → page id; bare `CB-676`; `ENG` as a project/space key. Deterministic, no LLM. | ~2 d | Pasting any URL from the address bar works; existing URIs unaffected |
| **2 — Ask for what is missing** | When a source is under-specified, prompt for exactly the missing arguments, driven by the tool's `input_schema` rather than a hardcoded list. Non-interactive callers get a precise error naming the field. | ~3 d | `orchestrator ingest` with no id prompts for it, and says which forms it accepts |
| **3 — Natural-language entry** | `orchestrator ask "<request>"` binds the allow-listed MCP tools into `agentic/loop.py` and lets the model select and fill them, under existing policy gates. Prints the tool calls it made. | ~5–7 d | "pull the CB epic and its stories" reaches `jira_get_issue` + `jira_search` with no URI typed |
| **4 — Make it measurable** | Register as a catalog skill so the existing A/B harness scores it against the URI path — task success, tool-call count, wrong-tool rate. Promote or drop on evidence. | ~3 d | A scorecard exists; the skill earns its place or is removed |

**Phase 1 is the bang-for-buck and should ship alone if the rest stalls.** It removes the
single most common friction (nobody has a page id) for two days' work and no invariant risk.
Phase 3 is where cost and risk concentrate.

### The DB case is not symmetric

Atlassian has stable identifiers a user can paste. A database does not: "get me the orders
data" has no key to hand. The equivalent first step is **introspect, then offer** — list
schemas and tables, and let the user pick from what is actually there. That is closer to
Phase 2's elicitation than Phase 1's parsing, and it argues for treating "what can this
server tell me?" as a first-class step rather than a special case of Atlassian handling.

`mcp ingest-db` already assumes `--query-tool query --sql-arg sql`. The server's
`input_schema` makes those defaults unnecessary — worth folding into Phase 2.

## Non-goals

- **Replacing the URI scheme.** `jira://CB-676` stays: it is scriptable, unambiguous, and what
  the SDLC pipeline passes internally. This adds a friendlier front door, not a migration.
- **A chat UI.** The surface is a CLI command and, later, the existing web inbox.
- **Inferring identifiers from context** ("the epic I mentioned earlier"). Wrong-page-read is
  a real harm, and cross-invocation memory makes it likelier.
- **Natural language for `understand` / `state`.** Those are deterministic by design.

## Open questions

1. **Where does prose enter?** A dedicated `orchestrator ask "<request>"`, or `--source`
   detecting that its argument is not a URI? A separate command keeps the deterministic
   `--source` contract clean; overloading `--source` is fewer concepts. *Lean: a separate
   command — `--source` is consumed by the SDLC pipeline and should stay parseable.*
2. **How much elicitation is too much?** Prompting for every optional argument is worse than
   the URI it replaced. *Lean: prompt only for `required` in the schema; everything else takes
   the server's default and is reported, not asked.*
3. **What does an ambiguous request do?** "the CB epic" with three matching epics must not
   pick one. *Lean: list the candidates and ask — consistent with "ask rather than guess".*
4. **Does Phase 3 need its own policy profile?** The agentic loop already gates tool calls, but
   an NL entry point is reachable by people who have not read what the tools do. Worth deciding
   whether read-only is the default posture regardless of `write_enabled`, with writes needing
   an explicit flag *and* the existing approval gate.

## Risks

**The measurable one:** Phase 3 puts an LLM between a user's sentence and a real Jira account.
The governance layer bounds *what* it can call, not whether calling it was what the user meant.
Phase 4 exists to quantify that — wrong-tool rate is the number to watch, and it should gate
promotion rather than be reported after the fact.

**The quiet one:** every phase adds a way to reach the same tools. Three entry points that
diverge in behaviour is worse than one awkward URI. They must share a resolution path —
the same lesson the architecture renderers taught when the SVG clustered and the mermaid
did not.
