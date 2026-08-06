"""Reading an existing file into a prompt without lying about what fits.

Shared by codegen (which must anchor edits against real text) and the acceptance judge
(which must see the code it is judging). Both had the same bug independently: a file too
large for the budget was dropped in silence, and a reader that cannot see a file reports
it as missing rather than as unread. One of them told a model to copy an anchor verbatim
from an empty block; the other told a judge that a 100 KB ``cli.py`` simply was not part
of the change.
"""

from __future__ import annotations

import difflib
import re
from pathlib import Path


def _anchor_line(lines: list[str], anchor: str) -> int | None:
    """The line the model was probably aiming at when its ``find`` failed to match.

    A failed anchor is still the best available statement of intent — it says which part
    of the file the model believes it is editing, even when the text is wrong. Exact match
    on a stripped line first, then the closest match, so a snippet that differs only in
    indentation or a renamed identifier still lands in the right neighbourhood.
    """
    needles = [line.strip() for line in anchor.splitlines() if line.strip()]
    if not needles:
        return None
    stripped = [line.strip() for line in lines]
    for needle in needles:
        if needle in stripped:
            return stripped.index(needle)
    # Containment, before fuzz. An anchor is very often a *name* rather than a whole line —
    # `mcp_contracts` against `def mcp_contracts(json_out: bool) -> None:`. Ratio-matching
    # scores that pair at 0.48 and rejects it, because the line is three times longer than
    # the thing being looked for, so the window landed on line 1 of a 3000-line file.
    for needle in needles:
        for index, line in enumerate(stripped):
            if needle in line:
                return index
    for needle in needles:
        close = difflib.get_close_matches(needle, stripped, n=1, cutoff=0.6)
        if close:
            return stripped.index(close[0])
    return None


def _grow_window(lines: list[str], center: int, budget: int) -> tuple[int, int]:
    """Widen outward from ``center`` for as long as the budget allows.

    Deliberately not a fixed line count: the window is as large as there is room for, so
    a lone target file gets most of the pool and five files each get a fifth.
    """
    low = high = center
    size = len(lines[center]) + 1
    while size < budget and (low > 0 or high < len(lines) - 1):
        grew = False
        if low > 0 and size + len(lines[low - 1]) + 1 < budget:
            low -= 1
            size += len(lines[low]) + 1
            grew = True
        if high < len(lines) - 1 and size + len(lines[high + 1]) + 1 < budget:
            high += 1
            size += len(lines[high]) + 1
            grew = True
        if not grew:
            break
    return low, high


def _excerpt_file(rel: str, body: str, *, budget: int, anchors: list[str], label: str) -> str:
    """``rel``'s content for a prompt: whole when it fits, windowed when it doesn't.

    The version of this that broke a run said "below is the CURRENT EXACT content" and
    then appended nothing, because a 100 KB ``cli.py`` failed a ``len(block) > budget``
    check and the loop moved on. A model told to copy an anchor verbatim from an empty
    block can only guess, so every retry re-failed the same way and the run died having
    never once seen the file it was editing.

    Windows are placed by anchor and sized by what is left, never by a fixed constant,
    and whatever is not shown is stated rather than dropped in silence.
    """
    lines = body.splitlines()
    if not lines:
        return ""
    whole = f"--- {rel} ({label}) ---\n{body}\n"
    if len(whole) <= budget:
        return whole

    centers: list[int] = []
    for anchor in anchors:
        found = _anchor_line(lines, anchor)
        if found is not None and found not in centers:
            centers.append(found)
    if not centers:
        # Nothing to aim at — the head of the file is still worth more than nothing,
        # and the note below keeps it honest about being partial.
        centers = [0]

    per_window = max(budget // (len(centers) + 1), 400)
    spans: list[tuple[int, int]] = []
    for center in sorted(centers):
        low, high = _grow_window(lines, center, per_window)
        if spans and low <= spans[-1][1] + 1:
            spans[-1] = (spans[-1][0], max(spans[-1][1], high))
        else:
            spans.append((low, high))

    chunks: list[str] = []
    shown = 0
    for low, high in spans:
        window = "\n".join(lines[low : high + 1])
        # Line numbers go in the header, never in a gutter: the body has to stay
        # byte-identical to the file or a `find` copied out of it will not anchor.
        block = f"--- {rel} ({label} — excerpt, lines {low + 1}-{high + 1} of {len(lines)}) ---\n{window}\n"
        if len(block) > budget - sum(len(c) for c in chunks):
            break
        chunks.append(block)
        shown += high - low + 1
    if not chunks:
        # Not even one window fits — a minified bundle, a generated blob, a single line
        # longer than the whole budget. Saying nothing here would put us back where this
        # module started: a reader concluding "absent" from what is only "unread".
        return (
            f"--- {rel}: not shown ({len(lines)} line(s), too large for the budget) ---\n"
            "This file is part of the change and could not be included at all. Its absence "
            "is NOT evidence that it is missing or that anything it contains is wrong.\n"
        )
    chunks.append(
        f"--- {rel}: {len(lines) - shown} of {len(lines)} lines not shown ---\n"
        "This file is too large to include whole. The excerpts above are verbatim, so "
        "anchors copied from them will match; anything you need that is not shown, do "
        "not guess at — say so in your summary instead of inventing an anchor.\n"
    )
    return "".join(chunks)


def _excerpt_files(
    root: Path,
    rels: list[str],
    *,
    budget: int,
    anchors_by_path: dict[str, list[str]] | None = None,
    label: str,
) -> str:
    """Render several files into one prompt block, sharing ``budget`` fairly.

    Smallest first, each taking an equal share of what remains: a file that fits whole
    consumes only its own size and hands the rest back, so one oversized module gets
    everything the small ones did not need instead of being dropped for being first
    in line and too big.
    """
    anchors_by_path = anchors_by_path or {}
    readable: list[tuple[str, str]] = []
    for rel in rels:
        target = (root / rel).resolve()
        if not target.is_relative_to(root.resolve()) or not target.is_file():
            continue
        try:
            readable.append((rel, target.read_text(encoding="utf-8")))
        except (OSError, UnicodeDecodeError):
            continue
    if not readable:
        return ""

    readable.sort(key=lambda pair: len(pair[1]))
    chunks: list[str] = []
    remaining = budget
    for index, (rel, body) in enumerate(readable):
        share = max(remaining // (len(readable) - index), 400)
        block = _excerpt_file(rel, body, budget=share, anchors=anchors_by_path.get(rel, []), label=label)
        if not block:
            continue
        chunks.append(block)
        remaining = max(remaining - len(block), 0)
    return "".join(chunks)


# Identifier-ish words worth locating in a file: dotted/underscored names and quoted
# phrases, which is how a criterion refers to the code it is about.
_SPEC_ANCHOR_RE = re.compile(r"`([^`]{3,60})`|\b([A-Za-z_][A-Za-z0-9_]{3,}(?:\.[A-Za-z_][A-Za-z0-9_]*)*)\b")


def _spec_anchors(blob: str) -> list[str]:
    """Terms from the spec to aim a window at, most specific first."""
    found: list[str] = []
    for backticked, bare in _SPEC_ANCHOR_RE.findall(blob):
        term = (backticked or bare).strip()
        if term and term not in found:
            found.append(term)
    # Backticked terms sort first: a criterion that says `_type_label` is pointing at a
    # symbol, where a bare word may just be prose.
    found.sort(key=lambda t: 0 if f"`{t}`" in blob else 1)
    return found[:12]
