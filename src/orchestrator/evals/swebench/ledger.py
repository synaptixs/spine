"""The books: every instance in exactly one bucket, and no headline from an incomplete ledger.

``ledger.jsonl`` is append-only. A retry appends a new row for the same instance and the latest wins
when the book is read, so the history of what was tried stays in the file. The ledger holds no patch
text — it is published beside the number — only a hash and the names of the files; each patch lives
in its own file and is turned into a prediction for the official grader by :func:`predictions`.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any

from orchestrator.evals.swebench.outcome import BUCKETS, GENERATION_BUCKETS


@dataclass(frozen=True)
class LedgerRow:
    instance_id: str
    repo: str
    bucket: str
    reason: str
    verdict: str = ""
    spent_usd: float = 0.0
    seconds: float = 0.0
    run_id: str = ""
    image_digest: str = ""
    spine_sha: str = ""
    model: str = ""
    patch_sha256: str = ""
    patch_files: list[str] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)
    #: How many times this instance has been run, this row included. ``spent_usd`` is cumulative
    #: across them: a retry row carries the earlier attempts' spend, so the latest row is the truth.
    attempts: int = 1


_KNOWN = frozenset(BUCKETS) | frozenset(GENERATION_BUCKETS)


def _check(bucket: str) -> None:
    if bucket not in _KNOWN:
        raise ValueError(f"unknown bucket {bucket!r}; expected one of {', '.join(sorted(_KNOWN))}")


class Ledger:
    """An append-only JSONL file of :class:`LedgerRow`."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)

    def append(self, row: LedgerRow) -> None:
        _check(row.bucket)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(asdict(row), sort_keys=True) + "\n")

    def history(self, instance_id: str) -> list[LedgerRow]:
        """Every row ever written for ``instance_id``, in order — the attempts behind the latest."""
        found: list[LedgerRow] = []
        if self.path.is_file():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    row = LedgerRow(**json.loads(line))
                    if row.instance_id == instance_id:
                        found.append(row)
        return found

    def rows(self) -> list[LedgerRow]:
        """The latest row per instance, in order of first appearance."""
        latest: dict[str, LedgerRow] = {}
        if self.path.is_file():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    row = LedgerRow(**json.loads(line))
                    latest[row.instance_id] = row
        return list(latest.values())


def predictions(rows: Iterable[LedgerRow], patch_dir: Path, *, model_name: str) -> list[dict[str, str]]:
    """The official grader's input: one prediction per submitted instance.

    A submitted row whose patch file is missing raises: an empty prediction would be graded as
    "no change" and filed as ``unresolved``, hiding a harness fault as a pipeline result.
    """
    out = []
    for row in rows:
        if row.bucket != "submitted":
            continue
        patch = (patch_dir / f"{row.instance_id}.diff").read_text(encoding="utf-8")
        out.append({"instance_id": row.instance_id, "model_name_or_path": model_name, "model_patch": patch})
    return out


def apply_grading(rows: Sequence[LedgerRow], report: dict[str, Any]) -> list[LedgerRow]:
    """Settle the ``submitted`` rows from the official grader's report; others pass through.

    ``infra_failure``, ``error`` and ``incomplete`` instances are ``grader-error`` — the harness
    failed, not the patch. ``ambiguous_failure`` is counted as ``unresolved``, as the grader does,
    with the reason recording it. A submitted instance the report never mentions is a
    ``grader-error`` too: absence is not a result.
    """
    ids = {key: set(report.get(key, [])) for key in (
        "resolved_ids", "unresolved_ids", "ambiguous_failure_ids", "empty_patch_ids",
        "error_ids", "infra_failure_ids", "incomplete_ids",
    )}  # fmt: skip
    final = []
    for row in rows:
        if row.bucket != "submitted":
            final.append(row)
            continue
        i = row.instance_id
        if i in ids["resolved_ids"]:
            final.append(replace(row, bucket="resolved", reason="resolved by the official grader"))
        elif i in ids["unresolved_ids"]:
            final.append(
                replace(row, bucket="unresolved", reason="the grader ran the tests and they did not pass")
            )
        elif i in ids["ambiguous_failure_ids"]:
            final.append(
                replace(
                    row, bucket="unresolved", reason="unresolved; the grader marked the failure ambiguous"
                )
            )
        elif i in ids["empty_patch_ids"]:
            final.append(replace(row, bucket="unresolved", reason="the grader saw an empty patch"))
        elif i in (ids["error_ids"] | ids["infra_failure_ids"] | ids["incomplete_ids"]):
            final.append(replace(row, bucket="grader-error", reason="the grader failed on this instance"))
        else:
            final.append(replace(row, bucket="grader-error", reason="submitted but missing from the report"))
    return final


def summary(rows: Sequence[LedgerRow], *, expected: Iterable[str]) -> dict[str, Any]:
    """Counts per bucket over the whole denominator. Refuses a ledger that is not complete.

    Every expected instance must have a row, and none may still be ``submitted``: a number built
    on a ledger with holes is neither published-as-bad nor dropped, it is just wrong.
    """
    by_id = {r.instance_id: r for r in rows}
    wanted = list(expected)
    missing = [i for i in wanted if i not in by_id]
    if missing:
        raise ValueError(f"{len(missing)} instance(s) have no ledger row: {', '.join(missing[:5])}")
    provisional = [i for i in wanted if by_id[i].bucket == "submitted"]
    if provisional:
        raise ValueError(
            f"{len(provisional)} instance(s) are still 'submitted' (not graded): {', '.join(provisional[:5])}"
        )
    counts = {b: 0 for b in BUCKETS}
    for i in wanted:
        counts[by_id[i].bucket] += 1
    total = len(wanted)
    return {
        "total": total,
        "counts": counts,
        "resolved_pct": 100.0 * counts["resolved"] / total if total else 0.0,
        "spent_usd": sum(by_id[i].spent_usd for i in wanted),
    }
