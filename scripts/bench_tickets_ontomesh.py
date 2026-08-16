"""Benchmark tickets authored against `synaptixs/ontomesh` — an external repository.

**Why these exist.** The 200-run result in `docs/specs/codegen-model-comparison-results.md`
was measured on Spine's own code, with tickets Spine wrote and grounding tuned on this
repository. It is a ceiling, not a capability. These tickets move the measurement to a
codebase Spine did not write, so the claim can drop *"measured on this repository"*.

**Selection, and what was rejected.** Every ticket targets `db_introspector` or
`log_templates`, chosen because both import cleanly with only `src` on the path.
`causality_dag` was rejected despite being richer material — it needs `numpy`, which the
benchmark's interpreter does not have, so its held-out suites could never run. A ticket whose
grader cannot import is not a hard ticket, it is a broken one.

**Shape.** Two `edit` tickets name their target file; three `create` tickets do not, and each
must import a real ontomesh type to be correct. That split is the experiment: on Spine,
grounding changed nothing for `edit` (98/100 either way) and everything for `create` (0/50
ungrounded). If the same asymmetry appears on a codebase Spine did not write, the mechanism
is not Spine-specific.

**The gate is weaker here.** ontomesh has no `[tool.mypy]` section, so mypy cannot run and is
excluded (reported by `capture_baseline`). Acceptance is tests + ruff + fit. Its numbers are
therefore *not* comparable to the Spine run's three-tool gate.

Usage:
    BENCH_REPO=/path/to/ontomesh EVAL_TASKSET=ontomesh \\
        SDLC_CODEGEN_MODEL=claude-opus-5 uv run python scripts/codegen_benchmark.py
"""

from __future__ import annotations

# --- held-out suites -------------------------------------------------------------------
#
# The model never sees these. They import ontomesh's real modules the same way ontomesh's own
# tests do — flat, with `src` on the path — which is what `run_held_out_tests` provides.

_HO_ONTO_KEBAB = """
from db_introspector import snake_to_kebab


def test_basic_conversion():
    assert snake_to_kebab("asset_type_id") == "asset-type-id"


def test_single_word_unchanged():
    assert snake_to_kebab("assets") == "assets"


def test_empty_string():
    assert snake_to_kebab("") == ""


def test_does_not_capitalise():
    # snake_to_camel capitalises; this one must not.
    assert snake_to_kebab("domain_events") == "domain-events"


def test_collapses_repeated_underscores():
    assert "--" not in snake_to_kebab("a__b")
"""

_HO_ONTO_RATIO = """
from log_templates import levenshtein_ratio


def test_identical_is_one():
    assert levenshtein_ratio("abc", "abc") == 1.0


def test_disjoint_is_zero_ish():
    assert levenshtein_ratio("abc", "xyz") < 0.5


def test_both_empty_is_one():
    assert levenshtein_ratio("", "") == 1.0


def test_symmetric():
    assert levenshtein_ratio("kitten", "sitting") == levenshtein_ratio("sitting", "kitten")


def test_bounded():
    for a, b in [("a", ""), ("", "b"), ("hello", "hallo"), ("x", "xxxxxxxx")]:
        r = levenshtein_ratio(a, b)
        assert 0.0 <= r <= 1.0
"""

_HO_FIND = '''
import importlib
import pkgutil


def _find(func_name):
    """Locate a function the model placed somewhere on the src path."""
    import sys
    for entry in list(sys.path):
        try:
            mods = [m.name for m in pkgutil.iter_modules([entry])]
        except Exception:
            continue
        for name in mods:
            try:
                mod = importlib.import_module(name)
            except Exception:
                continue
            fn = getattr(mod, func_name, None)
            if callable(fn):
                return fn
    raise AssertionError(f"{func_name} not found on the src path")
'''

_HO_ONTO_COLMD = (
    _HO_FIND
    + """
from db_introspector import ColumnModel


def _cols():
    return [
        ColumnModel(table_name="assets", name="asset_id", sqlite_type="INTEGER", is_pk=True,
                    is_fk=False, fk_references=None, not_null=True, default_value=None),
        ColumnModel(table_name="assets", name="owner_id", sqlite_type="INTEGER", is_pk=False,
                    is_fk=True, fk_references="people.id", not_null=False, default_value=None),
    ]


def test_returns_non_empty_markdown():
    out = _find("render_columns_markdown")(_cols())
    assert isinstance(out, str) and out.strip()


def test_names_every_column():
    out = _find("render_columns_markdown")(_cols())
    assert "asset_id" in out and "owner_id" in out


def test_marks_the_primary_key():
    out = _find("render_columns_markdown")(_cols())
    assert "asset_id" in out and out.count("|") > 4  # rendered as a table


def test_handles_an_empty_list():
    assert isinstance(_find("render_columns_markdown")([]), str)
"""
)

_HO_ONTO_TABLESUM = (
    _HO_FIND
    + """
from db_introspector import ColumnModel, TableModel


def _table():
    cols = [
        ColumnModel(table_name="assets", name="asset_id", sqlite_type="INTEGER", is_pk=True,
                    is_fk=False, fk_references=None, not_null=True, default_value=None),
        ColumnModel(table_name="assets", name="owner_id", sqlite_type="INTEGER", is_pk=False,
                    is_fk=True, fk_references="people.id", not_null=False, default_value=None),
        ColumnModel(table_name="assets", name="note", sqlite_type="TEXT", is_pk=False,
                    is_fk=False, fk_references=None, not_null=False, default_value=None),
    ]
    return TableModel(name="assets", columns=cols, pk_columns=["asset_id"],
                      fk_map={"owner_id": "people"})


def test_returns_a_string():
    assert isinstance(_find("summarise_table")(_table()), str)


def test_reports_the_column_count():
    assert "3" in _find("summarise_table")(_table())


def test_names_the_table():
    assert "assets" in _find("summarise_table")(_table())


def test_counts_keys():
    out = _find("summarise_table")(_table())
    assert "1" in out  # one pk and one fk
"""
)

_HO_ONTO_TEMPLATEMD = (
    _HO_FIND
    + """
from log_templates import Template


def _templates():
    return [
        Template(id=1, cluster_id=7, template="user <*> logged in", sample_line="user bob logged in",
                 hits=12, severity="INFO", service="auth"),
        Template(id=2, cluster_id=9, template="disk <*> full", sample_line="disk /dev/sda full",
                 hits=3, severity="ERROR", service="node"),
    ]


def test_returns_non_empty_markdown():
    out = _find("render_templates_markdown")(_templates())
    assert isinstance(out, str) and out.strip()


def test_includes_each_template():
    out = _find("render_templates_markdown")(_templates())
    assert "logged in" in out and "full" in out


def test_reports_hit_counts():
    out = _find("render_templates_markdown")(_templates())
    assert "12" in out and "3" in out


def test_handles_an_empty_list():
    assert isinstance(_find("render_templates_markdown")([]), str)
"""
)


def build(ticket_cls):  # type: ignore[no-untyped-def]
    """Return the ontomesh task set. Takes the `Ticket` class to avoid a circular import."""
    return [
        # ---- edit: the target file is named, so grounding should NOT matter -------------
        ticket_cls(
            key="EDIT-ONTO-KEBAB-1",
            kind="edit",
            must_edit=["src/db_introspector.py"],
            held_out_tests={"test_kebab_ho.py": _HO_ONTO_KEBAB},
            spec={
                "title": "snake_to_kebab identifier helper",
                "summary": (
                    "Extend the existing module src/db_introspector.py with a module-level "
                    "function snake_to_kebab(s: str) -> str that converts a snake_case SQL "
                    "identifier to kebab-case: 'asset_type_id' becomes 'asset-type-id'. It sits "
                    "alongside the existing snake_to_camel / snake_to_lower_camel / "
                    "snake_to_label helpers and must follow their style. Unlike those, it does "
                    "NOT capitalise anything. Repeated underscores must not produce repeated "
                    "hyphens. Modify that existing file; do not create a new module."
                ),
                "acceptance_criteria": [
                    "snake_to_kebab('asset_type_id') == 'asset-type-id'",
                    "a single word is returned unchanged",
                    "the empty string returns the empty string",
                    "no capitalisation is applied",
                ],
            },
        ),
        ticket_cls(
            key="EDIT-ONTO-RATIO-1",
            kind="edit",
            must_edit=["src/log_templates.py"],
            held_out_tests={"test_ratio_ho.py": _HO_ONTO_RATIO},
            spec={
                "title": "levenshtein_ratio similarity helper",
                "summary": (
                    "Extend the existing module src/log_templates.py with a module-level "
                    "function levenshtein_ratio(a: str, b: str) -> float returning a normalised "
                    "similarity in [0.0, 1.0]: 1.0 for identical strings, lower as they diverge. "
                    "REUSE the existing private _levenshtein helper in that module rather than "
                    "reimplementing the distance. Two empty strings are identical, so the result "
                    "is 1.0 (do not divide by zero). The function must be symmetric. Modify that "
                    "existing file; do not create a new module."
                ),
                "acceptance_criteria": [
                    "identical strings return 1.0",
                    "two empty strings return 1.0 rather than raising",
                    "the result is always within [0.0, 1.0]",
                    "levenshtein_ratio(a, b) == levenshtein_ratio(b, a)",
                    "the existing _levenshtein is reused, not duplicated",
                ],
            },
        ),
        # ---- create: no path given, so the model must find what already exists ---------
        ticket_cls(
            key="NEW-ONTO-COLMD-1",
            kind="create",
            held_out_tests={"test_colmd_ho.py": _HO_ONTO_COLMD},
            spec={
                "title": "Render database columns as a Markdown table",
                "summary": (
                    "Add a new module exposing render_columns_markdown(columns) that renders a "
                    "list of ontomesh ColumnModel objects as a Markdown table: one row per "
                    "column with its name, type, and whether it is a primary or foreign key. "
                    "Reuse the existing ColumnModel type rather than defining a new one."
                ),
                "acceptance_criteria": [
                    "returns a non-empty Markdown string",
                    "every column name appears",
                    "primary and foreign keys are distinguishable",
                    "an empty list yields a string rather than raising",
                ],
            },
        ),
        ticket_cls(
            key="NEW-ONTO-TABLESUM-1",
            kind="create",
            held_out_tests={"test_tablesum_ho.py": _HO_ONTO_TABLESUM},
            spec={
                "title": "Summarise a table model in one line",
                "summary": (
                    "Add a new module exposing summarise_table(table) that turns an ontomesh "
                    "TableModel into a short plain-text summary: the table name, how many "
                    "columns it has, and how many are primary and foreign keys. Reuse the "
                    "existing TableModel and ColumnModel types."
                ),
                "acceptance_criteria": [
                    "returns a string naming the table",
                    "reports the column count",
                    "reports primary-key and foreign-key counts",
                ],
            },
        ),
        ticket_cls(
            key="NEW-ONTO-TEMPLATEMD-1",
            kind="create",
            held_out_tests={"test_templatemd_ho.py": _HO_ONTO_TEMPLATEMD},
            spec={
                "title": "Render mined log templates as a Markdown report",
                "summary": (
                    "Add a new module exposing render_templates_markdown(templates) that renders "
                    "a list of ontomesh Template objects (the log-template miner's output) as a "
                    "Markdown report: the template string, its hit count, and its severity and "
                    "service where present. Reuse the existing Template type."
                ),
                "acceptance_criteria": [
                    "returns a non-empty Markdown string",
                    "each template string appears",
                    "hit counts are reported",
                    "an empty list yields a string rather than raising",
                ],
            },
        ),
    ]
