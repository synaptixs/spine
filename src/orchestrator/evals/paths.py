"""Where eval scorecards are written.

Mirrors ``memory_bank_dir`` in :mod:`orchestrator.knowledge.understand`: an
explicit argument wins, then ``$ORCHESTRATOR_EVALS_DIR``, then the in-repo
default of ``<repo>/docs/evals``.

The override exists because scorecards are *markdown*, and doc ingestion turns
markdown on disk into ``Doc`` nodes whether or not git tracks it. Writing them
under the analysed repo's own ``docs/`` therefore grows the graph with pages CI
never sees, and ``understand --check`` fails on any tree where evals have been
run — measured: one three-line file took the graph from 8843 to 8844 nodes and
staled two episteme pages. Point ``$ORCHESTRATOR_EVALS_DIR`` at a directory
outside the repo to keep measurement and comprehension from colliding.

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
