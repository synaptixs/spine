# Running Spine as a shared server

One Spine server inside your network, reached by everyone's AI app (Claude Code, Codex, VS Code
with Copilot, Gemini CLI, Cursor) with their **company login**. It serves only the repositories
you list, it records every call against the person who made it, and it reads and plans: it
never writes code or opens pull requests itself. The apps' own models do that.

For a single developer on a laptop, none of this applies: use the one-step install in
[AGENT_GUIDE.md §3](https://github.com/synaptixs/spine/blob/develop/AGENT_GUIDE.md#3-install).

## What you run

| Piece | Where |
|---|---|
| The image | `ghcr.io/synaptixs/spine-mcp:<version>`, built from [`deploy/Dockerfile`](Dockerfile) at each release |
| An example | [`compose.example.yml`](compose.example.yml) and [`repos.example.yaml`](repos.example.yaml) |
| Port | `8080`, plain HTTP. **Put TLS in front**: your reverse proxy or ingress terminates TLS and forwards to the container. Apps refuse a company login over plain HTTP |

The container starts `orchestrator-mcp --http --host 0.0.0.0 --port 8080 --level plan`. That
means the comprehension tools plus the build document and its approval, with no tool that spends
money or writes elsewhere. It **refuses to start** without both of the following:

- **a token check:** a company login, an introspection URL, or a static token;
- **a repository list.**

The start-up log names whichever one is missing.

## 1. The company login

Register Spine as an API with your identity provider, then give the server these settings in an
env file (`spine.env` in the example; never commit it):

| Setting | What it is |
|---|---|
| `ORCHESTRATOR_MCP_ISSUER_URL` | your provider's issuer. The server finds its signing keys from the discovery document there |
| `ORCHESTRATOR_MCP_RESOURCE_URL` | this server's public URL, e.g. `https://spine.example.internal/mcp` |
| `ORCHESTRATOR_MCP_AUDIENCE` | the audiences a token may name, comma-separated. Defaults to the resource URL |
| `ORCHESTRATOR_MCP_SUBJECT_CLAIM` | the claim naming the person: `sub` by default |
| `ORCHESTRATOR_MCP_ROLE_MAP` | which company groups or app roles get which Spine permission (below) |
| `ORCHESTRATOR_MCP_ROLES_CLAIM` | the claim the role map reads: `roles` by default, `groups` for group claims |

**Entra ID (single tenant).** Both token versions pass one configuration:

```bash
ORCHESTRATOR_MCP_ISSUER_URL=https://login.microsoftonline.com/<tenant-id>/v2.0
ORCHESTRATOR_MCP_ACCEPTED_ISSUERS=https://sts.windows.net/<tenant-id>/
ORCHESTRATOR_MCP_AUDIENCE=api://<app-id-uri>,<api-client-id>
ORCHESTRATOR_MCP_SUBJECT_CLAIM=oid
ORCHESTRATOR_MCP_ROLE_MAP=Spine.Read=spine:read; Spine.Plan=spine:read spine:plan
```

**Okta (custom authorization server).**

```bash
ORCHESTRATOR_MCP_ISSUER_URL=https://<your-org>.okta.com/oauth2/<server-id>
ORCHESTRATOR_MCP_AUDIENCE=api://spine
ORCHESTRATOR_MCP_ROLES_CLAIM=groups
ORCHESTRATOR_MCP_ROLE_MAP=spine-readers=spine:read; spine-planners=spine:read spine:plan
```

**Permissions.** `spine:read` covers the code and documentation map, blast radius, explanations,
the requirements check and `doctor`. `spine:plan` adds the build document and its approval. With
a role map set, a person's permissions come **only** from their groups or roles. A permission
inside the token itself is ignored, because people can often grant those to themselves.
Someone in no mapped group reaches no tool.

Every setting, with its default, is in
[OPERATIONS.md §10.2](https://github.com/synaptixs/spine/blob/develop/OPERATIONS.md#step-10--call-it-from-claude-codex-or-your-ide-mcp-server).

## 2. The repository list

Spine serves only the checkouts you list in `ORCHESTRATOR_MCP_REPOS`, which the image sets to
`/srv/spine/.spine/repos.yaml`. Mount your list there. It uses the same form as a project's
`.spine/repos.yaml`:

```yaml
repos:
  billing: /repos/billing
  web: /repos/web
```

- **Paths, not URLs.** Mount the checkouts into the container (at `/repos` in the example) and
  keep them current yourself, for example with a `git pull` on a schedule. Git URLs are not
  served in this version.
- **Everything is checked.** A call may name a listed checkout or a folder inside one.
  Anything else is refused with the list of keys, never a path on the server. That covers:
  - the repository a tool is pointed at;
  - a `repos.yaml` other than yours;
  - an OpenSpec change outside a checkout;
  - a file-based requirements source outside a checkout;
  - every Jira, Confluence or Notion source, which would otherwise read through the server's
    own credentials.

  Paths are compared after symlinks are resolved.
- **No links out.** A listed checkout that holds a symbolic link pointing outside it is not
  served until the link is removed. That covers a committed `episteme -> /etc` or a dangling
  link, even one into another listed checkout, because a tool would read or write through it.
  The refusal names the link. Serve clean checkouts: a Python `.venv` or `npm link` inside
  one usually holds such a link (to the system interpreter, to a global package).
- **Answers across every repository.** A call that passes your list's path as `repos` gets
  answers across all listed repositories at once.
- **Edits apply without a restart.** If the list stops parsing, the server refuses every call
  until you fix it.
- **Mount read-only unless you want answers written.** `requirements_answer` writes into the
  OpenSpec change it answers, and a read-only mount makes that one tool fail.

## 3. Volumes

| Mount | Holds |
|---|---|
| `/data` | every cache: the code graph per commit (`/data/pkg`), requirements sources, pulled documentation, workspaces. Keep it on a volume, or each restart rebuilds the graph from scratch |
| `/repos` | your checkouts, read-only is fine |
| `/srv/spine/.spine/repos.yaml` | your repository list |

The container runs as uid 10001 and can run with a read-only root filesystem, given a tmpfs at
`/tmp` (the example does both).

## 4. The audit

Every call made with a login is one JSON line on the container's stderr. That covers reads,
plans, scope denials and repository refusals:

```json
{"arguments": {"keys": ["repo_path", "symbol"], "sha256": "…"}, "event": "mcp_tool_invoked", "outcome": "ok", "principal": {"client_id": "…", "email": "ada@example.com", "issuer": "https://login.microsoftonline.com/…/v2.0", "scopes": ["spine:read"], "subject": "…"}, "scope": "spine:read", "tool": "blast_radius"}
```

- **Fields:**
  - who: the subject, email, issuer and calling app;
  - what: the tool and its permission;
  - the argument names and a hash of their values (never the values);
  - the outcome: `ok`, `error`, `denied` with the permission it lacked, or `refused` with the
    argument the list refused.
- **Sink:** ship stderr to your log platform. The **email is personal data** and is there on
  purpose: the audit exists to say who did what. Set your log retention to match.
- **Registry:** if you also run Spine's registry, set `ORCHESTRATOR_API_URL` (and
  `ORCHESTRATOR_API_KEY`) and each record goes to its audit log too.

## 5. Connecting the apps

Each person adds the server's URL to their app as a remote MCP server; the app opens the
company sign-in. The per-app steps and the central rollout files are added with the live
sign-in work. Until then, any app that sends a bearer token from your provider works.

## 6. Upgrading

Pull the new tag, and keep `/data`: the graph cache is keyed by commit and the Spine version, so
nothing stale is served. Read the CHANGELOG first. A release that changes the tool list, or what
a level shows, says so there.

## Not in this version

- Git URLs in the repository list.
- Apps that connect from the vendor's cloud (claude.ai, ChatGPT): they need a public URL.
- A Helm chart.
- Tools that spend money: the `build` level is for a laptop or a separate, deliberately
  configured server.
