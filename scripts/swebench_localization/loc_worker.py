"""Run in Spine's own environment: one instance, one pre-fix tree, one JSON line on stdout.

Reads no gold field. Usage: ``python loc_worker.py <tree> <cache-dir> <query.json>``. It extracts the
tree's graph (cold: a fresh cache directory), runs ``build_investigation`` three ways (first line only = A;
first line plus the rest as one flat query = B; and with first-line ranking = V) and ranks the tree's
``.py`` files with BM25 for the baseline. ``python loc_worker.py --probe`` prints the environment JSON
(:func:`environment_probe`) and exits.
"""

from __future__ import annotations

import asyncio
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
    ``name_weight`` and ``model`` (a ``pkg.embed.MODELS`` alias; empty: no embedding model, three legs).
    Reads no gold field.
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
    embedder = None
    if cell.get("model"):
        # A pinned alias from `pkg.embed.MODELS`. A cell that names a model it cannot load stops the run:
        # scoring it on three legs would record a number for a cell that never ran.
        from orchestrator.pkg.embed import ModelError, embedder_for

        try:
            embedder = embedder_for(cell["model"])
        except ModelError as exc:
            raise SystemExit(str(exc)) from exc
    config = HybridConfig(
        recipe=Recipe(cell.get("recipe", "B")), name_weight=float(cell.get("name_weight", 1.0))
    )
    hybrid = HybridRetriever(store, tree, config=config, embedder=embedder)
    out["files_h"] = files(
        build_investigation(q["first"], q["rest"], store=store, root=tree, rank_on_title=True, hybrid=hybrid)
    )
    out["files_rca1"] = [fault] if fault else _ranked_candidates(text, loc, store, tree, hybrid)[0]
    out["files_design1"] = _landing_files(spec, store, tree, hybrid)
    out["legs_used"] = list(hybrid.legs_used)
    out["model"] = hybrid.model
    return out


def _research_arm(q: dict, store, tree: Path) -> dict:  # type: ignore[no-untyped-def]
    """The landing files autorun's research hands on, through the research path itself (SSPN-430).

    Every other arm calls ``build_investigation`` directly, and the hybrid arms hand it a retriever. None of
    them runs the tool autorun runs, so none can show whether research ranks with hybrid search: they read
    the same before and after that wiring. This arm calls ``build_evidence``, which runs ``sdlc.investigate``
    as the run does, at the shipped default, so the ordinary switches decide the ranking
    (``SPINE_HYBRID_AUTORUN=0`` for lexical, ``SPINE_EMBED_MODEL=none`` for three legs).

    ``files_research`` is ``Evidence.files``: the landing set the run adopts, weak rows dropped, in rank
    order. ``q["research"]`` (a ``title`` and a ``problem``, e.g. the spec intake wrote) replaces the ticket
    text when given, because research is handed the spec and not the ticket. A Spine without
    ``sdlc.evidence`` yields nothing.

    Any exception research raises is returned as ``research_error`` (``"<Type>: <message>"``) instead of
    ending the worker (SSPN-434): the instance's other arms are still scored, and research is counted as not
    scored for it, never as a miss.
    """
    try:
        from orchestrator.sdlc.evidence import build_evidence
    except ImportError:
        return {}
    text = q.get("research") or {}
    title, problem = text.get("title") or q["first"], text.get("problem") or q["rest"]
    try:
        evidence = asyncio.run(build_evidence(title, problem, store=store, root=tree))
    except Exception as exc:  # noqa: BLE001 - any failure is recorded on the row, see the docstring
        return {"research_error": f"{type(exc).__name__}: {exc}"}
    return {"files_research": list(evidence.files), "research_retrieval": getattr(evidence, "retrieval", "")}


#: The variables that decide how the worker ranks: the hybrid switches, the embedding model and where its
#: files are. Recorded in the probe when set.
_ENV_PREFIX = "SPINE_HYBRID_"
_ENV_NAMES = ("SPINE_EMBED_MODEL", "SPINE_MODEL_DIR")


def environment_probe() -> dict:
    """What decides this worker's rankings besides the code: printed by ``--probe``, digested on every line.

    ``spine_version``; ``front_ends``, the grammar modules the extractor can use (the same list the graph
    cache keys on, found with ``find_spec``, nothing imported), sorted; ``embed_model``, the model the meaning
    leg would use (its pinned id, ``none``, or ``error: …``), resolved without reading its files; ``env``, the
    hybrid switches, ``SPINE_EMBED_MODEL`` and ``SPINE_MODEL_DIR`` where set. No timestamp and no other path,
    so two probes of one environment are identical.
    """
    import importlib.util

    from orchestrator import __version__
    from orchestrator.pkg import embed
    from orchestrator.pkg.persistence import _GRAMMAR_MODULES

    try:
        model = embed.default_embedder()
        embed_model = model.model_id if model is not None else embed.NO_MODEL
    except embed.ModelError as exc:
        embed_model = f"error: {exc}"
    env = {k: v for k, v in os.environ.items() if k.startswith(_ENV_PREFIX) or k in _ENV_NAMES}
    return {
        "spine_version": __version__,
        "front_ends": sorted(m for m in _GRAMMAR_MODULES if importlib.util.find_spec(m) is not None),
        "embed_model": embed_model,
        "env": env,
    }


def main() -> int:
    if sys.argv[1:] == ["--probe"]:
        print(json.dumps(environment_probe(), sort_keys=True))
        return 0
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
    # Research runs its own search, RCA, blast radius and churn, so it is timed apart (SSPN-434).
    research = _research_arm(q, store, tree)
    t3 = time.time()
    bm = loc_lib.bm25_rank(docs, q["first"] + "\n" + q["rest"])
    t4 = time.time()
    print(
        json.dumps(
            {
                "files_a": a,
                "files_b": b,
                "files_v": v,
                "files_bm25": bm,
                **arms,
                **research,
                "n_py_files": len(docs),
                "nodes": len(batch.nodes),
                "extract_s": round(t1 - t0, 1),
                "investigate_s": round(t2 - t1, 1),
                "research_s": round(t3 - t2, 1),
                "bm25_s": round(t4 - t3, 1),
                "peak_rss_mb": _peak_rss_mb(),
                "env_sha256": loc_lib.env_digest(environment_probe()),
            }
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
