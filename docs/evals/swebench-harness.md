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

`--workdir` must be outside the repository checkout. `--max-cost` caps one instance; `--max-total` is a
hard stop for the whole run: reaching it ends the run and lists what was not run, and running the same
command again continues from the ledger (a `generator-error` row is retried; a park or a budget stop is
a result). The model key is read from the checkout's `.env` (`--env-file`) — **only** `ANTHROPIC_API_KEY`
and `OPENAI_API_KEY` are read from it; the rest of that file never reaches a run.

Per instance it pulls the image, builds the generation repository with `git archive` into one fresh
commit, runs `sdlc autorun` as its own process group with a wall-clock limit, and files the outcome in
exactly one bucket (`ledger.jsonl`). The project's tests run in a throwaway offline container from the
instance image with only the run's changed files laid over it.

## 3. Grade

`predictions.jsonl` (written beside the ledger) is the official grader's input:

```bash
swebench eval verified -p predictions.jsonl --run-id pilot -j 4
```

`orchestrator.evals.swebench.ledger.apply_grading` settles the ledger from the grader's report, and
`summary` refuses to produce a headline from a ledger with a missing or still-provisional row.

## Buckets

`resolved` · `unresolved` · `parked` · `generator-error` · `budget-stop` · `grader-error` — every
instance in exactly one, all in the denominator. A run that finished without producing a change is
`unresolved`; a crash is a `generator-error`.

## Limits worth knowing

* A C or Cython source edit is not rebuilt inside the test container (the official grader rebuilds).
* The in-loop runner takes no baseline: a test that already fails in a file the run modifies stays red.
* Contamination of the model by these public repositories cannot be ruled out.
