"""Evidence — the deterministic research artifact (Phase 1 of the GraphIR SDLC workflow).

Covers the three properties the spec asks of it: it keeps the structure `autorun` currently
throws away, it computes the blast radius from the *landing sites* rather than from a design's
proposal, and it is byte-stable at a commit.
"""

from __future__ import annotations

import json
import subprocess
import sys

from orchestrator.pkg import FactStore
from orchestrator.pkg.facts import FactBatch
from orchestrator.sdlc.evidence import (
    build_evidence,
    evidence_digest,
    evidence_from_parts,
    render_evidence_md,
    to_dict,
)

TITLE = "render handler to_row fail"
PROBLEM = "the render, handler and to_row functions fail"

_GRAPH_SRC = """
from orchestrator.pkg.facts import Edge, EdgeKind, FactBatch, Node, NodeKind, Provenance


def _n(nid, kind, name, file, line=1):
    return Node(id=nid, kind=kind, name=name, language="python", provenance=Provenance(file, line))


def graph():
    b = FactBatch()
    for n in (
        _n("py:report", NodeKind.MODULE, "report.py", "report.py"),
        _n("py:web", NodeKind.MODULE, "web.py", "web.py"),
        _n("py:report.render", NodeKind.FUNCTION, "render", "report.py", 10),
        _n("py:report.to_row", NodeKind.FUNCTION, "to_row", "report.py", 20),
        _n("py:web.handler", NodeKind.FUNCTION, "handler", "web.py", 5),
    ):
        b.add_node(n)
    b.add_edge(Edge("py:report", "py:report.render", EdgeKind.CONTAINS))
    b.add_edge(Edge("py:report", "py:report.to_row", EdgeKind.CONTAINS))
    b.add_edge(Edge("py:web", "py:web.handler", EdgeKind.CONTAINS))
    b.add_edge(Edge("py:web", "py:report", EdgeKind.IMPORTS))
    b.add_edge(Edge("py:web.handler", "py:report.render", EdgeKind.CALLS, Provenance("web.py", 6)))
    b.add_edge(Edge("py:report.to_row", "py:report.render", EdgeKind.CALLS, Provenance("report.py", 22)))
    return b
"""

exec(compile(_GRAPH_SRC, "<graph-fixture>", "exec"))  # noqa: S102 — shared verbatim with the subprocess


def _store() -> FactStore:
    return FactStore(graph())  # type: ignore[name-defined]  # noqa: F821 — defined by the exec above


async def test_landing_keeps_the_whole_fact_not_the_filename() -> None:
    """`autorun` reduces landing to ``where.split(":")[0]`` before anything downstream sees it,
    so design and codegen receive filenames where the research proved symbols. Defect 3."""
    ev = await build_evidence(TITLE, PROBLEM, store=_store())
    assert ev.landing, "the ticket names a symbol the graph holds"
    hit = next(h for h in ev.landing if h.name == "render")
    assert hit.where.startswith("report.py:")
    assert hit.kind and hit.module
    assert hit.callers >= 1, "render is called by handler and to_row"


async def test_the_blast_radius_is_keyed_off_landing_not_off_a_proposal() -> None:
    """`design.py` computes impact from ``design["files_to_touch"]`` — the files the design
    guessed at. A wrong guess yields a faithful analysis of a fiction that reads as
    verification. Evidence keys it off where the ticket actually lands. Defect 2."""
    ev = await build_evidence(TITLE, PROBLEM, store=_store())
    assert "report.py" in ev.files
    modules = {m["module"] for m in (ev.blast_radius.get("modules") or [])}
    assert modules, "the landing file resolved to a module"
    assert {m["ref"] for m in (ev.blast_radius.get("modules") or [])} <= set(ev.files)


async def test_rca_runs_and_is_recorded_without_a_model() -> None:
    """RCA is deterministic and is not reachable from `autorun` at all today. Defect 1."""
    ev = await build_evidence("render raises TypeError", "render() raises", store=_store())
    assert ev.rca, "an RCA section exists at all"
    assert ev.rca["llm"] is False, "no model may run inside a tool node"
    assert "hypotheses" in ev.rca


async def test_an_ungrounded_graph_says_so_rather_than_looking_clean() -> None:
    """`grounded=false` must be visible. An empty Evidence that announces itself beats a
    confident-looking one assembled from nothing — the same rule as the `invention` oracle
    printing 0 for "not measured"."""
    ev = await build_evidence("anything", "at all", store=FactStore(FactBatch()))
    assert ev.grounded is False
    assert "no grounded nodes" in render_evidence_md(ev)


async def test_both_paths_that_build_evidence_assemble_it_identically() -> None:
    """`build_evidence` and the shadow pass both end in ``evidence_from_parts``. Two assemblers
    would be two definitions of the same artifact, and the first disagreement between them
    would be reported as a divergence in the *pipeline*."""
    store = _store()
    ev = await build_evidence(TITLE, PROBLEM, store=store)
    parts = evidence_from_parts(
        title=TITLE,
        problem=PROBLEM,
        issue_type="",
        investigate={
            "landing": to_dict(ev)["landing"],
            "areas": list(ev.areas),
            "grounded": ev.grounded,
        },
        rca=ev.rca,
        blast=ev.blast_radius,
    )
    assert evidence_digest(parts) == evidence_digest(ev)


def test_the_digest_is_stable_across_hash_seeds() -> None:
    """Set and dict iteration order is randomised per *process* by PYTHONHASHSEED, and the seed
    is fixed for the life of a process — so an in-process loop cannot see this class of bug.
    Subprocesses can. This is the technique that caught `state` rendering three different
    reports from one input in 3.19.0.
    """
    # Assembled by concatenation rather than an indented template: the graph fixture is shared
    # verbatim with this module, so any re-indentation of it would be a second definition.
    script = "\n".join(
        [
            "import asyncio, json",
            "from orchestrator.pkg import FactStore",
            _GRAPH_SRC,
            "from orchestrator.sdlc.evidence import build_evidence, evidence_digest",
            f"TITLE = {TITLE!r}",
            f"PROBLEM = {PROBLEM!r}",
            "ev = asyncio.run(build_evidence(TITLE, PROBLEM, store=FactStore(graph())))",
            'print(json.dumps({"digest": evidence_digest(ev)}))',
        ]
    )
    # The ticket deliberately lands on three symbols across two modules. With a single
    # landing site this test passes on code that is genuinely unstable — verified by
    # reverting the sort in `evidence_from_parts` and watching it stay green.
    digests = set()
    for seed in ("0", "1", "42", "12345", "99991"):
        out = subprocess.run(  # noqa: S603
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            check=True,
            env={"PYTHONHASHSEED": seed, "PATH": "/usr/bin:/bin"},
        )
        digests.add(json.loads(out.stdout.strip().splitlines()[-1])["digest"])
    assert len(digests) == 1, f"Evidence is not byte-stable across hash seeds: {digests}"


def test_evidence_never_imports_the_tool_registry() -> None:
    """`runtime.tool_registry` imports this module to register the SDLC's tools, so any import
    back is a cycle — and **two lazy imports are still a cycle**.

    The first attempt at this fix moved which side deferred and left the loop standing; CodeQL
    flagged it again, correctly, because the cycle is a property of the dependency graph rather
    than of import timing. The digest now lives in `core.digest`, which depends on neither, so
    this edge does not exist at any scope.

    Asserted on the AST rather than by importing: an import-order test passes whenever something
    else happened to import one side first, which is exactly how a cycle hides. `ast.walk` rather
    than `tree.body`, so a function-level import counts too.
    """
    import ast
    import inspect
    import pathlib as _pathlib

    source = _pathlib.Path(inspect.getsourcefile(build_evidence) or "").read_text(encoding="utf-8")
    modules = {
        node.module
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert "orchestrator.runtime.tool_registry" not in modules
