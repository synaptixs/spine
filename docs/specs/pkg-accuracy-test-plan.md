# Testing PKG accuracy — manual scenarios

Hands-on verification for everything [`pkg-accuracy-roadmap.md`](pkg-accuracy-roadmap.md)
shipped: four oracles, the CI gate, the measured caveat, and the intent layer.

**Every command below was run and its output captured** — the "expect" lines are real output,
not intentions. Where a number depends on the repository, that is said.

Companion to [`cli-test-plan.md`](cli-test-plan.md), which covers the CLI surface broadly;
this covers one subsystem deeply.

---

## 0. Prerequisite — use the right `orchestrator`

**The most likely reason a command below fails is that your shell resolves a different,
older install.** `pkg accuracy` does not exist in any published release yet.

```bash
command -v orchestrator && orchestrator pkg --help | grep -c accuracy
```

If that prints a path outside this repo's `.venv`, or prints `0`, pick one:

```bash
# A. point at the repo's venv — nothing to install
export SPINE=/Users/falcon/projects/ai/spine/.venv/bin/orchestrator

# B. or install this working tree into whichever venv is active
uv pip install -e /Users/falcon/projects/ai/spine
```

Every command here uses `$SPINE`. With option B, substitute `orchestrator`.

Confirm before continuing — you should see both:

```bash
$SPINE pkg --help | grep -E "accuracy|verify"
```

---

## 1. The demo repository

Twenty lines that trigger every finding at once: a resolvable route, an **f-string route the
extractor cannot see**, a **call through a parameter**, and an **issue key in the commit
message**.

```bash
rm -rf /tmp/spine-testdrive && mkdir -p /tmp/spine-testdrive/svc && cd /tmp/spine-testdrive && touch svc/__init__.py && printf 'from fastapi import APIRouter\n\nrouter = APIRouter()\nENTITY = "widget"\n\n\n@router.get("/widgets")\ndef list_widgets() -> list[str]:\n    return []\n\n\n@router.get(f"/widgets/{ENTITY}")\ndef get_widget() -> dict:\n    return {}\n\n\ndef run(callback) -> int:\n    return callback()\n' > svc/api.py && git init -q && git add -A && git -c user.email=t@e -c user.name=t commit -q -m "feat(svc): widgets API (DEMO-1)" && echo ready
```

---

## 2. Parity — a route the graph is missing

```bash
$SPINE pkg accuracy --oracle parity /tmp/spine-testdrive
```

**Expect**

```
per-construct parity — 2 declared, 1 in graph
  shortfall 1 — declared in source, absent from the graph
  surplus   0 — expected where a router is mounted more than once
    short: svc/api.py:7 declares 2 Endpoint, graph holds 1
```

The f-string route is declared and unextractable. This is the class that hid `GET /healthz`
and `GET /readyz` in Spine's own `registry/api/app.py` — real routes, no nodes, invisible to
blast radius.

**Why shortfall and surplus are never averaged:** a router mounted twice legitimately yields
more `Endpoint` nodes than decorators. A single ratio would read as recall while hiding both.

---

## 3. Invention — a call to something that does not exist

```bash
$SPINE pkg accuracy --oracle invention /tmp/spine-testdrive
```

**Expect**

```
invented CALLS edges — 1 (100.00% of all calls)
  1 CALLS, 1 to external targets
  1 candidate(s) examined, 0 unexaminable
    svc/api.py:18 py:svc.api.run -CALLS-> py:callback (callback is local)
```

The graph claims a module named `callback` exists outside the tree. It is a parameter.

### 3a. Negative control — real external calls must never be flagged

```bash
printf 'import json\n\n\ndef dump(x: dict) -> str:\n    return json.dumps(x)\n' > /tmp/spine-testdrive/svc/ok.py
$SPINE pkg accuracy --oracle invention /tmp/spine-testdrive
```

**Expect:** still exactly **1** invented edge. `json.dumps` is genuinely external. Treating
imports as bindings would report every legitimate external call as fiction — the failure mode
that would matter most.

```bash
rm /tmp/spine-testdrive/svc/ok.py
```

### 3b. The sampler, for what no detector reaches

```bash
$SPINE pkg accuracy --oracle invention /tmp/spine-testdrive --sample 3 --kind CALLS
```

**Expect** a `sampled CALLS edge(s) for review` block, `deterministic for this commit`. For
`CONSUMES`, `EXPOSES` and `REFERENCES` no detector exists — only a person reading the source
can say whether a join is real, and determinism means two reviewers see the same facts.

---

## 4. The gate — three outcomes that must differ

This is the sequence worth running in full. All three verified.

```bash
cd /tmp/spine-testdrive
$SPINE pkg accuracy --scoreboard .
$SPINE pkg accuracy --check . ; echo "exit=$?"
```

**Expect:** `OK — 0 gated regression(s)`, **`exit=0`**. Today's numbers are baselined *in* —
the gate stops things getting worse, it does not demand perfection.

### 4a. A real regression must fail the build

```bash
printf 'from fastapi import APIRouter\nrouter = APIRouter()\nP = "x"\n\n\n@router.get(f"/more/{P}")\ndef more() -> dict:\n    return {}\n' > svc/broken.py
$SPINE pkg accuracy --check . ; echo "exit=$?"
```

**Expect**

```
[REGRESSION] parity: shortfall increased — was 1, now 2
exit=1
```

```bash
rm svc/broken.py
```

### 4b. Ordinary code must **not** fail the build

The design decision the whole phase turns on.

```bash
printf 'def handler(cb) -> int:\n    return cb()\n' > svc/normal.py
$SPINE pkg accuracy --check . ; echo "exit=$?"
```

**Expect**

```
[trend]      invention: 1 -> 2 (ungated — moves with ordinary commits)
pkg accuracy --check: OK — 0 gated regression(s), 0 improvement(s).
exit=0
```

A callback parameter is normal Python, not a defect. Invention is measured against the
repository itself, so it moves whenever anyone writes code — it is **recorded, never gated**.
A tolerance band was rejected: an arbitrary threshold eventually fires on something legitimate
and gets widened until it means nothing.

```bash
rm svc/normal.py
```

| metric | gate | why |
|---|---|---|
| corpus precision & recall | **strict** | measured against committed fixtures; repo churn cannot move it |
| parity shortfall | **ratchet** | rises only when the graph falls behind the source |
| invention | **never** | moves on blameless commits |
| runtime recall | **never** | non-deterministic; moves when the suite grows |

---

## 5. Corpus scores, and the skip that broke CI

```bash
cd /Users/falcon/projects/ai/spine && $SPINE pkg accuracy
```

**Expect** 7 cases: Python `CALLS` **P 0.80 / R 0.73**, TypeScript **P 1.00 / R 0.50**,
every other kind 1.00/1.00. The two languages fail differently — Python trades precision for
reach and emits phantoms; TypeScript keeps precision perfect and emits less.

### 5a. An absent optional extra is unmeasured, not zero

The bug that failed CI on #179.

```bash
uv pip uninstall --python .venv/bin/python tree-sitter-typescript
$SPINE pkg accuracy | tail -5
```

**Expect**

```
  SKIPPED 3 case(s) — no front-end installed for typescript: …
  Not scored zero: an absent optional extra is not a regression.

pkg accuracy: 4 case(s) scored.
```

Not `0.0000`. A front-end whose extra is missing emits nothing, which would score every kind
at zero and read as total collapse.

```bash
$SPINE pkg accuracy --check . ; echo "exit=$?"   # expect exit=0 — a richer baseline is not a regression
uv pip install --python .venv/bin/python 'tree-sitter-typescript>=0.21'
```

---

## 6. Runtime oracle — recall from real execution

```bash
$SPINE pkg accuracy --oracle runtime . --tests tests/pkg
```

**Expect** `CALLS recall ≈ 0.70 lower bound`, ~1,357 observed pairs, **0 unmapped**, coverage
~21%, ~35s.

**It executes the repository's test suite.** No other command here does — it is never implied,
echoes the command first, and runs in a subprocess.

**It measures recall only.** A call the tests never made is *untested*, not *wrong*; precision
is not computable from a trace, and the report says so every run.

The number moves with the suite: it read 0.61 before `tests/pkg` gained 18 tests and 0.70
after, with no change to the graph. That is why it is never gated.

---

## 7. The thesis, in one command

```bash
$SPINE pkg verify corpus/python/dispatch/.repo
```

**Expect:** `pkg verify: OK — 0 error(s), 0 warning(s)`

That fixture contains a `CALLS` edge to a function that does not exist. A graph asserting a
call to a non-existent symbol is perfectly self-consistent. **Consistency was never accuracy.**

### 7a. The new checks on the real repo

```bash
$SPINE pkg verify . | tail -3
```

**Expect** `source-parity` warnings naming `registry/api/app.py:110` (the `/healthz` and
`/readyz` gap) and an `invented-call` warning reporting ~496 edges, ~3.2%. Warnings, never
errors — a check that fails a build for a known front-end limitation is a check people switch
off.

---

## 8. The measured caveat a reader actually sees

```bash
cd /Users/falcon/projects/ai/spine && .venv/bin/python -c "from orchestrator.sdlc.builddoc import _blast_prose; print(_blast_prose({'call_graph_available': True, 'modules': []}, 'python').split('**Caveat:**')[1])"
```

**Expect** the caveat ending:

> Measured `CALLS` recall for python is **0.73** (against the extractor's own test corpus, not
> this repository) — treat this list as a lower bound.

The parenthetical is the deliverable, not decoration. A reader who takes 0.73 as a statement
about *their* repository has been misled by us.

```bash
.venv/bin/python -c "from orchestrator.sdlc.builddoc import _blast_prose; print(_blast_prose({'call_graph_available': True, 'modules': []}, 'go').split('**Caveat:**')[1])"
```

**Expect** the clause **absent** — no `0.00`. Six of eight front-ends have no corpus, and a
language nobody measured has not scored badly; it has not been scored.

---

## 9. The intent layer

**No CLI surface — see Known gaps.** Reachable only from Python today:

```bash
.venv/bin/python -c "
from pathlib import Path
from orchestrator.pkg import RepoCodeExtractor
from orchestrator.pkg.intent_link import link_intents
from orchestrator.pkg.facts import EdgeKind
root = Path('/tmp/spine-testdrive')
b = RepoCodeExtractor().extract(root); cov = link_intents(b, root)
print(f'{cov.symbols_attributed}/{cov.symbols_total} symbols -> {cov.intents} intent(s)')
[print(' ', e.src, '->', e.dst) for e in b.edges if e.kind is EdgeKind.SERVES]
"
```

**Expect**

```
4/4 symbols -> 1 intent(s)
  py:svc.api.list_widgets -> intent:DEMO-1
  py:svc.api.get_widget -> intent:DEMO-1
  py:svc.api.run -> intent:DEMO-1
```

On Spine itself the rate is **11.8%** (1,172 of 9,969 symbols, 34 intents) — low because this
repository's history was squashed on import from a private one, so `git blame` names a handful
of large import commits carrying no issue keys. A property of the repository, not the method.

### 9a. Degradation — silence, never a crash

```bash
mkdir -p /tmp/no-git/app && touch /tmp/no-git/app/__init__.py && printf 'def f() -> int:\n    return 1\n' > /tmp/no-git/app/mod.py
.venv/bin/python -c "
from pathlib import Path
from orchestrator.pkg import RepoCodeExtractor
from orchestrator.pkg.intent_link import link_intents
root = Path('/tmp/no-git')
cov = link_intents(RepoCodeExtractor().extract(root), root)
print('intents:', cov.intents, '| commits scanned:', cov.commits_scanned)
"
```

**Expect:** `intents: 0 | commits scanned: 0`. A shallow clone, a tarball or a vendored tree
yields no intents and no error.

---

## Known gaps

Both found while writing this plan. Neither is a defect in what shipped; both are places the
plans were incomplete.

**`link_intents` is wired into nothing — and wiring it naively would be a mistake.**
`doc_link` and `import_link` are invoked from `knowledge/analysis.py` and `pkg/extractor.py`;
the intent pass is library-only, so `Intent` nodes never appear in `pkg extract`, `understand`,
`state` or `episteme/`. Scenario 9 is the only way to see them.

Measured on this repository before deciding what to do about it:

| | |
|---|---|
| extraction | **2.0s** |
| intent scan | **23.0s** |
| slowdown if wired in by default | **11×** |

`git blame` runs once per file, so making the pass default-on would make every extraction more
than ten times slower. The fix is a design ticket, not a wiring change — an opt-in flag, a
commit-keyed cache, or replacing per-file blame with a single `git log --numstat` walk.
`FEATURES.md` now records it as *library*, with the measurement, rather than claiming it is
automatic.

*(A second gap — `CONTRIBUTING.md` telling contributors to run a bare `orchestrator pkg
accuracy --check`, which fails for anyone whose shell resolves a different install — was fixed
in the same change as this document: it now says `uv run`, matching the pre-commit hooks.)*

---

## Cleanup

```bash
rm -rf /tmp/spine-testdrive /tmp/no-git
cd /Users/falcon/projects/ai/spine && git status --short   # expect clean
```
