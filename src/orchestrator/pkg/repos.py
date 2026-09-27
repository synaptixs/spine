"""Which repositories are part of this system — declared, never guessed.

A multi-repo graph needs a **key** per repository, and that key is baked into every scoped node
id (see :mod:`orchestrator.pkg.scoping`) and every cache entry. So it has to be stable across a
developer's laptop and CI. Anything derived is not: a directory name differs per checkout, a
remote URL differs between SSH and HTTPS forms, and a key that drifts silently invalidates
caches and makes two runs incomparable.

**So it is declared.** ``.spine/repos.yaml`` sits beside ``.spine/workflows/``, which already
establishes repo-carried configuration::

    repos:
      billing: ../billing-service
      web:     ../storefront
      shared:  ../shared-lib

**Discovery from manifests is deliberately not done.** Reading ``go.mod`` or ``package.json`` to
find "our" repositories means guessing which of forty dependencies belong to the system, which
is a judgement the team owns and a second resolution problem this package does not need.

**The failure mode is loud, which is why a hand-maintained file is acceptable here.** A repo
someone forgot to add produces no nodes, no landing sites and a visibly narrower graph — you
notice on the first run. That is the opposite of a configuration whose absence reads as health.

**Local paths only, for now.** Cloning is ``WorkspaceManager``'s job; pulling it in here would
drag in auth, shallow-clone policy and workspace layout for a feature whose point is that
merging works. Remote support is a later decision, not an oversight.

**``docs:`` — the external documents a repository is described by** (SSPN-80, decisions D6, D15,
D35). Keyed by repository key, beside ``repos:`` and ``joins:``::

    docs:
      billing:
        - name: handbook
          server: atlassian              # an onboarded server in mcp.json
          confluence: {roots: ["12345"], max_depth: 3, max_docs: 100}
        - name: tickets
          server: atlassian
          jira: {jql: "project = BILL AND labels = api", max_issues: 100}
        - name: kb
          server: chroma                 # any RAG system's MCP server (SSPN-82)
          rag: {collection: billing-docs, top_k: 10, max_queries: 300,
                trust_read_only: [chroma_query_documents, chroma_get_documents]}

``rag`` is the third kind: the pull discovers the server's retrieve / list tools from their
input schemas (``tool`` and ``query_arg`` override that), and ``trust_read_only`` is how an
operator vouches, in this committed file, for tools a server does not annotate
``readOnlyHint`` — most RAG servers declare none.

What to read is repository configuration, so it is committed here; the credentials to read it
stay in ``mcp.json`` and the environment. Declaring a source pulls nothing —
``orchestrator mcp ingest-docs`` does, into a cache outside the checkout. A file without the
block is exactly as valid as it was.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from orchestrator.pkg.scoping import ScopeError, validate_repo_key

#: Join kinds this release understands. A kind absent here is refused at load rather than
#: ignored, because a silently dropped join produces missing edges — which look like two
#: services that are not coupled, and read as health.
JOIN_KINDS = frozenset({"http", "data", "package"})

#: Relative to the directory the command runs in — the same `.spine/` a repo already carries.
DEFAULT_CONFIG = Path(".spine") / "repos.yaml"


class RepoConfigError(ValueError):
    """The declaration cannot be used. Always names the file and the offending key."""


@dataclass(frozen=True)
class Join:
    """One declared relationship between two repositories.

    **Declaring a join does not create an edge — it narrows the search.** "web talks to billing
    over HTTP under /v1" is a topology fact; matching ``POST /v1/orders/42`` against
    ``POST /v1/orders/{id}`` is still resolution, and still done from extracted facts. What the
    declaration removes is the part that cannot be resolved from evidence at all: *which*
    repository is even a candidate.
    """

    kind: str
    consumer: str
    provider: str
    base: str = ""

    def __str__(self) -> str:
        under = f" under {self.base}" if self.base else ""
        return f"{self.consumer} -{self.kind}-> {self.provider}{under}"


def joins_from_list(raw: Any, *, where: Path | str = "<inline>") -> tuple[Join, ...]:
    """Validate a ``joins:`` block. Order-independent: sorted, so two spellings agree."""
    if raw in (None, []):
        return ()
    if not isinstance(raw, list):
        raise RepoConfigError(f"{where}: 'joins' must be a list")
    out: list[Join] = []
    for i, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise RepoConfigError(f"{where}: joins[{i}] is not a mapping")
        kind = str(entry.get("kind", "")).strip()
        if kind not in JOIN_KINDS:
            raise RepoConfigError(
                f"{where}: joins[{i}] has kind {kind!r} — expected one of {sorted(JOIN_KINDS)}"
            )
        consumer, provider = str(entry.get("consumer", "")), str(entry.get("provider", ""))
        for role, value in (("consumer", consumer), ("provider", provider)):
            if not value:
                raise RepoConfigError(f"{where}: joins[{i}] has no {role}")
            try:
                validate_repo_key(value)
            except ScopeError as exc:
                raise RepoConfigError(f"{where}: joins[{i}] {role} — {exc}") from exc
        if consumer == provider:
            raise RepoConfigError(f"{where}: joins[{i}] joins {consumer!r} to itself")
        base = str(entry.get("base", "")).rstrip("/")
        if base and kind != "http":
            raise RepoConfigError(f"{where}: joins[{i}] — 'base' means a URL prefix and applies to http only")
        out.append(Join(kind, consumer, provider, base))
    return tuple(sorted(out, key=lambda j: (j.kind, j.consumer, j.provider, j.base)))


#: The external document kinds a ``docs:`` entry can declare, each with the settings it takes.
DOC_SOURCE_KINDS = frozenset({"confluence", "jira", "rag"})
#: The kinds whose coverage the declaration fixes — their unbound claims can be drift (D20). A
#: ``rag`` source is enumerated only when its pull walked the corpus (its manifest says so).
ENUMERATED_KINDS = frozenset({"confluence", "jira"})
#: A source name becomes a cache folder name, so it is held to what is safe as one everywhere.
_SOURCE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_CONFLUENCE_KEYS = frozenset({"roots", "max_depth", "max_docs"})
_JIRA_KEYS = frozenset({"jql", "max_issues"})
_RAG_KEYS = frozenset(
    {"collection", "tool", "query_arg", "top_k", "max_queries", "max_chunks", "trust_read_only"}
)
_KIND_KEYS = {"confluence": _CONFLUENCE_KEYS, "jira": _JIRA_KEYS, "rag": _RAG_KEYS}
_ENTRY_KEYS = frozenset({"name", "server"}) | DOC_SOURCE_KINDS


@dataclass(frozen=True)
class DocSource:
    """One external document source a repository declares under ``docs:``.

    ``confluence`` walks page trees from ``roots`` (page ids) to ``max_depth``, at most
    ``max_docs`` pages; ``jira`` pages a JQL search to at most ``max_issues``. Both are
    *enumerated* sources — what they cover is fixed by the declaration, not by what anyone asked
    — which is why their unbound claims can be reported as drift (D20).

    ``rag`` (SSPN-82) pulls chunks from a retrieval server: by walking the corpus when the server
    offers a list tool (at most ``max_chunks``), else by one query per module and class (at most
    ``max_queries``, ``top_k`` chunks each). ``collection``, ``tool`` and ``query_arg`` pin what
    discovery would otherwise infer; ``trust_read_only`` names the tools the operator vouches
    are read-only when the server does not say so.
    """

    name: str
    server: str
    kind: str
    roots: tuple[str, ...] = ()
    max_depth: int = 3
    max_docs: int = 100
    jql: str = ""
    max_issues: int = 100
    collection: str = ""
    tool: str = ""
    query_arg: str = ""
    top_k: int = 10
    max_queries: int = 300
    max_chunks: int = 2000
    trust_read_only: tuple[str, ...] = ()

    @property
    def enumerated(self) -> bool:
        """Fixed by the declaration alone — a ``rag`` source's pull decides for itself."""
        return self.kind in ENUMERATED_KINDS

    @property
    def cap(self) -> int:
        if self.kind == "confluence":
            return self.max_docs
        return self.max_issues if self.kind == "jira" else self.max_chunks


def _int_setting(block: dict[str, Any], key: str, default: int, *, minimum: int, where: str) -> int:
    value = block.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise RepoConfigError(f"{where}: '{key}' must be an integer >= {minimum}, got {value!r}")
    return value


def _doc_source(entry: Any, *, where: str) -> DocSource:
    if not isinstance(entry, dict):
        raise RepoConfigError(f"{where} is not a mapping")
    unknown = sorted(str(k) for k in entry if k not in _ENTRY_KEYS)
    if unknown:
        raise RepoConfigError(
            f"{where} has unknown key(s) {unknown} — expected name, server, confluence|jira|rag"
        )
    name = entry.get("name")
    if not isinstance(name, str) or not _SOURCE_NAME_RE.match(name):
        raise RepoConfigError(
            f"{where}: 'name' must be letters, digits, '.', '_' or '-' (it names a cache folder), "
            f"got {name!r}"
        )
    server = entry.get("server")
    if not isinstance(server, str) or not server.strip():
        raise RepoConfigError(f"{where}: 'server' must name an onboarded MCP server")
    kinds = [k for k in sorted(DOC_SOURCE_KINDS) if k in entry]
    if len(kinds) != 1:
        raise RepoConfigError(
            f"{where}: declare exactly one of 'confluence', 'jira' or 'rag', found {kinds or 'none'}"
        )
    kind = kinds[0]
    block = entry[kind]
    if not isinstance(block, dict):
        raise RepoConfigError(f"{where}: '{kind}' must be a mapping")
    allowed = _KIND_KEYS[kind]
    extra = sorted(str(k) for k in block if k not in allowed)
    if extra:
        raise RepoConfigError(f"{where}: '{kind}' has unknown key(s) {extra} — expected {sorted(allowed)}")
    if kind == "confluence":
        roots = block.get("roots")
        if not isinstance(roots, list) or not roots:
            raise RepoConfigError(f"{where}: 'confluence.roots' must be a non-empty list of page ids")
        ids: list[str] = []
        for root in roots:
            if isinstance(root, bool) or not isinstance(root, str | int) or not str(root).strip():
                raise RepoConfigError(f"{where}: 'confluence.roots' holds {root!r}, which is not a page id")
            ids.append(str(root).strip())
        return DocSource(
            name=name,
            server=server.strip(),
            kind=kind,
            roots=tuple(dict.fromkeys(ids)),
            max_depth=_int_setting(block, "max_depth", 3, minimum=0, where=where),
            max_docs=_int_setting(block, "max_docs", 100, minimum=1, where=where),
        )
    if kind == "rag":
        return _rag_source(name, server.strip(), block, where=where)
    jql = block.get("jql")
    if not isinstance(jql, str) or not jql.strip():
        raise RepoConfigError(f"{where}: 'jira.jql' must be a non-empty JQL string")
    return DocSource(
        name=name,
        server=server.strip(),
        kind=kind,
        jql=jql.strip(),
        max_issues=_int_setting(block, "max_issues", 100, minimum=1, where=where),
    )


def _optional_name(block: dict[str, Any], key: str, *, where: str) -> str:
    value = block.get(key, "")
    if not isinstance(value, str) or (key in block and not value.strip()):
        raise RepoConfigError(f"{where}: 'rag.{key}' must be a non-empty string, got {value!r}")
    return value.strip()


def _rag_source(name: str, server: str, block: dict[str, Any], *, where: str) -> DocSource:
    trusted = block.get("trust_read_only", [])
    if not isinstance(trusted, list) or not all(isinstance(t, str) and t.strip() for t in trusted):
        raise RepoConfigError(f"{where}: 'rag.trust_read_only' must be a list of tool names, got {trusted!r}")
    return DocSource(
        name=name,
        server=server,
        kind="rag",
        collection=_optional_name(block, "collection", where=where),
        tool=_optional_name(block, "tool", where=where),
        query_arg=_optional_name(block, "query_arg", where=where),
        top_k=_int_setting(block, "top_k", 10, minimum=1, where=where),
        max_queries=_int_setting(block, "max_queries", 300, minimum=1, where=where),
        max_chunks=_int_setting(block, "max_chunks", 2000, minimum=1, where=where),
        trust_read_only=tuple(dict.fromkeys(t.strip() for t in trusted)),
    )


def docs_from_mapping(
    raw: Any, *, known: set[str] | frozenset[str], where: Path | str = "<inline>"
) -> tuple[tuple[str, tuple[DocSource, ...]], ...]:
    """Validate a ``docs:`` block — the ``joins:`` pattern: every error names ``docs[<key>][i]``.

    Keys must be declared repositories; a source's ``name`` is unique within its repository.
    Sorted by key, sources in declaration order, so two spellings of one file agree.
    """
    if raw in (None, {}):
        return ()
    if not isinstance(raw, dict):
        raise RepoConfigError(f"{where}: 'docs' must be a mapping of repo key to a list of sources")
    out: list[tuple[str, tuple[DocSource, ...]]] = []
    for key, entries in raw.items():
        if key not in known:
            raise RepoConfigError(
                f"{where}: docs[{key}] names an undeclared repository — declared repos are {sorted(known)}"
            )
        if not isinstance(entries, list):
            raise RepoConfigError(f"{where}: docs[{key}] must be a list of sources")
        sources: list[DocSource] = []
        seen: set[str] = set()
        for i, entry in enumerate(entries):
            source = _doc_source(entry, where=f"{where}: docs[{key}][{i}]")
            if source.name in seen:
                raise RepoConfigError(f"{where}: docs[{key}][{i}] repeats the source name {source.name!r}")
            seen.add(source.name)
            sources.append(source)
        if sources:
            out.append((str(key), tuple(sources)))
    return tuple(sorted(out))


@dataclass(frozen=True)
class RepoSet:
    """Repository keys mapped to their checkout roots, in a stable order.

    Ordering is by key, not by declaration order, so two people whose YAML lists the same repos
    in a different order still produce the same merged graph. Determinism at this layer is what
    lets the merged graph be diffed at all.
    """

    roots: tuple[tuple[str, Path], ...]
    source: Path | None = None
    joins: tuple[Join, ...] = ()
    #: The ``docs:`` block — ``(repo key, its external doc sources)``, sorted by key.
    docs: tuple[tuple[str, tuple[DocSource, ...]], ...] = ()

    def __post_init__(self) -> None:
        if not self.roots:
            raise RepoConfigError(f"{self.source or '<inline>'}: no repositories declared")

    @property
    def keys(self) -> tuple[str, ...]:
        return tuple(key for key, _ in self.roots)

    def path(self, key: str) -> Path:
        for candidate, root in self.roots:
            if candidate == key:
                return root
        raise KeyError(key)

    def doc_sources(self, key: str) -> tuple[DocSource, ...]:
        """The external doc sources ``key`` declares, or ``()``."""
        return next((sources for candidate, sources in self.docs if candidate == key), ())

    def key_for(self, root: Path | str) -> str | None:
        """The key whose checkout is ``root`` (both resolved), or ``None``."""
        target = Path(root).resolve()
        return next((key for key, path in self.roots if path == target), None)

    def __len__(self) -> int:
        return len(self.roots)

    def __iter__(self) -> Any:
        return iter(self.roots)


def from_mapping(
    mapping: dict[str, Any],
    *,
    base: Path,
    source: Path | None = None,
    joins: Any = None,
    docs: Any = None,
) -> RepoSet:
    """Build a :class:`RepoSet` from ``{key: path}``. Relative paths resolve against ``base``.

    Every key is validated here rather than at merge time. A bad key caught at load names the
    file it came from; the same key caught later surfaces as a malformed node id with nothing
    pointing back at its origin.
    """
    where = source or Path("<inline>")
    if not isinstance(mapping, dict):
        raise RepoConfigError(f"{where}: 'repos' must be a mapping of key to path")

    roots: list[tuple[str, Path]] = []
    seen_paths: dict[Path, str] = {}
    for key, raw in mapping.items():
        if not isinstance(key, str):
            raise RepoConfigError(f"{where}: repo key {key!r} is not a string")
        try:
            validate_repo_key(key)
        except ScopeError as exc:
            raise RepoConfigError(f"{where}: {exc}") from exc
        if not isinstance(raw, str) or not raw.strip():
            raise RepoConfigError(f"{where}: repo {key!r} has no path")

        root = Path(raw).expanduser()
        root = root if root.is_absolute() else (base / root)
        root = root.resolve()
        if not root.is_dir():
            raise RepoConfigError(f"{where}: repo {key!r} points at {root}, which is not a directory")
        # Two keys for one checkout would scope the same facts twice under different ids —
        # every symbol duplicated, every count doubled, and nothing to signal it.
        if root in seen_paths:
            raise RepoConfigError(f"{where}: repos {seen_paths[root]!r} and {key!r} both point at {root}")
        seen_paths[root] = key
        roots.append((key, root))

    # A root inside another declared root is fine when the inner one is a git checkout of its
    # own (a submodule, most often): the outer repo's walk stops at that boundary, so each
    # file is scoped exactly once. A plain subdirectory has no boundary — the outer walk
    # reaches every file the inner key also claims, and the merged graph carries every
    # symbol twice under two ids. Refuse that at load, where the message can name the file.
    for inner_key, inner in roots:
        for outer_key, outer in roots:
            if inner is outer or not inner.is_relative_to(outer):
                continue
            if not (inner / ".git").exists():
                raise RepoConfigError(
                    f"{where}: repo {inner_key!r} at {inner} is inside repo {outer_key!r} at {outer} "
                    "but is not a git checkout of its own, so both keys would scope the same files "
                    "— make it a submodule, or declare only one of them"
                )

    declared = joins_from_list(joins, where=where)
    known = {key for key, _ in roots}
    for join in declared:
        for role, key in (("consumer", join.consumer), ("provider", join.provider)):
            if key not in known:
                raise RepoConfigError(
                    f"{where}: join {join} names an undeclared {role} {key!r} "
                    f"— declared repos are {sorted(known)}"
                )
    return RepoSet(tuple(sorted(roots)), source, declared, docs_from_mapping(docs, known=known, where=where))


def load_repo_config(path: Path | str, *, base: Path | None = None) -> RepoSet:
    """Read a ``repos.yaml``. ``base`` defaults to the file's own directory."""
    import yaml

    config = Path(path)
    try:
        text = config.read_text(encoding="utf-8")
    except OSError as exc:
        raise RepoConfigError(f"{config}: cannot be read — {exc}") from exc
    try:
        doc = yaml.safe_load(text) or {}
    except yaml.YAMLError as exc:
        raise RepoConfigError(f"{config}: invalid YAML — {exc}") from exc
    if not isinstance(doc, dict) or "repos" not in doc:
        raise RepoConfigError(f"{config}: expected a top-level 'repos:' mapping")
    return from_mapping(
        doc["repos"], base=base or config.parent, source=config, joins=doc.get("joins"), docs=doc.get("docs")
    )


def find_repo_config(start: Path | str = ".") -> Path | None:
    """``.spine/repos.yaml`` under ``start``, or None. Does not walk upward.

    Deliberately not a search: a config found in a parent directory would silently change what
    a command in a subdirectory means.
    """
    candidate = Path(start) / DEFAULT_CONFIG
    return candidate if candidate.is_file() else None


__all__ = [
    "DEFAULT_CONFIG",
    "DOC_SOURCE_KINDS",
    "ENUMERATED_KINDS",
    "JOIN_KINDS",
    "DocSource",
    "Join",
    "RepoConfigError",
    "RepoSet",
    "docs_from_mapping",
    "joins_from_list",
    "find_repo_config",
    "from_mapping",
    "load_repo_config",
]
