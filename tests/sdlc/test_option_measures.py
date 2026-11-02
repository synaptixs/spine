"""P3 of SSPN-297 (SSPN-300): each option measured against the graph, with no model (D7).

Hand-built graphs pin each measure exactly; one real fixture repository checks the measures
against what the ``blast_radius`` and ``regression_gaps`` tools say about the same code."""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from orchestrator.intake.options import Option
from orchestrator.pkg import FactStore
from orchestrator.pkg.facts import Edge, EdgeKind, FactBatch, Node, NodeKind, Provenance
from orchestrator.sdlc import option_measures as om


def _node(nid: str, name: str, file: str, kind: NodeKind = NodeKind.FUNCTION, line: int = 1) -> Node:
    return Node(id=nid, kind=kind, name=name, language="python", provenance=Provenance(file, line))


def _store() -> FactStore:
    """core.validate ← web.handler (untested) ← api.route (untested); tests call validate only.
    core.py also holds `helper`, called by nothing. A doc describes validate."""
    b = FactBatch()
    for n in (
        _node("py:core", "core", "core.py", NodeKind.MODULE),
        _node("py:core.validate", "validate", "core.py", line=10),
        _node("py:core.helper", "helper", "core.py", line=20),
        _node("py:web", "web", "web.py", NodeKind.MODULE),
        _node("py:web.handler", "handler", "web.py", line=5),
        _node("py:api", "api", "api.py", NodeKind.MODULE),
        _node("py:api.route", "route", "api.py", line=3),
        _node("py:tests.test_core.test_validate", "test_validate", "tests/test_core.py", line=3),
        Node(id="doc:README.md#validate", kind=NodeKind.DOC, name="Validate", language="markdown"),
    ):
        b.add_node(n)
    for parent, child in (
        ("py:core", "py:core.validate"),
        ("py:core", "py:core.helper"),
        ("py:web", "py:web.handler"),
        ("py:api", "py:api.route"),
    ):
        b.add_edge(Edge(parent, child, EdgeKind.CONTAINS, Provenance("x.py", 1)))
    b.add_edge(Edge("py:web.handler", "py:core.validate", EdgeKind.CALLS, Provenance("web.py", 6)))
    b.add_edge(Edge("py:api.route", "py:web.handler", EdgeKind.CALLS, Provenance("api.py", 4)))
    b.add_edge(Edge("py:core.helper", "py:core.validate", EdgeKind.CALLS, Provenance("core.py", 21)))
    b.add_edge(
        Edge(
            "py:tests.test_core.test_validate",
            "py:core.validate",
            EdgeKind.CALLS,
            Provenance("tests/test_core.py", 4),
        )
    )
    b.add_edge(
        Edge("doc:README.md#validate", "py:core.validate", EdgeKind.MENTIONS, Provenance("README.md", 1))
    )
    return FactStore(b)


def _opt(*anchors: str, creates: tuple[str, ...] = (), title: str = "T") -> Option:
    return Option(title=title, approach="a", anchors=anchors, creates=creates, source="user")


# ---- each measure, exactly --------------------------------------------------------------------


def test_a_symbol_anchor_resolves_and_its_callers_are_counted() -> None:
    m = om.measure_option(_opt("validate"), _store())
    assert m.grounding == "grounded" and m.resolved == ("validate",) and m.unresolved == ()
    # handler, helper and the test call validate: three callers, as blast_radius would list them
    assert m.callers == 3


def test_untested_counts_what_regression_gaps_calls_uncovered() -> None:
    from orchestrator.sdlc.coverage import build_regression_plan

    store = _store()
    expected = {i.name for i in build_regression_plan(store, "py:core.validate").impacted if not i.covered}
    assert {"handler", "route"} <= expected  # the fixture says what it means to
    assert set(om.measure_option(_opt("validate"), store).untested or ()) == expected


def test_docs_are_the_doc_nodes_describing_the_anchors() -> None:
    assert om.measure_option(_opt("validate"), _store()).docs == 1
    assert om.measure_option(_opt("route"), _store()).docs == 0


def test_areas_are_owning_modules_never_symbol_ids() -> None:
    m = om.measure_option(_opt("validate", "handler"), _store())
    assert m.areas == ("core.py", "web.py")


def test_an_anchor_the_graph_does_not_hold_is_unresolved_and_says_so() -> None:
    m = om.measure_option(_opt("validate", "no_such_symbol"), _store())
    assert m.resolved == ("validate",) and m.unresolved == ("no_such_symbol",)


def test_a_path_anchor_names_the_code_in_that_file() -> None:
    m = om.measure_option(_opt("core.py"), _store())
    assert m.resolved == ("core.py",)
    # validate and helper live there; their outside callers are handler and the test
    assert m.callers == 2
    assert m.areas == ("core.py",)


def test_a_path_anchor_with_a_leading_dot_slash_is_the_same_file() -> None:
    assert om.measure_option(_opt("./core.py"), _store()).resolved == ("./core.py",)


def test_reuse_is_resolved_anchors_against_everything_the_option_touches() -> None:
    m = om.measure_option(_opt("validate", "ghost", creates=("new.py",)), _store())
    assert m.reuse == pytest.approx(1 / 2)  # one resolved anchor, one new file; the ghost counts neither way
    assert om.measure_option(_opt(creates=("new.py",)), _store()).reuse == 0.0


def test_callers_are_distinct_across_anchors() -> None:
    m = om.measure_option(_opt("validate", "handler"), _store())
    # validate ← handler, helper, test; handler ← route. handler is an anchor itself, so not a caller.
    assert m.callers == 3


def test_an_ambiguous_anchor_is_measured_on_every_match_and_says_how_many() -> None:
    b = FactBatch()
    b.add_node(_node("py:a.save", "save", "a.py"))
    b.add_node(_node("py:b.save", "save", "b.py"))
    m = om.measure_option(_opt("save"), FactStore(b))
    (anchor,) = m.anchors
    assert anchor.matches == 2 and len(anchor.measured) == 2
    assert m.areas == ("a.py", "b.py")


def test_a_path_anchor_is_bounded_and_the_bound_is_reported() -> None:
    b = FactBatch()
    for i in range(om.MAX_NODES_PER_ANCHOR + 5):
        b.add_node(_node(f"py:big.f{i:03d}", f"f{i:03d}", "big.py"))
    m = om.measure_option(_opt("big.py"), FactStore(b))
    (anchor,) = m.anchors
    assert anchor.matches == om.MAX_NODES_PER_ANCHOR + 5
    assert len(anchor.measured) == om.MAX_NODES_PER_ANCHOR and m.truncated


# ---- honesty: what cannot be measured says so ----------------------------------------------------


def test_an_option_naming_no_anchors_is_ungrounded_never_low_risk() -> None:
    m = om.measure_option(_opt(creates=("x.py",)), _store())
    assert m.grounding == "ungrounded" and "no anchors" in m.reason
    assert (m.callers, m.untested, m.docs) == (None, None, None)


def test_with_no_repository_every_option_is_ungrounded() -> None:
    m = om.measure_option(_opt("validate"), None)
    assert m.grounding == "ungrounded" and "no repository" in m.reason and m.callers is None


def test_an_empty_graph_is_ungrounded_not_clean() -> None:
    m = om.measure_option(_opt("validate"), FactStore(FactBatch()))
    assert m.grounding == "ungrounded" and "empty" in m.reason and m.callers is None


def test_an_option_none_of_whose_anchors_resolve_is_ungrounded_and_never_wins() -> None:
    # SSPN-312 (R1): invented anchors measured 0 callers and 0 untested, so they ranked first.
    ghost = om.measure_option(_opt("invented_fn", title="Ghost"), _store())
    assert ghost.grounding == "ungrounded" and "none of its anchors" in ghost.reason
    assert ghost.unresolved == ("invented_fn",) and ghost.callers is None
    real = om.measure_option(_opt("validate", "invented2", title="Real"), _store())
    rec = om.recommend([ghost, real])
    assert rec.title == "Real" and rec.not_ranked == ("Ghost",)


def test_a_graph_holding_only_docs_is_empty_not_grounded() -> None:
    b = FactBatch()
    b.add_node(Node(id="doc:README.md#x", kind=NodeKind.DOC, name="X", language="markdown"))
    m = om.measure_option(_opt("validate"), FactStore(b))
    assert m.grounding == "ungrounded" and "empty" in m.reason


def test_untested_code_is_counted_per_symbol_not_per_name() -> None:
    # SSPN-312 (R2): `web.handler` and `cli.handler` are two untested callers, not one.
    b = FactBatch()
    for n in (
        _node("py:core.validate", "validate", "core.py"),
        _node("py:web.handler", "handler", "web.py"),
        _node("py:cli.handler", "handler", "cli.py"),
    ):
        b.add_node(n)
    for caller, at in (("py:web.handler", "web.py"), ("py:cli.handler", "cli.py")):
        b.add_edge(Edge(caller, "py:core.validate", EdgeKind.CALLS, Provenance(at, 2)))
    m = om.measure_option(_opt("validate"), FactStore(b))
    assert m.untested is not None and len(m.untested) == 2


def test_a_dirty_tree_is_measured_but_untrusted() -> None:
    m = om.measure_option(_opt("validate"), _store(), trusted=False)
    assert m.grounding == "untrusted" and m.callers == 3


def test_callers_are_reported_as_a_floor() -> None:
    d = om.measure_option(_opt("validate"), _store()).to_dict()
    assert d["callers"] == {"at_least": 3}


# ---- determinism and no model --------------------------------------------------------------------


def test_the_same_options_and_graph_give_the_same_bytes() -> None:
    opts = [_opt("validate", title="A"), _opt("core.py", "ghost", creates=("n.py",), title="B")]
    first = json.dumps([m.to_dict() for m in om.measure_options(opts, _store())])
    second = json.dumps([m.to_dict() for m in om.measure_options(list(opts), _store())])
    assert first == second


def test_measuring_imports_no_model_client(tmp_path: Path) -> None:
    script = textwrap.dedent(
        """
        import sys
        from orchestrator.intake.options import Option
        from orchestrator.pkg import FactStore
        from orchestrator.pkg.facts import FactBatch
        from orchestrator.sdlc import option_measures as om
        opts = [Option(title="t", approach="a", anchors=("x",), source="user")]
        om.measure_options(opts, FactStore(FactBatch()))
        print("LOADED:" + ",".join(m for m in ("litellm", "anthropic", "openai") if m in sys.modules))
        """
    )
    out = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=120, check=False
    )
    assert out.returncode == 0, out.stderr[-2000:]
    assert out.stdout.strip().endswith("LOADED:"), out.stdout[-500:]


# ---- against the real tools, on a real repository ------------------------------------------------


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "app").mkdir(parents=True)
    (repo / "tests").mkdir()
    (repo / "app" / "__init__.py").write_text("")
    (repo / "app" / "core.py").write_text(
        "def validate(x):\n    return bool(x)\n\n\ndef unused():\n    return 1\n"
    )
    (repo / "app" / "web.py").write_text(
        "from app.core import validate\n\n\ndef handler(x):\n    return validate(x)\n"
    )
    (repo / "tests" / "test_core.py").write_text(
        "from app.core import validate\n\n\ndef test_validate():\n    assert validate(1)\n"
    )
    return repo


def test_on_a_real_repository_the_measures_agree_with_blast_radius_and_regression_gaps(
    tmp_path: Path,
) -> None:
    from orchestrator.pkg import load_or_extract
    from orchestrator.plugin.server import blast_radius, regression_gaps

    repo = _repo(tmp_path)
    store = FactStore(load_or_extract(repo))
    m = om.measure_option(_opt("validate"), store)
    radius = blast_radius(repo_path=str(repo), symbol="validate")
    gaps = regression_gaps(repo_path=str(repo), symbol="validate")
    assert m.resolved == ("validate",)
    assert m.callers == radius["matches"][0]["caller_count"] == 2  # pinned: 0 == 0 would also agree
    assert set(m.untested or ()) == {u["name"] for u in gaps["uncovered"]} == {"handler"}
    assert m.areas == ("app/core.py",)


def test_an_area_is_the_owning_module_even_when_the_symbol_is_defined_elsewhere() -> None:
    """A C++ method defined in a `.cpp` belongs to the module that declares its class."""
    b = FactBatch()
    b.add_node(_node("cpp:decl", "decl", "decl.h", NodeKind.MODULE))
    b.add_node(_node("cpp:Widget", "Widget", "decl.h", NodeKind.TYPE))
    b.add_node(_node("cpp:Widget::draw", "draw", "impl.cpp"))
    b.add_edge(Edge("cpp:decl", "cpp:Widget", EdgeKind.CONTAINS, Provenance("decl.h", 1)))
    b.add_edge(Edge("cpp:Widget", "cpp:Widget::draw", EdgeKind.CONTAINS, Provenance("decl.h", 2)))
    assert om.measure_option(_opt("cpp:Widget::draw"), FactStore(b)).areas == ("decl.h",)


def test_a_caller_a_test_reaches_is_not_untested() -> None:
    b = FactBatch()
    for n in (
        _node("py:core.validate", "validate", "core.py"),
        _node("py:web.handler", "handler", "web.py"),
        _node("py:api.route", "route", "api.py"),
        _node("py:tests.test_web.test_handler", "test_handler", "tests/test_web.py"),
    ):
        b.add_node(n)
    b.add_edge(Edge("py:web.handler", "py:core.validate", EdgeKind.CALLS, Provenance("web.py", 2)))
    b.add_edge(Edge("py:api.route", "py:core.validate", EdgeKind.CALLS, Provenance("api.py", 2)))
    b.add_edge(
        Edge(
            "py:tests.test_web.test_handler",
            "py:web.handler",
            EdgeKind.CALLS,
            Provenance("tests/test_web.py", 2),
        )
    )
    assert om.measure_option(_opt("validate"), FactStore(b)).untested == ("route",)


# ---- P4 (SSPN-301): the recommendation rule (D8) ---------------------------------------------------


def _m(
    title: str, *, unresolved: int = 0, untested: int = 0, callers: int = 0, reuse: float = 0.0
) -> om.OptionMeasure:
    return om.OptionMeasure(
        title=title,
        grounding="grounded",
        unresolved=tuple(f"u{i}" for i in range(unresolved)),
        callers=callers,
        untested=tuple(f"t{i}" for i in range(untested)),
        docs=0,
        reuse=reuse,
    )


@pytest.mark.parametrize(
    ("better", "worse"),
    [
        (_m("A", unresolved=0, untested=9, callers=9), _m("B", unresolved=1)),  # unresolved first
        (_m("A", untested=0, callers=9), _m("B", untested=1)),  # then untested
        (_m("A", callers=1, reuse=0.0), _m("B", callers=2, reuse=1.0)),  # then callers
        (_m("A", reuse=0.9), _m("B", reuse=0.1)),  # then reuse, most first
    ],
)
def test_each_criterion_decides_in_its_order(better: om.OptionMeasure, worse: om.OptionMeasure) -> None:
    assert om.recommend([worse, better]).title == "A"
    assert om.recommend([better, worse]).ranking == ("A", "B")


def test_a_tie_keeps_the_order_written() -> None:
    assert om.recommend([_m("B"), _m("A")]).title == "B"


def test_an_ungrounded_option_is_never_recommended_nor_ranked() -> None:
    nothing = om.OptionMeasure(title="Ungrounded", grounding="ungrounded", reason="names no anchors")
    rec = om.recommend([nothing, _m("Grounded", unresolved=3, untested=5)])
    assert rec.title == "Grounded" and rec.ranking == ("Grounded",) and rec.not_ranked == ("Ungrounded",)


def test_with_nothing_grounded_there_is_no_recommendation_and_it_says_why() -> None:
    rec = om.recommend([om.OptionMeasure(title="X", grounding="ungrounded", reason="no repository")])
    assert rec.title is None and "grounded" in rec.reason


def test_the_rule_and_its_bias_are_stated_with_every_recommendation() -> None:
    rec = om.recommend([_m("A")])
    assert "fewest unresolved" in rec.rule and "not the best product" in rec.bias
    assert rec.to_dict()["rule"] == rec.rule


def test_an_untrusted_winner_says_so() -> None:
    shaky = om.OptionMeasure(title="A", grounding="untrusted", callers=0, untested=(), docs=0, reuse=1.0)
    assert "untrusted" in om.recommend([shaky]).reason
