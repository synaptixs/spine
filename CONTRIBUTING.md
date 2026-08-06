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

For larger features, the core team often develops them ahead of time and publishes
them on a release cadence — so opening an issue first avoids duplicated effort.

## Opening a pull request

1. Fork the repo and create a branch from `main` (e.g. `fix/email-validator-edge-case`).
2. Make your change with tests; keep it focused — one concern per PR.
3. Make sure the quality gate is green locally — note `mypy src tests`, **not** just `src`;
   typing `src` alone passes here and fails CI:
   ```bash
   mypy src tests
   ruff format --check .
   ```
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
   is `main`. `main` regenerates from its own tree after the merge either way.
4. Open the PR with a clear description of **what** and **why**, linking any issue.
5. A maintainer reviews; the `security scan` check must pass.

We use [Conventional Commits](https://www.conventionalcommits.org/) for commit
messages (e.g. `fix(planner): handle empty claims list`).

## Development setup

See [SETUP.md](SETUP.md) for the full local stack and [USER_GUIDE.md](USER_GUIDE.md)
for the everyday workflow. In short: Python 3.12+, [`uv`](https://docs.astral.sh/uv/),
then `uv sync`.

## Code of Conduct

By participating you agree to the [Code of Conduct](CODE_OF_CONDUCT.md). Report
unacceptable behavior through the contact channel in [SECURITY.md](SECURITY.md).

## License

By contributing, you agree that your contributions are licensed under the project's
[MIT License](LICENSE).
