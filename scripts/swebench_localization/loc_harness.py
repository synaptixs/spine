"""SWE-bench Verified localization harness: the driver. Run it in the *bench* environment.

Question scored: *given an issue, is a file the fixing patch changed among the first k files that
Spine's ``investigate`` names?* No model call, no Docker, no grader: the answer key is the file list of
the fixing patch. Each instance is checked out at its pre-fix commit from a local bare mirror, scored by
``loc_worker.py`` in Spine's own environment, and compared with chance and a BM25 text-search baseline.

The bench environment needs ``datasets`` and ``pyarrow`` (the same one ``swebench_prep.py`` uses); Spine's
environment (``uv sync`` with the language extras) is only used by the worker::

    scripts/swebench_localization/clone_mirrors.sh ~/.cache/spine-swebench/mirrors
    <bench-python> scripts/swebench_localization/check_commits.py
    <bench-python> scripts/swebench_localization/loc_harness.py --mode run --all --out run.jsonl
    <bench-python> scripts/swebench_localization/analyze.py run.jsonl

Gold (the fixing patch) is read here only after the worker has returned and never reaches Spine.
``--mode dry`` exercises the whole path but writes no rank and no file list (counts and status only);
``--mode run`` writes everything. Instances that cannot be scored are ``skipped`` with a reason and are
never counted as misses; the summary refuses to print unless ``scored + skipped = total``.

The header records the worker's environment (``loc_worker.py --probe``: Spine version, installed
front-ends, embedding model, hybrid switches) and its digest; the run refuses to start without it. An
existing ``--out`` is resumed only when every field in :data:`RESUME_FIELDS` matches its header; otherwise
the run refuses and names them.
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import loc_lib  # noqa: E402

DATASET = "princeton-nlp/SWE-bench_Verified"
DATASET_REV = "c104f840cc67f8b6eec6f759ebc8b2693d585d4a"
REPO_ROOT = HERE.parents[1]
DEFAULT_MIRRORS = Path(os.environ.get("SWEBENCH_MIRRORS", "~/.cache/spine-swebench/mirrors")).expanduser()
KS = (1, 3, 5, 10)
WORKER_TIMEOUT = 900
_PERMANENT = ("tree missing", "no .py files in tree", "zero nodes")

MIRRORS = DEFAULT_MIRRORS
#: The hybrid grid cell scored as ``rank_h`` (pre-registration §4), and where graphs are saved and re-read.
HYBRID_CELL: dict = {}
#: instance id -> the ``title`` and ``problem`` autorun's research was handed (the spec intake wrote). The
#: research arm searches with it instead of the ticket text; an instance without one uses the ticket.
RESEARCH_SPECS: dict = {}
GRAPHS: Path | None = None
SPINE = REPO_ROOT


def load_rows(dataset_file: Path | None = None) -> list[dict]:
    """The 500 Verified rows at the pinned revision (the Hugging Face cache is used when it is warm), or the
    rows of a local parquet file of the same shape (``--dataset-file``, used for the fresh-issue arm)."""
    if dataset_file is not None:
        import pyarrow.parquet as pq

        return pq.read_table(dataset_file).to_pylist()
    from datasets import load_dataset

    return [dict(r) for r in load_dataset(DATASET, revision=DATASET_REV, split="test")]


def sh(args: list[str], env: dict[str, str] | None = None, cwd: Path | None = None):
    return subprocess.run(args, capture_output=True, text=True, env=env, cwd=cwd, check=False)


def build_tree(repo: str, base: str, dest: Path, tmp: Path) -> tuple[bool, str]:
    """Pre-fix tree by read-tree + checkout-index (not archive). Returns (ok, reason)."""
    mirror = MIRRORS / (repo.replace("/", "__") + ".git")
    if not mirror.is_dir():
        return False, "mirror missing"
    env = {**os.environ, "GIT_DIR": str(mirror), "GIT_INDEX_FILE": str(tmp / "index")}
    if sh(["git", "cat-file", "-e", f"{base}^{{commit}}"], env=env).returncode != 0:
        return False, "commit not in mirror"
    if sh(["git", "read-tree", base], env=env).returncode != 0:
        return False, "read-tree failed"
    dest.mkdir(parents=True)
    env["GIT_WORK_TREE"] = str(dest)  # a bare mirror has none; checkout-index refuses without one
    done = sh(["git", "checkout-index", "-a", "-f"], env=env)
    if done.returncode != 0:
        return False, "checkout-index failed: " + done.stderr[-120:]
    if not any(dest.iterdir()):
        return False, "empty checkout"
    return True, ""


def run_worker(tree: Path, cache: Path, qfile: Path) -> tuple[dict, float]:
    t0 = time.time()
    p = subprocess.run(
        [str(SPINE / ".venv/bin/python"), str(HERE / "loc_worker.py"), str(tree), str(cache), str(qfile)],
        capture_output=True,
        text=True,
        cwd=SPINE,
        timeout=WORKER_TIMEOUT,
        check=False,
    )
    wall = time.time() - t0
    line = p.stdout.strip().splitlines()[-1] if p.stdout.strip() else ""
    try:
        data = json.loads(line)
    except ValueError:
        data = {"fatal": "worker output unreadable: " + (p.stdout + p.stderr)[-200:]}
    return data, round(wall, 1)


def worker_once(tree: Path, cache: Path, qfile: Path) -> dict:
    """One worker attempt; a timeout is reported as a fatal result, like any other failure."""
    try:
        data, _wall = run_worker(tree, cache, qfile)
    except subprocess.TimeoutExpired:
        return {"fatal": f"worker timeout {WORKER_TIMEOUT}s"}
    return data


def score_one(r: dict, mode: str, sabotage: str | None = None) -> dict:
    iid, repo, base, text = r["instance_id"], r["repo"], r["base_commit"], r["problem_statement"]
    gold = loc_lib.gold_paths(r["patch"])
    first, _, rest = text.strip().partition("\n")
    row: dict = {
        "instance_id": iid,
        "repo": repo,
        "n_gold": len(gold),
        "quotes_gold": loc_lib.quotes_gold(text, gold),
    }
    if sabotage == "bad-commit":
        base = "0" * 40
    t0 = time.time()
    with tempfile.TemporaryDirectory(prefix="loc-") as td_name:
        td = Path(td_name)
        tree, cache, q = td / "tree", td / "cache", td / "q.json"
        ok, why = build_tree(repo, base, tree, td)
        if not ok:
            return {**row, "status": "skipped", "reason": why}
        if sabotage == "delete-tree":
            import shutil

            shutil.rmtree(tree)
        query: dict = {"first": first, "rest": rest, "hybrid": HYBRID_CELL}
        if iid in RESEARCH_SPECS:
            spec = RESEARCH_SPECS[iid]
            query["research"] = {"title": spec.get("title", ""), "problem": spec.get("problem", "")}
        if GRAPHS is not None:
            saved = GRAPHS / f"{iid}.json"
            query["graph_file" if saved.is_file() else "save_graph"] = str(saved)
        q.write_text(json.dumps(query))
        data = worker_once(tree, cache, q)
        if "fatal" in data and data["fatal"] not in _PERMANENT:
            data = worker_once(tree, cache, q)  # one retry; a permanent failure cannot change
        if "fatal" in data:
            return {**row, "status": "skipped", "reason": data["fatal"], "wall_s": round(time.time() - t0, 1)}
    row.update(
        status="scored",
        wall_s=round(time.time() - t0, 1),
        peak_rss_mb=data.get("peak_rss_mb"),
        n_py_files=data["n_py_files"],
        nodes=data["nodes"],
        extract_s=data["extract_s"],
        investigate_s=data["investigate_s"],
        bm25_s=data["bm25_s"],
        returned_a=len(data["files_a"]),
        returned_b=len(data["files_b"]),
        returned_v=len(data.get("files_v", data["files_b"])),
        returned_bm25=len(data["files_bm25"]),
    )
    if mode == "run":
        fa, fb, fm = data["files_a"], data["files_b"], data["files_bm25"]
        fv = data.get("files_v", fb)
        row.update(
            gold=gold,
            files_a=fa,
            files_b=fb,
            files_bm25=fm,
            files_v=fv,
            rank_a=loc_lib.rank_of(fa, gold),
            rank_b=loc_lib.rank_of(fb, gold),
            rank_bm25=loc_lib.rank_of(fm, gold),
            rank_v=loc_lib.rank_of(fv, gold),
            **{
                f"{key}{arm}": value
                for arm in ("h", "rca0", "rca1", "design0", "design1", "research")
                if f"files_{arm}" in data
                for key, value in (
                    ("files_", data[f"files_{arm}"]),
                    ("rank_", loc_lib.rank_of(data[f"files_{arm}"], gold)),
                )
            },
            **({"research_retrieval": data["research_retrieval"]} if "research_retrieval" in data else {}),
            has_research_spec=iid in RESEARCH_SPECS,
            all_gold_in_10_b=loc_lib.all_gold_within(fb, gold, 10),
            chance={str(k): loc_lib.chance_p(data["n_py_files"], len(gold), k) for k in KS},
        )
    # What the worker says about itself, in either mode: a research arm that raised (research is then not
    # scored for this instance, never a miss), research's own time, and the digest of its environment.
    row.update({key: data[key] for key in ("research_error", "research_s", "env_sha256") if key in data})
    return row


def harness_digest() -> str:
    h = hashlib.sha256()
    for n in ("loc_lib.py", "loc_worker.py", "loc_harness.py"):
        h.update((HERE / n).read_bytes())
    return h.hexdigest()


def probe_environment() -> dict:
    """The worker's environment (``loc_worker.py --probe``), read in Spine's own virtualenv before any row.

    Refuses to start when the probe fails or prints no JSON: a results file is evidence only with its
    header, and a header that cannot say which front-ends, model and switches scored the rows says nothing.
    """
    done = sh([str(SPINE / ".venv/bin/python"), str(HERE / "loc_worker.py"), "--probe"], cwd=SPINE)
    try:
        if done.returncode != 0:
            raise ValueError
        probe = json.loads(done.stdout)
        if not isinstance(probe, dict):
            raise ValueError
    except ValueError:
        raise SystemExit(
            f"the environment probe failed (exit {done.returncode}): {(done.stdout + done.stderr)[-400:]}"
        ) from None
    return probe


#: The header fields that decide what a row holds. A resume keeps the old file's scored rows only when every
#: one of them matches; ``started_utc``, ``total`` and the probe itself (``env_sha256`` digests it) do not.
RESUME_FIELDS = (
    "mode",
    "spine_sha",
    "hybrid_cell",
    "dataset_rev",
    "dataset_file_sha256",
    "ids_file_sha256",
    "harness_sha256",
    "env_sha256",
    "research_specs_sha256",
)


def resume_conflicts(old: dict, new: dict) -> list[str]:
    """The row-deciding fields on which an existing results file's header differs from this run's.

    A field the old header lacks is a mismatch: a file written before the field existed cannot show it
    matches.
    """
    missing = object()
    return [f for f in RESUME_FIELDS if old.get(f, missing) != new.get(f)]


def main() -> None:
    global MIRRORS, SPINE, HYBRID_CELL, GRAPHS, RESEARCH_SPECS
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mode", choices=["dry", "run"], required=True)
    ap.add_argument(
        "--hybrid-cell",
        default="recipe=B,name_weight=1",
        help="grid cell scored as rank_h: recipe=A|B,name_weight=1|2[,model=<pinned id>] (pre-reg §4)",
    )
    ap.add_argument(
        "--graphs", type=Path, help="save each extracted graph here, and re-read it when it is there"
    )
    ap.add_argument(
        "--research-specs",
        type=Path,
        help="JSON of instance id -> {title, problem}: what research was handed; scored as rank_research",
    )
    ap.add_argument("--ids", help="comma-separated instance ids; omit with --all")
    ap.add_argument("--all", action="store_true")
    ap.add_argument(
        "--dataset-file", type=Path, help="score the rows of this local parquet file instead of Verified"
    )
    ap.add_argument("--ids-file", type=Path, help='JSON file with an "ids" list; score only those instances')
    ap.add_argument("--out", required=True)
    ap.add_argument("--jobs", type=int, default=1)
    ap.add_argument(
        "--spine", type=Path, default=REPO_ROOT, help="Spine checkout to score (default: this one)"
    )
    ap.add_argument("--mirrors", type=Path, default=DEFAULT_MIRRORS, help="directory of bare mirrors")
    ap.add_argument("--expect-sha", help="refuse to run unless the Spine checkout is at this commit")
    ap.add_argument("--allow-dirty", action="store_true", help="allow local changes in the Spine checkout")
    ap.add_argument("--sabotage", help="id:delete-tree|bad-commit (self-test of the refusal rules)")
    a = ap.parse_args()
    HYBRID_CELL = dict(kv.split("=", 1) for kv in a.hybrid_cell.split(",") if kv)
    RESEARCH_SPECS = json.loads(a.research_specs.read_text()) if a.research_specs else {}
    GRAPHS = a.graphs.expanduser().resolve() if a.graphs else None
    if not (a.all or a.ids or a.ids_file):
        ap.error("give --ids, --ids-file or --all")
    SPINE, MIRRORS = a.spine.resolve(), a.mirrors.expanduser().resolve()

    head = sh(["git", "-C", str(SPINE), "rev-parse", "HEAD"]).stdout.strip()
    dirty = sh(["git", "-C", str(SPINE), "status", "--short"]).stdout.strip()
    if a.expect_sha and head != a.expect_sha:
        raise SystemExit(f"Spine checkout is at {head}, expected {a.expect_sha}")
    if dirty and not a.allow_dirty:
        raise SystemExit("the Spine checkout has local changes (use --allow-dirty to score it anyway)")
    environment = probe_environment()

    rows = load_rows(a.dataset_file)
    by = {r["instance_id"]: r for r in rows}
    if a.ids_file:
        todo = [by[i] for i in json.loads(a.ids_file.read_text())["ids"]]
    elif a.all:
        todo = rows
    else:
        todo = [by[i] for i in a.ids.split(",")]
    total = len(todo)
    out = Path(a.out)
    sab_id, _, sab_kind = (a.sabotage or "::").partition(":")
    header = {
        "header": True,
        "mode": a.mode,
        "spine_sha": head,
        "hybrid_cell": HYBRID_CELL,
        "dataset_rev": DATASET_REV,
        "dataset_file_sha256": hashlib.sha256(a.dataset_file.read_bytes()).hexdigest()
        if a.dataset_file
        else None,
        "ids_file_sha256": hashlib.sha256(a.ids_file.read_bytes()).hexdigest() if a.ids_file else None,
        "harness_sha256": harness_digest(),
        "environment": environment,
        "env_sha256": loc_lib.env_digest(environment),
        "research_specs_sha256": hashlib.sha256(a.research_specs.read_bytes()).hexdigest()
        if a.research_specs
        else None,
        "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "total": total,
    }
    done: dict[str, dict] = {}
    if out.exists():
        # A resume: keep the old scored rows only if they were scored by this same run (SSPN-434). Checked
        # before the file is opened for writing, so a refusal leaves it as it was.
        old = [json.loads(line) for line in out.read_text().splitlines()]
        if not old or not old[0].get("header"):
            raise SystemExit(f"refusing to resume {out}: its first line is no header (use a new --out)")
        old_header = old[0]
        done = {d["instance_id"]: d for d in old[1:] if d.get("status") == "scored"}
        conflicts = resume_conflicts(old_header, header)
        if conflicts:
            raise SystemExit(
                f"refusing to resume {out}: {', '.join(conflicts)} differ from its header (use a new --out)"
            )
    lock = threading.Lock()
    results: list[dict] = list(done.values())
    with out.open("w") as fh:
        fh.write(json.dumps(header) + "\n")
        for d in done.values():
            fh.write(json.dumps(d) + "\n")
        t_start = time.time()
        pending = [r for r in todo if r["instance_id"] not in done]

        def work(r: dict) -> dict:
            return score_one(r, a.mode, sabotage=sab_kind if r["instance_id"] == sab_id else None)

        n_done = 0
        with cf.ThreadPoolExecutor(max_workers=a.jobs) as ex:
            futs = {ex.submit(work, r): r for r in pending}
            for fut in cf.as_completed(futs):
                row = fut.result()
                with lock:
                    results.append(row)
                    fh.write(json.dumps(row) + "\n")
                    fh.flush()
                    n_done += 1
                    print(
                        f"[{n_done}/{len(pending)}] {row['instance_id']} {row['status']} "
                        f"{row.get('reason', '')} {row.get('wall_s', '')}s",
                        flush=True,
                    )
                    if n_done == 50 and total >= 100:
                        skipped = sum(1 for x in results if x["status"] == "skipped")
                        proj_h = (time.time() - t_start) / n_done * (len(pending) - n_done) / 3600
                        if skipped / n_done > 0.05 or proj_h > 12:
                            print(
                                f"PAUSE (pre-registered stop rule): skipped {skipped}/{n_done}, "
                                f"projected {proj_h:.1f} h remaining",
                                flush=True,
                            )
                            for f in futs:
                                f.cancel()
                            break
    scored, skipped = loc_lib.accounting(results, total)
    print(f"ACCOUNTING OK: scored={scored} skipped={skipped} total={total}")
    for x in results:
        if x["status"] == "skipped":
            print("  SKIPPED", x["instance_id"], "-", x["reason"])


if __name__ == "__main__":
    main()
