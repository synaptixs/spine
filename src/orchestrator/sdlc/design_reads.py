"""What a deterministic design lists to read, in what order, and why (sdlc-search-use P4).

The rules here are pure; ``design._fallback_design`` feeds them. Each answers a measured failure:

* **B165, narrowing (D9, D28, D36).** A ticket naming more than three files made every one an edit
  target: on SSPN-376 that was 14 files for one fix (precision 1/14), and codegen was refused for
  size. With RCA's hypotheses, only the named files they name stay edit targets and the rest are
  listed to read (simulated: 14 → 3 files on SSPN-376, 8 → 2 on SSPN-286). With no RCA, or when RCA
  names none of them, nothing changes: a design with no edit target is worse than a long one.
* **Search finds (D29).** Up to :data:`FINDS_CAP` files search rates strongly (not weak) for the
  ticket that are not edit targets.
* **Completion (D19, D27, D34).** Up to three one-hop neighbours of the files to edit, ranked by
  where hybrid search first reached them (``seed_signals.completion_candidates``). On the
  pre-registered probe that put a missed fix file in the top 3 on 20 of 41 issues, but only 12 % of
  the files it adds are fix files, so they are always read, never edited.
* **Order and cap (D30, D12).** Moved named files, then the reads a design already lists, then
  completion, then search finds; :data:`READ_CAP` at most, the rest reported as left out.
* **Reasons with no path (D32).** Codegen treats every path in a design's read section as read-only
  and removes it from the files to edit, so a reason that named a file to edit would take it away.
  A reason names symbols in backticks — which codegen also uses as the excerpt's anchor — and
  :func:`safe_reason` replaces any that names a path.

**B165, completion and search finds are off by default** (decided 2026-11-12, after P4's graded run).
On the 8-ticket dev set, against the pre-registered bar (fix files in the build document's §7
Changed ∪ Read must rise):

- recall stayed at 15/22;
- completion and search finds reached no fix file, which matches the completion probe's 0/3 on this
  codebase against 20/41 on SWE-bench;
- B165 raised edit-target precision from 0.484 to 0.579, but only by moving 4 real fix files into
  the files codegen is told "do NOT modify";
- it relieved no context budget: SSPN-181 stayed TOO_BIG.

The code and its tests stay, so a later measurement can turn them on with evidence. The order, the
cap, the reasons and codegen's anchored, capped excerpts apply to whatever a design lists to read.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from orchestrator.sdlc.seed_signals import CompletionHit
from orchestrator.sdlc.source_paths import named_paths, normalise

#: B165 narrows only past this many named files (D9: "more than 3").
B165_MIN_NAMED = 4
#: Files to read a design lists at most (D12).
READ_CAP = 5
#: Search finds a design lists at most (D29).
FINDS_CAP = 2
#: Bytes codegen spends on the files to read (D12: a quarter of the 200 KB budget).
REFERENCE_BUDGET = 50_000

_GENERIC_REASON = "listed to read"


def b165_on() -> bool:
    """B165 narrowing, **off by default**; ``SPINE_DESIGN_B165=1`` turns it on. See the module notes."""
    return os.environ.get("SPINE_DESIGN_B165", "0").strip() == "1"


def completion_on() -> bool:
    """Search-based reads — completion candidates and search finds — **off by default**;
    ``SPINE_DESIGN_COMPLETION=1`` turns them on. See the module notes."""
    return os.environ.get("SPINE_DESIGN_COMPLETION", "0").strip() == "1"


@dataclass(frozen=True)
class Read:
    """A file to read. ``reason`` is shown to codegen and never names a path; ``evidence`` may, and
    goes only to the build document."""

    path: str
    reason: str
    evidence: str = ""


@dataclass(frozen=True)
class Narrowed:
    """B165's answer: the files to edit, the named files moved to read, and a risk line (or "")."""

    edit: list[str]
    moved: list[str]
    note: str = ""


def _text(h: Any) -> str:
    if isinstance(h, dict):
        claim, evidence = h.get("claim") or "", h.get("evidence") or ()
    else:
        claim, evidence = getattr(h, "claim", "") or "", getattr(h, "evidence", ()) or ()
    return " ".join([str(claim), *(str(e) for e in evidence)])


def rca_files(hypotheses: Iterable[Any] | None) -> set[str] | None:
    """The paths RCA's hypotheses name in their claim or evidence; ``None`` when RCA did not run.

    Takes ``RCAReport.hypotheses`` or the dicts ``evidence._rca_to_dict`` writes.
    """
    if hypotheses is None:
        return None
    return {normalise(p) for h in hypotheses for p in named_paths(_text(h))}


def _named_by(path: str, named: set[str]) -> bool:
    p = normalise(path)
    return any(p == n or p.endswith("/" + n) or n.endswith("/" + p) for n in named)


def narrow(stated: Sequence[str], named_by_rca: set[str] | None) -> Narrowed:
    """B165: with more than three named files and RCA's files in hand, edit only those RCA names."""
    files = list(stated)
    if named_by_rca is None or len(files) < B165_MIN_NAMED or not b165_on():
        return Narrowed(edit=files, moved=[])
    keep = [f for f in files if _named_by(f, named_by_rca)]
    if not keep:
        return Narrowed(
            edit=files,
            moved=[],
            note=(
                f"The ticket names {len(files)} files and RCA's hypotheses name none of them, so all stay "
                "files to edit; RCA did not narrow them."
            ),
        )
    moved = [f for f in files if f not in keep]
    return Narrowed(
        edit=keep,
        moved=moved,
        note=(
            f"The ticket names {len(files)} files; RCA's hypotheses name {len(keep)}, so only those are "
            f"files to edit and the other {len(moved)} are listed to read."
        ),
    )


def safe_reason(text: str) -> str:
    """``text``, or a generic reason when it names a path (D32)."""
    return _GENERIC_REASON if named_paths(text) else text


def _short(node_id: str) -> str:
    """``py:pkg.mod.Cls.fn`` → ``fn``: a symbol's own name, never a path."""
    tail = node_id.split(":", 1)[-1].split("@", 1)[0]
    return tail.rsplit(".", 1)[-1] or tail


def moved_reads(moved: Sequence[str]) -> list[Read]:
    return [Read(path=p, reason="named by the ticket; RCA's hypotheses do not name it") for p in moved]


def pick_finds(rows: Sequence[tuple[str, str, bool]], taken: set[str]) -> list[Read]:
    """Up to :data:`FINDS_CAP` files search rates strongly that are not already ``taken``.

    ``rows`` are ``(path, symbol name, weak)`` in search order, as the investigation lands them.
    """
    out: list[Read] = []
    seen: set[str] = set()
    for path, name, weak in rows:
        if weak or not path or path in taken or path in seen:
            continue
        seen.add(path)
        out.append(Read(path=path, reason=safe_reason(f"search ranks it for this ticket: `{name}`")))
        if len(out) == FINDS_CAP:
            break
    return out


def completion_reads(hits: Sequence[CompletionHit], *, of_n: int) -> list[Read]:
    """One read per completion candidate: the link by symbol, the location only as evidence."""
    out: list[Read] = []
    for h in hits:
        reason = safe_reason(
            f"one link from a file to edit: `{_short(h.src)}` {h.edge_kind} `{_short(h.dst)}` "
            f"(search rank {h.rank}; top {len(hits)} of {of_n} neighbours)"
        )
        out.append(Read(path=h.file, reason=reason, evidence=f"{h.src} {h.edge_kind} {h.dst} at {h.at}"))
    return out


def assemble(
    groups: Sequence[Sequence[Read]], *, edit: Sequence[str], cap: int = READ_CAP
) -> tuple[list[Read], list[str]]:
    """The reads in group order, never an edit target, never twice; the first ``cap`` kept, the rest
    returned as left out."""
    taken = set(edit)
    ordered: list[Read] = []
    for group in groups:
        for r in group:
            if r.path in taken:
                continue
            taken.add(r.path)
            ordered.append(r)
    return ordered[:cap], [r.path for r in ordered[cap:]]
