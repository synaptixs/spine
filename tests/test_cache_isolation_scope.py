"""The suite's PKG cache isolation reaches module-scoped fixtures too (B157, G6 P8).

pytest builds a module-scoped fixture before the first test's function-scoped autouse fixtures. A
module fixture that extracts a graph (``tests/evals/test_agent_corpus.py``'s ``repo_store`` extracts
this repository, 24.8 MB) therefore ran before the cache directory was redirected and wrote into the
developer's own cache. The redirect is session-scoped so every fixture scope sees it.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def cache_dir_seen_by_a_module_fixture() -> str | None:
    return os.environ.get("ORCHESTRATOR_PKG_CACHE_DIR")


def test_a_module_fixture_sees_the_suites_cache(
    cache_dir_seen_by_a_module_fixture: str | None, _session_pkg_cache: Path
) -> None:
    assert cache_dir_seen_by_a_module_fixture == str(_session_pkg_cache)
