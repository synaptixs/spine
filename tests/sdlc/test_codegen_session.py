"""`CodegenSession` sits in a leaf module so codegen and its in-loop tools need not import each other.

It lived in `agentic/codegen_tools.py`, which imports `sdlc/codegen.py`; codegen imported it back, which
CodeQL reads as a cycle (`py/cyclic-import`, alerts 313 and 314) and which blocks a develop -> main
promotion until resolved.
"""

from __future__ import annotations

import ast
from pathlib import Path

from orchestrator.agentic import codegen_tools
from orchestrator.sdlc import codegen, codegen_session


def test_the_session_is_one_class_whichever_module_it_is_imported_from() -> None:
    assert codegen_tools.CodegenSession is codegen_session.CodegenSession


def test_the_leaf_module_imports_nothing_from_orchestrator() -> None:
    tree = ast.parse(Path(codegen_session.__file__).read_text(encoding="utf-8"))
    imported = [
        node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.level == 0
    ] + [alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names]

    assert not [name for name in imported if name.split(".")[0] == "orchestrator"]


def test_codegen_does_not_import_its_tools_module_just_to_build_a_session() -> None:
    tree = ast.parse(Path(codegen.__file__).read_text(encoding="utf-8"))
    names = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and node.names
        and any(a.name == "CodegenSession" for a in node.names)
    }

    assert names == {"orchestrator.sdlc.codegen_session"}
