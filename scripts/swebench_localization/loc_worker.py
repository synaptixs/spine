"""Run in Spine's own environment: one instance, one pre-fix tree, one JSON line on stdout.

Reads no gold field. Usage: ``python loc_worker.py <tree> <cache-dir> <query.json>``. It extracts the
tree's graph (cold: a fresh cache directory), runs ``build_investigation`` three ways (first line only = A;
first line plus the rest as one flat query = B; and with first-line ranking = V) and ranks the tree's
``.py`` files with BM25 for the baseline.
"""

from __future__ import annotations

import contextlib
import json
import os
import resource
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import loc_lib  # noqa: E402


def _peak_rss_mb() -> int:
    """Peak resident set of this process in MB (macOS reports bytes, Linux kilobytes)."""
    raw = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return round(raw / (1e6 if sys.platform == "darwin" else 1e3))


def _hybrid_arms(q: dict, store, tree: Path, files) -> dict:  # type: ignore[no-untyped-def]
    """Today's RCA and design files, and the hybrid arms for the grid cell in ``q["hybrid"]``
    (pre-registration §2).

    A Spine without ``pkg.hybrid`` yields only today's arms. ``q["hybrid"]`` holds ``recipe`` (``A``/``B``),
    ``name_weight`` and ``model`` (empty: no embedding model). Reads no gold field.
    """
    from orchestrator.sdlc.design import _landing_files
    from orchestrator.sdlc.localize import localize_trace

    text = q["first"] + "\n" + q["rest"]
    spec = {"title": q["first"], "description": q["rest"]}
    loc = localize_trace(text, store=store)
    fault = loc.fault.where.split(":", 1)[0] if loc.fault else ""
    out: dict = {
        "files_rca0": [fault] if fault else (loc.stated_files[:1]),
        "files_design0": _landing_files(spec, store, tree),
    }
    try:
        from orchestrator.pkg.hybrid import HybridConfig, HybridRetriever
        from orchestrator.pkg.symbol_text import Recipe
        from orchestrator.sdlc.investigate import build_investigation
        from orchestrator.sdlc.rca import _ranked_candidates
    except ImportError:
        return out
    cell = q.get("hybrid") or {}
    if cell.get("model"):
        raise SystemExit("embedding models arrive with the semantic extra (PR2)")
    config = HybridConfig(
        recipe=Recipe(cell.get("recipe", "B")), name_weight=float(cell.get("name_weight", 1.0))
    )
    hybrid = HybridRetriever(store, tree, config=config)
    out["files_h"] = files(
        build_investigation(q["first"], q["rest"], store=store, root=tree, rank_on_title=True, hybrid=hybrid)
    )
    out["files_rca1"] = [fault] if fault else _ranked_candidates(text, loc, store, tree, hybrid)[0]
    out["files_design1"] = _landing_files(spec, store, tree, hybrid)
    out["legs_used"] = list(hybrid.legs_used)
    return out


def main() -> int:
    tree, cache, qfile = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
    if not tree.is_dir():
        print(json.dumps({"fatal": "tree missing"}))
        return 2
    q = json.loads(qfile.read_text())

    from orchestrator.pkg.walk_rules import PKG_IGNORE_DIRS

    docs: dict[str, str] = {}
    for dp, dns, fns in os.walk(tree):
        dns[:] = [d for d in dns if d not in PKG_IGNORE_DIRS]
        for fn in fns:
            if fn.endswith(".py"):
                p = Path(dp) / fn
                with contextlib.suppress(OSError):
                    docs[str(p.relative_to(tree))] = p.read_text(encoding="utf-8", errors="replace")
    if not docs:
        print(json.dumps({"fatal": "no .py files in tree"}))
        return 2

    from orchestrator.pkg.persistence import load_or_extract
    from orchestrator.pkg.store import FactStore
    from orchestrator.sdlc.investigate import build_investigation

    t0 = time.time()
    graph_file = q.get("graph_file")
    if graph_file and Path(graph_file).is_file():
        # A graph saved by an earlier run of the same Spine extractor (P4 of the hybrid track): extraction
        # is the
        # expensive step and the extractor does not change between grid cells.
        from orchestrator.pkg.persistence import load_facts

        batch = load_facts(Path(graph_file))
    else:
        batch = load_or_extract(tree, cache_dir=cache)
        if q.get("save_graph"):
            from orchestrator.pkg.persistence import save_facts

            Path(q["save_graph"]).parent.mkdir(parents=True, exist_ok=True)
            save_facts(batch, Path(q["save_graph"]))
    if len(batch.nodes) == 0:
        print(json.dumps({"fatal": "zero nodes"}))
        return 2
    store = FactStore(batch)
    t1 = time.time()

    def files(inv) -> list[str]:
        out: list[str] = []
        for landing in inv.landing:
            path = landing.where.rsplit(":", 1)[0]
            if path not in out:
                out.append(path)
        return out

    a = files(build_investigation(q["first"], "", store=store, root=tree))
    # B is "current Spine": title and body as one flat query (the default call). V asks for the first-line
    # ranking (SSPN-192). A Spine without the argument raises TypeError and V is then B.
    b = files(build_investigation(q["first"], q["rest"], store=store, root=tree))
    try:
        v = files(build_investigation(q["first"], q["rest"], store=store, root=tree, rank_on_title=True))
    except TypeError:
        v = b
    arms = _hybrid_arms(q, store, tree, files)
    t2 = time.time()
    bm = loc_lib.bm25_rank(docs, q["first"] + "\n" + q["rest"])
    t3 = time.time()
    print(
        json.dumps(
            {
                "files_a": a,
                "files_b": b,
                "files_v": v,
                "files_bm25": bm,
                **arms,
                "n_py_files": len(docs),
                "nodes": len(batch.nodes),
                "extract_s": round(t1 - t0, 1),
                "investigate_s": round(t2 - t1, 1),
                "bm25_s": round(t3 - t2, 1),
                "peak_rss_mb": _peak_rss_mb(),
            }
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
