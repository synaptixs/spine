"""The credential half of ``conftest.py``'s isolation, pinned.

These two tests are a pair, and the order matters: the first pollutes the process the way a
``run_feature`` test does, the second proves the autouse fixture wiped it before the next
test began. Written as a pair because that *is* the defect — no single test can observe it,
which is exactly why a suite run could file real Jira issues (SSPN-10, SSPN-11) while every
individual test looked fine.

Keep them in this file, in this order.
"""

from __future__ import annotations

import os
from pathlib import Path

from orchestrator.core.env import load_local_env
from orchestrator.intake.jira import JiraConfig


def test_a_test_may_load_a_dotenv_into_the_process(tmp_path: Path) -> None:
    """Stand-in for ``run_feature``, which calls ``load_local_env()`` on every run."""
    env = tmp_path / ".env"
    env.write_text("JIRA_BASE_URL=https://real.atlassian.net\nJIRA_API_TOKEN=real-token\n")

    assert load_local_env(env) == 2
    assert os.environ["JIRA_API_TOKEN"] == "real-token"


def test_the_next_test_inherits_no_credentials() -> None:
    """...and the next test starts clean, so an adapter built to be unconfigured is.

    Without the fixture this fails on the ``configured`` assertion — and a ``JiraAdapter``
    holding those values, with no mock transport, reaches the live API for real.
    """
    assert not any(k.startswith(("JIRA_", "CONFLUENCE_", "NOTION_", "ATLASSIAN_")) for k in os.environ)
    assert not JiraConfig(dry_run=False, _env_file=None).configured  # type: ignore[call-arg]
