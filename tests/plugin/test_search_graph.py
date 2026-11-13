"""The ``search_graph`` MCP tool (D5, D20): free text in, ranked code out, each hit saying what found it."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

pytest.importorskip("mcp")

from orchestrator.plugin.server import _TIER, LEVELS, search_graph  # noqa: E402


def _repo(tmp_path: Path) -> str:
    (tmp_path / "billing.py").write_text(
        "def persist(record, path):\n"
        '    """Append one row to the accounting ledger."""\n'
        "    audit(record)\n\n\n"
        "def audit(record):\n    return record\n"
    )
    (tmp_path / "README.md").write_text("# Billing\n\n## Saving rows\n\nCall `persist` to save a row.\n")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    return str(tmp_path)


def test_it_ranks_by_content_and_graph_and_says_how(tmp_path: Path) -> None:
    out = search_graph(_repo(tmp_path), "rows lost from the accounting ledger")
    assert out["legs_used"] == ["name", "content", "graph"]
    first = out["symbols"][0]
    assert first["name"] == "persist" and "content" in first["legs"]
    audit = next(s for s in out["symbols"] if s["name"] == "audit")
    assert "graph via persist" in audit["found_by"]
    assert out["files"][0] == "billing.py"


def test_docs_come_back_beside_the_code_never_as_symbols(tmp_path: Path) -> None:
    out = search_graph(_repo(tmp_path), "saving rows to the accounting ledger")
    assert all(not s["id"].startswith("doc:") for s in out["symbols"])
    assert out["doc_count"] >= 1 and any("README.md" in d["doc"] for d in out["docs"])


def test_it_needs_a_query_and_a_repo(tmp_path: Path) -> None:
    assert "error" in search_graph(_repo(tmp_path), "   ")
    assert "error" in search_graph("", "ledger")


def test_it_is_a_read_only_comprehension_tool_at_the_first_level() -> None:
    assert _TIER["search_graph"].name in LEVELS["pkg"]
