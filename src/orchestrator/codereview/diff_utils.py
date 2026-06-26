"""Unified-diff helpers shared by the code-aware verifiers and the review
orchestration (Block A.4 / A.5).

The GitHub files API hands us a per-file ``patch`` — a sequence of hunks
that look like::

    @@ -1,3 +1,4 @@
     context line
    -removed line
    +added line

Reviews must anchor inline comments to a line number in the *new* version
of the file. ``iter_added_lines`` walks the hunks tracking the new-file
line counter and yields ``(new_line_number, content)`` for every added
(``+``) line, so a verifier can report the exact line a finding sits on.
"""

from __future__ import annotations

import re
from collections.abc import Iterator

_HUNK_HEADER = re.compile(r"^@@ .*?\+(\d+)")


def iter_added_lines(patch: str) -> Iterator[tuple[int, str]]:
    """Yield ``(new_file_line, content)`` for each added line in a patch.

    - ``@@`` hunk headers reset the new-file line counter.
    - context lines (leading space) advance the counter.
    - removed lines (leading ``-``) do not advance it.
    - added lines (leading ``+``) are yielded, then advance it.

    File-header lines (``+++`` / ``---``) are skipped defensively; the
    GitHub patch field omits them, but raw unified diffs include them.
    """
    new_line = 0
    in_hunk = False
    for raw in patch.splitlines():
        if raw.startswith("@@"):
            m = _HUNK_HEADER.match(raw)
            if m:
                new_line = int(m.group(1))
                in_hunk = True
            continue
        if not in_hunk:
            continue
        if raw.startswith(("+++", "---")):
            continue
        if raw.startswith("+"):
            yield new_line, raw[1:]
            new_line += 1
        elif raw.startswith("-"):
            continue  # removed line — does not advance the new-file counter
        else:
            new_line += 1  # context line
