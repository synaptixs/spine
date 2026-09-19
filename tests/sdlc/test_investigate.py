"""Investigation brief (C4): ticket × codebase grounding, deterministic."""

from __future__ import annotations

from pathlib import Path

from orchestrator.pkg import FactStore
from orchestrator.pkg.facts import Edge, EdgeKind, FactBatch, Node, NodeKind, Provenance
from orchestrator.sdlc.investigate import build_investigation, render_investigation_md


def _node(nid: str, kind: NodeKind, name: str, file: str, line: int = 1) -> Node:
    return Node(id=nid, kind=kind, name=name, language="python", provenance=Provenance(file, line))


def _graph() -> FactBatch:
    """auth.py defines authenticate(); web.py calls it."""
    b = FactBatch()
    auth = _node("py:auth", NodeKind.MODULE, "auth.py", "auth.py")
    web = _node("py:web", NodeKind.MODULE, "web.py", "web.py")
    authn = _node("py:auth.authenticate", NodeKind.FUNCTION, "authenticate", "auth.py", 10)
    handler = _node("py:web.handler", NodeKind.FUNCTION, "handler", "web.py", 5)
    for n in (auth, web, authn, handler):
        b.add_node(n)
    b.add_edge(Edge("py:auth", "py:auth.authenticate", EdgeKind.CONTAINS))
    b.add_edge(Edge("py:web", "py:web.handler", EdgeKind.CONTAINS))
    b.add_edge(Edge("py:web.handler", "py:auth.authenticate", EdgeKind.CALLS, Provenance("web.py", 6)))
    return b


def test_build_investigation_locates_symbols_and_callers() -> None:
    store = FactStore(_graph())
    inv = build_investigation("Fix authenticate 500", "authenticate throws on empty token", store=store)

    assert inv.grounded is True
    names = {land.name for land in inv.landing}
    assert "authenticate" in names
    hit = next(land for land in inv.landing if land.name == "authenticate")
    assert hit.callers == 1  # web.handler calls it
    assert hit.module == "auth.py"  # owning module resolved via CONTAINS
    assert "auth.py" in inv.areas


def test_prior_notes_passthrough_and_render() -> None:
    store = FactStore(_graph())
    inv = build_investigation(
        "authenticate bug", "x", store=store, prior_notes=["[fix-pattern] guard empty token (runs: 12)"]
    )
    md = render_investigation_md(inv)
    assert "# Investigation — authenticate bug" in md
    assert "## Where it lands in the code" in md and "`authenticate`" in md
    assert "## Prior art / related work" in md and "guard empty token" in md
    assert "## Next step" in md and "orchestrator design" in md


def test_greenfield_is_honest() -> None:
    inv = build_investigation("Anything", "new feature", store=FactStore(FactBatch()))
    assert inv.grounded is False and inv.landing == []
    md = render_investigation_md(inv)
    assert "No knowledge graph yet" in md
    assert "None surfaced" in md  # no prior notes without the registry DB


def test_no_match_is_honest_when_grounded() -> None:
    store = FactStore(_graph())
    inv = build_investigation("Refactor billing invoices", "unrelated to the graph", store=store)
    assert inv.landing == []  # nothing lexically matched
    md = render_investigation_md(inv)
    assert "No symbols matched" in md


# ---- P3: the brief quotes the code it located --------------------------------


def _landing_repo(tmp_path: Path, body: str = "x = 1\ny = 2\nz = 3\n") -> Path:
    (tmp_path / "a.py").write_text(body, encoding="utf-8")
    return tmp_path


def test_a_landing_site_carries_the_source_at_its_line(tmp_path: Path) -> None:
    """The defect this closes: a document about code, containing none of it.

    The brief already computed the exact `file:line` for every row and then declined to open
    it — so the reader did the file-opening the brief existed to save them.
    """
    from orchestrator.sdlc.investigate import Investigation, Landing, render_investigation_md

    _landing_repo(tmp_path, "def helper():\n    return Filterable\n")
    inv = Investigation(
        title="t",
        problem="p",
        landing=[Landing(name="helper", where="a.py:1", kind="Function", callers=0, module="a")],
        roots={"": tmp_path},
    )
    md = render_investigation_md(inv)
    assert "```python" in md and "def helper():" in md


def test_a_weak_landing_is_never_quoted(tmp_path: Path) -> None:
    """Never spend the reader's attention on a row the brief itself doubts.

    A weak landing matched on a fragment other files share. Twelve lines of it costs exactly
    the attention that should have gone to a strong one — measured on a real ticket, two of
    three excerpts went to DTOs matched only on `auct`.
    """
    from orchestrator.sdlc.investigate import Investigation, Landing, render_investigation_md

    _landing_repo(tmp_path, "class Auction:\n    pass\n")
    inv = Investigation(
        title="t",
        problem="p",
        landing=[Landing(name="Auction", where="a.py:1", kind="Type", callers=0, module="a", weak=True)],
        roots={"": tmp_path},
    )
    assert "```" not in render_investigation_md(inv)


def test_a_file_that_moved_since_extraction_loses_its_excerpt_not_the_brief(tmp_path: Path) -> None:
    """A brief that dies because a file moved is worse than one that omits an excerpt."""
    from orchestrator.sdlc.investigate import Investigation, Landing, render_investigation_md

    inv = Investigation(
        title="t",
        problem="p",
        landing=[Landing(name="gone", where="deleted.py:9000", kind="Function", callers=0, module="a")],
        roots={"": tmp_path},
    )
    md = render_investigation_md(inv)
    assert "`gone`" in md and "```" not in md


def test_the_brief_says_how_many_landings_it_did_not_quote(tmp_path: Path) -> None:
    """Invariant 7 — a clipped view must not imply completeness."""
    from orchestrator.sdlc.investigate import Investigation, Landing, render_investigation_md

    _landing_repo(tmp_path, "\n".join(f"line{i}" for i in range(40)) + "\n")
    landing = [
        Landing(name=f"s{i}", where=f"a.py:{i + 1}", kind="Function", callers=0, module="a") for i in range(6)
    ]
    md = render_investigation_md(Investigation(title="t", problem="p", landing=landing, roots={"": tmp_path}))
    assert "_Source shown for 3 of 6 landing(s)" in md


def test_an_untested_landing_says_so_and_a_covered_one_says_so(tmp_path: Path) -> None:
    """ "No test reaches this" is the line a reviewer acts on."""
    from orchestrator.sdlc.investigate import Investigation, Landing, render_investigation_md

    landing = [
        Landing(name="hot", where="a.py:1", kind="Function", callers=3, module="a", covered=True),
        Landing(name="cold", where="a.py:2", kind="Function", callers=0, module="a", covered=False),
    ]
    md = render_investigation_md(Investigation(title="t", problem="p", landing=landing))
    assert "`hot`" in md and "reached by tests" in md
    assert "**no test reaches this**" in md


def test_a_language_with_no_call_graph_is_silent_not_accusing() -> None:
    """`covered=None` means "cannot tell". A front-end that emits no CALLS edges has not
    proven an absence of tests, and printing one would be an invention."""
    from orchestrator.sdlc.investigate import Investigation, Landing, render_investigation_md

    landing = [Landing(name="x", where="a.rb:1", kind="Function", callers=0, module="a")]
    md = render_investigation_md(Investigation(title="t", problem="p", landing=landing))
    assert "no test reaches this" not in md and "reached by tests" not in md
