# Changelog

All notable changes to this project are documented here. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/); the package is `synaptixs-spine`
(import/CLI stay `orchestrator`).

## 3.5.0 — Security hardening

This release is the output of a security baseline of Spine's own source tree. Nothing
here is a claim that the codebase is "secure" — it is a description, verifiable against
this repository, of the checks we now run and the issues we found and fixed.

### 🔒 Security

- **Continuous checks in CI, on every pull request:**
  - **CodeQL** dataflow analysis for Python and JavaScript.
  - **`pip-audit`** over the resolved lockfile (not the ambient environment — bare
    `pip-audit` in a uv checkout audits the wrong thing and false-passes).
  - **`bandit`-class static analysis** via ruff's flake8-bandit (`S`) rules, wired
    into the existing lint gate.
  - **Dependabot** for weekly dependency and GitHub-Actions updates.
- **A multi-model adversarial self-review** across the full source tree: 863 candidate
  findings were triaged by one model, then independently verified by a stronger model
  instructed to *refute* each one. 174 of the high-severity candidates were refuted as
  safe-by-design; **7 confirmed issues were fixed, each with a regression test.**
- **All patchable dependency CVEs resolved** — 17 of 18 known advisories fixed by
  version bumps (aiohttp, starlette, cryptography, langsmith, langgraph, pydantic-
  settings). The one remaining (`click`'s `click.edit()` command injection) is
  unreachable — Spine never calls that function — and is documented rather than
  force-fixed, because the fix would regress the `semgrep` scanner by ~2 years.
- Coordinated disclosure via [SECURITY.md](SECURITY.md).

### Fixed

Security fixes from the review above, described at the level of *what class of issue*
rather than a reproduction:

- **Path traversal** in the knowledge-base reader and the `memory-bank` capability
  endpoint — an untrusted section name or a symlink committed in a cloned repo could
  read files outside the intended directory. Reads are now confined to the bank dir.
- **Stored XSS** in the operator web UI — the shared HTML escaper escaped `&<>` but not
  quotes, so an untrusted value (e.g. a cloned-repo file name) placed in a quoted HTML
  attribute could break out. The escaper now escapes quotes across all web UI files.
- **SSRF backstop** for remote-repo cloning — the internal-host guard missed obfuscated
  IPv4 encodings (integer, hex, octal, short-form) that resolve to loopback. These are
  now normalized and blocked. (The guard was already robust under its default
  restrictive host allow-list; this hardens the opt-in `*` mode.)
- **Prompt-injection hardening** in the codegen/design/review pipeline — untrusted
  cloned-repo content fed into LLM prompts is now fenced and marked as data, and the
  review judge is instructed to ignore injected verdicts. This is defense-in-depth; the
  human merge approval remains the authoritative gate.

### Added

- `SECURITY.md` disclosure policy surfaced in the README.
- Security review plan and methodology in `docs/specs/security-review-plan.md`.
