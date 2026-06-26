"""The brownfield config loader (pure parse — no clone/network)."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

_REPO = Path(__file__).resolve().parents[2]


def _load_script() -> ModuleType:
    """Import scripts/agentic_eval.py (with scripts/ + src/ on path)."""
    import sys

    for p in (str(_REPO / "src"), str(_REPO / "scripts")):
        if p not in sys.path:
            sys.path.insert(0, p)
    spec = importlib.util.spec_from_file_location("agentic_eval", _REPO / "scripts" / "agentic_eval.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_load_brownfield_builds_tasks_and_root(tmp_path: Path) -> None:
    mod = _load_script()
    doc = {
        "repo": str(tmp_path),
        "tickets": [
            {
                "key": "BF-1",
                "kind": "edit",
                "must_edit": ["src/x.py"],
                "spec": {"title": "t", "summary": "s", "acceptance_criteria": ["c"]},
            },
            {
                "key": "BF-2",
                "kind": "create",
                "spec": {"title": "u", "summary": "v", "acceptance_criteria": []},
            },
        ],
    }
    repo_root, tasks = mod.load_brownfield(doc)
    assert repo_root == tmp_path.resolve()
    assert [t.id for t in tasks] == ["BF-1", "BF-2"]
    assert [t.category for t in tasks] == ["edit", "create"]
    # payload carries a real benchmark Ticket the grader understands
    bf1 = tasks[0].payload["ticket"]
    assert bf1.must_edit == ["src/x.py"] and bf1.kind == "edit"
