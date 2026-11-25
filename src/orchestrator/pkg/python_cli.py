"""typer / click commands → ``Endpoint`` nodes (``CLI <path>``) + ``EXPOSES``; a test running
``X.invoke(app, [literal argv])`` → ``CONSUMES`` to the one command it names (B130).

**The gap this closes.** Nothing in Python calls a command handler — the framework does, after
parsing ``argv``. So ``orchestrator regression`` listed ``cli.change.regression`` under *"add
tests here"* although a test runs it through ``CliRunner().invoke(app, ["regression", …])``:
the graph had no edge from that test to anything. A command is the same kind of thing as an
HTTP route (the framework dispatches to it), so it takes the same shape:
``py:endpoint:CLI sdlc plan`` with ``EXPOSES`` to its handler, the precedent being
``kotlin_nav.py``'s ``NAV`` verb. ``join_link`` and ``python_client`` match on the verb, so a
``CLI`` endpoint never pairs with an HTTP call.

**Names: the frameworks' own defaults, applied statically; anything computed → no node.**

==========  ===================================================================================
typer       ``@app.command()`` → function name lower-cased, ``_`` → ``-``; ``command("x")`` /
            ``name="x"``; ``add_typer(sub, name="x")``, else a literal ``Typer(name="x")``,
            else the sub-app's ``@callback`` function name, else its commands merge into the
            parent. ``hidden=True`` commands are still commands (tests invoke them).
click       ``@click.command`` / ``@click.group`` / ``@grp.command`` / ``@grp.group`` → the
            same default plus click 8.2's suffix strip (``_command``, ``_cmd``, ``_group``,
            ``_grp``); ``grp.add_command(cmd, name=…)``.
==========  ===================================================================================

A mount whose name is computed emits nothing under it, and the mounted app is *not* promoted to
a root of its own — that would put its commands at paths that do not exist. A wrong path in the
graph is worse than an absent one (the ``python_routes`` rule).

**Which command a test runs (D15, D20).** One edge, to the command the argv's longest literal
prefix names: ``invoke(app, ["sdlc", "plan", …])`` → ``CLI sdlc plan``. No edge when the argv
is not a literal list, starts with an option (which may take a value, so the command cannot be
located), names only a group, or carries ``--help``/``-h`` anywhere (the handler never runs).
A lone typer command and a standalone click command run without their name, so for those any
argv but help reaches them. Edging every command of the app instead would label commands no
test runs as reached — the gap the label exists to stop hiding.

**The edge starts at the enclosing function (D21)**, a helper as much as a test — the same
attribution ``python_client`` uses. Whether a *test* reaches that helper is coverage's
question, answered over ``CALLS``, not this module's.

**Emission is deferred to ``finalize``**: an app is defined in one module, its sub-apps mounted
in another, and re-exported through a package ``__init__`` — whole-repo knowledge.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field

from orchestrator.pkg.facts import Edge, EdgeKind, FactBatch, Node, NodeKind, Provenance

CLI_VERB = "CLI"
_HELP = frozenset({"--help", "-h"})
_CLICK_SUFFIXES = frozenset({"command", "cmd", "group", "grp"})
_MAX_ALIAS_HOPS = 10


@dataclass(frozen=True)
class PendingCommand:
    """``@<app>.command(…)`` on a function: registered on ``app_key`` under a name decided at
    emit time (the framework is known only once every app is)."""

    app_key: str
    explicit: str | None  # the literal name given, None when defaulted
    func_name: str
    handler_id: str
    provenance: Provenance


@dataclass(frozen=True)
class PendingMount:
    parent_key: str
    child_key: str
    explicit: str | None  # literal name given at the mount
    provenance: Provenance


@dataclass(frozen=True)
class PendingInvoke:
    caller_id: str
    app_key: str | None
    argv: tuple[str | None, ...] | None  # None: not a literal list; a None element: computed
    provenance: Provenance


@dataclass
class _App:
    """A typer app or a click group/command, as found."""

    framework: str  # "typer" | "click"
    standalone: bool = False  # a bare click command, not a group
    own_name: str | None = None  # literal Typer(name=…) / @click.group("x")
    func_name: str = ""  # click: the decorated function
    handler_id: str = ""  # click standalone command: its function
    provenance: Provenance | None = None


@dataclass
class CliState:
    """Whole-repo command knowledge, accumulated file by file, emitted in ``finalize``."""

    apps: dict[str, _App] = field(default_factory=dict)
    commands: list[PendingCommand] = field(default_factory=list)
    mounts: list[PendingMount] = field(default_factory=list)
    #: ``py:<module>.<bound name>`` → what the import binds it to; followed to find an app
    #: re-exported through a package ``__init__``.
    aliases: dict[str, str] = field(default_factory=dict)
    #: typer app key (as written) → its ``@callback`` function name
    callbacks: dict[str, str] = field(default_factory=dict)
    invokes: list[PendingInvoke] = field(default_factory=list)

    def clear(self) -> None:
        self.apps.clear()
        self.commands.clear()
        self.mounts.clear()
        self.aliases.clear()
        self.callbacks.clear()
        self.invokes.clear()


# ---- naming -------------------------------------------------------------------------------


def _typer_default(func_name: str) -> str:
    return func_name.lower().replace("_", "-")


def _click_default(func_name: str) -> str:
    name = func_name.lower().replace("_", "-")
    left, sep, suffix = name.rpartition("-")
    return left if sep and left and suffix in _CLICK_SUFFIXES else name


def _default(framework: str, func_name: str) -> str:
    return _click_default(func_name) if framework == "click" else _typer_default(func_name)


# ---- scanning -----------------------------------------------------------------------------


def _str(node: ast.expr | None) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _name_arg(call: ast.Call) -> tuple[bool, str | None]:
    """``(given, literal)`` for a name passed positionally first or as ``name=``.

    ``given`` and ``literal is None`` means a computed name: the caller emits nothing.
    """
    if call.args:
        return True, _str(call.args[0])
    for kw in call.keywords:
        if kw.arg == "name":
            if isinstance(kw.value, ast.Constant) and kw.value.value is None:
                return False, None
            return True, _str(kw.value)
    return False, None


def _resolve(expr: ast.expr, module_id: str, imports: dict[str, str]) -> str | None:
    """The whole-repo key for an app expression: ``app``, ``mod.app``. None when computed."""
    if isinstance(expr, ast.Attribute) and isinstance(expr.value, ast.Name):
        base = imports.get(expr.value.id)
        return f"{base}.{expr.attr}" if base else None
    if isinstance(expr, ast.Name):
        return imports.get(expr.id) or f"{module_id}.{expr.id}"
    return None


def _is_from(expr: ast.expr, imports: dict[str, str], module: str, attr: str) -> bool:
    """``module.attr`` or a bare ``attr`` imported from ``module`` (``click.group`` / ``group``)."""
    if isinstance(expr, ast.Attribute) and isinstance(expr.value, ast.Name):
        return expr.attr == attr and imports.get(expr.value.id) == f"py:{module}"
    if isinstance(expr, ast.Name):
        return imports.get(expr.id) == f"py:{module}.{attr}"
    return False


def scan_module(
    tree: ast.Module, *, module_id: str, rel: str, imports: dict[str, str], state: CliState
) -> None:
    """Collect this file's apps, commands, mounts, import aliases and ``invoke`` calls."""
    for alias, target in imports.items():
        state.aliases[f"{module_id}.{alias}"] = target
    # No import gate: the module that mounts sub-apps, or decorates commands onto an ``app`` it
    # imports, need not import typer itself (Spine's own ``cli/__init__.py``). Nothing here
    # becomes a fact unless it resolves, in ``emit``, to an app a ``Typer()`` or click decorator made.
    _collect_apps_and_mounts(tree, module_id=module_id, rel=rel, imports=imports, state=state)
    _collect_commands(
        tree.body, parent_id=module_id, module_id=module_id, rel=rel, imports=imports, state=state
    )
    _collect_invokes(
        tree.body,
        parent_id=module_id,
        caller_id=module_id,
        module_id=module_id,
        rel=rel,
        imports=imports,
        state=state,
    )


def _collect_apps_and_mounts(
    tree: ast.Module, *, module_id: str, rel: str, imports: dict[str, str], state: CliState
) -> None:
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            if not (_is_from(node.value.func, imports, "typer", "Typer")):
                continue
            given, literal = _name_arg_kw_only(node.value)
            for target in node.targets:
                if isinstance(target, ast.Name):
                    state.apps[f"{module_id}.{target.id}"] = _App(
                        framework="typer",
                        own_name=literal if given else None,
                        provenance=Provenance(rel, node.lineno),
                    )
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            attr = node.func.attr
            if attr not in {"add_typer", "add_command"} or not node.args:
                continue
            child = _resolve(node.args[0], module_id, imports)
            if child is None:
                continue
            parent = _resolve(node.func.value, module_id, imports)
            if attr == "add_command" and len(node.args) > 1:
                given, literal = True, _str(node.args[1])
            else:
                given, literal = _name_arg_kw_only(node)
            # A computed mount name — or a parent that cannot be followed — is recorded with an
            # empty name: the child is mounted (so it is not a root) but nothing is emitted under it.
            explicit = (literal if literal is not None else "") if given else None
            if parent is None:
                parent, explicit = "", ""
            state.mounts.append(PendingMount(parent, child, explicit, Provenance(rel, node.lineno)))


def _name_arg_kw_only(call: ast.Call) -> tuple[bool, str | None]:
    for kw in call.keywords:
        if kw.arg == "name":
            if isinstance(kw.value, ast.Constant) and kw.value.value is None:
                return False, None
            return True, _str(kw.value)
    return False, None


def _collect_commands(
    body: list[ast.stmt],
    *,
    parent_id: str,
    module_id: str,
    rel: str,
    imports: dict[str, str],
    state: CliState,
) -> None:
    """Walk defs, mirroring the front-end's id scheme so ``EXPOSES`` lands on a real node."""
    for stmt in body:
        if isinstance(stmt, ast.ClassDef):
            _collect_commands(
                stmt.body,
                parent_id=f"{parent_id}.{stmt.name}",
                module_id=module_id,
                rel=rel,
                imports=imports,
                state=state,
            )
            continue
        if not isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        fn_id = f"{parent_id}.{stmt.name}"
        _collect_commands(
            stmt.body, parent_id=fn_id, module_id=module_id, rel=rel, imports=imports, state=state
        )
        for dec in stmt.decorator_list:
            call = dec if isinstance(dec, ast.Call) else None
            func = dec.func if isinstance(dec, ast.Call) else dec
            prov = Provenance(rel, dec.lineno)
            if _is_from(func, imports, "click", "command") or _is_from(func, imports, "click", "group"):
                given, literal = _name_arg(call) if call else (False, None)
                if given and literal is None:
                    continue
                standalone = _is_from(func, imports, "click", "command")
                state.apps[fn_id] = _App(
                    framework="click",
                    standalone=standalone,
                    own_name=literal,
                    func_name=stmt.name,
                    handler_id=fn_id if standalone else "",
                    provenance=prov,
                )
                continue
            if not isinstance(func, ast.Attribute):
                continue
            owner = _resolve(func.value, module_id, imports)
            if owner is None:
                continue
            if func.attr == "command":
                given, literal = _name_arg(call) if call else (False, None)
                if given and literal is None:
                    continue  # computed name — no node rather than a wrong one
                state.commands.append(PendingCommand(owner, literal, stmt.name, fn_id, prov))
            elif func.attr == "group":  # click sub-group: a group of its own, mounted on owner
                given, literal = _name_arg(call) if call else (False, None)
                state.apps[fn_id] = _App(
                    framework="click", own_name=literal, func_name=stmt.name, provenance=prov
                )
                state.mounts.append(
                    PendingMount(owner, fn_id, ("" if literal is None else literal) if given else None, prov)
                )
            elif (
                func.attr == "callback"
            ):  # typer: names an unnamed mount, and stops a lone command running bare
                state.callbacks[owner] = stmt.name


def _collect_invokes(
    body: list[ast.stmt],
    *,
    parent_id: str,
    caller_id: str,
    module_id: str,
    rel: str,
    imports: dict[str, str],
    state: CliState,
) -> None:
    for stmt in body:
        if isinstance(stmt, ast.ClassDef):
            _collect_invokes(
                stmt.body,
                parent_id=f"{parent_id}.{stmt.name}",
                caller_id=caller_id,
                module_id=module_id,
                rel=rel,
                imports=imports,
                state=state,
            )
            continue
        if isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef):
            fn_id = f"{parent_id}.{stmt.name}"
            _collect_invokes(
                stmt.body,
                parent_id=fn_id,
                caller_id=fn_id,
                module_id=module_id,
                rel=rel,
                imports=imports,
                state=state,
            )
            continue
        for node in ast.walk(stmt):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "invoke"
                and node.args
            ):
                continue
            argv_expr = (
                node.args[1]
                if len(node.args) > 1
                else next((kw.value for kw in node.keywords if kw.arg == "args"), None)
            )
            argv: tuple[str | None, ...] | None = None
            if isinstance(argv_expr, ast.List | ast.Tuple):
                argv = tuple(_str(e) for e in argv_expr.elts)
            elif argv_expr is None:
                argv = ()
            state.invokes.append(
                PendingInvoke(
                    caller_id, _resolve(node.args[0], module_id, imports), argv, Provenance(rel, node.lineno)
                )
            )


# ---- emission -----------------------------------------------------------------------------


@dataclass(frozen=True)
class _Leaf:
    """One command below an app: the argv tokens that select it and its full name."""

    match: tuple[str, ...]
    path: tuple[str, ...]
    handler_id: str
    provenance: Provenance


class _Tree:
    """Every app's commands, resolved once the whole repo has been read."""

    def __init__(self, state: CliState) -> None:
        self.state = state
        self.apps = state.apps
        self.commands: dict[str, list[PendingCommand]] = {}
        for c in state.commands:
            self.commands.setdefault(self.canon(c.app_key), []).append(c)
        self.callbacks = {self.canon(k): v for k, v in state.callbacks.items()}
        self.children: dict[str, list[tuple[str, str | None]]] = {}
        self.mounted: set[str] = set()
        for m in state.mounts:
            child = self.canon(m.child_key)
            if child not in self.apps:
                continue
            self.mounted.add(child)
            parent = self.canon(m.parent_key) if m.parent_key else ""
            if parent in self.apps:
                self.children.setdefault(parent, []).append((child, m.explicit))

    def canon(self, key: str) -> str:
        """Follow import aliases (``cli.app`` → ``cli._app.app``) until a known app, bounded."""
        hops = 0  # bounded: an alias cycle (a imports b imports a) must not spin
        while hops < _MAX_ALIAS_HOPS and key not in self.apps and key in self.state.aliases:
            key = self.state.aliases[key]
            hops += 1
        return key

    def _prefix(self, child: str, explicit: str | None) -> tuple[str, ...] | None:
        """The name a mounted app is reached under; ``()`` merges it; ``None`` — computed, skip."""
        if explicit is not None:
            return (explicit,) if explicit else None
        app = self.apps[child]
        if app.framework == "click":
            return (app.own_name or _click_default(app.func_name),)
        if app.own_name:
            return (app.own_name,)
        if child in self.callbacks:
            return (_typer_default(self.callbacks[child]),)
        return ()

    def leaves(self, key: str, seen: frozenset[str] = frozenset()) -> list[_Leaf]:
        """The commands of ``key`` as a group: each selected by its name."""
        if key in seen:
            return []
        seen = seen | {key}
        app = self.apps[key]
        out: list[_Leaf] = []
        for c in self.commands.get(key, []):
            name = c.explicit if c.explicit is not None else _default(app.framework, c.func_name)
            out.append(_Leaf((name,), (name,), c.handler_id, c.provenance))
        for child, explicit in self.children.get(key, []):
            prefix = self._prefix(child, explicit)
            if prefix is None:
                continue
            capp = self.apps[child]
            if capp.standalone:
                cmd_path = prefix or (capp.own_name or _click_default(capp.func_name),)
                if capp.provenance is not None:
                    out.append(_Leaf(cmd_path, cmd_path, capp.handler_id, capp.provenance))
                continue
            for leaf in self.leaves(child, seen):
                out.append(_Leaf(prefix + leaf.match, prefix + leaf.path, leaf.handler_id, leaf.provenance))
        return out

    def entry(self, key: str) -> list[_Leaf]:
        """The commands of ``key`` when it is the app invoked: a lone typer command (no callback,
        no sub-app) and a standalone click command run without their name."""
        app = self.apps[key]
        if app.standalone:
            name = (app.own_name or _click_default(app.func_name),)
            return [_Leaf((), name, app.handler_id, app.provenance)] if app.provenance else []
        leaves = self.leaves(key)
        if (
            app.framework == "typer"
            and len(leaves) == 1
            and not self.children.get(key)
            and key not in self.callbacks
        ):
            return [_Leaf((), leaves[0].path, leaves[0].handler_id, leaves[0].provenance)]
        return leaves


def _select(leaves: list[_Leaf], argv: tuple[str | None, ...]) -> _Leaf | None:
    """The command the argv's longest literal prefix names, or None."""
    best: _Leaf | None = None
    for leaf in leaves:
        n = len(leaf.match)
        if n > len(argv) or any(argv[i] != leaf.match[i] for i in range(n)):
            continue
        if best is None or n > len(best.match):
            best = leaf
    return best


def emit(state: CliState, batch: FactBatch) -> None:
    """Add every root app's commands as ``Endpoint`` + ``EXPOSES``, then each ``invoke`` that
    names one of them as ``CONSUMES``. An app mounted somewhere — even under a computed name —
    is not a root: its commands exist only at the path the mount gives them."""
    tree = _Tree(state)
    by_handler: dict[str, list[tuple[tuple[str, ...], str]]] = {}
    added: set[str] = set()
    for key in sorted(tree.apps):
        if key in tree.mounted:
            continue
        for leaf in tree.entry(key):
            name = f"{CLI_VERB} {' '.join(leaf.path)}"
            endpoint_id = f"py:endpoint:{name}"
            if endpoint_id not in added:
                added.add(endpoint_id)
                batch.add_node(Node(endpoint_id, NodeKind.ENDPOINT, name, "python", leaf.provenance))
            batch.add_edge(Edge(endpoint_id, leaf.handler_id, EdgeKind.EXPOSES, leaf.provenance))
            by_handler.setdefault(leaf.handler_id, []).append((leaf.path, endpoint_id))

    edged: set[tuple[str, str]] = set()
    for inv in state.invokes:
        if inv.app_key is None or inv.argv is None:
            continue
        key = tree.canon(inv.app_key)
        if key not in tree.apps or any(t in _HELP for t in inv.argv if t is not None):
            continue
        chosen = _select(tree.entry(key), inv.argv)
        if chosen is None:
            continue
        n = len(chosen.path)
        for path, endpoint_id in by_handler.get(chosen.handler_id, []):
            if path[len(path) - n :] == chosen.path and (inv.caller_id, endpoint_id) not in edged:
                edged.add((inv.caller_id, endpoint_id))
                batch.add_edge(Edge(inv.caller_id, endpoint_id, EdgeKind.CONSUMES, inv.provenance))


__all__ = ["CLI_VERB", "CliState", "emit", "scan_module"]
