"""Choosing instances: a published seed, spread across repositories, with stated minimums.

The pilot (plan D5) is twenty instances drawn so that the slow, large suites are represented —
at least five Django and two Sympy — without the sample being all Django, which is 46% of Verified.
The result depends only on the *set* of (id, repo) pairs, the size, the seed and the minimums, never
on the order the dataset happens to arrive in, so the seed can be published and the draw repeated.
"""

from __future__ import annotations

import random
from collections import defaultdict
from collections.abc import Mapping, Sequence


def select(pool: Sequence[tuple[str, str]], n: int, *, seed: int, minimums: Mapping[str, int]) -> list[str]:
    """``n`` instance ids from ``pool`` (``(instance_id, repo)`` pairs), sorted."""
    by_repo: dict[str, list[str]] = defaultdict(list)
    for instance_id, repo in pool:
        by_repo[repo].append(instance_id)
    for ids in by_repo.values():
        ids.sort()
    total = sum(len(v) for v in by_repo.values())
    if n > total:
        raise ValueError(f"asked for {n} instances but the pool has only {total}")
    if sum(minimums.values()) > n:
        raise ValueError(f"the minimums ({sum(minimums.values())}) exceed the sample size ({n})")
    for repo, need in minimums.items():
        if need > len(by_repo.get(repo, [])):
            raise ValueError(
                f"minimum {need} for {repo}, which has only {len(by_repo.get(repo, []))} instances"
            )

    rng = random.Random(seed)  # noqa: S311 — a published, repeatable draw is the point; nothing here is secret
    chosen: set[str] = set()
    for repo in sorted(minimums):
        chosen |= set(rng.sample(by_repo[repo], minimums[repo]))

    # The rest in proportion to what is left in each repository (largest remainder, capped).
    left = {repo: [i for i in ids if i not in chosen] for repo, ids in sorted(by_repo.items())}
    remaining = n - len(chosen)
    room = {repo: len(ids) for repo, ids in left.items()}
    pool_left = sum(room.values())
    quota = {repo: remaining * room[repo] // pool_left for repo in left} if pool_left else {}
    leftover = remaining - sum(quota.values())
    by_fraction = sorted(left, key=lambda r: (-(remaining * room[r] % pool_left), r)) if pool_left else []
    while leftover > 0 and by_fraction:
        progressed = False
        for repo in by_fraction:
            if leftover and quota[repo] < room[repo]:
                quota[repo] += 1
                leftover -= 1
                progressed = True
        if not progressed:
            break
    for repo, count in quota.items():
        if count:
            chosen |= set(rng.sample(left[repo], count))
    return sorted(chosen)
