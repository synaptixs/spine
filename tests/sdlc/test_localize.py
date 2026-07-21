"""Fault localization (C6): stack trace → PKG symbols, deterministic."""

from __future__ import annotations

from orchestrator.pkg import FactStore
from orchestrator.pkg.facts import Edge, EdgeKind, FactBatch, Node, NodeKind, Provenance
from orchestrator.sdlc.localize import _extract_frames, localize_trace, render_localization_md


def _node(nid: str, kind: NodeKind, name: str, file: str, line: int, end: int) -> Node:
    return Node(id=nid, kind=kind, name=name, language="python", provenance=Provenance(file, line, end))


def _graph() -> FactBatch:
    """auth.py::authenticate (lines 10-14) is called by web.py::handler."""
    b = FactBatch()
    auth = _node("py:auth", NodeKind.MODULE, "auth.py", "auth.py", 1, 40)
    web = _node("py:web", NodeKind.MODULE, "web.py", "web.py", 1, 20)
    authn = _node("py:auth.authenticate", NodeKind.FUNCTION, "authenticate", "auth.py", 10, 14)
    handler = _node("py:web.handler", NodeKind.FUNCTION, "handler", "web.py", 5, 8)
    for n in (auth, web, authn, handler):
        b.add_node(n)
    b.add_edge(Edge("py:auth", "py:auth.authenticate", EdgeKind.CONTAINS))
    b.add_edge(Edge("py:web", "py:web.handler", EdgeKind.CONTAINS))
    b.add_edge(Edge("py:web.handler", "py:auth.authenticate", EdgeKind.CALLS, Provenance("web.py", 6)))
    return b


_TRACEBACK = """Traceback (most recent call last):
  File "/srv/app/web.py", line 6, in handler
    return authenticate(token)
  File "/srv/app/auth.py", line 12, in authenticate
    raise ValueError("empty token")
ValueError: empty token
"""


def test_extract_frames_and_exception() -> None:
    frames, exc = _extract_frames(_TRACEBACK)
    assert [(f[2], f[1]) for f in frames] == [("handler", 6), ("authenticate", 12)]
    assert exc == "ValueError: empty token"


def test_extract_pytest_style_frames() -> None:
    frames, exc = _extract_frames("auth.py:12: in authenticate\nE   ValueError: empty token\n")
    assert frames == [("auth.py", 12, "authenticate")]
    assert exc == "ValueError: empty token"


def test_localize_resolves_fault_and_callers() -> None:
    loc = localize_trace(_TRACEBACK, store=FactStore(_graph()))
    assert loc.exception == "ValueError: empty token"
    # both frames resolve despite absolute trace paths (suffix/basename match)
    assert [f.resolved for f in loc.frames] == [True, True]
    # innermost resolved frame is the fault site
    assert loc.fault is not None
    assert loc.fault.func == "authenticate" and loc.fault.where == "auth.py:12"
    assert loc.fault.module == "auth.py"
    # authenticate is called by web.handler
    assert any("py:web.handler" in c for c in loc.callers)


def test_render_contains_sections() -> None:
    md = render_localization_md(localize_trace(_TRACEBACK, store=FactStore(_graph())))
    assert "# Fault localization" in md
    assert "`ValueError: empty token`" in md
    assert "✓ `authenticate` — auth.py:12" in md
    assert "## Likely fault site" in md and "py:web.handler" in md


def test_external_frames_do_not_resolve() -> None:
    trace = 'File "/usr/lib/python3.12/json/decoder.py", line 355, in raw_decode\nJSONDecodeError: x\n'
    loc = localize_trace(trace, store=FactStore(_graph()))
    assert loc.frames and not loc.frames[0].resolved
    assert loc.fault is None
    md = render_localization_md(loc)
    assert "No trace frame resolved" in md


def test_greenfield_is_honest() -> None:
    loc = localize_trace(_TRACEBACK, store=FactStore(FactBatch()))
    assert loc.grounded is False and loc.fault is None
    assert all(not f.resolved for f in loc.frames)
