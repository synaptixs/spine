"""Recent git churn: which of these files changed lately, and how often.

Lifted out of ``rca.py``, where it was computed and then thrown away for half of all tickets.
``build_rca`` runs ``git log`` over the repo and intersects the result with the **fault file**,
which localizes a symptom — so the enhancement profile, having no fault to localize, drops the
whole node and the churn answer with it. "Is this area actively changing?" is not a
symptom-dependent question, and an enhancement's author wants it as much as a bug's.

**What it is intersected with is the whole design decision.** A repo-wide ``git log`` is not a
signal; it becomes one when crossed with the files a ticket is about. For a bug those are the
fault site. For an enhancement they are the landing sites — where the ticket's vocabulary
already lives in the code — which is a **weaker** claim and must be worded like one. A feature's
landing sites are what it will attach to, not what it will touch, so a hot landing site says
"the area you are building into is moving", never "expect a regression". Borrowing RCA's
regression framing here would state something this evidence does not support.

Best-effort by construction: no git, a shallow clone, or a repo with no history yields nothing
and says nothing. The one impurity in an otherwise pure-function pipeline is that this reads
history, so the digest is a function of *the commit* rather than of the working tree — see the
caveat in ``evidence.py``'s preamble, which this shares.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

__all__ = ["DEFAULT_COMMITS", "changed_recently", "recently_changed_files"]

#: How far back "recently" reaches. Forty commits is what `build_rca` has always used; keeping
#: the number in one place is what stops the bug path and the enhancement path from quietly
#: meaning two different things by the same word.
DEFAULT_COMMITS = 40


def recently_changed_files(root: Path | str | None, *, commits: int = DEFAULT_COMMITS) -> set[str]:
    """Repo-relative files touched in the last ``commits`` commits (best-effort)."""
    if root is None:
        return set()
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "log", "--name-only", "--pretty=format:", "-n", str(commits)],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return set()
    if out.returncode != 0:
        return set()
    return {line.strip() for line in out.stdout.splitlines() if line.strip()}


def changed_recently(
    paths: list[str] | tuple[str, ...],
    root: Path | str | None,
    *,
    commits: int = DEFAULT_COMMITS,
) -> tuple[str, ...]:
    """Which of ``paths`` are in that set, in the order given, de-duplicated.

    Suffix-matched on a path boundary, because the two sides are named differently: git reports
    ``src/orchestrator/pkg/store.py`` while a landing site may carry only ``store.py``. The
    boundary is what keeps ``store.py`` from matching ``my_store.py``.
    """
    if not paths:
        return ()
    changed = recently_changed_files(root, commits=commits)
    if not changed:
        return ()
    out: list[str] = []
    for raw in paths:
        path = str(raw).strip()
        if not path or path in out:
            continue
        if any(c == path or c.endswith("/" + path) or path.endswith("/" + c) for c in changed):
            out.append(path)
    return tuple(out)
