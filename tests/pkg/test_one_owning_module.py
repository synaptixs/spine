"""P5 of the KG track: one answer to "which module does this node belong to?" (SSPN-336).

Eight places walked ``CONTAINS`` up to a ``Module`` on their own, and they disagreed on the nodes
the walk cannot climb from:

* a node with **no** ``CONTAINS`` parent: every ``Endpoint`` the route front-ends emit, and some
  C++ functions. The walks fell back to the node's *file path*, which in a dotted language is a
  different vocabulary from the module's *name*: ``src/app/web.py`` beside ``app.web``. Measured
  on develop ``75cb0c0f``: Spine 72 endpoints, flask 199, fmt 271 C++ functions, open5gs 4.
* a **Module** node itself: ``state``'s area of a test's import target and ``localize``'s module
  for a frame in module-level code walked up *from* the module, found nothing, and fell back to
  its file, so a Python test importing ``flask`` never marked the ``flask`` area tested.

The one key is :meth:`FactStore.owning_module`: the node itself when it is a Module, else the
nearest ``CONTAINS`` ancestor that is one, else the Module whose file the node is in (D34), else
``None``. It reads facts (the edges, a Module's provenance), never a name.

Not fixed here, and not fixable by a key (D36): a C/C++ symbol id shared by several files
(B142/SSPN-339): the node itself is ambiguous.
"""

from __future__ import annotations

import ast
from pathlib import Path

from orchestrator.catalog.profile import ProjectProfile
from orchestrator.intake.options import Option
from orchestrator.knowledge import renderers
from orchestrator.knowledge.areas import AreaIndex
from orchestrator.knowledge.current_state import compute_current_state
from orchestrator.pkg import FactStore
from orchestrator.pkg.facts import Edge, EdgeKind, FactBatch, Node, NodeKind, Provenance
from orchestrator.sdlc import option_measures
from orchestrator.sdlc.localize import localize_trace

_SRC = Path(__file__).resolve().parents[2] / "src" / "orchestrator"


def _module(
    nid: str, name: str, file: str, *, language: str = "python", repo: str = "", test: bool = False
) -> Node:
    return Node(nid, NodeKind.MODULE, name, language, Provenance(file, 1, end_line=40, repo=repo), test=test)


def _fn(nid: str, name: str, file: str, line: int = 5, *, language: str = "python", repo: str = "") -> Node:
    return Node(nid, NodeKind.FUNCTION, name, language, Provenance(file, line, end_line=line + 2, repo=repo))


def _endpoint(nid: str, name: str, file: str, line: int = 10) -> Node:
    return Node(nid, NodeKind.ENDPOINT, name, "python", Provenance(file, line))


def _contains(batch: FactBatch, parent: Node, child: Node) -> None:
    assert child.provenance is not None
    batch.add_edge(Edge(parent.id, child.id, EdgeKind.CONTAINS, child.provenance))


def _web_app() -> tuple[FactBatch, Node, Node, Node]:
    """``app.web`` holds a function (parented) and a route (no parent, as every front-end emits it)."""
    b = FactBatch()
    web = _module("py:app.web", "app.web", "src/app/web.py")
    handler = _fn("py:app.web.handler", "handler", "src/app/web.py")
    route = _endpoint("py:endpoint:GET /health", "GET /health", "src/app/web.py")
    for n in (web, handler, route):
        b.add_node(n)
    _contains(b, web, handler)
    return b, web, handler, route


# ---- the key ----------------------------------------------------------------------------------


def test_a_node_belongs_to_the_nearest_module_up_its_contains_chain() -> None:
    b = FactBatch()
    mod = _module("py:app.core", "app.core", "src/app/core.py")
    cls = Node("py:app.core.Cart", NodeKind.TYPE, "Cart", "python", Provenance("src/app/core.py", 3))
    meth = _fn("py:app.core.Cart.total", "total", "src/app/core.py", 4)
    for n in (mod, cls, meth):
        b.add_node(n)
    _contains(b, mod, cls)
    _contains(b, cls, meth)
    assert FactStore(b).owning_module(meth.id) == mod


def test_a_module_belongs_to_itself() -> None:
    b, web, _handler, _route = _web_app()
    assert FactStore(b).owning_module(web.id) == web


def test_a_node_with_no_parent_belongs_to_the_module_at_its_file() -> None:
    b, web, _handler, route = _web_app()
    assert FactStore(b).owning_module(route.id) == web


def test_a_node_whose_file_holds_no_module_belongs_to_none() -> None:
    b = FactBatch()
    lone = _endpoint("py:endpoint:GET /x", "GET /x", "src/routes.py")
    b.add_node(lone)
    b.add_node(Node("py:floating", NodeKind.FUNCTION, "floating", "python"))  # no provenance at all
    store = FactStore(b)
    assert store.owning_module(lone.id) is None
    assert store.owning_module("py:floating") is None
    assert store.owning_module("py:not-in-the-graph") is None


def test_two_modules_at_one_file_prefer_the_nodes_own_language() -> None:
    """fmt's ``include/fmt/format.h`` is both a ``cpp:`` and a ``c:`` Module (the only such file
    in six measured repositories). A C++ function there belongs to the C++ one."""
    b = FactBatch()
    as_c = _module("c:include/fmt/format.h", "include/fmt/format.h", "include/fmt/format.h", language="c")
    as_cpp = _module(
        "cpp:include/fmt/format.h", "include/fmt/format.h", "include/fmt/format.h", language="cpp"
    )
    fn = _fn("cpp:fmt::format", "format", "include/fmt/format.h", language="cpp")
    other = _fn("go:x.F", "F", "include/fmt/format.h", language="go")
    for n in (as_c, as_cpp, fn, other):
        b.add_node(n)
    store = FactStore(b)
    assert store.owning_module(fn.id) == as_cpp
    # No Module in the node's language: the lower id, so the answer is the same on every run.
    assert store.owning_module(other.id) == as_c


def test_the_module_at_a_file_is_looked_up_in_the_nodes_own_repository() -> None:
    """Two services can both hold ``app/views.py``; a route in one is not owned by the other's module."""
    b = FactBatch()
    billing = _module("billing::py:app.views", "app.views", "app/views.py", repo="billing")
    users = _module("users::py:app.views", "app.views", "app/views.py", repo="users")
    route = Node(
        "users::py:endpoint:GET /me",
        NodeKind.ENDPOINT,
        "GET /me",
        "python",
        Provenance("app/views.py", 9, repo="users"),
    )
    for n in (billing, users, route):
        b.add_node(n)
    assert FactStore(b).owning_module(route.id) == users


def test_a_contains_cycle_ends_the_walk() -> None:
    b = FactBatch()
    x = Node("py:x", NodeKind.TYPE, "x", "python")
    y = Node("py:y", NodeKind.TYPE, "y", "python")
    b.add_node(x)
    b.add_node(y)
    b.add_edge(Edge(x.id, y.id, EdgeKind.CONTAINS, Provenance("a.py", 1)))
    b.add_edge(Edge(y.id, x.id, EdgeKind.CONTAINS, Provenance("a.py", 2)))
    assert FactStore(b).owning_module(x.id) is None


# ---- the surfaces that read it ----------------------------------------------------------------

_PROFILE = ProjectProfile(
    languages=frozenset({"python"}),
    framework=None,
    has_db=False,
    has_migrations=False,
    test_runner="pytest",
    task_type="feature",
)


def test_state_counts_the_area_a_python_test_imports_as_tested() -> None:
    """The import's target is a Module. Its area must be its *name's* area (``flask``), not its
    file's (``src/flask``), or no production area ever matches. On develop ``75cb0c0f`` a probe
    over every test import found 2 such areas dropped on flask and 2 on Spine.

    The test sits beside the code (pytest's ``test_*`` next to the module), so it is not in a
    ``tests`` zone and ``state``'s coupling rule cannot count it: only the import does."""
    b = FactBatch()
    flask = _module("py:flask", "flask", "src/flask/__init__.py")
    helper = _fn("py:flask.helper", "helper", "src/flask/__init__.py")
    test_mod = _module("py:flask.test_app", "flask.test_app", "src/flask/test_app.py", test=True)
    for n in (flask, helper, test_mod):
        b.add_node(n)
    _contains(b, flask, helper)
    b.add_edge(Edge(test_mod.id, flask.id, EdgeKind.IMPORTS, Provenance("src/flask/test_app.py", 1)))
    state = compute_current_state(b, _PROFILE)
    assert "flask" in state.area_funcs
    assert state.tested_areas == 1


def test_state_puts_an_unparented_function_in_its_files_module_area() -> None:
    """Not ``src/app`` (the file's first two segments) beside ``app.web`` (its module's)."""
    b, _web, _handler, _route = _web_app()
    orphan = _fn("py:app.web.orphan", "orphan", "src/app/web.py", 20)
    b.add_node(orphan)
    state = compute_current_state(b, _PROFILE)
    assert dict(state.area_funcs) == {"app.web": 2}


def test_the_episteme_area_index_agrees_with_state() -> None:
    b, web, handler, route = _web_app()
    index = AreaIndex(FactStore(b))
    assert {index.area_of(n) for n in (web, handler, route)} == {"app.web"}


def test_localize_names_the_module_of_a_frame_in_module_level_code() -> None:
    """A traceback through ``<module>`` lands on the Module itself; its module is ``app.web``."""
    b, _web, _handler, _route = _web_app()
    trace = (
        "Traceback (most recent call last):\n"
        '  File "/srv/repo/src/app/web.py", line 30, in <module>\n'
        "    boot()\n"
        "ValueError: no\n"
    )
    loc = localize_trace(trace, store=FactStore(b))
    assert [f.module for f in loc.frames if f.resolved] == ["app.web"]


def test_localize_names_the_module_of_a_frame_on_a_route() -> None:
    b, _web, _handler, _route = _web_app()
    trace = (
        "Traceback (most recent call last):\n"
        '  File "/srv/repo/src/app/web.py", line 10, in health\n'
        "    1/0\n"
        "ZeroDivisionError: division by zero\n"
    )
    loc = localize_trace(trace, store=FactStore(b))
    assert [(f.node_id, f.module) for f in loc.frames if f.resolved] == [
        ("py:endpoint:GET /health", "app.web")
    ]


def test_an_options_area_is_its_owning_modules_file() -> None:
    """A guard: ``option_measures`` measures functions and types, which always have a parent, so
    the move onto the one key must leave its answer alone. The orphan has none and still lands
    in its file's module."""
    b, _web, _handler, _route = _web_app()
    b.add_node(_fn("py:app.web.orphan", "orphan", "src/app/web.py", 20))
    option = Option(title="T", approach="a", anchors=("src/app/web.py",), source="user")
    assert option_measures.measure_option(option, FactStore(b)).areas == ("src/app/web.py",)


def test_the_symbol_index_links_an_unparented_function_to_its_files_module_page() -> None:
    b, web, _handler, _route = _web_app()
    orphan = _fn("py:app.web.orphan", "orphan", "src/app/web.py", 20)
    b.add_node(orphan)
    page = renderers.render_symbol_index(FactStore(b), page_of={web.id: "app.web"})
    row = next(line for line in page.splitlines() if "`orphan`" in line)
    assert "modules/app.web.md#orphan" in row


# ---- the property: one walk, nowhere copied ---------------------------------------------------

#: Walks that answer a different question, so they are not copies:
#: * ``plugin/server.py`` ``refs`` follows **every** owner (``all_parents_index``), because a doc
#:   naming the header that lost the one-parent race must still be found (B41).
#: * ``pkg/export.py`` ``export_sqlite`` projects each direct CONTAINS edge into a foreign key and
#:   walks nothing.
_NOT_A_COPY = {("plugin/server.py", "refs"), ("pkg/export.py", "export_sqlite")}


def _walks_up_to_a_module(fn: ast.AST) -> bool:
    """A loop that reads a parent index and compares against ``NodeKind.MODULE``."""
    for loop in ast.walk(fn):
        if not isinstance(loop, (ast.While, ast.For)):
            continue
        inside = list(ast.walk(loop))
        names = {n.id for n in inside if isinstance(n, ast.Name)} | {
            n.attr for n in inside if isinstance(n, ast.Attribute)
        }
        if any("parent" in x.lower() for x in names) and any(
            isinstance(n, ast.Attribute) and n.attr == "MODULE" for n in inside
        ):
            return True
    return False


def test_no_module_walks_contains_up_to_a_module_on_its_own() -> None:
    found = []
    for path in sorted(_SRC.rglob("*.py")):
        rel = path.relative_to(_SRC).as_posix()
        if rel == "pkg/store.py":
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and (rel, node.name) not in _NOT_A_COPY
                and _walks_up_to_a_module(node)
            ):
                found.append(f"{rel}:{node.name}")
    assert found == [], f"walk CONTAINS through FactStore.owning_module, not a copy: {found}"


#: The eight sites that had a copy, and the call each now makes. ``state`` goes through the
#: episteme's ``AreaIndex``, so the two name areas by one definition as well as one key.
_SITES = {
    "knowledge/areas.py": "self._store.owning_module(",
    "knowledge/current_state.py": "AreaIndex(FactStore(batch))",
    "knowledge/renderers.py": "store.owning_module(",
    "pkg/verify.py": "store.owning_module(",
    "sdlc/impact.py": "store.owning_module(",
    "sdlc/investigate.py": "store.owning_module(",
    "sdlc/localize.py": "store.owning_module(",
    "sdlc/option_measures.py": "store.owning_module(",
}


def test_every_former_copy_asks_the_store() -> None:
    missing = [rel for rel, call in _SITES.items() if call not in (_SRC / rel).read_text(encoding="utf-8")]
    assert missing == []
