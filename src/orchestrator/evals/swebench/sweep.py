"""Run a list of instances under a total spend cap.

The cap (plan D3) is written down before a run and is a hard stop: reaching it ends the sweep and
reports what was done and what was not. There is no silent resume — running the same command again
is a decision, and the instances still without a result stay visible as "not run", which the ledger
refuses to turn into a headline.

**The cap is a projection, not a tripwire.** Before an instance starts, the sweep checks that what has
been spent plus that instance's own ceiling (``cfg.max_cost_usd``) still fits; if it would not, the sweep
stops. What is booked is the spend each run *recorded* (build and review; the pipeline's intake and design
calls are small and are not under its own cap), so the total is a bound on recorded spend, not an
invoice.

A ``generator-error`` row is not retried by default. Retrying only the failures would bias a result
upward, and most are the pipeline's own crashes, which are results. ``retry_generator_errors=True`` is for
the day an infrastructure fault (a daemon, a pull) has been fixed, and the retry row carries the earlier
attempts' spend. Every other bucket is a result and is never re-run.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from orchestrator.evals.swebench.adapter import Config, Docker, Launcher, run_instance
from orchestrator.evals.swebench.instances import Instance
from orchestrator.evals.swebench.ledger import Ledger, LedgerRow


@dataclass
class SweepResult:
    ran: int = 0
    skipped: int = 0
    spent_usd: float = 0.0
    stopped_by_cap: bool = False
    not_run: list[str] = field(default_factory=list)


async def sweep(
    instances: Sequence[Instance],
    *,
    cfg: Config | Any,
    ledger: Ledger,
    docker: Docker | Any,
    launcher: Launcher | Any,
    max_total_usd: float,
    run: Callable[..., Awaitable[LedgerRow]] = run_instance,
    retry_generator_errors: bool = False,
) -> SweepResult:
    if max_total_usd <= 0:
        raise ValueError("the total spend cap must be positive")
    done = {r.instance_id: r for r in ledger.rows()}
    result = SweepResult(
        spent_usd=sum(r.spent_usd for r in done.values() if r.instance_id in _ids(instances))
    )
    for index, inst in enumerate(instances):
        iid = inst.generation.instance_id
        settled = done.get(iid)
        if settled is not None and not (retry_generator_errors and settled.bucket == "generator-error"):
            result.skipped += 1
            continue
        if result.spent_usd + float(cfg.max_cost_usd) > max_total_usd:
            result.stopped_by_cap = True
            result.not_run = [
                i.generation.instance_id
                for i in instances[index:]
                if done.get(i.generation.instance_id) is None
                or (retry_generator_errors and done[i.generation.instance_id].bucket == "generator-error")
            ]
            return result
        row = await run(inst, cfg, ledger, docker, launcher)
        result.ran += 1
        result.spent_usd += row.spent_usd - (settled.spent_usd if settled is not None else 0.0)
    return result


def _ids(instances: Sequence[Instance]) -> set[str]:
    return {i.generation.instance_id for i in instances}
