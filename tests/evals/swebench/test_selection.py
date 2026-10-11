"""The sampler: a published seed, stratified across the dataset's repositories, with minimums.

Pilot rules (plan D5): at least five Django and two Sympy. The same seed gives the same sample
whatever order the pool arrives in.
"""

from __future__ import annotations

from collections import Counter

import pytest

from orchestrator.evals.swebench.selection import select

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
