"""Choosing the instances, and running them under a total cap.

Pilot rules (plan D5): a published seed, stratified across the dataset's repositories, at least
five Django and two Sympy. Cap rules (D3): reaching the cap stops the sweep and reports what was
done; nothing resumes silently, and a stopped sweep leaves the rest as "not run", which the ledger
refuses to summarise.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

from orchestrator.evals.swebench.instances import GenerationInput, Instance
from orchestrator.evals.swebench.ledger import Ledger, LedgerRow
from orchestrator.evals.swebench.selection import select
from orchestrator.evals.swebench.sweep import sweep

# --- the sample ---------------------------------------------------------------------------

#: The real shape of Verified: repo → instance count.
VERIFIED = {
    "django/django": 231, "sympy/sympy": 75, "sphinx-doc/sphinx": 44, "matplotlib/matplotlib": 34,
    "scikit-learn/scikit-learn": 32, "astropy/astropy": 22, "pydata/xarray": 22, "pytest-dev/pytest": 19,
    "pylint-dev/pylint": 10, "psf/requests": 8, "mwaskom/seaborn": 2, "pallets/flask": 1,
}  # fmt: skip


def _pool() -> list[tuple[str, str]]:
    return [
        (f"{repo.replace('/', '__')}-{n}", repo)
        for repo, count in VERIFIED.items()
        for n in range(1, count + 1)
    ]


def test_the_sample_is_the_requested_size_and_free_of_duplicates() -> None:
    chosen = select(_pool(), 20, seed=7, minimums={"django/django": 5, "sympy/sympy": 2})
    assert len(chosen) == 20 and len(set(chosen)) == 20


def test_the_pilot_minimums_hold_for_every_seed() -> None:
    repos = dict(_pool())
    for seed in range(25):
        counts = Counter(
            repos[i] for i in select(_pool(), 20, seed=seed, minimums={"django/django": 5, "sympy/sympy": 2})
        )
        assert counts["django/django"] >= 5 and counts["sympy/sympy"] >= 2, (seed, counts)


def test_the_same_seed_gives_the_same_sample_whatever_order_the_pool_arrives_in() -> None:
    pool = _pool()
    a = select(pool, 20, seed=3, minimums={})
    b = select(list(reversed(pool)), 20, seed=3, minimums={})
    assert a == b
    assert a != select(pool, 20, seed=4, minimums={})


def test_the_sample_spreads_across_repositories() -> None:
    repos = dict(_pool())
    counts = Counter(
        repos[i] for i in select(_pool(), 20, seed=1, minimums={"django/django": 5, "sympy/sympy": 2})
    )
    assert len(counts) >= 6  # not twenty Django instances


def test_asking_for_more_than_exists_or_an_impossible_minimum_is_an_error() -> None:
    with pytest.raises(ValueError, match="only"):
        select(_pool(), 10_000, seed=1, minimums={})
    with pytest.raises(ValueError, match="minimum"):
        select(_pool(), 3, seed=1, minimums={"django/django": 5})
    with pytest.raises(ValueError, match="mwaskom/seaborn"):
        select(_pool(), 20, seed=1, minimums={"mwaskom/seaborn": 5})


# --- the sweep ----------------------------------------------------------------------------


def _inst(i: int) -> Instance:
    gen = GenerationInput(f"r__r-{i}", "psf/requests", "1", "b" * 40, "p", "img:latest", "sha256:" + "a" * 64)
    return Instance(generation=gen, test_patch_files=())


class _Runner:
    """Stands in for run_instance: files a row with a fixed cost."""

    def __init__(self, cost: float = 4.0, bucket: str = "submitted") -> None:
        self.cost, self.bucket = cost, bucket
        self.ran: list[str] = []

    async def __call__(self, inst: Instance, *args: Any) -> LedgerRow:
        self.ran.append(inst.generation.instance_id)
        ledger: Ledger = args[1]
        row = LedgerRow(inst.generation.instance_id, "psf/requests", self.bucket, "r", spent_usd=self.cost)
        ledger.append(row)
        return row


def _go(tmp_path: Path, insts: list[Instance], runner: _Runner, **kw: Any) -> Any:
    from types import SimpleNamespace

    book = Ledger(tmp_path / "ledger.jsonl")
    cfg = SimpleNamespace(max_cost_usd=kw.pop("per_instance", 1.0))
    return asyncio.run(
        sweep(insts, cfg=cfg, ledger=book, docker=object(), launcher=object(), run=runner, **kw)
    ), book


def test_every_instance_is_run_in_order(tmp_path: Path) -> None:
    runner = _Runner()
    result, book = _go(tmp_path, [_inst(1), _inst(2), _inst(3)], runner, max_total_usd=100.0)

    assert runner.ran == ["r__r-1", "r__r-2", "r__r-3"]
    assert result.stopped_by_cap is False and result.ran == 3 and result.skipped == 0
    assert [r.instance_id for r in book.rows()] == runner.ran


def test_the_sweep_stops_at_the_cap_and_says_what_is_left(tmp_path: Path) -> None:
    runner = _Runner(cost=4.0)
    result, _ = _go(tmp_path, [_inst(i) for i in range(1, 6)], runner, max_total_usd=10.0, per_instance=4.0)

    assert runner.ran == ["r__r-1", "r__r-2"]
    assert result.stopped_by_cap is True and result.spent_usd == 8.0
    assert result.not_run == ["r__r-3", "r__r-4", "r__r-5"]


def test_instances_already_in_the_ledger_are_not_run_again(tmp_path: Path) -> None:
    book = Ledger(tmp_path / "ledger.jsonl")
    book.append(LedgerRow("r__r-1", "psf/requests", "submitted", "done", spent_usd=3.0))
    runner = _Runner()

    result, _ = _go(tmp_path, [_inst(1), _inst(2)], runner, max_total_usd=100.0)

    assert runner.ran == ["r__r-2"] and result.skipped == 1
    assert result.spent_usd == 7.0  # the earlier spend still counts against the cap


def test_a_generator_error_row_is_retried_when_asked_but_a_parked_one_is_not(tmp_path: Path) -> None:
    book = Ledger(tmp_path / "ledger.jsonl")
    book.append(LedgerRow("r__r-1", "psf/requests", "generator-error", "docker", spent_usd=0.0))
    book.append(LedgerRow("r__r-2", "psf/requests", "parked", "UNLOCALIZED", spent_usd=0.1))
    runner = _Runner()

    _go(tmp_path, [_inst(1), _inst(2)], runner, max_total_usd=100.0, retry_generator_errors=True)

    assert runner.ran == ["r__r-1"]  # asked to retry infrastructure failures; a verdict is a result


def test_a_zero_or_negative_cap_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="cap"):
        _go(tmp_path, [_inst(1)], _Runner(), max_total_usd=0.0)


# --- review round: the cap is a projection, spend is cumulative, retries are opt-in ------------


def test_the_sweep_does_not_start_an_instance_that_could_take_it_past_the_cap(tmp_path: Path) -> None:
    from types import SimpleNamespace

    runner = _Runner(cost=4.0)
    book = Ledger(tmp_path / "ledger.jsonl")
    cfg = SimpleNamespace(max_cost_usd=4.0)

    result = asyncio.run(
        sweep(
            [_inst(i) for i in range(1, 6)],
            cfg=cfg,
            ledger=book,
            docker=object(),
            launcher=object(),
            run=runner,
            max_total_usd=10.0,
        )
    )

    assert runner.ran == ["r__r-1", "r__r-2"]  # 8 spent; a third could reach 12 > 10
    assert result.stopped_by_cap is True and result.spent_usd == 8.0
    assert result.not_run == ["r__r-3", "r__r-4", "r__r-5"]


def test_a_cap_smaller_than_one_instances_ceiling_runs_nothing(tmp_path: Path) -> None:
    from types import SimpleNamespace

    runner = _Runner()
    result = asyncio.run(
        sweep(
            [_inst(1)],
            cfg=SimpleNamespace(max_cost_usd=10.0),
            ledger=Ledger(tmp_path / "l.jsonl"),
            docker=object(),
            launcher=object(),
            run=runner,
            max_total_usd=5.0,
        )
    )
    assert runner.ran == [] and result.stopped_by_cap is True and result.not_run == ["r__r-1"]


def test_a_generator_error_is_not_retried_unless_asked(tmp_path: Path) -> None:
    """Retrying only the failures biases a result upward; it is opt-in, after the cause is fixed."""
    book = Ledger(tmp_path / "ledger.jsonl")
    book.append(LedgerRow("r__r-1", "psf/requests", "generator-error", "docker", spent_usd=1.0))

    default = _Runner()
    _go(tmp_path, [_inst(1)], default, max_total_usd=100.0)
    assert default.ran == []

    asked = _Runner()
    _go(tmp_path, [_inst(1)], asked, max_total_usd=100.0, retry_generator_errors=True)
    assert asked.ran == ["r__r-1"]


def test_earlier_spend_on_a_retried_instance_is_not_erased(tmp_path: Path) -> None:
    book = Ledger(tmp_path / "ledger.jsonl")
    book.append(LedgerRow("r__r-1", "psf/requests", "generator-error", "docker", spent_usd=3.0))

    class _Retry(_Runner):
        async def __call__(self, inst: Instance, *args: Any) -> LedgerRow:
            self.ran.append(inst.generation.instance_id)
            row = LedgerRow(
                inst.generation.instance_id, "psf/requests", "submitted", "r", spent_usd=3.5, attempts=2
            )
            args[1].append(row)
            return row

    result, _ = _go(tmp_path, [_inst(1)], _Retry(), max_total_usd=100.0, retry_generator_errors=True)

    assert result.spent_usd == 3.5  # the row is cumulative; it must not be added to the 3.0 again


def test_a_minimum_for_a_tiny_repository_is_met_for_every_seed() -> None:
    """With ten slots, flask (1 of 500) and seaborn (2 of 500) get a proportional share of zero, so
    unless the minimums are enforced they are mostly absent — this test can fail, the Django one could not."""
    repos = dict(_pool())
    for seed in range(25):
        counts = Counter(
            repos[i]
            for i in select(_pool(), 10, seed=seed, minimums={"pallets/flask": 1, "mwaskom/seaborn": 2})
        )
        assert counts["pallets/flask"] == 1 and counts["mwaskom/seaborn"] == 2, (seed, counts)
