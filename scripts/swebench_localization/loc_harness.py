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
SPINE = REPO_ROOT


def load_rows() -> list[dict]:
    """The 500 rows at the pinned dataset revision (the Hugging Face cache is used when it is warm)."""
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
        q.write_text(json.dumps({"first": first, "rest": rest}))
        data: dict = {}
        for _attempt in (1, 2):
            try:
                data, _wall = run_worker(tree, cache, q)
            except subprocess.TimeoutExpired:
                data = {"fatal": f"worker timeout {WORKER_TIMEOUT}s"}
            if "fatal" not in data or data["fatal"] in _PERMANENT:
                break  # deterministic; a retry cannot change it
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
        returned_bm25=len(data["files_bm25"]),
    )
    if mode == "run":
        fa, fb, fm = data["files_a"], data["files_b"], data["files_bm25"]
        row.update(
            gold=gold,
            files_a=fa,
            files_b=fb,
            files_bm25=fm,
            rank_a=loc_lib.rank_of(fa, gold),
            rank_b=loc_lib.rank_of(fb, gold),
            rank_bm25=loc_lib.rank_of(fm, gold),
            all_gold_in_10_b=loc_lib.all_gold_within(fb, gold, 10),
            chance={str(k): loc_lib.chance_p(data["n_py_files"], len(gold), k) for k in KS},
        )
    return row


def harness_digest() -> str:
    h = hashlib.sha256()
    for n in ("loc_lib.py", "loc_worker.py", "loc_harness.py"):
        h.update((HERE / n).read_bytes())
    return h.hexdigest()


def main() -> None:
    global MIRRORS, SPINE
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mode", choices=["dry", "run"], required=True)
    ap.add_argument("--ids", help="comma-separated instance ids; omit with --all")
    ap.add_argument("--all", action="store_true")
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
    if not a.all and not a.ids:
        ap.error("give --ids or --all")
    SPINE, MIRRORS = a.spine.resolve(), a.mirrors.expanduser().resolve()

    head = sh(["git", "-C", str(SPINE), "rev-parse", "HEAD"]).stdout.strip()
    dirty = sh(["git", "-C", str(SPINE), "status", "--short"]).stdout.strip()
    if a.expect_sha and head != a.expect_sha:
        raise SystemExit(f"Spine checkout is at {head}, expected {a.expect_sha}")
    if dirty and not a.allow_dirty:
        raise SystemExit("the Spine checkout has local changes (use --allow-dirty to score it anyway)")

    rows = load_rows()
    if a.all:
        todo = rows
    else:
        by = {r["instance_id"]: r for r in rows}
        todo = [by[i] for i in a.ids.split(",")]
    total = len(todo)
    out = Path(a.out)
    done: dict[str, dict] = {}
    if out.exists():
        for line in out.read_text().splitlines():
            d = json.loads(line)
            if d.get("status") == "scored":
                done[d["instance_id"]] = d
    sab_id, _, sab_kind = (a.sabotage or "::").partition(":")
    header = {
        "header": True,
        "mode": a.mode,
        "spine_sha": head,
        "dataset_rev": DATASET_REV,
        "harness_sha256": harness_digest(),
        "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "total": total,
    }
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
