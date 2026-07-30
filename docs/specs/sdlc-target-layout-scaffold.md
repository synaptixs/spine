# Design: SDLC target layout — scaffold vs. use-existing

**Status:** Implemented in 1.1.0 (`layout.py`, `scaffold.py`, codegen `layout` wiring,
`--layout`/`--package-name` flags, `[sdlc]` extra + pytest preflight)
**Author:** orchestrator
**Scope:** `sdlc feature` / `sdlc run` codegen file placement
**Ships as:** 1.1.0 (feature), bundling the `[sdlc]`/pytest fix

### Locked decisions
1. **`src/` layout** is the scaffold default.
2. **Brownfield** (existing structure present) → **use the existing structure**, never scaffold.
3. **Greenfield** → **scaffold a new repo structure first**, project-type-aware
   ("depending upon the project"), **local-first, then pushed to the remote** (safe → live).
4. **Bundle the `[sdlc]` extra + pytest preflight into this feature** (not a separate patch).

---

## 1. Problem

The codegen pipeline has **no concept of a target project layout**. File paths are
chosen implicitly, and only one signal switches behavior: whether
`PKGCodegenGrounder` finds existing symbols ([codegen.py `_grounding`](../../src/orchestrator/sdlc/codegen.py)).

- **Brownfield** (repo has recognizable code): a "BROWNFIELD RULES" prompt tells
  the model to "create files inside the existing package structure." Mostly works.
- **Greenfield** (empty repo): the base prompt assumes "a fresh, empty worktree"
  and lets the **LLM invent every path**. Nothing pins the project's identity.

### Observed symptom
Running `sdlc feature` against the (empty) `Answer-Engine-Optimization` repo placed
code at `src/orchestrator/pkg/page.py` — the **orchestrator's own namespace** leaked
into an unrelated project — plus a stray root-level `stack_decision.py`. Because each
feature run improvises independently, a greenfield repo drifts into an incoherent
structure across runs.

### Root causes
1. No **project identity** (package name) is derived from the target repo and passed
   to codegen.
2. No **scaffold step** — greenfield never gets a defined skeleton; the model fills
   the vacuum with arbitrary (often wrong) paths.
3. No **explicit user control** over "create new structure" vs "extend existing."

---

## 2. Goals / non-goals

**Goals**
- Deterministic, project-appropriate file placement in **both** greenfield and
  brownfield repos (eliminate the `src/orchestrator/...` leak).
- An explicit `--layout` choice, defaulting to **auto-detect** (decided with the user).
- Greenfield repos get a **coherent, runnable skeleton** generated once, reused by
  every subsequent feature.
- Fold in the known **pytest gap**: a scaffolded project declares pytest so its tests
  actually run.

**Non-goals**
- Multi-language scaffolding beyond Python in v1 (design leaves room; impl ships Python).
- Changing brownfield grounding/retrieval behavior beyond consuming the layout contract.
- Monorepo / multi-package targets (single package root in v1).

---

## 3. Design

### 3.1 `TargetLayout` contract (the core fix)

A small, deterministic value computed **once per run** and threaded into codegen so
paths stop being invented.

```python
@dataclass(frozen=True)
class TargetLayout:
    package_name: str      # e.g. "answer_engine_optimization"
    source_dir: str        # e.g. "src/answer_engine_optimization" or "answer_engine_optimization"
    tests_dir: str         # e.g. "tests"
    src_layout: bool       # True => src/ layout
    scaffolded: bool       # True if we created the skeleton this run
    mode: str              # "new" | "existing"
```

- **`package_name`** derives from the repo name, sanitized to a valid Python module
  (`Answer-Engine-Optimization.` → `answer_engine_optimization`: lowercase, strip
  trailing punctuation, `-`/space → `_`, collapse repeats). Overridable via
  `--package-name`.
- Codegen prompts (both greenfield and brownfield) reference these concrete values:
  *"New modules go under `{source_dir}/`; tests under `{tests_dir}/`; the package is
  `{package_name}`."* This replaces the vague "fresh empty worktree" / "existing
  package structure" language that lets the model improvise.

### 3.2 Layout resolution — `--layout auto|new|existing` (default `auto`)

Reuse the existing detector: [`ProjectProfile.from_repo`](../../src/orchestrator/catalog/profile.py)
(languages, framework, test_runner) plus a cheap "is there a recognizable package?" scan.

```
auto:      empty / no recognizable package  -> mode = new
           recognizable package present     -> mode = existing
new:       force scaffold (into a package subdir if the repo is non-empty; warn)
existing:  never scaffold; detect layout from the repo, place files accordingly
```

"Recognizable package" = presence of a `pyproject.toml`/`setup.cfg` with a package,
or an importable top-level package dir with `__init__.py`, or a `src/<pkg>/` layout.

**Why `auto`, not always-new:** pointing `--repo` at an established codebase and
force-scaffolding would collide with / pollute it. `auto` keeps the desired greenfield
behavior (AEO is empty → `new`) without that footgun. `--layout new` remains available
for an explicit override.

### 3.3 Scaffolder (template-based, idempotent)

A new `orchestrator/sdlc/scaffold.py`. **No LLM** — a fixed, minimal, runnable skeleton.
**Project-type-aware** ("depending upon the project"): the template is selected from
[`ProjectProfile`](../../src/orchestrator/catalog/profile.py) (language/framework). v1
ships the Python `src/` template; the seam allows future templates (Node, .NET) without
reworking the resolver.

Python `src/` template:
```
<repo>/
  pyproject.toml          # name = package_name; [tool.pytest.ini_options]; deps incl. pytest
  README.md               # one line: generated project
  .gitignore              # it is a real *repo* structure (Python .gitignore)
  src/<package>/__init__.py
  tests/__init__.py
```

- Idempotent: if the skeleton already exists (e.g. a prior run scaffolded it, or it
  arrived via the remote clone), it is a no-op — only missing files are written. Safe to
  re-run per feature.
- The generated `pyproject` declares `pytest` (+ `pytest-asyncio`), directly addressing
  the [pytest runtime gap](#5-pytest-runtime-gap-bundled).

### 3.6 Local-first, then remote (safe → live)

Scaffolding rides the **existing safe/live seam** — no new push machinery:

- **Safe (`--safe`, default):** resolve layout → scaffold (greenfield) → generate feature
  → **commit locally** in the worktree. Nothing leaves the machine. This is where you
  inspect the new structure before it touches the remote.
- **Live (`--live`):** the same worktree (scaffold + feature) is **pushed to the remote**
  and a PR is opened — so the first greenfield run *establishes the repo structure on the
  remote* as part of the feature PR.
- **Self-healing detection:** once the first scaffold lands on the remote, every later run
  clones a repo that already has the structure → `auto` detects it as **`existing`** →
  no re-scaffold, just extend. Greenfield is a one-time event per repo.

### 3.4 Wiring into `feature_runner`

Insert layout resolution + scaffold **after** the worktree is created
([feature_runner.py:131](../../src/orchestrator/sdlc/feature_runner.py)) and **before**
grounding/codegen:

```
path = await WorkspaceManager(...).create(sdlc_id, issue_key)
layout = resolve_layout(path, mode=cli_layout, package_name=cli_package_name)   # NEW
if layout.mode == "new":
    scaffold(path, layout)                                                       # NEW (idempotent)
emit(f"[layout] mode={layout.mode} package={layout.package_name} src={layout.source_dir}")
grounder = PKGCodegenGrounder.from_repo(path)
codegen  = LLMCodegenAdapter(llm, model=..., grounder=grounder, layout=layout)   # pass layout
```

`LLMCodegenAdapter` gains an optional `layout: TargetLayout | None`; its
implement/refine prompts include the layout block when present. Backward compatible —
`None` preserves today's behavior.

### 3.5 CLI surface

```
orchestrator sdlc feature ... [--layout auto|new|existing] [--package-name NAME] [--src-layout/--flat]
orchestrator sdlc run     ... [--layout auto|new|existing] [--package-name NAME]
```

Defaults: `--layout auto`, `--package-name` derived, `--src-layout` on.

---

## 4. Behavior matrix

| Repo state | `auto` resolves to | Result |
|---|---|---|
| Empty | `new` | Scaffold skeleton (with pytest) → generate into `src/<pkg>/` + `tests/` |
| Recognizable Python package | `existing` | Detect layout, generate per conventions, no scaffold |
| Non-empty, no recognizable package | `new` (into subdir) | Scaffold a package subdir; warn; never clobber existing files |

---

## 5. pytest runtime gap (bundled)

**Bundled into this feature** (decision 4). Today `pip install agent-orchestrator` omits
pytest (dev-only extra), so the `SubprocessTestRunner` (`python -m pytest`) fails for
installed users. Three layers of fix, all in 1.1.0:

- **Scaffolded projects** declare pytest in their generated `pyproject` (§3.3) — so the
  greenfield path is runnable out of the box.
- **`[sdlc]` extra** (pytest + pytest-asyncio) so `pip install 'agent-orchestrator[sdlc]'`
  yields a working pipeline for brownfield targets too.
- **Preflight check** in `SubprocessTestRunner`: if `pytest` isn't importable by the
  runner interpreter, fail with a clear "install pytest / use the `[sdlc]` extra" message
  instead of letting the refine model edit `pyproject.toml` blindly.

---

## 6. Testing plan

- **Unit — package-name derivation:** `Answer-Engine-Optimization.`, names with spaces,
  leading digits, unicode → valid module names.
- **Unit — `resolve_layout`:** empty dir → `new`; dir with `src/<pkg>/__init__.py` →
  `existing`; non-empty non-package → `new`+warn; explicit `new`/`existing` override auto.
- **Unit — scaffold idempotency:** scaffolding twice writes no duplicates; existing
  files untouched.
- **Integration (feature_runner):** stub codegen asserts it receives a `layout`; a
  greenfield run scaffolds then places files under `src/<pkg>/` (no `src/orchestrator/...`).
- **Regression:** `layout=None` path unchanged (existing tests stay green).
- **Live:** `sdlc feature` against the empty AEO repo → files under
  `src/answer_engine_optimization/`, tests under `tests/`, `VERDICT: PASSED`.

---

## 7. Rollout

- Feature-sized → ship as **1.1.0** through develop → main → TestPyPI.
- Backward compatible (layout optional; default `auto` only scaffolds empty repos).
- Docs: USER_GUIDE Step 7 + the QA test plan note the `--layout` flag and the `[sdlc]`
  extra / pytest requirement.

---

## 8. Resolved decisions

1. **Scaffold layout:** `src/` layout. ✅
2. **Brownfield:** existing structure present → **use it**, never scaffold. ✅
3. **Greenfield:** scaffold first, project-type-aware; **local-first (safe) then pushed to
   remote (live)** — see §3.6. ✅
4. **Scaffold contents:** real *repo* structure — include `.gitignore`; starter CI is out
   of scope for v1 (keep minimal). ✅ (interpreted from "repo structure")
5. **pytest fix:** bundled into this feature (§5), not a separate patch. ✅

### One low-stakes default (override if you disagree)
- `--layout new` on a **non-empty, non-package** repo → scaffold into a `src/<pkg>/` subdir
  and **warn**; never clobber existing files. (Stricter alternative: refuse without
  `--force`.)
