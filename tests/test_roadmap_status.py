"""The roadmap-currency gate must itself keep finding what it checks.

Every check reads a fixture tree under ``tmp_path`` rather than this repo's own
``docs/specs/`` (except the one test that runs the gate for real, matching what CI does).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "roadmap-status.py"


@pytest.fixture
def status(tmp_path: Path) -> ModuleType:
    """The script as a module, pointed at a fixture tree.

    Registered in ``sys.modules`` before exec, the same reason ``test_state_numbers.py``
    does it: the script defines a ``@dataclass`` under ``from __future__ import
    annotations``, and dataclasses resolves those string annotations through
    ``sys.modules[cls.__module__]`` — without the registration that lookup returns
    ``None`` and the class body raises.
    """
    import sys

    spec = importlib.util.spec_from_file_location("roadmap_status", _SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["roadmap_status"] = mod
    spec.loader.exec_module(mod)
    mod.set_root(tmp_path)
    (tmp_path / "docs" / "specs").mkdir(parents=True, exist_ok=True)
    return mod


def _write(root: Path, name: str, body: str) -> Path:
    p = root / "docs" / "specs" / name
    p.write_text(body, encoding="utf-8")
    return p


_HEADER = "| Phase | Work | Effort | Exit criteria | Status | Started | Finished | Evidence |"
_SEP = "|---|---|---|---|---|---|---|---|"


def test_this_repos_own_roadmaps_pass(status: ModuleType) -> None:
    """Dogfooding: the gate run against the real tree, the same thing CI runs."""
    import sys

    real_root = Path(__file__).resolve().parents[1]
    status.set_root(real_root)
    try:
        assert status.check() == []
    finally:
        sys.modules.pop("roadmap_status", None)


def test_table_is_found_by_its_exact_header_not_any_bold_phase_looking_row(status: ModuleType) -> None:
    """A decisions table (`| # | Decision | Options | Recommendation |`) also has rows
    starting `| **C1** |` — it must not be mistaken for the phase table."""
    doc = _write(
        status.ROOT,
        "x-roadmap.md",
        "| # | Decision | Options | Recommendation |\n"
        "|---|---|---|---|\n"
        "| **C1** | thing | a, b | a |\n\n"
        f"{_HEADER}\n{_SEP}\n"
        "| **P1 Comprehension** | work | 1d | exit | 🟡 | 2026-01-01 | | some evidence |\n",
    )
    tables = status.phase_tables()
    assert list(tables) == [doc]
    assert len(tables[doc]) == 1
    assert tables[doc][0].phase_id == "P1"


def test_done_without_evidence_fails(status: ModuleType) -> None:
    _write(
        status.ROOT,
        "x-roadmap.md",
        f"{_HEADER}\n{_SEP}\n| **P1 Thing** | work | 1d | exit | ✅ | 2026-01-01 | 2026-01-02 |  |\n",
    )
    problems = status.check_evidence_completeness(status.phase_tables())
    assert len(problems) == 1
    assert "P1" in problems[0] and "DONE" in problems[0]


def test_done_without_finished_date_fails(status: ModuleType) -> None:
    _write(
        status.ROOT,
        "x-roadmap.md",
        f"{_HEADER}\n{_SEP}\n| **P1 Thing** | work | 1d | exit | ✅ | 2026-01-01 |  | some evidence |\n",
    )
    problems = status.check_evidence_completeness(status.phase_tables())
    assert len(problems) == 1


def test_done_with_both_passes(status: ModuleType) -> None:
    _write(
        status.ROOT,
        "x-roadmap.md",
        f"{_HEADER}\n{_SEP}\n"
        "| **P1 Thing** | work | 1d | exit | ✅ | 2026-01-01 | 2026-01-02 | commit abc123 |\n",
    )
    assert status.check_evidence_completeness(status.phase_tables()) == []


def test_partial_status_is_never_flagged_for_missing_evidence(status: ModuleType) -> None:
    """Only DONE demands receipts — a started-but-not-done row is exactly what most rows
    in this repo's own roadmaps look like today."""
    _write(
        status.ROOT,
        "x-roadmap.md",
        f"{_HEADER}\n{_SEP}\n| **P1 Thing** | work | 1d | exit | 🟡 | 2026-01-01 |  |  |\n",
    )
    assert status.check_evidence_completeness(status.phase_tables()) == []


def test_finished_before_started_is_flagged(status: ModuleType) -> None:
    doc = _write(
        status.ROOT,
        "x-roadmap.md",
        f"{_HEADER}\n{_SEP}\n| **P1 Thing** | work | 1d | exit | ✅ | 2026-01-05 | 2026-01-01 | evidence |\n",
    )
    problems = status.check_started_before_finished(status.phase_tables())
    assert len(problems) == 1
    assert doc.name in problems[0] and "P1" in problems[0]


def test_finished_on_or_after_started_passes(status: ModuleType) -> None:
    _write(
        status.ROOT,
        "x-roadmap.md",
        f"{_HEADER}\n{_SEP}\n"
        "| **P1 Same day** | work | 1d | exit | ✅ | 2026-01-01 | 2026-01-01 | evidence |\n"
        "| **P2 Later** | work | 1d | exit | ✅ | 2026-01-01 | 2026-01-05 | evidence |\n",
    )
    assert status.check_started_before_finished(status.phase_tables()) == []


def test_non_date_started_or_finished_is_left_alone(status: ModuleType) -> None:
    """Anything that isn't a plain YYYY-MM-DD in both cells (a note, a range, empty) is
    never guessed at — only the exact, already-established convention is checked."""
    _write(
        status.ROOT,
        "x-roadmap.md",
        f"{_HEADER}\n{_SEP}\n"
        "| **P1 Thing** | work | 1d | exit | 🟡 | 2026-01-05 |  |  |\n"
        "| **P2 Other** | work | 1d | exit | 🟡 | ongoing | see below |  |\n",
    )
    assert status.check_started_before_finished(status.phase_tables()) == []


def test_dependency_blocks_a_started_phase_until_the_dependency_is_done(status: ModuleType) -> None:
    _write(
        status.ROOT,
        "base.md",
        f"{_HEADER}\n{_SEP}\n| **P2 Base thing** | work | 1d | exit | 🟡 | 2026-01-01 |  | evidence |\n",
    )
    _write(
        status.ROOT,
        "dependent.md",
        "**Depends on:** [base.md](base.md) merged (P2 for the graph).\n\n"
        f"{_HEADER}\n{_SEP}\n| **C-1 Thing** | work | 1d | exit | 🟡 | 2026-01-01 |  | evidence |\n",
    )
    problems = status.check_cross_spec_dependency(status.phase_tables())
    assert len(problems) == 1
    assert "base.md" in problems[0] and "P2" in problems[0]


def test_dependency_is_silent_before_anything_starts(status: ModuleType) -> None:
    _write(
        status.ROOT,
        "base.md",
        f"{_HEADER}\n{_SEP}\n| **P2 Base thing** | work | 1d | exit | ⬜ |  |  |  |\n",
    )
    _write(
        status.ROOT,
        "dependent.md",
        "**Depends on:** [base.md](base.md) merged (P2 for the graph).\n\n"
        f"{_HEADER}\n{_SEP}\n| **C-1 Thing** | work | 1d | exit | ⬜ |  |  |  |\n",
    )
    assert status.check_cross_spec_dependency(status.phase_tables()) == []


def test_dependency_passes_once_satisfied(status: ModuleType) -> None:
    _write(
        status.ROOT,
        "base.md",
        f"{_HEADER}\n{_SEP}\n"
        "| **P2 Base thing** | work | 1d | exit | ✅ | 2026-01-01 | 2026-01-02 | commit x |\n",
    )
    _write(
        status.ROOT,
        "dependent.md",
        "**Depends on:** [base.md](base.md) merged (P2 for the graph).\n\n"
        f"{_HEADER}\n{_SEP}\n| **C-1 Thing** | work | 1d | exit | 🟡 | 2026-01-01 |  | evidence |\n",
    )
    assert status.check_cross_spec_dependency(status.phase_tables()) == []


def test_stale_header_against_a_populated_table_fails(status: ModuleType) -> None:
    doc = _write(
        status.ROOT,
        "x-roadmap.md",
        "**Status:** Proposed — plan for review, no code written.\n\n"
        f"{_HEADER}\n{_SEP}\n| **P1 Thing** | work | 1d | exit | 🟡 | 2026-01-01 |  | real evidence here |\n",
    )
    problems = status.check_top_status_freshness(status.phase_tables())
    assert len(problems) == 1
    assert doc.name in problems[0]


def test_fresh_header_against_a_populated_table_passes(status: ModuleType) -> None:
    _write(
        status.ROOT,
        "x-roadmap.md",
        "**Status:** In progress — P1 landed.\n\n"
        f"{_HEADER}\n{_SEP}\n| **P1 Thing** | work | 1d | exit | 🟡 | 2026-01-01 |  | real evidence here |\n",
    )
    assert status.check_top_status_freshness(status.phase_tables()) == []


def test_unindexed_roadmap_fails(status: ModuleType) -> None:
    _write(status.ROOT, "SPEC-INDEX.md", "nothing here mentions the other file\n")
    _write(
        status.ROOT,
        "x-roadmap.md",
        f"{_HEADER}\n{_SEP}\n| **P1 Thing** | work | 1d | exit | ⬜ |  |  |  |\n",
    )
    problems = status.check_indexed(status.phase_tables())
    assert len(problems) == 1
    assert "x-roadmap.md" in problems[0]


def test_indexed_roadmap_passes(status: ModuleType) -> None:
    _write(status.ROOT, "SPEC-INDEX.md", "See [x-roadmap](x-roadmap.md) for details.\n")
    _write(
        status.ROOT,
        "x-roadmap.md",
        f"{_HEADER}\n{_SEP}\n| **P1 Thing** | work | 1d | exit | ⬜ |  |  |  |\n",
    )
    assert status.check_indexed(status.phase_tables()) == []


def test_broken_relative_link_fails(status: ModuleType) -> None:
    _write(
        status.ROOT,
        "x-roadmap.md",
        "See [missing](does-not-exist.md) for the rule.\n\n"
        f"{_HEADER}\n{_SEP}\n| **P1 Thing** | work | 1d | exit | ⬜ |  |  |  |\n",
    )
    problems = status.check_relative_links(status.phase_tables())
    assert len(problems) == 1
    assert "does-not-exist.md" in problems[0]


def test_working_relative_link_and_bare_urls_pass(status: ModuleType) -> None:
    _write(status.ROOT, "other.md", "# Other\n")
    _write(
        status.ROOT,
        "x-roadmap.md",
        "See [other](other.md) and [github](https://github.com) and #anchor-only.\n\n"
        f"{_HEADER}\n{_SEP}\n| **P1 Thing** | work | 1d | exit | ⬜ |  |  |  |\n",
    )
    assert status.check_relative_links(status.phase_tables()) == []


def test_no_phase_tables_is_not_an_error(status: ModuleType) -> None:
    _write(status.ROOT, "unrelated.md", "# Nothing to see\n\nJust prose, no table here.\n")
    assert status.check() == []


def test_header_found_but_only_row_malformed_is_reported_not_silently_dropped(
    status: ModuleType,
) -> None:
    """A document whose header matches exactly but whose only data row has the wrong
    column count (a dropped `|`) used to vanish from `tables` with no diagnostic at all —
    every other check silently skipped it. Now reports both the zero-rows fact and the
    specific malformed row."""
    doc = _write(
        status.ROOT,
        "x-roadmap.md",
        f"{_HEADER}\n{_SEP}\n| **P1 Thing** | work | 1d | exit | ⬜ |  |  |\n",  # 7 cells, not 8
    )
    tables = status.phase_tables()
    assert doc not in tables  # confirms the silent-drop this check exists to catch

    problems = status.check_header_found_but_unparsed(tables)
    assert len(problems) == 2
    assert all(doc.name in p for p in problems)
    assert any("no row after it parsed" in p for p in problems)
    assert any("was skipped" in p for p in problems)


def test_a_malformed_row_mid_table_is_skipped_not_truncating(status: ModuleType) -> None:
    """The bug found live in this repo's own roadmap: a literal `|` inside a cell's own
    prose (quoting an example table row) split that one row into 9 cells. The old
    behavior treated any non-8-cell row as the end of the table, silently dropping every
    row after it — P5 and P6 vanished with zero rows parsed wrong and zero diagnostics.
    A malformed row must be skipped, not treated as end-of-table, so rows after it still
    get collected — and the skip itself must still be reported."""
    doc = _write(
        status.ROOT,
        "x-roadmap.md",
        f"{_HEADER}\n{_SEP}\n"
        "| **P1 Good** | work | 1d | exit | ⬜ |  |  |  |\n"
        # 9 cells: an extra "|" inside the evidence prose, e.g. quoting `| **C1** |`.
        "| **P2 Bad** | work | 1d | exit | ⬜ |  |  | quoting `| x |` here |\n"
        "| **P3 Good** | work | 1d | exit | ⬜ |  |  |  |\n",
    )
    tables = status.phase_tables()
    assert [r.phase_id for r in tables[doc]] == ["P1", "P3"]  # P2 skipped, P3 still reached

    problems = status.check_header_found_but_unparsed(tables)
    assert len(problems) == 1
    assert "was skipped" in problems[0]
    assert "P2 Bad" in problems[0]


def test_header_found_and_parsed_is_not_reported(status: ModuleType) -> None:
    _write(
        status.ROOT,
        "x-roadmap.md",
        f"{_HEADER}\n{_SEP}\n| **P1 Thing** | work | 1d | exit | ⬜ |  |  |  |\n",
    )
    assert status.check_header_found_but_unparsed(status.phase_tables()) == []
