# Spine — try it, break it, tell us

**Governed, provenance-grounded autonomous software delivery.** Point Spine at a
requirement and your repo; it opens a **reviewed, tested pull request** — with a
human in control at two gates. We're looking for early users and honest feedback.

> *Spine* is the product; it installs as the **`agent-orchestrator`** package and its
> command is **`orchestrator`**. MIT-licensed, open source.

---

## What it does

Spine reads a requirement (Confluence, Notion, or a Markdown file), **understands your
codebase**, generates code grounded in that repo's own conventions, writes and runs
tests until green, has it reviewed, and opens a PR. It pauses for **your approval**
before it starts and before anything merges. Nothing is pushed or merged unless you say so.

```
requirement → understand repo → implement → test → review → CI → open PR → (you merge)
                    └────── grounded in your code · per-edge checks · full audit ──────┘
              human gate ▲ (before build)                         ▲ human gate (before merge)
```

## It spans the delivery lifecycle

| Stage | Spine | |
|---|---|---|
| Requirements → backlog | intents + specs from your docs | ✅ proven |
| Understand the code | knowledge graph + committed `memory-bank/` (Python/Java/TS) | ✅ |
| Implement → test → refine | grounded codegen, runs tests, fixes until green | ✅ proven |
| Review → CI → merge | semantic review, real PR, gate-approved **merge-on-green** | ✅ proven |
| Govern + observe | two human gates, policy, per-run budget, append-only audit, OpenTelemetry | ✅ |
| Learn across runs | cross-run memory — recalls past conventions & pitfalls, each cited | ✅ |
| Operate → drift → **remediate** | production drift → scoped, **human-gated** remediation PR, end-to-end provenance | 🧪 built, in validation |

**Proven today:** the full **requirements → merged PR** loop (run autonomously with
both gates, real merged PRs). **Built and validating now:** extending the same governed
loop into *operate → self-remediate* with end-to-end provenance.

## Why it's different

- **Grounded, not guessing** — generates against your repo's real structure + (optionally) a domain ontology, with citations.
- **Governed by design** — human gates, allow-listed tools, spend budgets, and an audit trail of every decision — safe to point at real code.
- **Inspectable** — live tracing + provenance you can query, not a black box.
- **Yours** — self-hostable, any LLM provider (or fully offline on a local model), MCP in both directions (use external tools, or call Spine *from* Claude Code / your IDE).

## Try it (about 10 minutes)

```bash
pip install --extra-index-url https://pypi.org/simple/ agent-orchestrator
orchestrator init && orchestrator doctor                       # scaffold .env, check readiness
orchestrator sdlc feature --source file://./spec.md --safe     # build locally — no pushes, no PRs
```

`--safe` makes **no external writes** — you get a local branch + diff to inspect first.

- **[Setup & Install](SETUP.md)** — the exact install one-liner + full stack
- **[User Guide](USER_GUIDE.md)** — first build → real PR → local models → web dashboard → MCP
- **[README](README.md)** — features, FAQ

## We want your feedback

Open an issue (or start a discussion) on anything — especially:

1. **Did the generated code respect your repo's conventions and actually merge?** (the make-or-break signal)
2. **Are two gates — before build, before merge — the right control points?**
3. **Would you let a *gated* agent open remediation PRs from production drift? What would it take to trust that?**
4. **Is the audit trail + provenance what your compliance / SRE folks need?**
5. **Biggest friction** from `pip install` to your first PR?

## Honest status

Single-tenant today (RBAC is in progress). Published on **TestPyPI** while the PyPI
name is sorted — see the [Setup guide](SETUP.md) for the install line. Codegen is
strongest on Python; Java/TypeScript are supported. The *operate → remediate*
extension is wired and tested but not yet proven against live production data — try it
with the runbook and tell us where it bends.

---

**Build something, or hit a wall this didn't cover? [Open an issue.](../../issues)**
Spine is MIT-licensed — see [CONTRIBUTING.md](CONTRIBUTING.md) and [LICENSE](LICENSE).
