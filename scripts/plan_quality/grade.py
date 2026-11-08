"""Grade one `sdlc plan` quality run against its answer key; every quality-scorecard number comes from here.

    python scripts/plan_quality/grade.py <run_dir> <target>

``run_dir`` holds ``ticket.md``, ``answer-key.json`` (``make_key.py``), ``investigate.md``, ``rca-det.md``,
``build.md`` and ``design-det.md``, plus optionally ``rca-llm.md``, ``design-llm.md`` and
``search-deep.json`` (``probe_search.py``). ``target`` is the repository at the answer key's base commit:
a checkout, or ``<repo>@<sha>`` read straight from git. Paths are resolved against that commit's tracked
files, so the grader never uses the code it is grading. Writes ``grade.json`` and ``grade.md`` beside the
run and prints the table.

Why the guards exist (2026-11-08, SSPN-389): a hand-graded scorecard carried four miscounts and an
unmeasured claim into Jira, and the first version of this script then skipped four of RCA's ten files
without a word. So:

* a section that parses to nothing is an error, never a zero;
* a parse that accounts for fewer lines than the section holds is an error (``same_count``);
* every hit is split into "the ticket named it" and "found it" — finding a file the ticket names is not
  search;
* deep-search ranks are reported only when the probe reproduces the list RCA printed.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

PATH_RE = re.compile(r"[\w.-]+(?:/[\w.-]+)*\.(?:py|md|json|toml|ya?ml|ts|js)\b")


class ParseError(Exception):
    """A stage's output did not parse the way the grader expects; its numbers would be wrong."""


def tracked(target: str) -> list[str]:
    """Tracked files of a checkout, or of ``<repo>@<sha>`` read from git without a checkout."""
    if "@" in target:
        repo, sha = target.rsplit("@", 1)
        args = ["git", "-C", repo, "ls-tree", "-r", "--name-only", sha]
    else:
        args = ["git", "-C", target, "ls-files"]
    return subprocess.run(args, capture_output=True, text=True, check=True).stdout.split()


def resolve(path: str, files: list[str]) -> str | None:
    """A path as written, or its one segment-aligned suffix match; ``None`` when absent or ambiguous."""
    p = path.strip("`'\" ").removeprefix("./")
    if p in files:
        return p
    matches = [f for f in files if f.endswith("/" + p)]
    return matches[0] if len(matches) == 1 else None


def section(text: str, start: str, end_re: str = r"^## ") -> str:
    i = text.find(start)
    if i < 0:
        raise ParseError(f"section {start!r} not found")
    rest = text[i + len(start) :]
    m = re.search(end_re, rest, re.M)
    return rest[: m.start()] if m else rest


def ordered(items: list[str]) -> list[str]:
    seen: list[str] = []
    for x in items:
        if x not in seen:
            seen.append(x)
    return seen


def paths_in(text: str, files: list[str]) -> tuple[list[str], list[str]]:
    found: list[str] = []
    unresolved: list[str] = []
    for p in PATH_RE.findall(text):
        r = resolve(p, files)
        if r:
            found.append(r)
        else:
            unresolved.append(p)
    return ordered(found), ordered(unresolved)


def need(name: str, xs: list[Any]) -> list[Any]:
    if not xs:
        raise ParseError(f"{name}: parsed to nothing — the output format changed or the stage was empty")
    return xs


def same_count(name: str, parsed: list[Any], text: str, line_re: str) -> None:
    """A parse must account for every line of its kind; a silent drop is how hand-grading went wrong."""
    expected = len(re.findall(line_re, text, re.M))
    if len(parsed) != expected:
        raise ParseError(f"{name}: parsed {len(parsed)} but the section has {expected} such lines")


def _stage_lists(run: Path, files: list[str]) -> tuple[dict[str, list[str]], dict[str, list[str]], list[Any]]:
    stages: dict[str, list[str]] = {}
    unresolved: dict[str, list[str]] = {}
    inv = (run / "investigate.md").read_text()
    landings = re.findall(r"— ((?:src|tests|scripts)/[^\s:]+):\d+", inv)
    same_count("investigate landings", landings, inv, r"^- `[^`]+` \(")
    stages["search: investigate landings"] = need("investigate", ordered(landings))

    rca_det = (run / "rca-det.md").read_text()
    fault = section(rca_det, "## Fault site")
    ranking = need("rca-det ranking", re.findall(r"^- `([^`]+)` — ", fault, re.M))
    same_count("rca-det ranking", ranking, fault, r"^- ")
    stages["search: rca fault-site ranking"] = ranking

    llm_path = run / "rca-llm.md"
    rca_llm = llm_path.read_text() if llm_path.exists() else None
    for label, txt in (("rca det: hypotheses", rca_det), ("rca llm: hypotheses", rca_llm)):
        if txt is not None:
            stages[label], unresolved[label] = paths_in(section(txt, "## Root-cause hypotheses"), files)
            need(label, stages[label])
    if rca_llm is not None:
        label = "rca llm: fix approach"
        stages[label], unresolved[label] = paths_in(section(rca_llm, "## Suggested fix approach"), files)

    build = (run / "build.md").read_text()
    fs = re.search(r"\*\*Fault site:\*\* `([^`]+)`", section(build, "## 3. Root cause"))
    stages["build §3 fault site"] = [resolve(fs.group(1), files) or fs.group(1)] if fs else []
    s4 = section(build, "## 4.", r"^\*\*Areas:\*\*")
    rows4 = need("§4", re.findall(r"^\| `([^`]+)` \| (\w+) \| `([^`:]+):\d+` \| (\d+) \|", s4, re.M))
    same_count("§4", rows4, s4, r"^\| `")
    stages["build §4 evidence rows (files)"] = [r[2] for r in rows4]
    target = section(section(build, "## 5."), "subgraph target[", r"^\s*end\s*$")
    stages["build §5 'what changes'"] = need(
        "§5", [resolve(p, files) or p for p in re.findall(r'\["([^"]+)"\]', target)]
    )
    changed = section(section(build, "## 7."), "**Changed**", r"^\*\*")
    s7 = need("§7", re.findall(r"^\| `([^`]+)` \| [\d,]+ b \|", changed, re.M))
    same_count("§7", s7, changed, r"^\| `")
    stages["build §7 changed = codegen input"] = s7

    for label, name in (("design det", "design-det.md"), ("design llm", "design-llm.md")):
        if not (run / name).exists():
            continue
        sec = section((run / name).read_text(), "## Files to edit")
        raw = need(label, re.findall(r"^- (\S+)", sec, re.M))
        same_count(label, raw, sec, r"^- ")
        stages[label] = ordered([r for p in raw if (r := resolve(p, files))])
        unresolved[label] = [p for p in raw if not resolve(p, files)]
    return stages, unresolved, rows4


def grade_run(run: Path, files: list[str]) -> dict[str, Any]:
    """Every stage's file list against the answer key; ``files`` is the target commit's tracked files."""
    key = json.loads((run / "answer-key.json").read_text())
    graded = [f for f in key["fix_files"] if f not in key.get("doc_only_files", [])]
    ticket_named, _ = paths_in((run / "ticket.md").read_text(), files)
    stages, unresolved, rows4 = _stage_lists(run, files)
    search_top = stages["search: rca fault-site ranking"]
    result: dict[str, Any] = {"graded_files": graded, "ticket_named": ticket_named, "stages": {}}
    for label, lst in stages.items():
        hits = ordered([f for f in lst if f in graded])
        result["stages"][label] = {
            "n": len(lst),
            "unresolved": unresolved.get(label, []),
            "fix_hits": {f: lst.index(f) + 1 for f in hits},
            "recall": f"{len(hits)}/{len(graded)}",
            "precision": f"{len(hits)}/{len(lst)}" if lst else "n/a",
            "fix_hits_not_named_in_ticket": [f for f in hits if f not in ticket_named],
            "from_ticket": sum(f in ticket_named for f in lst),
            "from_search_top10_not_ticket": sum(f in search_top and f not in ticket_named for f in lst),
            "from_neither": [f for f in lst if f not in ticket_named and f not in search_top],
        }
    symbols = key.get("changed_symbols", {})
    pairs = {(f, s) for f, names in symbols.items() for s in names}
    result["evidence_rows"] = {
        "rows": len(rows4),
        "in_fix_file": sum(r[2] in graded for r in rows4),
        "changed_symbols_listed": [r[0] for r in rows4 if (r[2], r[0]) in pairs],
        "zero_caller_rows": sum(r[3] == "0" for r in rows4),
    }
    deep_path = run / "search-deep.json"
    if deep_path.exists():
        result["deep_search"] = _deep(json.loads(deep_path.read_text()), search_top, graded, symbols)
    return result


def _deep(
    deep: dict[str, Any], search_top: list[str], graded: list[str], symbols: dict[str, list[str]]
) -> dict[str, Any]:
    # RCA reads the first 50 hits, so its list is the distinct files of those 50, at most ten — fewer
    # when 50 hits hold fewer files (SSPN-73: 4). Compare like with like.
    top10 = deep.get("files_within_rca_limit_50", deep["files"])[:10]
    out: dict[str, Any] = {
        "reproduces_rca_top10": top10 == search_top,
        "hits": deep["hits"],
        "files": len(deep["files"]),
    }
    if top10 != search_top:
        out["mismatch"] = {"probe_top10": top10, "rca_top10": search_top}
        return out
    files = deep["files"]
    visible = deep.get("files_within_rca_limit_50", [])
    out["fix_file_rank"] = {f: (files.index(f) + 1 if f in files else None) for f in graded}
    out["fix_files_rca_can_see (first 50 hits)"] = {f: f in visible for f in graded}
    out["changed_symbol_rank"] = {
        f"{f}::{s}": next((r["rank"] for r in deep["rows"] if r["file"] == f and r["name"] == s), None)
        for f, names in symbols.items()
        for s in names
    }
    return out


def render(r: dict[str, Any]) -> str:
    out = [
        f"Graded fix files: {', '.join(r['graded_files'])}",
        f"Named in the ticket ({len(r['ticket_named'])}): {', '.join(r['ticket_named'])}",
        "",
        "| stage | files | recall | precision | fix hits (rank) | hits the ticket did not name "
        "| from ticket | from search top-10 (not ticket) | from neither |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for label, s in r["stages"].items():
        hits = ", ".join(f"{Path(f).name} #{k}" for f, k in s["fix_hits"].items()) or "—"
        out.append(
            f"| {label} | {s['n']} | {s['recall']} | {s['precision']} | {hits} | "
            f"{len(s['fix_hits_not_named_in_ticket'])} | {s['from_ticket']} | "
            f"{s['from_search_top10_not_ticket']} | {len(s['from_neither'])} |"
        )
    e = r["evidence_rows"]
    out += [
        "",
        f"§4 evidence: {e['rows']} rows; {e['in_fix_file']} in a fix file; changed symbols listed: "
        f"{e['changed_symbols_listed'] or 'none'}; {e['zero_caller_rows']} rows with 0 callers",
    ]
    d = r.get("deep_search")
    if d:
        out.append(
            f"Deep search ({d['hits']} hits / {d['files']} files): "
            f"reproduces RCA top 10 = {d['reproduces_rca_top10']}"
        )
        for k in ("fix_file_rank", "changed_symbol_rank"):
            if k in d:
                out.append(f"  {k}: " + ", ".join(f"{a.split('/')[-1]} → {b}" for a, b in d[k].items()))
        if "mismatch" in d:
            out.append(f"  MISMATCH — ranks withheld: {d['mismatch']}")
    for label, s in r["stages"].items():
        if s["unresolved"]:
            out.append(f"unresolved in {label}: {s['unresolved']}")
    return "\n".join(out)


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__, file=sys.stderr)
        return 2
    run = Path(argv[1])
    res = grade_run(run, tracked(argv[2]))
    (run / "grade.json").write_text(json.dumps(res, indent=1))
    md = render(res)
    (run / "grade.md").write_text(md + "\n")
    print(md)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
