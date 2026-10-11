#!/usr/bin/env python3
"""Export SWE-bench Verified rows for the harness, with image digests pinned.

Run this in the *bench* venv (``pip install 'swebench==5.0.2' datasets``), not in Spine's: those
packages are deliberately not Spine dependencies. It writes two files:

* ``instances.jsonl`` — the rows the harness may read: id, repo, version, base commit, the issue text,
  the image and its digest, and the **names** of the files the test patch touches. Nothing else from
  the dataset survives (see ``orchestrator.evals.swebench.instances``); the harness refuses a file
  that carries a gold field.
* ``pins.json`` — what makes the run repeatable: the dataset and its revision, the ``swebench``
  version, the selection and its seed.

Selection: ``--ids a,b,c``, or ``--sample N --seed S`` (stratified, with minimums), or ``--all``.
Image digests come from the registry (``docker buildx imagetools inspect``), so nothing is pulled.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType

REPO = Path(__file__).resolve().parent.parent
PKG = REPO / "src" / "orchestrator" / "evals" / "swebench"
DATASET = "princeton-nlp/SWE-bench_Verified"


def _load(name: str) -> ModuleType:
    """Load a harness module by path. ``instances`` and ``selection`` are stdlib-only, so this works
    in the bench venv, which cannot import the ``orchestrator`` package."""
    spec = importlib.util.spec_from_file_location(f"swebench_{name}", PKG / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def digest_of(image: str) -> str:
    done = subprocess.run(
        ["docker", "buildx", "imagetools", "inspect", image, "--format", "{{.Manifest.Digest}}"],
        capture_output=True,
        text=True,
    )
    out = done.stdout.strip()
    if done.returncode or not out.startswith("sha256:"):
        raise SystemExit(f"cannot read the digest of {image}: {(done.stderr or done.stdout).strip()[-200:]}")
    return out


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument(
        "--revision", help="Hugging Face dataset revision (default: the current head, recorded)"
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--ids", help="comma-separated instance ids")
    group.add_argument("--sample", type=int, help="stratified sample size")
    group.add_argument("--all", action="store_true")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--min-django", type=int, default=5)
    parser.add_argument("--min-sympy", type=int, default=2)
    args = parser.parse_args()

    from datasets import load_dataset  # bench venv only

    instances, selection = _load("instances"), _load("selection")
    ds = load_dataset(DATASET, split="test", revision=args.revision)
    revision = args.revision or "head (not resolved to a commit)"
    if not args.revision:
        try:
            from huggingface_hub import HfApi

            revision = HfApi().dataset_info(DATASET).sha or revision
        except Exception as exc:  # noqa: BLE001 — recorded in pins.json, which says what was and was not resolved
            print(f"warning: could not resolve the dataset revision to a commit: {exc}", file=sys.stderr)
    rows = {r["instance_id"]: r for r in ds}

    if args.all:
        chosen = sorted(rows)
    elif args.ids:
        chosen = [i.strip() for i in args.ids.split(",") if i.strip()]
        unknown = [i for i in chosen if i not in rows]
        if unknown:
            raise SystemExit(f"not in the dataset: {', '.join(unknown)}")
    else:
        pool = [(i, r["repo"]) for i, r in rows.items()]
        chosen = selection.select(
            pool,
            args.sample,
            seed=args.seed,
            minimums={"django/django": args.min_django, "sympy/sympy": args.min_sympy},
        )

    args.out.mkdir(parents=True, exist_ok=True)
    with (args.out / "instances.jsonl").open("w", encoding="utf-8") as handle:
        for iid in chosen:
            row = rows[iid]
            digest = digest_of(instances.image_name(iid))
            handle.write(
                json.dumps(instances.record_from_row(row, image_digest=digest), sort_keys=True) + "\n"
            )
    minimums = {"django/django": args.min_django, "sympy/sympy": args.min_sympy}
    selection_pin: dict[str, object] = (
        {"ids": chosen}
        if (args.ids or args.all)
        else {"sample": args.sample, "seed": args.seed, "minimums": minimums}
    )
    pins = {
        "dataset": DATASET,
        "dataset_revision": revision,
        "swebench_version": importlib.metadata.version("swebench"),
        "selection": selection_pin,
        "count": len(chosen),
    }
    (args.out / "pins.json").write_text(json.dumps(pins, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {len(chosen)} instances to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
