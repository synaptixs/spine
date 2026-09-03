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
from orchestrator.pkg.docs import DocBinding, MentionKind, extract_mentions


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


# ---- a dotted filename is not a symbol path (doc-binding audit, 2026-09-01) --------


def test_a_filename_is_not_drift(tmp_path: Path) -> None:
    """`md.js` is a file, and the drift list was calling it a missing symbol.

    Dotted, lowercase, multi-segment tokens are shaped exactly like symbol paths, so
    filenames match nothing and arrive as claims the prose made and the code failed to
    support. Measured 2026-09-01: 55 of them, against zero `MENTIONS` edges either way —
    drift precision, not binding coverage.
    """
    from orchestrator.pkg.extractor import RepoCodeExtractor

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "m.py").write_text("X = 1\n", encoding="utf-8")
    rec = DocReconciler(RepoCodeExtractor().extract(repo), repo_root=repo)
    page = DocPage(
        title="x",
        text="Rendered by `md.js` and `graph.html`, brought up with compose.dev.yml.",
    )
    _bindings, drift = rec.reconcile([page])
    assert [d.mention for d in drift] == []


def test_the_leaf_only_fallback_still_rescues_a_qualified_name(tmp_path: Path) -> None:
    """Prose writes `store.find`; the symbol is `FactStore.find`. That binding is deliberate.

    `bind`'s last resort matches the final segment alone, which looks reckless enough to
    delete on sight. It was audited on 2026-09-01: it rescues 76 mentions on this repository
    and mis-binds no filename. This pins it so the audit does not have to be repeated.
    """
    from orchestrator.pkg.extractor import RepoCodeExtractor

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "m.py").write_text("class FactStore:\n    def find(self):\n        return 1\n", encoding="utf-8")
    rec = DocReconciler(RepoCodeExtractor().extract(repo), repo_root=repo)
    page = DocPage(title="x", text="Call `store.find`.")
    (mention,) = [m for m in extract_mentions(page) if m.text == "store.find"]
    assert rec.bind(mention, base_dir="").anchor_ids == ["py:m.FactStore.find"]


# ---- a cited path names its module (doc-file-binding, 2026-09-02) ------------


def test_a_cited_path_binds_to_the_module_built_from_it(tmp_path: Path) -> None:
    """The fact was already in the graph and was being discarded.

    `bind` recorded the hit in `anchor_files`; `link_docs` emits only for `anchor_ids`. The
    path maps to exactly one `Module` node by the extractor's own provenance — not a guess.
    Measured 2026-09-01: 938 such mentions, and 108 `Doc` sections that bound to nothing.
    """
    from orchestrator.pkg.doc_link import link_docs
    from orchestrator.pkg.extractor import RepoCodeExtractor
    from orchestrator.pkg.facts import EdgeKind

    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "store.py").write_text("def find():\n    return 1\n", encoding="utf-8")
    (repo / "README.md").write_text("# Guide\n\nThe store lives in `src/store.py`.\n", encoding="utf-8")

    batch = link_docs(RepoCodeExtractor().extract(repo), repo)
    targets = {e.dst for e in batch.edges if e.kind is EdgeKind.MENTIONS}
    assert any(t.endswith("store") for t in targets), targets


def test_a_path_owned_by_two_modules_binds_to_neither() -> None:
    """Exactly one, or nothing — the same rule that holds `CALLS` precision at 1.00."""
    b = FactBatch()
    for mid in ("c:alpha", "c:beta"):
        b.add_node(Node(mid, NodeKind.MODULE, mid.split(":")[1], "c", Provenance("src/shared.h", 1)))
    rec = DocReconciler(b)
    page = DocPage(title="t", text="See `src/shared.h`.")
    (mention,) = [m for m in extract_mentions(page) if m.text == "src/shared.h"]
    assert rec.bind(mention, base_dir="").anchor_ids == []


def test_a_path_matching_several_files_binds_to_nothing() -> None:
    """Ambiguous about *which file* before it is ambiguous about which module.

    `_files` is a set, so the matching comprehension yielded hash order. Nothing read that
    order until file mentions began naming a module — and then `anchor_files[0]` picked a
    different file per process, so the graph changed between runs of the same commit. Caught
    by varying `PYTHONHASHSEED`; the count moved 3392/3394/3390 and is now fixed.
    """
    b = FactBatch()
    for path in ("src/a/store.py", "src/b/store.py"):
        mid = "py:" + path.replace("/", ".").removesuffix(".py")
        b.add_node(Node(mid, NodeKind.MODULE, mid, "python", Provenance(path, 1)))
    rec = DocReconciler(b)
    page = DocPage(title="t", text="See `store.py`.")
    (mention,) = [m for m in extract_mentions(page) if m.text == "store.py"]
    binding = rec.bind(mention, base_dir="")
    assert binding.anchor_files == ["src/a/store.py", "src/b/store.py"], "sorted, not hash order"
    assert binding.anchor_ids == []


# ---- prose names a file by its stem (2026-09-02) -----------------------------


def test_a_snake_case_stem_binds_to_the_file_and_its_module(tmp_path: Path) -> None:
    """`doc_source` in prose means `doc_source.py`; `comprehension_labels` means the YAML.

    A module's node name is its dotted path — `orchestrator.pkg.doc_source` — so a bare stem
    matches no name and no id tail. And the extractor's provenance covers only files it
    parsed, so a `.yaml` stem has no node at all. The tree is walked instead, with "exactly
    one" applied twice: one file with that stem, then one module owning it.
    """
    from orchestrator.pkg.extractor import RepoCodeExtractor

    repo = tmp_path / "repo"
    (repo / "src" / "orchestrator" / "pkg").mkdir(parents=True)
    (repo / "src" / "orchestrator" / "pkg" / "doc_source.py").write_text(
        "def read():\n    return 1\n", encoding="utf-8"
    )
    (repo / "evals").mkdir()
    (repo / "evals" / "comprehension_labels.yaml").write_text("a: 1\n", encoding="utf-8")
    rec = DocReconciler(RepoCodeExtractor().extract(repo), repo_root=repo)

    def bind(token: str) -> DocBinding:
        page = DocPage(title="t", text=f"See `{token}`.")
        (mention,) = [m for m in extract_mentions(page) if m.text == token]
        return rec.bind(mention, base_dir="")

    module = bind("doc_source")
    assert module.anchor_files == ["src/orchestrator/pkg/doc_source.py"]
    assert module.anchor_ids == ["py:orchestrator.pkg.doc_source"]

    # A data file has no module. It still stops being drift: the prose named something real.
    data = bind("comprehension_labels")
    assert data.anchor_files == ["evals/comprehension_labels.yaml"]
    assert data.anchor_ids == []


def test_a_plain_word_stem_does_not_bind(tmp_path: Path) -> None:
    """`bug` is an English word here, and `bug.yaml` exists. Binding them is the guess.

    Measured 2026-09-02: matching any stem produced 149 bindings against snake-case's 88, and
    the extra 61 were `bug`, `enhancement`, `invention`, `validity` — prose, bound to modules
    that happen to share a filename.
    """
    from orchestrator.pkg.extractor import RepoCodeExtractor

    repo = tmp_path / "repo"
    (repo / "profiles").mkdir(parents=True)
    (repo / "profiles" / "bug.yaml").write_text("name: bug\n", encoding="utf-8")
    rec = DocReconciler(RepoCodeExtractor().extract(repo), repo_root=repo)
    page = DocPage(title="t", text="Report a `bug` when it misbehaves.")
    (mention,) = [m for m in extract_mentions(page) if m.text == "bug"]
    assert rec.bind(mention, base_dir="").anchor_files == []


def test_a_stem_owned_by_two_files_binds_to_neither(tmp_path: Path) -> None:
    """Exactly one, or nothing — before the module question is even asked."""
    from orchestrator.pkg.extractor import RepoCodeExtractor

    repo = tmp_path / "repo"
    for pkg in ("a", "b"):
        (repo / pkg).mkdir(parents=True)
        (repo / pkg / "doc_source.py").write_text("x = 1\n", encoding="utf-8")
    rec = DocReconciler(RepoCodeExtractor().extract(repo), repo_root=repo)
    page = DocPage(title="t", text="See `doc_source`.")
    (mention,) = [m for m in extract_mentions(page) if m.text == "doc_source"]
    binding = rec.bind(mention, base_dir="")
    assert binding.anchor_files == [] and binding.anchor_ids == []
