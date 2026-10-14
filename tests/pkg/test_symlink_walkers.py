"""N7 / N8: one symlink rule for the code walker, the doc walker and language detection.

`RepoCodeExtractor` admits a symlinked file under its own name unless its target is somewhere
the walk would never go — outside the root, or inside an ignored, dot-prefixed or nested-checkout
directory (`tests/pkg/test_vendored_trees.py`). The doc walker and `language_file_counts` walk the
same tree with the same ignore/dot/nested filters but had no symlink rule, so a link into
`node_modules` brought vendored prose in as a first-party `Doc` and voted for a language that
contributes no graph nodes. A link leaving the root was read too, and its text can reach a
codegen prompt (`sdlc/grounding.py`).

The agreement test is the property: for every kind of link, the three walkers answer alike.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from orchestrator.catalog.profile import language_file_counts
from orchestrator.pkg.doc_source import read_doc_pages
from orchestrator.pkg.extractor import RepoCodeExtractor

pytestmark = pytest.mark.skipif(os.name == "nt", reason="symlink creation needs privileges on Windows")

#: (id, where the link points, admitted?) — targets are relative to the `links/` directory.
_CASES = [
    pytest.param("../lib", True, id="in-tree"),
    pytest.param("../node_modules/pkg", False, id="node_modules"),
    pytest.param("../vendor/pkg", False, id="vendor"),
    pytest.param("../build/pkg", False, id="build"),
    pytest.param("../.hidden/pkg", False, id="dot-directory"),
    pytest.param("../inner", False, id="nested-checkout"),
    pytest.param("OUTSIDE", False, id="outside-root"),
]


def _repo(tmp_path: Path, target_dir: str) -> tuple[Path, Path]:
    """A repo whose `links/` holds one `.md` and one `.py` symlink, both into `target_dir`; plus
    one real file of each kind so the walks have something to find either way."""
    root = tmp_path / "repo"
    outside = tmp_path / "outside"
    for d in (root / "links", root / "lib", root / "node_modules" / "pkg", root / "vendor" / "pkg"):
        d.mkdir(parents=True, exist_ok=True)
    for d in (root / "build" / "pkg", root / ".hidden" / "pkg", root / "inner", outside):
        d.mkdir(parents=True, exist_ok=True)
    (root / "inner" / ".git").write_text("gitdir: elsewhere\n", encoding="utf-8")
    for d in (root / "lib", root / "node_modules" / "pkg", root / "vendor" / "pkg", root / "build" / "pkg"):
        _put(d)
    _put(root / ".hidden" / "pkg")
    _put(root / "inner")
    _put(outside)
    (root / "real.md").write_text("# Real\n\nFirst-party prose.\n", encoding="utf-8")
    (root / "real.py").write_text("def real() -> int:\n    return 1\n", encoding="utf-8")
    for suffix in (".md", ".py"):
        target = outside / f"doc{suffix}" if target_dir == "OUTSIDE" else Path(target_dir) / f"doc{suffix}"
        os.symlink(target, root / "links" / f"link{suffix}")
    return root, outside


def _put(directory: Path) -> None:
    (directory / "doc.md").write_text("# Linked\n\nProse behind a link.\n", encoding="utf-8")
    (directory / "doc.py").write_text("def linked() -> int:\n    return 2\n", encoding="utf-8")


def _doc_admitted(root: Path) -> bool:
    return "links/link.md" in {p.source_file or p.title for p in read_doc_pages(root, sections=False)}


def _lang_admitted(root: Path) -> bool:
    return language_file_counts(root).get("python", 0) == 3  # real.py + lib/doc.py + the link


def _code_admitted(root: Path) -> bool:
    return "links/link.py" in {
        n.provenance.file for n in RepoCodeExtractor().extract(root).nodes if n.provenance
    }


@pytest.mark.parametrize(("target", "admitted"), _CASES)
def test_the_three_walkers_agree_on_every_kind_of_link(tmp_path: Path, target: str, admitted: bool) -> None:
    root, _ = _repo(tmp_path, target)
    assert {
        "docs": _doc_admitted(root),
        "language": _lang_admitted(root),
        "code": _code_admitted(root),
    } == {"docs": admitted, "language": admitted, "code": admitted}


def test_an_outside_link_is_not_read_into_any_page(tmp_path: Path) -> None:
    """The text behind a link that leaves the root must not be in any page: pages feed the codegen
    prompt when they bind a symbol."""
    root, outside = _repo(tmp_path, "OUTSIDE")
    (outside / "doc.md").write_text("# Secret\n\nPRIVATE-MARKER about `real`.\n", encoding="utf-8")
    assert not [p for p in read_doc_pages(root) if "PRIVATE-MARKER" in p.text]


def test_a_dangling_link_is_skipped_by_every_walker(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "real.py").write_text("def real() -> int:\n    return 1\n", encoding="utf-8")
    os.symlink("nowhere.md", root / "gone.md")
    os.symlink("nowhere.py", root / "gone.py")
    assert [p.title for p in read_doc_pages(root)] == []
    assert language_file_counts(root) == {"python": 1}


def test_a_symlinked_directory_is_not_descended(tmp_path: Path) -> None:
    """`os.walk` does not follow directory links; pinned so a `followlinks=True` never slips in."""
    root = tmp_path / "repo"
    (root / "node_modules" / "pkg").mkdir(parents=True)
    (root / "node_modules" / "pkg" / "README.md").write_text("# V\n", encoding="utf-8")
    (root / "node_modules" / "pkg" / "a.js").write_text("export const a = 1;\n", encoding="utf-8")
    (root / "docs").mkdir()
    os.symlink("../node_modules/pkg", root / "docs" / "vendored", target_is_directory=True)
    assert read_doc_pages(root) == []
    assert language_file_counts(root) == {}


def test_language_detection_is_not_swayed_by_links_into_an_ignored_tree(tmp_path: Path) -> None:
    """N8's consequence: 3 JS links into node_modules beside 1 real .py made `auto` pick JS."""
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    (root / "src" / "app.py").write_text("def f() -> int:\n    return 1\n", encoding="utf-8")
    (root / "node_modules" / "pkg").mkdir(parents=True)
    for i in range(3):
        (root / "node_modules" / "pkg" / f"m{i}.js").write_text(
            f"export const v{i} = {i};\n", encoding="utf-8"
        )
        os.symlink(f"../node_modules/pkg/m{i}.js", root / "src" / f"link{i}.js")
    assert language_file_counts(root) == {"python": 1}
