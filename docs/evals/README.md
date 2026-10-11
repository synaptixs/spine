# Evals

Honest, reproducible scorecards from the agentic eval harness
(`orchestrator.evals` + `scripts/agentic_eval.py`). Each dated file is one run:
acceptance rate, cost, convergence, intervention rate, and variance across
repeats — single-shot vs. the agentic loop.

Generate one (on demand — real LLM cost, writes only to /tmp worktrees):

```bash
uv run python scripts/agentic_eval.py                 # single-shot baseline
EVAL_REPEATS=3 uv run python scripts/agentic_eval.py  # surface variance
```

For the SWE-bench Verified harness (a different instrument: it runs the SDLC pipeline on public
benchmark instances and keeps six-bucket books), see [swebench-harness.md](swebench-harness.md).

Scorecards are committed here as the project's credibility record. The harness
is persona-agnostic — the same runner scores the codebase-auditor persona
(Bet 4) once it lands.
