"""What a person said about the tests a run wrote, kept and summarised (test-trust P4b, SSPN-228).

One JSON line per question put to a person (``kind: record``) and one per run's outcome (``kind:
outcome``), in a file beside the run state. The report groups them by diagnosed cause: how often each
answer was given and how many of those runs went on to pass. A choice given at least :data:`CANDIDATE_AT`
times for one cause with no disagreement is listed as a *candidate rule*: it is shown for a person to
review and never applied by anything. The measure of the whole mechanism is questions per run falling over
time; the benchmark has no person, so this is what shows it in real use.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

__all__ = [
    "CANDIDATE_AT",
    "InterventionRecord",
    "append_outcome",
    "append_record",
    "load_records",
    "render_report",
]

CANDIDATE_AT = 3


@dataclass(frozen=True)
class InterventionRecord:
    run_id: str
    approval_id: str
    issue_key: str
    repo: str
    cause: str
    evidence: str
    files: tuple[str, ...]
    names: tuple[str, ...]
    #: Empty when nobody answered within the window.
    choice: str
    note: str
    by: str
    waited_seconds: float
    #: Filled from the run's outcome line when read: did the run go on to pass, and under which label.
    passed: bool | None = None
    result: str = ""

    @property
    def answered(self) -> bool:
        return bool(self.choice)


def append_record(path: Path, record: InterventionRecord) -> None:
    body = {k: v for k, v in asdict(record).items() if k not in ("passed", "result")}
    _append(path, {"kind": "record", **body})


def append_outcome(path: Path, *, run_id: str, passed: bool, label: str = "") -> None:
    _append(path, {"kind": "outcome", "run_id": run_id, "passed": passed, "label": label})


def _append(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True) + "\n")


def load_records(path: Path) -> list[InterventionRecord]:
    """Every readable record, with its run's outcome joined on. A damaged or partial line is skipped."""
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    outcomes = {r["run_id"]: r for r in rows if r.get("kind") == "outcome" and "run_id" in r}
    found: list[InterventionRecord] = []
    for row in rows:
        if row.get("kind") != "record":
            continue
        try:
            outcome = outcomes.get(str(row["run_id"]))
            found.append(
                InterventionRecord(
                    run_id=str(row["run_id"]),
                    approval_id=str(row["approval_id"]),
                    issue_key=str(row.get("issue_key", "")),
                    repo=str(row.get("repo", "")),
                    cause=str(row["cause"]),
                    evidence=str(row.get("evidence", "")),
                    files=tuple(row.get("files", ())),
                    names=tuple(row.get("names", ())),
                    choice=str(row.get("choice", "")),
                    note=str(row.get("note", "")),
                    by=str(row.get("by", "")),
                    waited_seconds=float(row.get("waited_seconds", 0.0)),
                    passed=bool(outcome["passed"]) if outcome else None,
                    result=str(outcome.get("label", "")) if outcome else "",
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return found


def _cell(text: str, limit: int = 100) -> str:
    one = " ".join(text.split()).replace("|", "\\|")
    return one if len(one) <= limit else one[: limit - 1] + "…"


def render_report(records: list[InterventionRecord]) -> str:
    """Per-cause counts of what people answered and how those runs ended, then the candidate rules."""
    if not records:
        return "No questions have been put to a person yet."
    by_cause: dict[str, list[InterventionRecord]] = defaultdict(list)
    for r in records:
        by_cause[r.cause].append(r)
    lines = [
        f"{len(records)} question(s) across {len({r.run_id for r in records})} run(s).",
        "",
        "| Cause | Questions | Answers | Runs that passed |",
        "|---|---|---|---|",
    ]
    for cause, group in sorted(by_cause.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        counts = Counter(r.choice or "no answer" for r in group)
        answers = ", ".join(f"{c} {n}" for c, n in sorted(counts.items()))
        known = [r for r in group if r.passed is not None]
        passed = f"{sum(1 for r in known if r.passed)} of {len(known)}" if known else "—"
        lines.append(f"| {_cell(cause)} | {len(group)} | {answers} | {passed} |")
    words = [r for r in records if r.choice == "explain" and r.note.strip()]
    if words:
        lines += ["", "What people said when they explained:"]
        lines += [
            f"- {_cell(r.cause)}: {_cell(r.note, 160)} ({_cell(r.by or 'unknown', 40)})" for r in words[-10:]
        ]
    candidates = []
    for cause, group in sorted(by_cause.items()):
        answered = Counter(r.choice for r in group if r.answered)
        if len(answered) == 1:
            choice, n = answered.most_common(1)[0]
            if n >= CANDIDATE_AT:
                candidates.append(
                    f"- `{cause}` → `{choice}`: answered the same way {n} times and never otherwise"
                )
    if candidates:
        lines += [
            "",
            "Candidate rules (for a person to review; not applied by anything):",
            *candidates,
        ]
    return "\n".join(lines)
