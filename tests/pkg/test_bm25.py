"""BM25 over symbol text (hybrid KG search, D4): the content leg the name ranker does not have.

A ticket that says "ledger" finds the function whose *body* writes the ledger even when no symbol is named
for it — the signal the BM25 file baseline has and Spine did not. The index is deterministic: scores are
rounded and ties go to the id, so the same graph always ranks the same way regardless of the order symbols
were added.
"""

from __future__ import annotations

import math
from pathlib import Path

from orchestrator.pkg.bm25 import BM25Index, BM25Params, rank_symbol_texts
from orchestrator.pkg.extractor import RepoCodeExtractor
from orchestrator.pkg.retrieval import _tokens, word_tokens
from orchestrator.pkg.store import FactStore
from orchestrator.pkg.symbol_text import Recipe, symbol_texts


def test_word_tokens_keep_order_and_repeats_and_agree_with_the_name_rankers_tokens() -> None:
    text = "Deleting the invoices deletes invoice rows; exportInvoice and export_invoice"
    words = word_tokens(text)
    assert words.count("invoic") >= 3  # repeats kept: BM25 needs term frequency
    assert words.index("delet") < words.index("export")  # order kept
    assert set(words) == _tokens(text)  # one tokenizer: the name leg and the content leg stem alike


def test_a_rare_word_outranks_a_common_one() -> None:
    index = BM25Index({"a": ["ledger", "row"], "b": ["row", "row"], "c": ["row", "file"]})
    ranked = index.ranked(["ledger", "row"])
    assert ranked[0][0] == "a"


def test_with_equal_counts_the_shorter_text_wins() -> None:
    index = BM25Index({"short": ["ledger", "x"], "long": ["ledger", "x", "y", "z", "w", "v", "u", "t"]})
    assert [d for d, _ in index.ranked(["ledger"])] == ["short", "long"]


def test_a_text_without_any_query_word_is_not_returned() -> None:
    index = BM25Index({"a": ["ledger"], "b": ["render"]})
    assert [d for d, _ in index.ranked(["ledger"])] == ["a"]


def test_an_empty_query_or_index_returns_nothing() -> None:
    assert BM25Index({"a": ["ledger"]}).ranked([]) == []
    assert BM25Index({}).ranked(["ledger"]) == []


def test_ties_go_to_the_id_and_the_order_of_insertion_does_not_matter() -> None:
    docs = {"zeta": ["ledger", "row"], "alpha": ["ledger", "row"], "mid": ["ledger", "file"]}
    forward = BM25Index(docs).ranked(["ledger", "row"])
    backward = BM25Index(dict(reversed(list(docs.items())))).ranked(["ledger", "row"])
    assert forward == backward
    assert [d for d, _ in forward][:2] == ["alpha", "zeta"]


def test_a_tie_reached_through_different_words_still_goes_to_the_id() -> None:
    # "b" is scored first (its word sorts first); an equal score for "a" must still come out ahead.
    index = BM25Index({"a": ["row"], "b": ["ledger"]})
    assert [d for d, _ in index.ranked(["ledger", "row"])] == ["a", "b"]


def test_the_score_is_the_published_formula() -> None:
    # Lucene's non-negative idf ln(1 + (N - df + 0.5) / (df + 0.5)); tf saturates with k1, length norm with b.
    docs = {"a": ["ledger", "ledger", "row"], "b": ["row"], "c": ["file", "row", "x", "y"]}
    p = BM25Params(k1=1.2, b=0.75)
    n, avgdl = 3, (3 + 1 + 4) / 3
    idf = math.log(1 + (n - 1 + 0.5) / (1 + 0.5))
    tf, dl = 2, 3
    expected = idf * tf * (p.k1 + 1) / (tf + p.k1 * (1 - p.b + p.b * dl / avgdl))
    [(doc, score)] = BM25Index(docs, p).ranked(["ledger"])
    assert doc == "a"
    assert score == round(expected, 6)


def test_a_repeated_query_word_counts_once() -> None:
    index = BM25Index({"a": ["ledger"], "b": ["row"]})
    assert index.ranked(["ledger", "ledger", "ledger"]) == index.ranked(["ledger"])


def test_the_body_finds_what_no_name_says(tmp_path: Path) -> None:
    (tmp_path / "billing.py").write_text(
        "def persist(record, path):\n"
        '    """Append one row to the accounting ledger."""\n'
        "    with open(path, 'a') as fh:\n"
        "        fh.write(record)\n"
        "\n\n"
        "def render(record):\n"
        "    return str(record)\n"
    )
    store = FactStore(RepoCodeExtractor().extract(tmp_path))
    texts = symbol_texts(store, tmp_path, Recipe.WITH_BODY)
    hits = rank_symbol_texts(texts, "Rows go missing from the accounting ledger")
    assert hits and hits[0][0].endswith("billing.persist")
    # Recipe A has no body, so the same question finds nothing: the content is what the leg adds.
    assert rank_symbol_texts(symbol_texts(store, tmp_path, Recipe.NAME_SIGNATURE), "accounting ledger") == []
