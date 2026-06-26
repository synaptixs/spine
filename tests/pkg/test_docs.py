"""Doc-semantic layer: mention extraction, anchor binding, drift findings."""

from __future__ import annotations

from pathlib import Path

from orchestrator.pkg import (
    DocPage,
    DocReconciler,
    FactBatch,
    Node,
    NodeKind,
    Provenance,
)
from orchestrator.pkg.docs import MentionKind, extract_mentions


def _batch() -> FactBatch:
    b = FactBatch()
    b.add_node(
        Node(
            "py:billing.invoice",
            NodeKind.MODULE,
            "billing.invoice",
            "python",
            Provenance("src/billing/invoice.py", 1),
        )
    )
    b.add_node(
        Node(
            "py:billing.invoice.Invoice",
            NodeKind.TYPE,
            "Invoice",
            "python",
            Provenance("src/billing/invoice.py", 4),
        )
    )
    b.add_node(
        Node(
            "py:billing.invoice.Invoice.total",
            NodeKind.FUNCTION,
            "total",
            "python",
            Provenance("src/billing/invoice.py", 5),
        )
    )
    b.add_node(
        Node(
            "py:billing.tax.calc_tax",
            NodeKind.FUNCTION,
            "calc_tax",
            "python",
            Provenance("src/billing/tax.py", 1),
        )
    )
    return b


PAGE = DocPage(
    title="Billing design",
    text=(
        "The `Invoice` type computes totals; `calc_tax` applies regional rules.\n"
        "Entry point: billing.invoice.Invoice.total — see `src/billing/invoice.py`.\n"
        "GitHub and Python are mentioned in prose. The retry_handler covers errors.\n"
        "Legacy: `apply_discount` was removed last sprint.\n"
    ),
)


# ---- extraction -------------------------------------------------------------


def test_extraction_kinds() -> None:
    mentions = {m.text: m.kind for m in extract_mentions(PAGE)}
    assert mentions["Invoice"] is MentionKind.BACKTICK
    assert mentions["calc_tax"] is MentionKind.BACKTICK
    assert mentions["billing.invoice.Invoice.total"] is MentionKind.DOTTED
    assert mentions["src/billing/invoice.py"] is MentionKind.FILE
    assert mentions["retry_handler"] is MentionKind.SNAKE
    assert mentions["GitHub"] is MentionKind.CAMEL  # extracted, but can never drift


def test_backtick_takes_precedence_over_plain_match() -> None:
    page = DocPage(title="t", text="`calc_tax` and later calc_tax again")
    kinds = [m.kind for m in extract_mentions(page) if m.text == "calc_tax"]
    assert kinds == [MentionKind.BACKTICK]  # de-duplicated, backtick wins


# ---- binding ----------------------------------------------------------------


def test_bindings_resolve_symbols_paths_and_files() -> None:
    bindings, _ = DocReconciler(_batch()).reconcile([PAGE])
    by_text = {b.mention.text: b for b in bindings}

    assert by_text["Invoice"].anchor_ids == ["py:billing.invoice.Invoice"]
    assert by_text["calc_tax"].anchor_ids == ["py:billing.tax.calc_tax"]
    # dotted path binds via the id tail
    assert by_text["billing.invoice.Invoice.total"].anchor_ids == ["py:billing.invoice.Invoice.total"]
    # file mention binds against known provenance files
    assert by_text["src/billing/invoice.py"].anchor_files == ["src/billing/invoice.py"]


def test_unbound_code_claims_become_drift() -> None:
    _, drift = DocReconciler(_batch()).reconcile([PAGE])
    drifted = {f.mention for f in drift}
    assert "apply_discount" in drifted  # backticked, removed from code → drift
    assert "retry_handler" in drifted  # snake_case claim with no anchor → drift
    assert all("disagree" in f.message for f in drift)


def test_camelcase_prose_never_drifts() -> None:
    _, drift = DocReconciler(_batch()).reconcile([PAGE])
    assert "GitHub" not in {f.mention for f in drift}
    assert "Python" not in {f.mention for f in drift}


def test_fully_grounded_page_has_no_drift() -> None:
    page = DocPage(title="ok", text="`Invoice` totals are computed by `calc_tax`.")
    _, drift = DocReconciler(_batch()).reconcile([page])
    assert drift == []


def test_precision_rules_suppress_non_code_claims() -> None:
    page = DocPage(
        title="ops",
        text="Set `ONTOMESH_DB_URL` and run `pytest` on `develop`. The `retry_policy` is gone.",
    )
    _, drift = DocReconciler(_batch()).reconcile([page])
    drifted = {f.mention for f in drift}
    assert "ONTOMESH_DB_URL" not in drifted  # env var, not a symbol claim
    assert "pytest" not in drifted and "develop" not in drifted  # plain tool/branch words
    assert "retry_policy" in drifted  # underscore claim with no anchor → real drift


def test_file_mentions_bind_against_repo_root(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("# hi", encoding="utf-8")
    page = DocPage(title="t", text="See `README.md` and `MISSING.md`.")
    _, drift = DocReconciler(_batch(), repo_root=tmp_path).reconcile([page])
    drifted = {f.mention for f in drift}
    assert "README.md" not in drifted  # exists on disk → bound
    assert "MISSING.md" in drifted  # doc references a file that isn't there
