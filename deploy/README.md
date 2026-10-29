# Running Spine as a shared server

One Spine server inside your network, reached by everyone's AI app (Claude Code and Codex, in
their own apps or in VS Code) with their **company login**. It serves only the repositories
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
| `ORCHESTRATOR_MCP_RESOURCE_URL` | this server's public URL, e.g. `https://spine.example.com/mcp` |
| `ORCHESTRATOR_MCP_AUDIENCE` | the audiences a token may name, comma-separated. Defaults to the resource URL |
| `ORCHESTRATOR_MCP_SUBJECT_CLAIM` | the claim naming the person: `sub` by default |
| `ORCHESTRATOR_MCP_ROLE_MAP` | which company groups or app roles get which Spine permission (below) |
| `ORCHESTRATOR_MCP_ROLES_CLAIM` | the claim the role map reads: `roles` by default, `groups` for group claims |

**Entra ID (single tenant).** The API's **Application ID URI must be this server's MCP URL**,
character for character, e.g. `https://spine.example.com/mcp` with no trailing `/`. Apps send that
URL as the token's `resource`, and Entra refuses a sign-in whose `resource` is not the App ID URI
of the scope it asks for (`AADSTS9010010`). An `https` App ID URI needs a domain verified in the
tenant (or the tenant's own `<tenant>.onmicrosoft.com`). Both token versions pass one
configuration:

```bash
ORCHESTRATOR_MCP_ISSUER_URL=https://login.microsoftonline.com/<tenant-id>/v2.0
ORCHESTRATOR_MCP_ACCEPTED_ISSUERS=https://sts.windows.net/<tenant-id>/
ORCHESTRATOR_MCP_RESOURCE_URL=https://spine.example.com/mcp
ORCHESTRATOR_MCP_AUDIENCE=https://spine.example.com/mcp,<api-client-id>
ORCHESTRATOR_MCP_SUBJECT_CLAIM=oid
ORCHESTRATOR_MCP_ROLE_MAP=Spine.Read=spine:read; Spine.Plan=spine:read spine:plan
ORCHESTRATOR_MCP_ADVERTISED_SCOPES=https://spine.example.com/mcp/Spine.Access
```

The last line is the scope an app asks Entra for. Without it the server advertises `spine:read`,
which Entra takes for a Microsoft Graph scope, and every sign-in fails with `AADSTS9010010`.

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
- **Refused sign-ins** are JSON lines on the same stream, with an `event` beginning
  `plugin.oidc.`. The one to know is `plugin.oidc.no_spine_scope`: a genuine token whose groups
  or roles grant no Spine permission. It names the person (`subject`) and what the token carried
  (`found`), which is how you tell "signed in as the wrong account" from "not in the group".

## 5. Connecting the apps

Each person adds the server's URL to their app as a remote MCP server, and the app opens the
company sign-in in their browser. Spine never sees a password: the app signs in, and Spine checks
the token it brings.

**Tested** on 2026-10-28/29, each on Entra ID and on Okta, with a person who may plan, one who
may only read, and the audit naming each:

| App | Version |
|---|---|
| Claude Code in VS Code (extension) | 2.1.312, VS Code 1.142 |
| Claude desktop app, Code tab | the release current on 2026-10-29 |
| Codex app (the ChatGPT app's Codex) | codex 0.162 |

The Claude Code CLI (2.1.312), which shares the extension's configuration and sign-in, passed on
Entra. Other apps that follow the MCP sign-in rules should work; they were not tested.

### 5.1 Your provider: one public client for every app

Register **one** client that every app signs in through: a public client (no secret), with PKCE.
Each app returns to a fixed address on the person's machine, and the client must list them:

| App | Sign-in returns to |
|---|---|
| Claude Code (CLI, VS Code, desktop app) | `http://localhost:33420/callback` |
| Codex (app; the CLI shares its sign-in code but was not tested) | `http://127.0.0.1:33421/callback/<id>`, where `<id>` is fixed for your server's URL (below) |

**Codex's `<id>`** is the first 9 bytes of the SHA-256 of the MCP URL, base64url without
padding. Codex normalises the URL first (host in lowercase, no default `:443`), so give it in that
form. It is the same for every person, so register it once, before anyone signs in:

```bash
python3 -c 'import base64,hashlib,sys; print(base64.urlsafe_b64encode(hashlib.sha256(sys.argv[1].encode()).digest()[:9]).decode().rstrip("="))' https://spine.example.com/mcp
```

With Entra and Okta, Codex (0.162) sent this form even with a `callback_url` configured: it uses
a plain `callback_url` only with a provider that advertises RFC 9207
(`authorization_response_iss_parameter_supported`), and neither does. Register `/callback/<id>`.

**Entra ID.** Two app registrations:

- **The API** ("Spine API"): Application ID URI = the MCP URL (§1); expose a scope
  `Spine.Access`; app roles `Spine.Read` and `Spine.Plan` (user/group); token configuration →
  optional claim `email` on the access token (or the audit has no email); manifest
  `requestedAccessTokenVersion: 2`.
- **The client** ("Spine apps"): allow public client flows; platform **Mobile and desktop
  applications** with the two return addresses above; API permission `Spine.Access` (delegated),
  admin consent granted; and on the API, add it as a pre-authorized client of `Spine.Access`.
- **People:** in Enterprise applications → Spine API → Users and groups, assign `Spine.Read` to
  readers and both roles to planners.

**Okta.** A custom authorization server and one app integration:

- **Authorization server:** audience `api://spine`; scope `spine:read`; two access-token claims,
  always included: `groups` (filter *starts with* `spine-`) and `email` (`user.email`); an access
  policy for the client with a rule allowing **Authorization Code**.
- **App integration:** OIDC, **Native**, Authorization Code with PKCE, no secret, sign-in redirect
  URIs = the two return addresses above. Okta matches them exactly, port included.
- **People:** groups `spine-readers` and `spine-planners`, both assigned to the app.

### 5.2 Claude Code (CLI, VS Code extension, desktop app Code tab)

One command per person, or the central file in §5.4:

```bash
claude mcp add --transport http --scope user --client-id <client-id> --callback-port 33420 spine https://spine.example.com/mcp
```

Then `/mcp` → **spine** → **Authenticate**, and sign in. The CLI, the VS Code extension and the
desktop app share this server entry and its saved sign-in, so it is done once per machine.

- **Switching person** (a shared machine, or a tester): `/mcp` → **spine** → **Clear
  authentication**, then **Authenticate**.
- **A private certificate authority:** the desktop app trusts the macOS keychain. The CLI and a VS
  Code started from a shell need `NODE_EXTRA_CA_CERTS=<your CA bundle>` in their environment if the
  server's certificate is not from a public authority.

### 5.3 Codex (the ChatGPT app's Codex, and the codex CLI)

Add to `~/.codex/config.toml`, or use the central file in §5.4:

```toml
[mcp_servers.spine]
url = "https://spine.example.com/mcp"

[mcp_servers.spine.oauth]
client_id = "<client-id>"
callback_port = 33421
```

Then, in the app, **Settings → MCP servers → spine → Authenticate** (or `codex mcp login spine`;
the CLI was not part of the live test).
Restart the app after editing the file. **Click Authenticate once**: the return port takes one
sign-in at a time, and a second click while the first is waiting fails with "Couldn't connect".

### 5.4 Central rollout

Copy these to every machine with your device management, with your URL and client id filled in:

| File | Goes to | Effect |
|---|---|---|
| [`rollout/claude-code-managed-settings.json`](rollout/claude-code-managed-settings.json) | `/Library/Application Support/ClaudeCode/managed-settings.json` (macOS), `/etc/claude-code/managed-settings.json` (Linux), `C:\Program Files\ClaudeCode\managed-settings.json` (Windows), or the same key in server-managed settings | every person gets `spine` and keeps their own servers; they cannot remove it, though they can turn it off in `/mcp` (Claude Code 2.1.259 or later). This is Claude Code's file: it reaches the desktop app's Code tab, not the desktop app's chat |
| [`rollout/codex-managed_config.toml`](rollout/codex-managed_config.toml) | `/etc/codex/managed_config.toml`, or MDM `com.openai.codex` key `config_toml_base64` | the server as a default each launch. Codex does not document `mcp_servers` here; **not verified on a managed device** |
| [`rollout/codex-requirements.toml`](rollout/codex-requirements.toml) | `/etc/codex/requirements.toml`, or MDM key `requirements_toml_base64` | optional allowlist: with it, **only** the servers it names run in Codex |

Each person still signs in once in their own app; the files carry no credential. The server entry
in each file is the one tested above, as a person's own setting; delivery through managed
settings follows each vendor's documentation and was not run on a managed device here. Check one
machine first: `claude mcp list` and `codex mcp list` should show `spine`.

### 5.5 When sign-in goes wrong

| What you see | Why | What to do |
|---|---|---|
| Entra `AADSTS9010010` | the app asked for a scope Entra does not tie to the MCP URL | set `ORCHESTRATOR_MCP_ADVERTISED_SCOPES` (§1), and make the App ID URI exactly the MCP URL |
| Entra `AADSTS50011`, or Okta "redirect_uri must be a Login redirect URI" | the app's return address is not registered | add the address from §5.1 (Codex: compute its `<id>`) |
| The app connects, then every tool fails, and the log shows `plugin.oidc.no_spine_scope` | the token belongs to someone in no Spine group | read the log's `subject`: if it is not the person, their browser signed in with another account it remembered (often an admin). Sign out of the provider, or finish the sign-in in a private window. Close **every** private window before switching person: they share one session |
| "needs scope 'spine:plan'" | the person may read but not plan | by design; add them to the planners group or role |
| Codex: "Couldn't connect" right after Authenticate | a sign-in is already waiting on port 33421 | quit the app, reopen it, click Authenticate once |
| The assistant tries to open `/repos/...` on the laptop | repository paths name the **server's** checkouts | nothing to fix; say so in your team's guidance |

## 6. Upgrading

Pull the new tag, and keep `/data`: the graph cache is keyed by commit and the Spine version, so
nothing stale is served. Read the CHANGELOG first. A release that changes the tool list, or what
a level shows, says so there.

## Not in this version

- Git URLs in the repository list.
- Apps that connect from the vendor's cloud (claude.ai, ChatGPT): they need a public URL.
- A tested guide for other apps (GitHub Copilot in VS Code, Cursor, Gemini CLI).
- Plain Google sign-in: Google's access tokens are opaque, not signed JWTs, so this server cannot
  check them. A Google setup that issues JWT access tokens (Identity Platform) is a standard OIDC
  provider.
- A Helm chart.
- Tools that spend money: the `build` level is for a laptop or a separate, deliberately
  configured server.
