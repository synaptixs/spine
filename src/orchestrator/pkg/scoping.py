"""Repository scoping — how one graph holds facts from several checkouts.

A node id is language-prefixed, not repository-prefixed: ``py:shop.cart.Cart`` is the same id in
every repository that happens to define that class. :meth:`FactBatch.merge` is ``add_node`` in a
loop, so merging two graphs today **silently collapses distinct classes into one node** and
lands their edges on each other. Nothing dangles and ``pkg verify`` reports zero — internally
consistent, externally false, which is the failure this project keeps having.

This module is the decision recorded in ``docs/specs/multi-repo-roadmap.md`` (option C): a repo
scope inside the id **and** a ``repo`` on the provenance, **applied at merge time only**.

**Merge time is the load-bearing half.** ``RepoCodeExtractor`` is untouched, so a single-repo
extraction is byte-identical to what it was — which is what protects the commit-keyed cache, the
committed ``scoreboard.json``, every corpus fixture, and ``understand --check``. Nothing moves
because a feature nobody enabled was added.

## The id form

The scope goes **after** the language prefix, terminated by ``@``::

    py:shop.cart.Cart          →  py:svc-a@shop.cart.Cart
    cpp:Namespace::func        →  cpp:svc-b@Namespace::func
    ts:app/handler.Handler     →  ts:web@app/handler.Handler

After the prefix, because fourteen call sites recover a name with ``id.partition(":")`` or
``split(":", 1)[-1]``. A scope placed *before* the prefix breaks every one of them, including
area grouping in ``knowledge/areas.py`` and ``current_state.py``.

``@`` because ``.``, ``::`` and ``/`` are all taken — by module paths, C++ qualified names and
TypeScript module paths respectively — and no id body in this repository contains ``@`` at all.

**The one collision, and it is real:** npm-scoped TypeScript packages already produce ids like
``ts:@vue/runtime-core:h``, where ``@`` is the *first* character of the body. :func:`unscope_id`
therefore requires the text before the first ``@`` to be a non-empty valid repo key, so an
unscoped npm id is read as unscoped rather than as scoped-to-nothing.

## External nodes are deliberately not scoped

``py:ValueError`` and ``ts:react:useState`` name the same thing in every repository that
references them. Scoping them would make two copies of one fact, and merging them instead is
what lets a merged graph answer *"which of our services depend on this package"* for free.

They are ungrounded by definition — no ``file:line`` — and already excluded from every count
that claims to describe first-party code, so nothing that reports on a repository's own symbols
is affected. The cost is stated rather than hidden: two repositories pinning different versions
of the same package share one node, because the graph does not hold versions.
"""

from __future__ import annotations

import re
from dataclasses import replace
from typing import TYPE_CHECKING

from orchestrator.pkg.facts import FactBatch, Provenance

if TYPE_CHECKING:
    from collections.abc import Mapping

#: A repo key appears in every scoped id, so it must be stable across clones and machines and
#: must not contain the separator. Deliberately narrow: a local directory name is not a stable
#: identity, and neither is anything with a path in it.
REPO_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

SEPARATOR = "@"


class ScopeError(ValueError):
    """A repo key that cannot be used as a scope."""


def validate_repo_key(repo: str) -> str:
    """Return ``repo`` if it can be a scope, else raise.

    Rejected loudly rather than sanitised: a key silently rewritten is a key that differs
    between the machine that wrote the cache and the machine that reads it.
    """
    if not REPO_KEY_RE.match(repo):
        raise ScopeError(
            f"{repo!r} is not a usable repo key — expected {REPO_KEY_RE.pattern}, "
            "and it must be stable across clones (a local directory name is not)"
        )
    return repo


def scope_id(node_id: str, repo: str) -> str:
    """Insert ``repo`` after the language prefix. Idempotent for an already-scoped id."""
    prefix, sep, body = node_id.partition(":")
    if not sep:  # no language prefix — nothing to scope around, leave it alone
        return node_id
    existing, _ = _split_scope(body)
    if existing == repo:
        return node_id
    return f"{prefix}:{repo}{SEPARATOR}{body}"


def unscope_id(node_id: str) -> tuple[str, str]:
    """``(repo, unscoped_id)``. ``repo`` is ``""`` when the id carries no scope."""
    prefix, sep, body = node_id.partition(":")
    if not sep:
        return "", node_id
    repo, rest = _split_scope(body)
    return repo, (f"{prefix}:{rest}" if repo else node_id)


def _split_scope(body: str) -> tuple[str, str]:
    """``(repo, rest)`` for an id body, or ``("", body)`` when it carries no scope.

    An npm-scoped TypeScript id (``@vue/runtime-core:h``) starts with the separator, so the
    emptiness check is what stops it being read as scoped-to-nothing.
    """
    head, sep, rest = body.partition(SEPARATOR)
    if not sep or not head or not REPO_KEY_RE.match(head):
        return "", body
    return head, rest


def scope_batch(batch: FactBatch, repo: str) -> FactBatch:
    """A copy of ``batch`` with every **grounded** node and edge endpoint scoped to ``repo``.

    External nodes keep their ids — see the module docstring. Provenance gains ``repo`` so a
    ``file:line`` in a merged graph can say which checkout it belongs to, while ``str()`` of it
    stays ``file:line``: six call sites parse that back to recover a path.
    """
    validate_repo_key(repo)
    external = {n.id for n in batch.nodes if n.external}

    def remap(node_id: str) -> str:
        return node_id if node_id in external else scope_id(node_id, repo)

    out = FactBatch()
    for node in batch.nodes:
        out.add_node(replace(node, id=remap(node.id), provenance=_with_repo(node.provenance, repo)))
    for edge in batch.edges:
        out.add_edge(
            replace(
                edge,
                src=remap(edge.src),
                dst=remap(edge.dst),
                provenance=_with_repo(edge.provenance, repo),
            )
        )
    return out


def _with_repo(prov: Provenance | None, repo: str) -> Provenance | None:
    return replace(prov, repo=repo) if prov is not None else None


def merge_repos(batches: Mapping[str, FactBatch]) -> FactBatch:
    """One graph from several, each scoped to its repo key. Deterministic.

    Repos are merged in sorted key order so the same inputs always produce the same batch,
    which is what lets a multi-repo graph be cached and diffed like a single-repo one.
    """
    merged = FactBatch()
    for repo in sorted(batches):
        merged.merge(scope_batch(batches[repo], repo))
    return merged


__all__ = [
    "REPO_KEY_RE",
    "SEPARATOR",
    "ScopeError",
    "merge_repos",
    "scope_batch",
    "scope_id",
    "unscope_id",
    "validate_repo_key",
]
