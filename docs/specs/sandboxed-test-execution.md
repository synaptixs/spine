# Build plan: reliable, isolated autonomous execution

**Status:** Implemented in 1.4.0 (`sdlc/testenv.py`: `VenvTestEnvironment` default +
auto-heal; configurable LLM timeout). Phases 1–4 shipped; runner seam + E2B backend remain.
**Goal:** make the SDLC codegen loop work on real customer repos by running generated
tests in a **per-project isolated environment** that has the generated project's own
dependencies, with a **configurable LLM timeout** and a **pluggable runner** seam — so the
failures we hit live this session (`rc=2` missing `requests`/`pytest-mock`, 60s extraction
timeouts) stop happening.

## Problem (root cause)
`SubprocessTestRunner` runs `python -m pytest` using **`sys.executable`** — the
orchestrator's own interpreter ([sdlc/testrunner.py](../../src/orchestrator/sdlc/testrunner.py)).
So the generated project's deps must already be installed in *our* env, and a missing one
fails at **collection** (`rc=2`) which the refine loop can't fix. Also bad: generated code
runs in our process (no isolation), and the LLM timeout is hardcoded at 60s
([core/llm/litellm_client.py:36](../../src/orchestrator/core/llm/litellm_client.py)).

## Goals
1. Generated tests run in an **isolated interpreter** with the **project's** deps, never the
   orchestrator's.
2. **Auto-heal** the common `ModuleNotFoundError` class (install the missing dep, retry).
3. **Configurable** LLM timeout (env-driven).
4. A **runner seam** so JUnit/jest can slot in later (bridge to multi-language).
5. Back-compatible: existing fast unit tests keep using the in-process path.

## Architecture

### New: `TestEnvironment` (sdlc/testenv.py)
```python
class TestEnvironment(Protocol):
    @property
    def python(self) -> str: ...                 # interpreter to run tests with
    async def ensure(self, worktree: Path) -> None: ...        # create + install deps (idempotent)
    async def install(self, packages: list[str]) -> bool: ...  # add packages on demand (auto-heal)

class VenvTestEnvironment:                        # DEFAULT for runs
    """A per-worktree venv (uv if available, else python -m venv + pip).
    Created ONCE per worktree and reused across refine iterations."""

class LocalTestEnvironment:                       # current behavior; back-compat + unit tests
    @property
    def python(self) -> str: return sys.executable
```
- **`uv` fast path**: `uv venv <wt>/.sdlc-venv` + `uv pip install`. Detect `uv`; fall back to
  stdlib `venv` + `pip`. (uv is already our toolchain; uv venv is ~instant.)
- **Reuse**: create the venv once per worktree (cache by deps hash), reuse across the
  test/refine loop so we don't pay venv cost per iteration.

### Dependency resolution (what to install)
On `ensure()`:
1. **The test framework** — `pytest` + `pytest-asyncio` (+ `pytest-mock` if any test imports
   `mocker`/`pytest_mock`, detected by a cheap grep of `tests/`).
2. **The project's declared deps** — parse the worktree's manifest: pyproject
   `[project.dependencies]` (+ extras), `requirements*.txt`. The scaffolded greenfield
   project already declares pytest (1.1.0 scaffold) — extend the scaffold to also pin
   runtime deps the generated code uses.
3. Install all into the venv.

### Auto-heal loop (the killer fix)
Wrap the run so an undeclared import self-heals:
```
for attempt in range(max_install_retries=2):
    result = await runner.run(env=env, path=worktree)
    if result.passed: return result
    missing = parse_missing_module(result.output)      # "No module named 'requests'"
    if not missing: return result                       # a real test failure → hand to refine
    pkg = MODULE_TO_PACKAGE.get(missing, missing)       # cv2→opencv-python, bs4→beautifulsoup4, …
    if not await env.install([pkg]): return result
    emit(f"[testenv] auto-installed {pkg} for missing module '{missing}', retrying")
return result
```
- `MODULE_TO_PACKAGE`: small known-alias table + default (module name == package).
- Bounded (`max_install_retries`) so a genuinely-bad import can't loop.
- Only triggers on import/collection errors (`rc==2` / `ModuleNotFoundError`), never on real
  assertion failures — those still go to the refine loop.

### Runner change (sdlc/testrunner.py)
`SubprocessTestRunner.run(*, path, env: TestEnvironment | None = None)` — use `env.python`
instead of `sys.executable` when given. Keep `_SECRET_ENV_PREFIXES` stripping (already there)
+ the venv guarantees dep isolation. Keep `pytest_available` but point it at `env.python`.
Add a `framework` field / detection so `jest`/`junit` runners drop in later.

### Configurable timeout (core/llm/litellm_client.py)
`request_timeout_seconds` defaults from `ORCHESTRATOR_LLM_TIMEOUT_SECONDS` (default raised to
**120**). Thread through the two construction sites (intake factory, feature_runner). One-line
fix, immediate win for large-page extraction.

### Wiring (sdlc/feature_runner.py)
```
env = make_test_environment()        # VenvTestEnvironment unless SDLC_TEST_ISOLATION=local
await env.ensure(path)               # create venv + install project deps + framework
if not await pytest_available(env.python):
    raise FeatureRunError("pytest unavailable in the project env …[sdlc] extra", code=2)
# test/refine loop calls run_with_autoheal(env, runner, path) instead of runner.run(...)
```

## Isolation depth (decision)
- **v1 = venv** (dep isolation + secret-env stripping). Fast, no infra.
- **v2 = cloud sandbox** — there is an existing **E2B gateway** (`tests/gateway/test_run_python_analysis.py`,
  `E2B_API_KEY`). Add an `E2BTestEnvironment` behind the same `TestEnvironment` protocol for
  full fs/network isolation when `E2B_API_KEY` is set. venv stays the zero-config default.

## File-level change list
| File | Change |
|---|---|
| `sdlc/testenv.py` *(new)* | `TestEnvironment` protocol, `VenvTestEnvironment`, `LocalTestEnvironment`, `MODULE_TO_PACKAGE`, `parse_missing_module`, `run_with_autoheal` |
| `sdlc/testrunner.py` | `run(..., env=None)`; `pytest_available(python)`; framework field |
| `sdlc/feature_runner.py` | build+ensure env, preflight on env, auto-heal wrapper in the loop |
| `core/llm/litellm_client.py` | env-configurable `request_timeout_seconds` (default 120) |
| `sdlc/scaffold.py` | scaffolded `pyproject` declares common runtime deps too |
| `pyproject.toml` | (none new; `uv` is the runtime; `[sdlc]` already pins pytest) |

## Phasing
1. **Configurable LLM timeout** — tiny, ships immediately. ✅ unblocks large-page extraction.
2. **`TestEnvironment` + `VenvTestEnvironment` + runner uses `env.python`** — the core isolation.
3. **Dependency resolution** (manifest + framework) on `ensure()`.
4. **Auto-heal** (`ModuleNotFoundError` → install → retry) + `MODULE_TO_PACKAGE`.
5. **Runner seam** (framework detection; pytest impl) — bridge to the languages work.
6. **E2B backend** (optional, behind `E2B_API_KEY`).

## Testing
- Unit: `parse_missing_module`, `MODULE_TO_PACKAGE`, auto-heal loop (stub runner → MNF then
  pass → asserts install called once + bounded), manifest dep parsing. (No real venv → fast.)
- Integration (marked `slow`): a real `uv venv`, install `requests`, run a generated test that
  imports it → green. Gate behind a marker so the default suite stays fast.
- Back-compat: `LocalTestEnvironment` preserves current behavior; existing feature_runner tests
  use it (the autouse fixture sets `SDLC_TEST_ISOLATION=local`).

## Decisions to confirm
1. **Default isolation** = `venv` (vs keep `local` default and opt-in). Recommend **venv default**,
   `SDLC_TEST_ISOLATION=local` to opt out — that's the whole point (real repos break on `local`).
2. **uv required?** Prefer uv, fall back to stdlib `venv`+`pip`. Recommend **fallback**, so a
   plain pip install still works.
3. **Auto-heal scope** — install undeclared deps automatically (recommended; it's the exact
   failure we hit), vs. fail-with-suggestion. Recommend **auto** with a bounded retry + a clear
   log line.

## Honest limits
- venv isolates **deps**, not filesystem/network — full sandbox is the E2B backend (v2).
- Auto-heal guesses module→package; the alias table covers the common cases, unknowns default
  to the module name (usually correct) and a wrong guess just fails the install harmlessly.
- Per-worktree venv adds setup time (mitigated by uv + reuse across refine iterations).

## First step
Phase 1 (configurable timeout) + Phase 2 (`VenvTestEnvironment`, default on) + Phase 4
(auto-heal) is the smallest set that **fixes the exact failures from this session** end to
end. Proof: re-run the live A-5 crawler intent — it should auto-install `requests`/`pytest-mock`
into the project venv and reach `VERDICT: PASSED` with no manual `pip install`.
