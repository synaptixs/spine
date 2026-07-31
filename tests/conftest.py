"""Repo-wide test isolation.

Keep artefacts out of the working tree. A test that writes into the repo root does more
than leave a mess here: generated markdown is ingested as a ``Doc`` node, so a stray file
silently changes the graph and makes the next ``understand`` regeneration disagree with CI —
which checks out tracked files only. The failure surfaces far from its cause, as a stale
``episteme`` on an unrelated pull request.

That is not hypothetical. ``tests/test_cli.py`` invokes ``sdlc feature --source jira://X-1``,
which resolves ``backlog_path()`` to ``./BACKLOG.md`` when nothing overrides it, and the file
is gitignored — so ``git status`` looks clean, the pre-commit hook regenerates *with* it
present and reports success, and only CI disagrees. It cost two red builds before anyone
looked at what the file contained.

``tests/sdlc/test_feature_runner.py`` already guarded itself this way; doing it once, here,
means no future test has to remember.
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolate_generated_artifacts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Redirect anything a run would write into the CWD to a per-test directory."""
    monkeypatch.setenv("ORCHESTRATOR_BACKLOG_PATH", str(tmp_path / "BACKLOG.md"))
