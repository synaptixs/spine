"""B49: per-call guard state must not live on the shared codegen adapter.

The Temporal worker builds one ``LLMCodegenAdapter`` per process and runs child features
concurrently, so a value one call stores on the adapter before awaiting the model is read by
whichever call applies next. Refine's pre-existing-file allowlist (the NSS-1243 guard and
B47's weakened-test guard read it) and implement's edit scope (B44's guard) both lived there.

Each test parks one call on the model and lets another run to completion in between, then
checks what ``apply_files`` was actually handed. No model is called.
"""

from __future__ import annotations

import ast
import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

import orchestrator.sdlc.codegen as codegen
from orchestrator.sdlc.codegen import CodeChange, LLMCodegenAdapter
from orchestrator.sdlc.scope import EditScope

_SCOPE = EditScope(files=("src/a.py",), confident=True)
_REPLY = json.dumps({"summary": "s", "files": [{"path": "src/a.py", "content": "x = 2\n"}]})
_SPEC: dict[str, Any] = {"title": "t", "summary": "change the module"}


class _Harness:
    """An adapter whose model calls wait on a per-issue gate, and a record of every apply."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        self.applied: list[tuple[str, frozenset[str] | None, EditScope | None]] = []
        self.gates: dict[str, asyncio.Event] = {}
        self.replies: dict[str, list[str]] = {}
        self.calls: dict[str, int] = {}
        self.tmp = tmp_path

        def _record(files: Any, root: Path, **kw: Any) -> CodeChange:
            self.applied.append((Path(root).name, kw.get("editable_existing"), kw.get("scope")))
            return CodeChange(summary="recorded")

        monkeypatch.setattr(codegen, "apply_files", _record)
        self.adapter = LLMCodegenAdapter(object(), edit_scope=_SCOPE)  # type: ignore[arg-type]
        self.adapter._complete = self._complete  # type: ignore[method-assign]

    async def _complete(self, system: str, user: str) -> str:
        key = next(k for k in ("A-1", "B-1", "C-1", "D-1") if f"Issue: {k}" in user)
        self.calls[key] = self.calls.get(key, 0) + 1
        queued = self.replies.get(key)
        if queued:
            reply = queued.pop(0)
            if queued:  # more replies to come: this one answers at once
                return reply
            await self.gates.setdefault(key, asyncio.Event()).wait()
            return reply
        await self.gates.setdefault(key, asyncio.Event()).wait()
        return _REPLY

    def root(self, name: str, module: str) -> Path:
        root = self.tmp / name
        (root / "src").mkdir(parents=True)
        (root / "src" / module).write_text("x = 1\n")
        return root

    def release(self, key: str) -> None:
        self.gates.setdefault(key, asyncio.Event()).set()

    def seen(self, name: str) -> list[tuple[frozenset[str] | None, EditScope | None]]:
        return [(e, s) for root, e, s in self.applied if root == name]


def _refine(h: _Harness, root: Path, key: str, module: str) -> asyncio.Task[CodeChange]:
    failures = f'File "src/{module}", line 1\nAssertionError: boom'
    return asyncio.create_task(h.adapter.refine(spec=_SPEC, path=str(root), issue_key=key, failures=failures))


def _implement(h: _Harness, root: Path, key: str) -> asyncio.Task[CodeChange]:
    return asyncio.create_task(h.adapter.implement(spec=_SPEC, path=str(root), issue_key=key))


async def _settle() -> None:
    for _ in range(5):
        await asyncio.sleep(0)


async def test_a_refine_that_finishes_first_does_not_clear_the_other_refines_allowlist(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    h = _Harness(monkeypatch, tmp_path)
    a, b = h.root("featA", "a.py"), h.root("featB", "b.py")
    ta, tb = _refine(h, a, "A-1", "a.py"), _refine(h, b, "B-1", "b.py")
    await _settle()
    assert h.calls == {"A-1": 1, "B-1": 1}  # both parked on the model: the interleaving is real
    h.release("B-1")
    await tb
    h.release("A-1")
    await ta

    assert h.seen("featB") == [(frozenset({"src/b.py"}), None)]
    # Before B49's fix A applied with `None` here: every refine-only guard switched off.
    assert h.seen("featA") == [(frozenset({"src/a.py"}), None)]


async def test_an_implement_during_a_refine_gets_no_refine_allowlist(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    h = _Harness(monkeypatch, tmp_path)
    a, c = h.root("featA", "a.py"), h.root("featC", "c.py")
    ta, tc = _refine(h, a, "A-1", "a.py"), _implement(h, c, "C-1")
    await _settle()
    assert h.calls == {"A-1": 1, "C-1": 1}
    h.release("C-1")
    await tc
    h.release("A-1")
    await ta

    assert h.seen("featC") == [(None, _SCOPE)]
    assert h.seen("featA") == [(frozenset({"src/a.py"}), None)]


async def test_a_refine_during_an_implement_gets_no_implement_scope(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    h = _Harness(monkeypatch, tmp_path)
    a, c = h.root("featA", "a.py"), h.root("featC", "c.py")
    tc, ta = _implement(h, c, "C-1"), _refine(h, a, "A-1", "a.py")
    await _settle()
    assert h.calls == {"C-1": 1, "A-1": 1}
    h.release("A-1")
    await ta
    h.release("C-1")
    await tc

    assert h.seen("featA") == [(frozenset({"src/a.py"}), None)]
    assert h.seen("featC") == [(None, _SCOPE)]


async def test_an_implement_that_finishes_first_does_not_clear_the_other_implements_scope(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    h = _Harness(monkeypatch, tmp_path)
    c, d = h.root("featC", "c.py"), h.root("featD", "d.py")
    tc, td = _implement(h, c, "C-1"), _implement(h, d, "D-1")
    await _settle()
    assert h.calls == {"C-1": 1, "D-1": 1}
    h.release("D-1")
    await td
    h.release("C-1")
    await tc

    assert h.seen("featD") == [(None, _SCOPE)]
    # Before B49's fix C applied with `scope=None`: B44's edit-scope guard switched off.
    assert h.seen("featC") == [(None, _SCOPE)]


async def test_the_allowlist_survives_a_corrective_retry_while_another_refine_runs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The retry loop awaits the model again after a failed apply; the values must ride through."""
    h = _Harness(monkeypatch, tmp_path)
    a, b = h.root("featA", "a.py"), h.root("featB", "b.py")
    # A's first answer claims a change it did not send — a corrective retry — and its second waits.
    claimed = json.dumps({"summary": "Rewrote src/a.py to fix the assertion", "files": []})
    h.replies["A-1"] = [claimed, _REPLY]
    ta = _refine(h, a, "A-1", "a.py")
    await _settle()
    tb = _refine(h, b, "B-1", "b.py")
    await _settle()
    assert h.calls == {"A-1": 2, "B-1": 1}  # A parked on its retry, B on its first call
    h.release("B-1")
    await tb
    h.release("A-1")
    await ta

    assert h.seen("featA") == [(frozenset({"src/a.py"}), None)]
    assert h.seen("featB") == [(frozenset({"src/b.py"}), None)]


async def test_revise_and_author_tests_during_a_refine_get_no_guards(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Neither is refine: they apply with no allowlist and no scope, whatever runs beside them."""
    h = _Harness(monkeypatch, tmp_path)
    a, c, d = h.root("featA", "a.py"), h.root("featC", "c.py"), h.root("featD", "d.py")
    ta = _refine(h, a, "A-1", "a.py")
    tc = asyncio.create_task(
        h.adapter.revise(spec=_SPEC, path=str(c), issue_key="C-1", blockers=["criterion 1"])
    )
    td = asyncio.create_task(h.adapter.author_tests(spec=_SPEC, path=str(d), issue_key="D-1"))
    await _settle()
    assert h.calls == {"A-1": 1, "C-1": 1, "D-1": 1}
    h.release("C-1")
    h.release("D-1")
    await asyncio.gather(tc, td)
    h.release("A-1")
    await ta

    assert h.seen("featC") == [(None, None)]
    assert h.seen("featD") == [(None, None)]
    assert h.seen("featA") == [(frozenset({"src/a.py"}), None)]


def _bound_names(node: ast.AST) -> list[ast.expr]:
    """Every target a statement binds, unpacking tuples, lists and starred names."""
    if isinstance(node, ast.Assign):
        pending = list(node.targets)
    elif isinstance(node, ast.AnnAssign | ast.AugAssign | ast.For | ast.AsyncFor):
        pending = [node.target]
    elif isinstance(node, ast.With | ast.AsyncWith):
        pending = [i.optional_vars for i in node.items if i.optional_vars is not None]
    else:
        return []
    out: list[ast.expr] = []
    while pending:
        target = pending.pop()
        if isinstance(target, ast.Tuple | ast.List):
            pending.extend(target.elts)
        elif isinstance(target, ast.Starred):
            pending.append(target.value)
        else:
            out.append(target)
    return out


def _is_self(node: ast.expr) -> bool:
    return isinstance(node, ast.Name) and node.id == "self"


def test_no_adapter_method_stores_state_on_the_adapter() -> None:
    """The class of B49, not its two instances: outside ``__init__`` nothing binds ``self.<name>``.

    Any method, not only ``async`` ones: a sync setter called from an async method leaks the same
    way. Plain, unpacked, ``for``/``with`` targets and ``setattr(self, …)`` all count.

    Per-worktree caches (``self._conventions[key] = …``) are keyed by the call's own root, so
    item assignment stays allowed; rebinding an attribute is what one call leaks into another.
    """
    tree = ast.parse(Path(codegen.__file__).read_text(encoding="utf-8"))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "LLMCodegenAdapter")
    offenders: list[str] = []
    for fn in cls.body:
        if not isinstance(fn, ast.FunctionDef | ast.AsyncFunctionDef) or fn.name == "__init__":
            continue
        for node in ast.walk(fn):
            for target in _bound_names(node):
                if isinstance(target, ast.Attribute) and _is_self(target.value):
                    offenders.append(f"{fn.name}:{target.lineno} self.{target.attr}")
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "setattr"
                and node.args
                and _is_self(node.args[0])
            ):
                offenders.append(f"{fn.name}:{node.lineno} setattr(self, …)")
    assert offenders == []
