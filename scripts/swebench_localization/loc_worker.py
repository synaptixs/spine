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
    batch = load_or_extract(tree, cache_dir=cache)
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
    # B is "current Spine": title and body as one flat query. V is the first-line ranking (SSPN-192).
    # The switch is read at call time; a Spine without the switch ignores it and V equals B.
    os.environ["INVESTIGATE_TITLE_RANK"] = "0"
    b = files(build_investigation(q["first"], q["rest"], store=store, root=tree))
    os.environ["INVESTIGATE_TITLE_RANK"] = "1"
    v = files(build_investigation(q["first"], q["rest"], store=store, root=tree))
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
