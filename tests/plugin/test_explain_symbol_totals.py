"""``explain_symbol`` says how many it dropped, and the doc radius walks every owner (B59, B41).

``explain_symbol`` cut ``called_by`` / ``called_through_interface`` / ``calls`` / ``instantiated_via_type``
to 15 and ``contains`` to 25 with no total, so a symbol with 40 callers read as having 15 — the same
"clipped view implies completeness" defect ``blast_radius`` avoids with ``caller_count`` (CLAUDE.md
invariant 7). Beside it, the doc radius skipped an ORM entity as an owner and kept one parent per child.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from orchestrator.plugin.server import explain_symbol


def _write(root: Path, name: str, text: str) -> None:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _callers_repo(tmp_path: Path, callers: int) -> str:
    _write(tmp_path, "core.py", "def target():\n    return 1\n")
    for i in range(callers):
        _write(tmp_path, f"user{i}.py", f"import core\n\n\ndef use{i}():\n    return core.target()\n")
    return str(tmp_path)


def test_a_symbol_with_more_callers_than_the_cap_reports_the_true_count(tmp_path: Path) -> None:
    m = explain_symbol(_callers_repo(tmp_path, 40), "target")["matches"][0]
    assert len(m["called_by"]) == 15
    assert m["called_by_count"] == 40


def test_a_symbol_within_the_cap_reports_a_count_equal_to_its_list(tmp_path: Path) -> None:
    m = explain_symbol(_callers_repo(tmp_path, 3), "target")["matches"][0]
    assert len(m["called_by"]) == m["called_by_count"] == 3
    assert m["called_through_interface"] == [] and m["called_through_interface_count"] == 0


def test_calls_and_contains_report_their_totals_too(tmp_path: Path) -> None:
    funcs = "".join(f"def f{i}():\n    return {i}\n\n\n" for i in range(20))
    fan_out = "def many():\n" + "".join(f"    f{i}()\n" for i in range(20)) + "    return 0\n"
    _write(tmp_path, "fan.py", funcs + fan_out)
    methods = "class Big:\n" + "".join(f"    def m{i}(self):\n        return {i}\n\n" for i in range(30))
    _write(tmp_path, "big.py", methods)

    many = explain_symbol(str(tmp_path), "many")["matches"][0]
    assert len(many["calls"]) == 15 and many["calls_count"] == 20

    big = explain_symbol(str(tmp_path), "py:big.Big")["matches"][0]
    assert len(big["contains"]) == 25 and big["contains_count"] == 30


def test_a_constructor_reports_the_total_who_create_its_type(tmp_path: Path) -> None:
    pytest.importorskip("tree_sitter_java", reason="install the 'java' extra")
    _write(tmp_path, "Job.java", "package a;\npublic class Job { public Job() { } }\n")
    for i in range(20):
        _write(tmp_path, f"U{i}.java", f"package a;\npublic class U{i} {{ void go() {{ new Job(); }} }}\n")

    ctor = {m["id"]: m for m in explain_symbol(str(tmp_path), "Job")["matches"]}["java:a.Job.Job"]

    assert len(ctor["instantiated_via_type"]) == 15
    assert ctor["instantiated_via_type_count"] == 20


# ---- the doc radius (B41 items 1 and 2) ---------------------------------------


def test_a_column_inherits_the_docs_of_its_orm_entity(tmp_path: Path) -> None:
    """Docs bind to `py:entity:users`; the owner walk took only TYPE and MODULE, so none reached a column."""
    _write(
        tmp_path,
        "models.py",
        "from sqlalchemy import Column, Integer, String\n"
        "from sqlalchemy.orm import declarative_base\n\n"
        "Base = declarative_base()\n\n\n"
        "class User(Base):\n"
        '    __tablename__ = "users"\n'
        "    id = Column(Integer, primary_key=True)\n"
        "    email = Column(String)\n",
    )
    _write(tmp_path, "README.md", "# Accounts\n\nThe `users` table holds every account.\n")

    m = explain_symbol(str(tmp_path), "py:entity:users.id")["matches"][0]

    assert [(d["doc"], d["via"]) for d in m["docs"]] == [("README.md#accounts", "class")]


def test_a_function_declared_in_a_header_and_defined_in_a_source_has_both_files_docs(
    tmp_path: Path,
) -> None:
    """`subtotal` sits under `src/cart.h` and `src/cart.c` (two CONTAINS parents); one was dropped."""
    _write(tmp_path, "src/cart.h", "int subtotal(int a);\n")
    _write(tmp_path, "src/cart.c", '#include "cart.h"\nint subtotal(int a) { return a; }\n')
    _write(
        tmp_path,
        "NOTES.md",
        "# Header\n\nThe header `src/cart.h` declares the API.\n\n"
        "## Impl\n\nThe implementation lives in `src/cart.c`.\n",
    )

    m = explain_symbol(str(tmp_path), "subtotal")["matches"][0]

    assert sorted(d["doc"] for d in m["docs"]) == ["NOTES.md#header", "NOTES.md#impl"]
    assert {d["via"] for d in m["docs"]} == {"module"}


def test_a_doc_naming_only_the_source_file_is_not_missed(tmp_path: Path) -> None:
    _write(tmp_path, "src/cart.h", "int subtotal(int a);\n")
    _write(tmp_path, "src/cart.c", '#include "cart.h"\nint subtotal(int a) { return a; }\n')
    _write(tmp_path, "NOTES.md", "# Impl\n\nThe implementation lives in `src/cart.c`.\n")

    m = explain_symbol(str(tmp_path), "subtotal")["matches"][0]

    assert [d["doc"] for d in m["docs"]] == ["NOTES.md#impl"]
