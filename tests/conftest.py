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

**Keep real credentials out of the process too.** The same reasoning applies one level up: a
unit test must not be able to reach a real tracker, whatever ran before it. That is also not
hypothetical — it filed two tickets in a live Jira project. ``run_feature`` calls
``load_local_env()``, which copies the repo ``.env`` into ``os.environ``; a later
``JiraConfig(dry_run=False, _env_file=None)`` blocks the *file* but inherits those variables,
so the adapter a test built to be *unconfigured* held real credentials — and, injecting no
mock transport, POSTed ``IssueRequest(summary="x")`` to production. The visible symptom was
an ordering-dependent ``DID NOT RAISE``, which reads as a flake and is not one. CI never saw
it: CI has no ``.env``.

Clearing the variables per test fixes it at the seam that matters, because pollution from one
test is wiped before the next one runs. Tests that genuinely want credentials (``-m
real_llm``) call ``load_local_env()`` inside the test, after this fixture, and are unaffected.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

# Anything that would let a unit test authenticate against a real tracker. Prefixes, not
# names: ``JIRA_URL``/``JIRA_USERNAME`` (the MCP server's spelling) sit beside
# ``JIRA_BASE_URL``/``JIRA_EMAIL`` (ours) in the same .env, and a partial list is a trap.
# LLM keys are deliberately absent — the real_llm tests need them and reload them anyway.
_TRACKER_ENV_PREFIXES = ("JIRA_", "CONFLUENCE_", "NOTION_", "ATLASSIAN_")


@pytest.fixture(autouse=True)
def _isolate_generated_artifacts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Redirect anything a run would write into the CWD to a per-test directory."""
    monkeypatch.setenv("ORCHESTRATOR_BACKLOG_PATH", str(tmp_path / "BACKLOG.md"))


@pytest.fixture(autouse=True)
def _isolate_tracker_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip tracker credentials so no test can write to a real Jira/Confluence/Notion.

    A test that wants them sets them itself — ``monkeypatch.setenv`` runs after this.
    """
    for name in [k for k in os.environ if k.startswith(_TRACKER_ENV_PREFIXES)]:
        monkeypatch.delenv(name, raising=False)
