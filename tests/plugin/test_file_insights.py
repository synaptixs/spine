"""``file_insights``: every function and class one file declares, with what the graph knows about
each — the one call an editor makes per open file (SSPN-242, D13). The numbers are the ones
``blast_radius`` and ``regression_gaps`` give for the same node; this tool only answers by file."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from orchestrator.plugin.server import _TIER, _TOOLS, COMPREHEND, blast_radius, file_insights, regression_gaps

_FILES = {
    "shop/__init__.py": "",
    "shop/cart.py": (
        "def cart_total(prices):\n"
        "    return sum(prices)\n"
        "\n"
        "\n"
        "class Basket:\n"
        "    def add(self, price):\n"
        "        return price\n"
    ),
    "shop/checkout.py": (
        "from shop.cart import cart_total\n\n\ndef checkout(prices):\n    return cart_total(prices)\n"
    ),
    # an attribute call on a receiver the graph cannot type: a possible caller, never a counted one
    "shop/basket.py": "def total_for(basket, prices):\n    return basket.cart_total(prices)\n",
    # a second caller, reached by a test: in the reach, but not untested
    "shop/refund.py": (
        "from shop.cart import cart_total\n\n\ndef refund(prices):\n    return -cart_total(prices)\n"
    ),
    "tests/test_refund.py": (
        "from shop.refund import refund\n\n\ndef test_refund():\n    assert refund([1]) == -1\n"
    ),
    "tests/test_cart.py": (
        "from shop.cart import cart_total\n\n\ndef test_cart_total():\n    assert cart_total([1, 2]) == 3\n"
    ),
    "web/price.ts": "export function applyTax(amount: number): number {\n  return amount * 1.2;\n}\n",
    "web/order.ts": (
        'import { applyTax } from "./price";\n\n'
        "export function orderTotal(amounts: number[]): number {\n"
        "  return applyTax(amounts.reduce((a, b) => a + b, 0));\n"
        "}\n"
    ),
}


def _repo(tmp_path: Path) -> str:
    for rel, text in _FILES.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return str(tmp_path)


def _symbol(out: dict[str, Any], name: str) -> dict[str, Any]:
    return next(s for s in out["symbols"] if s["name"] == name)


def test_lists_the_files_functions_and_classes_in_line_order(tmp_path: Path) -> None:
    out = file_insights(_repo(tmp_path), "shop/cart.py")
    assert out["found"] is True and out["file"] == "shop/cart.py"
    assert [(s["name"], s["kind"], s["line"]) for s in out["symbols"]] == [
        ("cart_total", "Function", 1),
        ("Basket", "Type", 5),
        ("add", "Function", 6),
    ]
    assert (out["symbol_count"], out["shown"], out["truncated"]) == (3, 3, False)


def test_counts_are_blast_radius_and_regression_gaps_for_the_same_node(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    s = _symbol(file_insights(repo, "shop/cart.py"), "cart_total")
    match = blast_radius(repo, "py:shop.cart.cart_total")["matches"][0]
    assert s["id"] == match["id"] == "py:shop.cart.cart_total"
    assert s["caller_count"] == match["caller_count"] == 3  # checkout, refund and the test
    assert s["interface_caller_count"] == match["interface_caller_count"] == 0
    gaps = regression_gaps(repo, "cart_total")
    assert s["target_covered"] is gaps["target_covered"] is True
    # checkout and refund are in the reach; only checkout is reached by no test
    assert s["untested_in_reach"] == len(gaps["uncovered"]) == 1
    assert gaps["impacted_count"] == 2
    assert s["untested_truncated"] is False


def test_untraced_calls_are_reported_beside_the_callers_never_folded_in(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    s = _symbol(file_insights(repo, "shop/cart.py"), "cart_total")
    assert (
        s["unresolved_calls"]
        == blast_radius(repo, "py:shop.cart.cart_total")["matches"][0]["unresolved_calls"]
    )
    assert s["unresolved_calls"]["count"] == 1
    assert s["unresolved_calls"]["sites"][0]["receiver"] == "basket"
    assert s["caller_count"] == 3


def test_a_typescript_function_says_untraced_calls_are_not_tracked_never_zero(tmp_path: Path) -> None:
    out = file_insights(_repo(tmp_path), "web/price.ts")
    s = _symbol(out, "applyTax")
    assert s["caller_count"] == 1 and s["line"] == 1
    assert s["unresolved_calls"] is None
    assert "not tracked" in s["unresolved_calls_note"] and "typescript" in s["unresolved_calls_note"]


def test_a_class_carries_no_untraced_key_because_the_question_does_not_apply(tmp_path: Path) -> None:
    s = _symbol(file_insights(_repo(tmp_path), "shop/cart.py"), "Basket")
    assert "unresolved_calls" not in s and "unresolved_calls_note" not in s


def test_an_absolute_or_dotted_path_inside_the_repo_names_the_same_file(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    relative = file_insights(repo, "shop/cart.py")
    assert file_insights(repo, str(tmp_path / "shop" / "cart.py")) == relative
    assert file_insights(repo, "./shop/cart.py") == relative


def test_a_path_outside_the_repo_is_refused(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "repo")
    (tmp_path / "elsewhere.py").write_text("def f():\n    pass\n", encoding="utf-8")
    for path in ("../elsewhere.py", str(tmp_path / "elsewhere.py")):
        out = file_insights(repo, path)
        assert "error" in out and "outside" in out["error"], path


def test_a_file_with_no_symbols_in_the_graph_says_so_rather_than_listing_nothing(tmp_path: Path) -> None:
    out = file_insights(_repo(tmp_path), "shop/missing.py")
    assert out["found"] is False and out["symbols"] == [] and out["symbol_count"] == 0
    assert "shop/missing.py" in out["note"]


def test_requires_a_path(tmp_path: Path) -> None:
    assert "error" in file_insights(_repo(tmp_path), "")


def test_bounds_the_list_and_says_how_many_it_clipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("orchestrator.plugin.server._MAX_FILE_SYMBOLS", 2)
    out = file_insights(_repo(tmp_path), "shop/cart.py")
    assert (out["symbol_count"], out["shown"], out["truncated"]) == (3, 2, True)
    assert [s["name"] for s in out["symbols"]] == ["cart_total", "Basket"]


def test_is_a_read_only_comprehension_tool_in_the_default_level() -> None:
    assert file_insights in _TOOLS
    assert _TIER["file_insights"] is COMPREHEND
