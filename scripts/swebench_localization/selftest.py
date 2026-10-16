"""Self-test of the pure helpers (no Spine, no dataset): ``python selftest.py``."""

from __future__ import annotations

import itertools
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import loc_lib as lib  # noqa: E402


def check(cond: bool, what: str) -> None:
    if not cond:
        raise SystemExit(f"selftest FAILED: {what}")


def main() -> None:
    want = ["parse", "http", "response", "v2", "query", "set", "get", "or", "create"]
    check(
        lib.tokenize("parseHTTPResponse_v2 QuerySet.get_or_create") == want, "tokenize camelCase/underscore"
    )
    check(lib.tokenize("") == [], "tokenize empty")
    docs = {
        "a/x.py": "def alpha(): pass",
        "b/y.py": "def zebra_stripe(): zebra()",
        "c/z.py": "def alpha(): zebra()",
    }
    r = lib.bm25_rank(docs, "zebra stripe")
    check(r[0] == "b/y.py" and "a/x.py" not in r, "bm25 ranks the rare-term file first, drops zero scores")
    check(lib.bm25_rank(docs, "nothing_matches_here") == [], "bm25 empty on no match")
    check(lib.bm25_rank({"p.py": "x", "a.py": "x"}, "x")[:2] == ["a.py", "p.py"], "bm25 ties break on path")
    for n, g, k in [(6, 1, 3), (6, 2, 3), (7, 3, 2), (5, 5, 2), (4, 1, 9)]:
        kk = min(k, n)
        combos = list(itertools.combinations(range(n), kk))
        gold = set(range(g))
        exact = sum(1 for c in combos if gold & set(c)) / len(combos)
        check(abs(lib.chance_p(n, g, k) - exact) < 1e-12, f"chance_p({n},{g},{k}) against brute force")
    check(lib.chance_p(0, 1, 3) == 0.0 and lib.chance_p(10, 0, 3) == 0.0, "chance_p degenerate")
    check(
        lib.rank_of(["a", "a", "b", "g"], {"g"}) == 3 and lib.rank_of(["a"], {"g"}) is None,
        "rank is by distinct files",
    )
    check(lib.all_gold_within(["a", "b", "g1", "g2"], ["g1", "g2"], 4), "all gold within 4")
    check(not lib.all_gold_within(["a", "b", "g1", "g2"], ["g1", "g2"], 3), "not all gold within 3")
    patch = (
        "diff --git a/django/db/models/query.py b/django/db/models/query.py\n--- a/x\n+++ b/x\n"
        "diff --git a/old.py b/new.py\n"
    )
    check(lib.gold_paths(patch) == ["django/db/models/query.py", "old.py"], "gold paths are the a/ side")
    fix = ["django/db/models/query.py"]
    check(lib.quotes_gold("see django/db/models/query.py", fix), "quotes full path")
    check(lib.quotes_gold("in query.py line 5", fix), "quotes basename")
    check(not lib.quotes_gold("see Query.PY", fix), "quote rule is case-sensitive")
    ok = [{"instance_id": "a", "status": "scored"}, {"instance_id": "b", "status": "skipped"}]
    check(lib.accounting(ok, 2) == (1, 1), "accounting accepts a complete set")
    for bad, total in [(ok[:1], 2), (ok + [ok[0]], 3), (ok, 3), ([{"instance_id": "a"}, ok[1]], 2)]:
        try:
            lib.accounting(bad, total)
        except AssertionError:
            continue
        raise SystemExit("selftest FAILED: accounting did not refuse a bad set")
    print("selftest: all checks passed")


if __name__ == "__main__":
    main()
