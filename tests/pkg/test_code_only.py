"""B101 review: the validity gate's source lookup reads code, not the comments and docstrings around it.

``symbol_text.code_only`` blanks comment and docstring text line for line (so a hit's line number is the
source's) and keeps string literals, because a name a registration call passes as a string is code.
"""

from __future__ import annotations

import pytest

from orchestrator.pkg.symbol_text import code_only


def _kept(lines: list[str], suffix: str) -> str:
    out = code_only(lines, suffix)
    assert out is not None and len(out) == len(lines)
    assert [len(a) for a in out] == [len(b) for b in lines], "columns still point at the source"
    return "\n".join(out)


def test_python_drops_docstrings_and_comments_and_keeps_strings() -> None:
    kept = _kept(
        [
            '"""Module: gone_one."""',
            "def f(arg):",
            '    """Uses gone_two."""',
            '    opts.add("kept_name")  # gone_three',
            "    return arg  # '#' in a comment",
        ],
        ".py",
    )
    assert "kept_name" in kept and "arg" in kept
    assert not any(word in kept for word in ("gone_one", "gone_two", "gone_three"))


def test_python_that_does_not_parse_falls_back_to_the_hash_scan() -> None:
    kept = _kept(["def f(:", "    x = 'kept_name'  # gone_one"], ".py")
    assert "kept_name" in kept and "gone_one" not in kept


@pytest.mark.parametrize("suffix", [".ts", ".java", ".go", ".c", ".kt", ".cs"])
def test_c_like_drops_line_and_block_comments_and_keeps_strings(suffix: str) -> None:
    kept = _kept(
        [
            "/** gone_one",
            " * gone_two */ int a = 1;",
            'reg("kept_name", "// not a comment"); // gone_three',
            "x = 'k'; y = 2; /* gone_four */ z = kept_two;",
        ],
        suffix,
    )
    assert all(w in kept for w in ("kept_name", "// not a comment", "kept_two", "int a = 1"))
    assert not any(w in kept for w in ("gone_one", "gone_two", "gone_three", "gone_four"))


def test_rust_lifetimes_do_not_open_a_string() -> None:
    kept = _kept(["fn f<'a>(x: &'a str) { // gone_one", '    reg("kept_name") }'], ".rs")
    assert "kept_name" in kept and "gone_one" not in kept


def test_a_quote_left_open_ends_with_its_line() -> None:
    kept = _kept(["s = 'unterminated", "x = 1  # gone_one"], ".rb")
    assert "gone_one" not in kept


def test_perl_pod_and_hash_comments_go() -> None:
    kept = _kept(["=pod", "gone_one", "=cut", "my $x = 'kept_name'; # gone_two"], ".pm")
    assert "kept_name" in kept and "gone_one" not in kept and "gone_two" not in kept


def test_sql_and_php() -> None:
    assert "gone" not in _kept(["SELECT kept FROM t -- gone", "/* gone */"], ".sql")
    assert "gone" not in _kept(["$a = 'kept'; # gone", "$b = 1; // gone"], ".php")


def test_a_language_it_does_not_know_is_not_read() -> None:
    """Unknown comment syntax: no lines at all, so nothing un-parks on a guess."""
    assert code_only(["legacy_mode = 1"], ".xyz") is None
