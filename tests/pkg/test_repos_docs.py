"""The ``docs:`` block in ``.spine/repos.yaml`` — external doc sources per repository (SSPN-80)."""

from __future__ import annotations

from pathlib import Path

import pytest

from orchestrator.pkg.repos import DocSource, RepoConfigError, load_repo_config


def _config(tmp_path: Path, docs: str = "") -> Path:
    (tmp_path / "app").mkdir(exist_ok=True)
    (tmp_path / "lib").mkdir(exist_ok=True)
    cfg = tmp_path / "repos.yaml"
    cfg.write_text(f"repos:\n  app: app\n  lib: lib\n{docs}", encoding="utf-8")
    return cfg


_BOTH = """docs:
  app:
    - name: handbook
      server: atlassian
      confluence: {roots: ["123", 456], max_depth: 2, max_docs: 40}
    - name: tickets
      server: atlassian
      jira: {jql: "project = APP", max_issues: 30}
"""


def test_a_file_without_docs_is_as_valid_as_before(tmp_path: Path) -> None:
    repo_set = load_repo_config(_config(tmp_path))
    assert repo_set.docs == ()
    assert repo_set.doc_sources("app") == ()


def test_confluence_and_jira_sources_parse_with_their_settings(tmp_path: Path) -> None:
    repo_set = load_repo_config(_config(tmp_path, _BOTH))
    assert repo_set.doc_sources("app") == (
        DocSource("handbook", "atlassian", "confluence", roots=("123", "456"), max_depth=2, max_docs=40),
        DocSource("tickets", "atlassian", "jira", jql="project = APP", max_issues=30),
    )
    assert repo_set.doc_sources("lib") == ()


def test_defaults_are_depth_3_and_100_documents(tmp_path: Path) -> None:
    docs = """docs:
  app:
    - {name: wiki, server: s, confluence: {roots: ["1"]}}
    - {name: jira, server: s, jira: {jql: "x"}}
"""
    wiki, jira = load_repo_config(_config(tmp_path, docs)).doc_sources("app")
    assert (wiki.max_depth, wiki.max_docs, wiki.cap) == (3, 100, 100)
    assert (jira.max_issues, jira.cap) == (100, 100)
    assert wiki.enumerated and jira.enumerated


def test_key_for_resolves_a_checkout_to_its_declared_key(tmp_path: Path) -> None:
    repo_set = load_repo_config(_config(tmp_path))
    assert repo_set.key_for(tmp_path / "app") == "app"
    assert repo_set.key_for(tmp_path / "app" / ".." / "lib") == "lib"
    assert repo_set.key_for(tmp_path) is None


@pytest.mark.parametrize(
    ("docs", "message"),
    [
        ("docs: [1]\n", "'docs' must be a mapping"),
        ("docs:\n  ghost: []\n", r"docs\[ghost\] names an undeclared repository"),
        ("docs:\n  app: {name: x}\n", r"docs\[app\] must be a list"),
        ("docs:\n  app: [3]\n", r"docs\[app\]\[0\] is not a mapping"),
        (
            "docs:\n  app:\n    - {server: s, jira: {jql: x}}\n",
            r"docs\[app\]\[0\]: 'name' must be",
        ),
        (
            "docs:\n  app:\n    - {name: ../up, server: s, jira: {jql: x}}\n",
            r"docs\[app\]\[0\]: 'name' must be",
        ),
        ("docs:\n  app:\n    - {name: a, jira: {jql: x}}\n", r"docs\[app\]\[0\]: 'server'"),
        ("docs:\n  app:\n    - {name: a, server: s}\n", "exactly one of 'confluence' or 'jira'"),
        (
            "docs:\n  app:\n    - {name: a, server: s, jira: {jql: x}, confluence: {roots: ['1']}}\n",
            "exactly one of 'confluence' or 'jira'",
        ),
        (
            "docs:\n  app:\n    - {name: a, server: s, confluence: {roots: []}}\n",
            "non-empty list of page ids",
        ),
        (
            "docs:\n  app:\n    - {name: a, server: s, confluence: {roots: ['1'], max_depth: -1}}\n",
            "'max_depth' must be an integer >= 0",
        ),
        (
            "docs:\n  app:\n    - {name: a, server: s, confluence: {roots: ['1'], max_doc: 5}}\n",
            r"unknown key\(s\) \['max_doc'\]",
        ),
        ("docs:\n  app:\n    - {name: a, server: s, jira: {jql: ''}}\n", "non-empty JQL"),
        (
            "docs:\n  app:\n    - {name: a, server: s, jira: {jql: x, max_issues: 0}}\n",
            "'max_issues' must be an integer >= 1",
        ),
        (
            "docs:\n  app:\n    - {name: a, server: s, jira: {jql: x}}\n"
            "    - {name: a, server: s, jira: {jql: y}}\n",
            r"docs\[app\]\[1\] repeats the source name 'a'",
        ),
        ("docs:\n  app:\n    - {name: a, server: s, wiki: {}}\n", r"unknown key\(s\) \['wiki'\]"),
    ],
)
def test_a_bad_docs_block_names_the_entry(tmp_path: Path, docs: str, message: str) -> None:
    with pytest.raises(RepoConfigError, match=message) as err:
        load_repo_config(_config(tmp_path, docs))
    assert "repos.yaml" in str(err.value)


def test_the_same_source_name_is_fine_in_two_repositories(tmp_path: Path) -> None:
    docs = """docs:
  app:
    - {name: wiki, server: s, confluence: {roots: ["1"]}}
  lib:
    - {name: wiki, server: s, confluence: {roots: ["2"]}}
"""
    repo_set = load_repo_config(_config(tmp_path, docs))
    assert [s.roots for s in repo_set.doc_sources("lib")] == [("2",)]
