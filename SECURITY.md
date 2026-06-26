# Security Policy

## Supported versions

The project is in early development (pre-1.0). Only the latest commit on `main` is supported. Once a versioned release exists, this section will list which versions receive fixes.

## Reporting a vulnerability

**Please do not open a public GitHub issue for security reports.**

To report a vulnerability, use one of:

1. **GitHub private advisory** — preferred. Go to the repository's *Security* tab and choose *Report a vulnerability*. This creates a private channel between you and the maintainers.
2. **Email** — send details to the maintainer listed in the GitHub org settings. (A dedicated address will be added once the project has one.)

Please include:

- A description of the issue and its potential impact.
- Steps to reproduce, or a proof-of-concept if available.
- The affected component, commit hash, or version.
- Your name and contact information (optional — anonymous reports are accepted).

## What to expect

| Stage | Target timeline |
|---|---|
| Acknowledgement of report | Within 3 business days |
| Initial assessment and severity rating | Within 7 business days |
| Fix or mitigation plan communicated | Within 30 days of confirmation |
| Public disclosure | Coordinated with the reporter, typically after a fix is released |

These are targets, not guarantees, while the project is pre-1.0.

## Scope

In scope:

- Code in this repository.
- Default configurations and example agent templates / tool contracts shipped with the project.
- Documented APIs and their authentication / authorization behavior.

Out of scope:

- Issues in third-party dependencies (please report those upstream; we will track and update once a fix is available).
- Self-inflicted misconfigurations (e.g., running the orchestrator without authentication on a public network).
- Denial-of-service from unbounded inputs the user supplies to their own deployment.
- Social engineering of project maintainers or contributors.

## Coordinated disclosure

We follow coordinated disclosure: please give us reasonable time to investigate and ship a fix before publishing details. We will credit reporters in release notes unless anonymity is requested.

## Hardening recommendations

While the project is pre-1.0:

- Do not run agent-orchestrator on data you cannot afford to lose or expose.
- Treat the registry, MCP gateway, and runtime as privileged services. Place them behind your authentication layer.
- Review tool contracts before enabling them — a tool with `side_effects: write` and no approval gate can take destructive action on your behalf.
- Pin LLM provider credentials to least-privilege scopes.

## Thanks

We appreciate responsible reporting and the time it takes to investigate and write a clear report. Thank you for helping keep the project and its users safe.
