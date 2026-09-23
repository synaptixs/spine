# Contributing to Spine

Thanks for your interest! Spine (distributed as the `synaptixs-spine` package)
is open source under the MIT license, and community input genuinely shapes it.

## Ways to contribute

| You want to… | How |
|---|---|
| **Report a bug** | Open a [bug report](https://github.com/synaptixs/spine/issues/new?template=bug_report.md) — include version, OS, model/provider, and steps. |
| **Request a feature or enhancement** | Open a [feature request](https://github.com/synaptixs/spine/issues/new?template=feature_request.md). |
| **Ask a question / share an idea / give feedback** | Start a [Discussion](https://github.com/synaptixs/spine/discussions). |
| **Report a security issue** | Follow [SECURITY.md](SECURITY.md) — please don't open a public issue. |
| **Fix a clearly-scoped bug** | A focused PR is welcome (see below). |
| **Build a new feature** | Open an issue or discussion first to align on direction before writing code. |

## How changes get reviewed and shipped

Maintainers triage issues and discussions and decide what lands. Releases flow
through a protected branch: changes are reviewed on a PR into `develop`, then a
maintainer opens a `develop → main` release PR, which requires a code-owner review
and a passing `security scan` check before it merges. Each `main` release gets
published release notes.

**Three things trip a promotion PR, and all three have.** Budget for them:

- **Its workflow runs land in `action_required` and must be approved by hand.** The head of a
  promotion PR is usually the `chore(episteme)` bot commit that lands after the release PR
  merges, and runs triggered by a bot are held. Nothing is wrong; approve them.
- **Any commit to `develop` dismisses a standing approval**, because the ruleset dismisses
  stale reviews on push — and the episteme bot commits after *every* merge. Approve and merge
  back to back; anything landing in between restarts the cycle.
- **An unresolved review thread blocks the merge**, including a Code-scanning comment at
  `Note` severity. The ruleset sets `required_review_thread_resolution`, which does not appear
  in `reviewDecision` — so the API reports reviews and checks as satisfied while the merge
  stays blocked. Resolve the thread.

For larger features, the core team often develops them ahead of time and publishes
them on a release cadence — so opening an issue first avoids duplicated effort.

**Auditing the documentation a change obliges.** `uv run --frozen python
scripts/docs_audit.py --base develop --head HEAD` cross-checks the
user documents against what the code registers (front-end counts, extras, CLI commands, MCP
tools), follows every relative link, and — add `--removed "<name>,<synonym>"` — finds mentions
of a feature you took out. Stdlib only; runs on any ref. The reviewer runs it on every PR
([docs/reviewing/docs-matrix.md](docs/reviewing/docs-matrix.md) says which document each kind of change obliges).

**Reviewing a pull request as a maintainer.** The checklist a merge or promotion decision
needs — the gate with CI's extras, fan-out code review, a real-repository smoke test for
front-end changes (`python scripts/validate-frontend.py <language> <git-url> [<git-url>
...]` — shallow-clones each repo, extracts, verifies, and prints the `state` stack line and
top unresolved import targets, so this no longer needs re-deriving by hand each time), and
the documentation audit above walked against
[docs/reviewing/docs-matrix.md](docs/reviewing/docs-matrix.md) (plus
[docs/reviewing/language-frontend-checklist.md](docs/reviewing/language-frontend-checklist.md)
when a front-end changed). A roadmap's own phase table is checked by
`python scripts/roadmap-status.py --check` — a DONE phase with no Evidence, a stale
top-of-document Status line, or a codegen phase started before its comprehension
dependency landed. It scans `docs/specs/*.md`; name a path to also check a plan kept
outside the checkout, which is where plans for non-language tracks live:

```bash
python scripts/roadmap-status.py --check ~/plans/my-track.md
```

**Starting a plan.** Copy [`docs/specs/templates/track-plan.md`](docs/specs/templates/track-plan.md)
— the skeleton every development plan here starts from, whatever the subject. Its §5 is a
generic housekeeping block (branch off `develop`, the gate, the docs audit, `episteme/`,
rebase-never-re-run, one MR) that is identical across tracks and refilled only in its Evidence
column, and its §6.1 is the table that assigns every tooling site hard-coding a name you are
renaming to the phase that must carry it — in the same commit, because a rename landing ahead
of its catch-up breaks the gate for everyone. A new language front-end has a more specific
skeleton, [`language-track.md`](docs/specs/templates/language-track.md), which specialises it.
Copy the template out of the checkout: a plan is not a design record, and tracking one adds a
`SPEC-INDEX.md` row, two counts beside it, and this gate's indexing check for a document no
user reads. Maintainers who use Claude Code carry it as a local `/review-pr`
skill and a `pr-reviewer` subagent under `.claude/`, which this repository does not track;
the repository keeps the reference documents and the scripts, which need no assistant.

## Opening a pull request

1. Fork the repo and create a branch from **`develop`** (e.g. `fix/email-validator-edge-case`).
   `main` only moves through a release promotion, so a branch cut from it is already behind.
2. Make your change with tests; keep it focused — one concern per PR.
3. Make sure the quality gate is green locally — note `mypy src tests`, **not** just `src`;
   typing `src` alone passes here and fails CI:
   ```bash
   mypy src tests
   ruff format --check .
   python scripts/render_architecture_svg.py --check        # the diagram stamps the version
   python scripts/render_knowledge_foundation_svg.py --check
   python scripts/mcp-tools.py --check                      # the agent guide tool inventory
   python scripts/matrix-count.py --check
   python scripts/state-numbers.py --check                  # claims re-derived from source
   uv run orchestrator pkg accuracy --check
   python scripts/sdlc_shapes.py                            # the SDLC pipeline on four repo shapes
   ```
   **CI runs all five generated-artifact `--check` gates.**
   They are listed together because running only the first is how a release PR failed on a
   diagram nobody had re-rendered: `mypy`, `ruff` and the tests were all green, and the version
   the picture claims comes from `pyproject.toml`. If you bump a version, re-run all five.
   `pkg accuracy --check` is the **accuracy gate**: it re-measures the graph and fails if a *gated*
   number has dropped. Only metrics scored against the committed fixtures in `corpus/` are
   gated strictly, plus per-file parity as a one-way ratchet. The invention count is recorded
   and never gated — it is measured against this repository, so it moves whenever anyone
   writes ordinary code, and gating it would fail blameless PRs. `src/orchestrator/pkg/scoreboard.json` is
   the committed baseline — inside the package, so it ships in the wheel and the build
   document can quote it; regenerate it with `uv run orchestrator pkg accuracy
   --scoreboard` when you have genuinely *improved* a number, and say so in the PR.

   `uv run` rather than a bare `orchestrator`, for the same reason the hooks use it: a bare
   command resolves to whichever install is on your PATH, which may be an older release that
   has no `pkg accuracy` at all.

   CI also runs the tests, `pkg verify`, and `orchestrator understand .` — which checks the
   knowledge base still *builds*, not that it matches. Installing the hooks catches the rest
   before you push, and is the only way the secret scan runs on your machine at all:
   ```bash
   pre-commit install
   ```

   **Do not commit `episteme/`.** It is generated from the whole tree — code *and* markdown,
   since every tracked `.md` becomes a `Doc` node — and it is regenerated automatically after
   merge by [`.github/workflows/episteme.yml`](.github/workflows/episteme.yml). CI fails a PR
   that carries it.

   That is not fussiness. A branch cannot keep the bank current: CI checks the *merge ref*, so
   the moment anything lands on `develop`, every open branch's committed bank is stale against
   a tree that did not exist when it was generated — and re-running the check never helps,
   only a rebase does. A documentation-only PR once broke a code PR that shared no files with
   it. Regenerating after merge, where the tree is stable, removes the conflict entirely.

   If you have local `episteme/` changes, drop them: `git checkout origin/develop -- episteme/`.

   The one exemption is a **release promotion** — a `develop` → `main` PR. Its `episteme/`
   diff is every bot commit made on `develop` since `main` last moved, which is the design
   working rather than a contributor carrying the artifact, so the check skips when the base
   is `main`. Since 3.20.0 that promotion is the **only** way `main`'s bank moves:
   regeneration runs on `develop` alone, and `main` inherits it verbatim.
4. Open the PR **against `develop`** — GitHub pre-selects `main`, the default branch; change
   it — with a clear description of **what** and **why**, linking any issue.
5. A maintainer reviews; the `security scan` check must pass.

### Release-cut documentation notes

Use the [release row in the docs matrix](docs/reviewing/docs-matrix.md) to check
the version, plugin manifests and generated artifacts together. Search the previous
version across tracked Markdown and JSON; verify both digits and number words in
language counts. The former capability catalog is now README's capability table;
the agent guide is `AGENT_GUIDE.md`. `CLAUDE_GUIDE.md` and `CODEX_GUIDE.md` are dated
redirects, not release claim surfaces. Preserve their links and do not stamp a
version into them. Historical changelog entries retain the names they shipped with.

### When a check fails on something you didn't change

**A re-run cannot fix a failure that came from the base branch. Push to your branch
instead.**

`gh run rerun` replays a run against the commit it was created for. For a
`pull_request` workflow that commit is the *merge ref computed when you pushed* — not
the base branch as it stands now. So merging a fix into `develop` and re-running your
PR re-audits the same stale tree, and reports the same failure. Only a new merge ref
clears it, which means pushing to the branch: a rebase onto the current base, or an
empty commit if you have nothing to change.

```bash
git fetch origin && git rebase origin/develop && git push --force-with-lease
```

This bites hardest on `dependency audit`, which resolves the dependency tree from the
merge ref. A vulnerable version pinned on `develop` keeps failing every open PR until
each one is rebased past the bump — re-running looks like the obvious fix and is the
one thing that cannot work. It applies equally to any check reading state your branch
does not own.

Re-running *is* the right move for a genuinely flaky failure — a network timeout, a
cache miss, a runner dying. The distinction is whether the failure depends on the tree.

We use [Conventional Commits](https://www.conventionalcommits.org/) for commit
messages (e.g. `fix(planner): handle empty claims list`).

## Development setup

**Codegen language changes:** add the complete row to `sdlc/toolchains.py` together with
its implementations; this table controls `SUPPORTED_LANGUAGES`. Preserve Temporal's
injected runners and worker defaults. Follow the [codegen checklist](docs/reviewing/language-frontend-checklist.md#codegen-registration-and-runner-proof)
for real green/red runner proof, missing-toolchain errors and scaffold idempotency. For
dispatch migrations, run the existing language tests before and after (including
`tests/sdlc/test_php_codegen.py` by name), then `uv run --frozen python
scripts/mutate-dispatch.py`; all eight mutations must be caught without skips. Its child
pytest commands install exactly the extras named by CI's sync step.

See [SETUP.md](SETUP.md) for the full local stack and [USER_GUIDE.md](USER_GUIDE.md)
for the everyday workflow. In short: Python 3.12+, [`uv`](https://docs.astral.sh/uv/),
then the **same extras CI syncs** — a bare `uv sync` is not enough to reproduce a CI result:

```bash
uv sync --frozen --extra dev --extra mcp --extra typescript --extra java --extra csharp \
  --extra c --extra cpp --extra go --extra php --extra perl --extra kotlin --extra clang
```

Without `mcp` every MCP test skips *and* `mypy src tests` reports three phantom
`unused-ignore` errors, because the `type: ignore`s guarding its optional imports have
nothing to ignore. Without the language extras only the `python` and `sql` front-ends
register, so most language tests skip and `pkg accuracy` cannot score their corpus cases.
Without `clang` the C/C++ semantic post-pass is absent, and `pkg accuracy --check` fails
with what reads like a real regression — `cpp/edges/CALLS matched edges — was 5, now 3`,
plus two unexplained misses. Nothing in the code changed; the pass that resolves those
edges just is not installed. `uv sync` **replaces** the environment, so a narrower sync
silently uninstalls `libclang` rather than leaving it in place.

For a semantic-parser diagnostic census, run `uv run --with libclang==18.1.1 python
scripts/parse-census.py clang /path/to/repo --suffix .c .cpp .cc .cxx --json`.
It uses repository-only include paths; diagnostics measure parse coverage, not call recall.
`scripts/validate-frontend.py` also reports semantic recovery, TU coverage, diagnostics,
and extraction time for C/C++. For a pinned branch, clone that ref and pass a local
copy without `.git` as the repository argument; the script accepts paths as well as URLs.

## Code of Conduct

By participating you agree to the [Code of Conduct](CODE_OF_CONDUCT.md). Report
unacceptable behavior through the contact channel in [SECURITY.md](SECURITY.md).

## License

By contributing, you agree that your contributions are licensed under the project's
[MIT License](LICENSE).
