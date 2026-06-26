# Contributing to agent-orchestrator

Thanks for your interest. agent-orchestrator is in early development — the Phase 1 walking skeleton is not yet complete, so the public API surface, schemas, and package boundaries are still moving. Please read this guide before opening a pull request.

## Current state of contributions

| You want to... | Status |
|---|---|
| Open an issue (bug, design question, spec feedback) | Welcome anytime. |
| Submit a PR fixing a clearly scoped bug | Welcome. |
| Submit a PR for a new feature | Open an issue first to align on direction. We are likely to decline unsolicited feature PRs until Phase 2. |
| Propose changes to specs in `docs/specs/` | Open an issue — these change frequently and central coordination matters. |
| Add a new agent template or tool contract example | Welcome once the registry and MCP gateway are merged (end of Sprint 4). |

If you're not sure whether your change is in scope, open an issue and ask.

## Code of Conduct

By participating in this project you agree to abide by the [Code of Conduct](CODE_OF_CONDUCT.md). Report unacceptable behavior to `conduct@fibonacci.example` (replace with real contact).

## Development setup

### Prerequisites

- Python 3.12+
- Node.js 20+
- `uv` for Python dependency management
- `pnpm` for JavaScript workspaces
- Docker (for local Postgres, Redis, MinIO)

### Initial setup

```bash
git clone https://github.com/synaptixs/spine.git
cd agent-orachestrator

# Bring up local services
docker compose -f docker-compose.dev.yml up -d

# Install Python and TS deps
uv sync
pnpm install

# Install pre-commit hooks
uv run pre-commit install
```

### Running tests

```bash
# Python: per-package unit tests
uv run pytest packages/orchestrator-core/tests

# Full unit suite
uv run pytest

# Integration tests (require docker-compose services running)
uv run pytest -m integration

# TypeScript
pnpm test
```

## Branching and pull requests

- Base branch: `main`.
- Branch naming: `type/short-description` — e.g. `fix/planner-timeout`, `feat/evidence-verifier`, `docs/glossary-spec`.
- Keep PRs focused. One conceptual change per PR. Large PRs will be asked to split.
- Rebase, don't merge, when updating your branch against `main`.
- PRs must pass: lint, type check, unit tests, and pre-commit hooks. CI runs these on every push.

### Pull request template

When opening a PR, include:

1. **What and why.** One paragraph. Why does this change exist?
2. **How to test.** Reproducible steps for a reviewer.
3. **Risk.** What could break? What did you not test?
4. **Linked issue.** `Closes #N` if applicable.

## Commit conventions

We use [Conventional Commits](https://www.conventionalcommits.org/). Examples:

```
feat(planner): support sequential workflow pattern
fix(verifier): handle empty claims list without crashing
docs(specs): clarify mandatory output fields in agent template
refactor(registry): extract validator into separate service
test(runtime): add golden test for manager-with-specialists
chore(deps): bump langgraph to 0.2.x
```

Scope is the package name (without the `orchestrator-` prefix) or a top-level area (`docs`, `infra`, `ci`).

## Code style

- **Python:** `ruff` for lint + format, `mypy --strict` for type checking. Both run in pre-commit.
- **TypeScript:** `eslint` + `prettier`. Strict mode on.
- **No `# type: ignore` or `any`** without a comment explaining why.
- **No bare `except Exception:`** — use the typed errors in `orchestrator-core/errors.py`.
- **No commented-out code.** Delete it; git remembers.
- **Public APIs need docstrings.** Internal helpers do not unless the *why* is non-obvious.

## Testing expectations

- Every new module ships with unit tests. Coverage floor for `orchestrator-core`, `orchestrator-registry`, `orchestrator-ir`, `orchestrator-verifier` is 80%.
- Integration tests live alongside unit tests but are marked `@pytest.mark.integration`.
- Changes that touch agent behavior must include or update an entry in the eval suite.
- Don't mock what you can run for real cheaply. Mock LLMs in unit tests; use real LLMs in nightly E2E.

## Security

Do not file security issues as public GitHub issues. See [SECURITY.md](SECURITY.md).

## License

By contributing, you agree that your contributions will be licensed under the [Apache License 2.0](LICENSE).
