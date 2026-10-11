# SWE-bench Verified harness — how to run it

**Status:** built and tested; **no benchmark number has been produced yet.** Tracked as SSPN-135.
Plan and decisions: `docs/specs/codegen-benchmark-roadmap.md`. The grader is the official `swebench`
package, run separately and pinned; nothing in Spine scores a patch.

The harness lives in `src/orchestrator/evals/swebench/` (standard library only) with two scripts. It
needs Docker, an `x86_64` image per instance (the published images have no arm64 build, so on Apple
Silicon they run under emulation), and a model key.

## 1. Export the instances (bench venv)

`swebench` and `datasets` are deliberately not Spine dependencies. In a separate virtual environment:

```bash
pip install 'swebench==5.0.2' datasets
python scripts/swebench_prep.py --out ~/swebench/pilot --sample 20 --seed 7
```

This writes `instances.jsonl` and `pins.json` (the dataset revision, the `swebench` version, the
selection and its seed). Each row carries the issue text, the base commit, the image **pinned by
digest**, and only the *names* of the files the test patch touches. The harness refuses a file that
carries the gold patch, the test patch, the grading tests or the issue's comment thread.

## 2. Run (Spine's venv)

```bash
python scripts/swebench_run.py --instances ~/swebench/pilot/instances.jsonl \
    --workdir ~/swebench/pilot/run --max-cost 10 --max-total 100
```

`--workdir` must be outside the repository checkout. `--max-cost` caps one instance; `--max-total` caps the
whole run, and it is a **projection over recorded spend**, not a tripwire: before an instance starts, the
run stops if what has been spent plus `--max-cost` would pass `--max-total`, and it lists what was not run.
What counts as spent is what each run *recorded* (build and review); the pipeline's own intake and design
calls are small and are not under its cap, so this bounds recorded spend, it is not an invoice.

Running the same command again continues from the ledger. A `generator-error` row is **not** retried by
default (retrying only the failures would bias a result upward); pass `--retry-generator-errors` after an
infrastructure fault has been fixed, and the new row carries the earlier attempts' spend. A park, a budget
stop or an unresolved run is a result and is never re-run. The model key is read from the checkout's `.env`
(`--env-file`) — **only** `ANTHROPIC_API_KEY` and `OPENAI_API_KEY` are read from it; the rest of that file
never reaches a run. The child sees an allow-listed environment only; `--pass-env NAME` (repeatable) adds a
named variable, one at a time, for a provider that needs more than the two keys.

Per instance it pulls the image, builds the generation repository — the instance's *base commit*, exported by a checkout (not
`git archive`, which rewrites `export-subst` files) — into one fresh commit, runs `sdlc autorun` as its own process group with a wall-clock limit, and files the outcome in
exactly one bucket (`ledger.jsonl`). The project's tests run in a throwaway offline container from the
instance image with only the run's changed files laid over it.

## 3. Grade

`predictions.jsonl` (written beside the ledger) is the official grader's input:

```bash
swebench eval verified -p predictions.jsonl --run-id pilot -j 4
```

Then settle the books from the grader's report (`<model>.<run-id>.json`, in the grader's report directory):

```bash
python scripts/swebench_report.py --instances ~/swebench/pilot/instances.jsonl \
    --workdir ~/swebench/pilot/run --report <report>.json
```

This prints the six buckets over the whole denominator and writes `results.json` (the summary and the
settled rows) beside the ledger, which stays append-only. It **refuses** to produce a headline from a
ledger with a missing instance, and a `submitted` instance the report does not mention is a
`grader-error`, not a pass: absence is not a result.

Before the generator sees it, the copy's git tree is compared with the image's tree for that commit and
the instance is refused (a `generator-error`) on any difference. The check exists because the images are
not what they seem: SWE-bench's setup commit on top of the base commit is not empty (file modes, a
`pyproject.toml` pin, a `tox.ini` flag), and `git archive` plus `git add -A` quietly lose or rewrite
tracked files. One instance of each of the twelve repositories passes it.

## Buckets

`resolved` · `unresolved` · `parked` · `generator-error` · `budget-stop` · `grader-error` — every
instance in exactly one, all in the denominator. A run that finished without producing a change is
`unresolved`; a crash is a `generator-error`.

## Limits worth knowing

* The in-loop signal ran on all twelve repositories' images; eleven were green, and sympy's runner
  exited non-zero on one test (a `RecursionError`) that fails on the unmodified base tree.
* A C or Cython source edit is not rebuilt inside the test container (the official grader rebuilds).
* The in-loop runner takes no baseline: a test that already fails in a file the run modifies stays red.
* Contamination of the model by these public repositories cannot be ruled out.
