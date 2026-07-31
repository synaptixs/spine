"""Where eval scorecards are written.

Mirrors ``memory_bank_dir`` in :mod:`orchestrator.knowledge.understand`: an
explicit argument wins, then ``$ORCHESTRATOR_EVALS_DIR``, then the in-repo
default of ``<repo>/docs/evals``.

The override exists because scorecards are *markdown*, and doc ingestion turns
markdown on disk into ``Doc`` nodes whether or not git tracks it — measured: one
three-line file moved this repo's graph by a node and staled two episteme pages.

For a repo that analyses *itself*, that leaves two consistent options and one
broken one. Committing each run's scorecards keeps the tree consistent but makes
every measurement a commit. Writing them outside the repo via
``$ORCHESTRATOR_EVALS_DIR`` keeps measurement and comprehension apart entirely.
The broken option is leaving generated scorecards sitting uncommitted in a
tracked ``docs/evals``: the local graph then describes pages CI cannot see, and
``understand --check`` fails on a diff nobody can reproduce.

The in-repo default is kept so a fresh clone behaves as it always has; only
repos that *are* the analysed repo need the override.
"""

from __future__ import annotations

import os
from pathlib import Path

EVALS_DIRNAME = "evals"
"""Leaf directory name, under ``docs/`` by default."""


def evals_dir(root: Path | str, out_dir: Path | str | None = None) -> Path:
    """Where eval scripts *write* scorecards and the promotions log.

    Precedence: explicit ``out_dir`` → ``$ORCHESTRATOR_EVALS_DIR`` → in-repo
    ``<root>/docs/evals``.
    """
    if out_dir is not None:
        return Path(out_dir)
    env = os.getenv("ORCHESTRATOR_EVALS_DIR")
    return Path(env) if env else Path(root) / "docs" / EVALS_DIRNAME
