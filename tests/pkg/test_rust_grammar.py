"""The installed Rust grammar must parse the syntax that blocked the pinned corpus."""

import pytest

pytest.importorskip("tree_sitter_rust_orchard", reason="install the 'rust' extra")


def test_attribute_on_struct_pattern_field_parses_without_error() -> None:
    import tree_sitter_rust_orchard
    from tree_sitter import Language, Parser

    source = b'fn f() { let JSONOutput { #[cfg(feature = "load")] node_types_json, .. } = value; }'
    root = Parser(Language(tree_sitter_rust_orchard.language())).parse(source).root_node
    assert not root.has_error
