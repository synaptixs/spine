"""Sum per-ticket ``grade.json`` files into one table, micro-averaged (hits and sizes added across tickets).

    python scripts/plan_quality/aggregate.py <set_dir> <split> <out.md>

``set_dir`` holds ``set.json`` (``select_set.py``) and one directory per ticket with its ``grade.json``.
``split`` is ``dev`` or ``held-out``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


def stage_totals(grades: dict[str, dict[str, Any]]) -> dict[str, dict[str, int]]:
    fix_total = sum(len(g["graded_files"]) for g in grades.values())
    stages: list[str] = []
    for g in grades.values():
        stages += [s for s in g["stages"] if s not in stages]
    out: dict[str, dict[str, int]] = {}
    for st in stages:
        rows = [g["stages"][st] for g in grades.values() if st in g["stages"]]
        out[st] = {
            "hits": sum(len(r["fix_hits"]) for r in rows),
            "fix_total": fix_total,
            "unnamed": sum(len(r["fix_hits_not_named_in_ticket"]) for r in rows),
            "listed": sum(r["n"] for r in rows),
            "covered": sum(1 for r in rows if r["fix_hits"]),
            "tickets": len(rows),
        }
    return out


def render(grades: dict[str, dict[str, Any]], split: str) -> str:
    totals = stage_totals(grades)
    fix_total = sum(len(g["graded_files"]) for g in grades.values())
    lines = [
        f"# Baseline — {split} set ({len(grades)} tickets, {fix_total} fix files), micro-averaged",
        "",
        "| stage | fix files found | of which the ticket did not name | files listed | precision "
        + "| tickets with ≥1 fix file |",
        "|---|---|---|---|---|---|",
    ]
    for st, t in totals.items():
        lines.append(
            f"| {st} | {t['hits']}/{t['fix_total']} | {t['unnamed']} | {t['listed']} | "
            f"{t['hits']}/{t['listed']} | {t['covered']}/{t['tickets']} |"
        )
    lines += [
        "",
        "| ticket | fix files | named in ticket | deep search reproduces RCA top 10 "
        + "| fix-file ranks in deep search (files) |",
        "|---|---|---|---|---|",
    ]
    for key, g in grades.items():
        d = g.get("deep_search", {})
        ranks = d.get("fix_file_rank", {})
        rk = ", ".join(f"{f.split('/')[-1]}→{v}" for f, v in ranks.items()) if ranks else "withheld"
        by = g.get("fix_named_by")  # path, module name or changed symbol; older grades: paths only
        named = sum((by[f] is not None) if by else (f in g["ticket_named"]) for f in g["graded_files"])
        lines.append(
            f"| {key} | {len(g['graded_files'])} | {named} | {d.get('reproduces_rca_top10')} | {rk} |"
        )
    return "\n".join(lines) + "\n"


def main(argv: list[str]) -> int:
    if len(argv) != 4:
        print(__doc__, file=sys.stderr)
        return 2
    set_dir, split, out = Path(argv[1]), argv[2], Path(argv[3])
    picked = [c for c in json.loads((set_dir / "set.json").read_text())["picked"] if c["split"] == split]
    grades = {c["key"]: json.loads((set_dir / c["key"] / "grade.json").read_text()) for c in picked}
    md = render(grades, split)
    out.write_text(md)
    print(md, end="")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
