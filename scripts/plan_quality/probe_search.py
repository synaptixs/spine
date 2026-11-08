"""RCA's own hybrid search, read past the ten files ``orchestrator rca`` prints.

    PYTHONPATH=<tool>/src python scripts/plan_quality/probe_search.py <ticket.md> <out.json>

Run with the *tool's* code on ``PYTHONPATH`` and the *target* checkout as the working directory. It
reproduces ``rca._ranked_candidates`` — the same problem text (the CLI's ``_load_bug_text``), the same
seeds (``localize_trace(...).stated_files``), the same ``HybridRetriever`` — but asks for 2,000 hits
instead of RCA's default 50, and records which files RCA's own 50 hits contain. ``grade.py`` uses a
rank only after checking that this probe reproduces the list RCA printed.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

RCA_HIT_LIMIT = 50


def distinct_files(rows: list[dict[str, Any]], limit: int | None = None) -> list[str]:
    """Files in first-seen order, over the first ``limit`` hits (all of them when ``None``)."""
    out: list[str] = []
    for r in rows if limit is None else rows[:limit]:
        f = r.get("file")
        if f and f not in out:
            out.append(f)
    return out


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__, file=sys.stderr)
        return 2
    from orchestrator.cli.change import _load_bug_text
    from orchestrator.pkg.embed import default_embedder
    from orchestrator.pkg.hybrid import HybridRetriever
    from orchestrator.pkg.persistence import load_or_extract
    from orchestrator.pkg.store import FactStore
    from orchestrator.sdlc.localize import localize_trace

    ticket, out = Path(argv[1]).resolve(), Path(argv[2])
    root = Path(".").resolve()
    problem = _load_bug_text(f"file://{ticket}", None, "")
    store = FactStore(load_or_extract(root))
    stated = set(localize_trace(problem, store=store).stated_files)
    seeds = sorted(
        n.id for n in store.nodes if n.grounded and n.provenance is not None and n.provenance.file in stated
    )
    hybrid = HybridRetriever(store, root, embedder=default_embedder())
    hits = hybrid.search(problem, limit=2000, extra_seeds=seeds)
    rows = [
        {
            "rank": i,
            "id": h.node.id,
            "name": h.node.name,
            "file": h.node.provenance.file if h.node.provenance else "",
            "legs": h.legs,
            "found_by": h.found_by,
        }
        for i, h in enumerate(hits, 1)
    ]
    report = {
        "stated": sorted(stated),
        "seeds": len(seeds),
        "hits": len(rows),
        "files": distinct_files(rows),
        "files_within_rca_limit_50": distinct_files(rows, RCA_HIT_LIMIT),
        "rows": rows,
        "model": hybrid.model,
    }
    out.write_text(json.dumps(report, indent=1))
    print(
        f"stated={len(stated)} seeds={len(seeds)} hits={len(rows)} "
        f"files={len(report['files'])} model={hybrid.model}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
