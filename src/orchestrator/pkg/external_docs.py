"""External documents — pulled over MCP, cached outside the checkout, bound at read time.

A repository is described by more than the markdown it carries: a Confluence space, the Jira
issues that specified it. ``orchestrator mcp ingest-docs`` pulls those (SSPN-80) into a cache;
this module owns everything that happens to them after the network: the cache format, the
conversion of an external page into the same :class:`~orchestrator.pkg.docs.DocPage` a local
file becomes, the binding that turns a page into ``Doc`` nodes and ``MENTIONS`` edges, the
de-duplication against the repository's own docs, and the standing (age, failure) every answer
that used them must carry.

**Three rules hold this module in place, and each has a reason.**

*It never imports ``orchestrator.mcp`` or ``orchestrator.intake``.* The pull is a network
operation with credentials; the read is a pure function of a cache and a graph. Keeping the
network on the other side of the package boundary is what lets a test (and a reader) see that
nothing here can make a call.

*Nothing in ``knowledge/`` reads it* (decision D8). ``understand`` and ``state`` are
deterministic because CI can reproduce them; CI has no Confluence credentials, so a pulled page
in ``episteme/`` would make ``understand --check`` fail on a diff nobody can reproduce. External
docs appear in ``blast_radius``, ``explain_symbol`` and ``docs_for`` — the read tools — and only
there.

*Binding happens at read time* (D17). The cache holds text, never edges. A symbol renamed after
the pull stops being named the next time a tool reads the cache; a stored edge would keep
pointing at code that no longer exists until someone remembered to re-pull.

**The admission rule is the local one.** A section becomes a ``MENTIONS`` edge only where the
same :class:`~orchestrator.pkg.docs.DocReconciler` finds exactly one anchor — a retrieved page is
not a mention until the binder says so. And a section whose text is identical to one of the
repository's own (a Confluence mirror of ``docs/``) is listed once, under the repository doc,
with ``also_in`` naming the source (D19): the repository copy is the one reviewed with the code.

**RAG chunks (SSPN-82) ride the same path.** A ``rag`` source caches chunks (``kind: "chunk"``) —
one ``Doc`` node each, never section-split, id ``doc:mcp:<server>/<chunk id>`` — plus a
``queries.json`` mapping each query the pull asked to the chunk ids it got back. Retrieval is not
admission: a chunk is listed only where the binder finds one anchor, exactly as above. What the
server returned for a symbol's query but the binder cannot tie to it is *counted*, per symbol, at
read time (:meth:`ExternalBinding.retrieval`) — so an answer can say "10 retrieved, 2 name the
symbol" without inventing the other eight edges (D9). A chunk whose source metadata names a doc
file of this repository (exact repo-relative path) is that file, indexed: it collapses into the
file's sections as ``also_in`` rather than listing twice (Q42). Only a pull that walked the corpus
reports drift: a query-driven pull only ever holds text retrieved *for* a current name.
"""

from __future__ import annotations

import hashlib
import json
import os
import posixpath
import re
import shutil
import tempfile
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import unquote

from orchestrator.pkg.doc_link import symbolish_drift
from orchestrator.pkg.doc_source import html_to_text, read_doc_pages, split_sections
from orchestrator.pkg.docs import DocDriftFinding, DocPage, DocReconciler, extract_mentions
from orchestrator.pkg.facts import Edge, EdgeKind, FactBatch, Node, NodeKind, Provenance
from orchestrator.pkg.repos import DocSource

#: Overrides the cache root (``~/.cache/orchestrator/docs``) — tests, CI, a shared volume.
ENV_CACHE_DIR = "ORCHESTRATOR_DOCS_CACHE_DIR"
#: Older than this, a pull is reported ``stale`` — still shown, never hidden.
STALE_AFTER_DAYS = 7
PAGES_FILE = "pages.jsonl"
MANIFEST_FILE = "manifest.json"
#: A query-driven ``rag`` pull's record of what it asked: query string → chunk ids returned.
QUERIES_FILE = "queries.json"
#: How a ``rag`` pull covered its corpus — recorded in the manifest as ``strategy``.
STRATEGY_ENUMERATE = "enumerate"
STRATEGY_QUERY = "query"

# A body that opens with a tag and holds a block element is markup, whatever the server claims.
_BLOCK_TAG_RE = re.compile(
    r"<(p|div|h[1-6]|ul|ol|li|table|pre|section|article|br|blockquote)\b", re.IGNORECASE
)
_WS_RE = re.compile(r"\s+")


def _now() -> datetime:
    """The clock every age is measured against — one seam for tests to move."""
    return datetime.now(UTC)


def utc_stamp(moment: datetime | None = None) -> str:
    """``2026-09-27T10:00:00Z`` — the one timestamp format the cache writes and reads."""
    return (moment or _now()).astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def docs_cache_root() -> Path:
    """``$ORCHESTRATOR_DOCS_CACHE_DIR``, else ``~/.cache/orchestrator/docs``."""
    override = os.environ.get(ENV_CACHE_DIR, "").strip()
    return Path(override).expanduser() if override else Path.home() / ".cache" / "orchestrator" / "docs"


def repo_cache_dir(repo_root: Path | str, repo_key: str, *, base: Path | None = None) -> Path:
    """``<cache>/<sha256(resolved root)[:16]>-<repo key>`` — one folder per checkout.

    The hash keeps two checkouts of one repository (a worktree, a CI runner) from sharing pulls
    they did not make; the key keeps the folder readable to a human listing the cache."""
    digest = hashlib.sha256(str(Path(repo_root).resolve()).encode("utf-8")).hexdigest()[:16]
    return (base or docs_cache_root()) / f"{digest}-{repo_key}"


def source_cache_dir(
    repo_root: Path | str, repo_key: str, source_name: str, *, base: Path | None = None
) -> Path:
    """The folder one source's last good pull lives in."""
    return repo_cache_dir(repo_root, repo_key, base=base) / source_name


def failure_path(dest: Path) -> Path:
    """``<source>.failure.json`` beside the source folder — never inside it, so a failed pull
    leaves the last good folder byte-for-byte untouched."""
    return dest.parent / f"{dest.name}.failure.json"


def _previous_path(dest: Path) -> Path:
    return dest.parent / f".{dest.name}.previous"


@dataclass(frozen=True)
class ExternalPage:
    """One pulled document, as cached: a Confluence page, a Jira issue or a RAG chunk.

    ``id`` is the source's own identifier (page id, issue key, chunk id — or ``sha1(text)[:12]``
    for a chunk the server gave none), so a doc id built from it is stable across pulls and
    points back at the page. ``source`` is a chunk's origin as the server's metadata names it (a
    path or uri — what repo-path collapse compares); ``score`` its relevance, when reported.
    Both are written only when set, so a P2 page's cache record is unchanged."""

    id: str
    title: str
    text: str
    url: str = ""
    kind: str = "confluence"
    source: str = ""
    score: float | None = None

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "id": self.id,
            "title": self.title,
            "url": self.url,
            "text": self.text,
            "kind": self.kind,
        }
        if self.source:
            out["source"] = self.source
        if self.score is not None:
            out["score"] = self.score
        return out

    @classmethod
    def from_json(cls, raw: Mapping[str, Any]) -> ExternalPage:
        score = raw.get("score")
        return cls(
            id=str(raw["id"]),
            title=str(raw.get("title") or raw["id"]),
            text=str(raw.get("text") or ""),
            url=str(raw.get("url") or ""),
            kind=str(raw.get("kind") or "confluence"),
            source=str(raw.get("source") or ""),
            score=float(score) if isinstance(score, int | float) and not isinstance(score, bool) else None,
        )


def chunk_id(text: str) -> str:
    """The id of a chunk its server gave none: ``sha1(text)[:12]`` — stable across pulls."""
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]  # noqa: S324 — an id, not security


_NAME_SPLIT_RE = re.compile(r"::|[./:\\#]")


def query_name(name: str) -> str:
    """A symbol's short name — the query a ``rag`` pull asks for it, and the key a read looks
    its retrieval up by: ``lib.client`` → ``client``, ``pkg::Client`` → ``Client``."""
    parts = [p for p in _NAME_SPLIT_RE.split(name) if p]
    return parts[-1] if parts else ""


def rag_queries(batch: FactBatch, cap: int) -> tuple[list[str], int]:
    """``(queries, candidates)`` for a query-driven ``rag`` pull (D16).

    One query per module and class — not methods: a method's name is rarely what prose calls
    it — ordered by how many distinct nodes call or import it, most first, then by id, so the
    cap keeps the symbols a change most often ripples from. Queries are short names,
    de-duplicated (two modules called ``utils`` ask once); ``candidates`` is how many distinct
    queries there were before ``cap``, so the pull can say "queried N of M"."""
    wanted = (NodeKind.MODULE, NodeKind.TYPE)
    callers: dict[str, set[str]] = {}
    for edge in batch.edges:
        if edge.kind in (EdgeKind.CALLS, EdgeKind.IMPORTS) and edge.src != edge.dst:
            callers.setdefault(edge.dst, set()).add(edge.src)
    nodes = sorted(
        (n for n in batch.nodes if n.kind in wanted),
        key=lambda n: (-len(callers.get(n.id, ())), n.id),
    )
    ordered: list[str] = []
    seen: set[str] = set()
    for node in nodes:
        name = query_name(node.name)
        if name and not name.startswith("__") and name not in seen:
            seen.add(name)
            ordered.append(name)
    return ordered[:cap], len(ordered)


def looks_like_html(body: str) -> bool:
    """True when ``body`` is markup: starts with ``<`` and holds a block tag."""
    return body.lstrip().startswith("<") and _BLOCK_TAG_RE.search(body) is not None


def page_text(body: str) -> str:
    """A pulled body as the markdown the binder reads: markdown as-is, HTML flattened by the
    same reader a local ``.html`` file goes through (headings become ATX headings, so a page
    still splits into sections)."""
    if looks_like_html(body):
        flattened = html_to_text(body)
        if flattened is not None:
            return flattened
    return body.strip()


def write_pull(
    dest: Path,
    pages: Iterable[ExternalPage],
    manifest: Mapping[str, Any],
    *,
    queries: Mapping[str, Sequence[str]] | None = None,
) -> dict[str, Any]:
    """Replace ``dest`` with a fresh pull — whole, or not at all (D37).

    Written to a temporary folder beside ``dest`` and swapped in with ``os.replace``, so a
    reader sees the old pull or the new one, never half of either. A crash before the swap
    leaves the old folder untouched; one between the two renames leaves it at
    ``.<name>.previous``, which :func:`read_source` falls back to. Success clears any
    ``failure.json`` an earlier attempt left. ``queries`` (a query-driven ``rag`` pull's
    query → chunk ids) is written in the same folder, so it swaps with the chunks it indexes.
    Returns the manifest as written."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix=f".{dest.name}.tmp-", dir=dest.parent))
    try:
        ordered = sorted(pages, key=lambda p: p.id)
        body = "".join(json.dumps(p.to_json(), sort_keys=True, ensure_ascii=False) + "\n" for p in ordered)
        (tmp / PAGES_FILE).write_text(body, encoding="utf-8")
        if queries is not None:
            asked = {q: list(ids) for q, ids in sorted(queries.items())}
            (tmp / QUERIES_FILE).write_text(
                json.dumps(asked, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8"
            )
        final = {
            **manifest,
            "content_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
            "status": "ok",
        }
        (tmp / MANIFEST_FILE).write_text(json.dumps(final, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        previous = _previous_path(dest)
        shutil.rmtree(previous, ignore_errors=True)
        if dest.exists():
            os.replace(dest, previous)
        os.replace(tmp, dest)
        shutil.rmtree(previous, ignore_errors=True)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    failure_path(dest).unlink(missing_ok=True)
    return final


def record_failure(dest: Path, reason: str, *, moment: datetime | None = None) -> Path:
    """Write ``failure.json`` beside ``dest`` and leave the last good pull alone (D21)."""
    path = failure_path(dest)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"failed_at": utc_stamp(moment), "reason": reason}
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


@dataclass(frozen=True)
class CachedSource:
    """What the cache holds for one source: its pages and how it came to hold them.

    ``status`` is ``ok`` (a good pull, no later failure), ``failed`` (the latest attempt failed —
    ``pages`` are the last good pull's, if there was one) or ``never_pulled``."""

    source: DocSource
    status: str
    pages: tuple[ExternalPage, ...] = ()
    pulled_at: str | None = None
    error: str | None = None
    manifest: Mapping[str, Any] = field(default_factory=dict)
    #: A query-driven ``rag`` pull's query → chunk ids; empty for every other pull.
    queries: Mapping[str, tuple[str, ...]] = field(default_factory=dict)

    @property
    def strategy(self) -> str | None:
        """``enumerate`` / ``query`` for a ``rag`` pull, as its manifest recorded it."""
        value = self.manifest.get("strategy")
        return str(value) if value else None

    @property
    def enumerated(self) -> bool:
        """Coverage fixed by the declaration or by a walk of the whole corpus — the pulls whose
        unbound claims can be drift (D20)."""
        return self.source.enumerated or self.strategy == STRATEGY_ENUMERATE


def _read_queries(folder: Path) -> dict[str, tuple[str, ...]]:
    path = folder / QUERIES_FILE
    if not path.is_file():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("queries.json is not an object")
    return {str(q): tuple(str(i) for i in ids) for q, ids in raw.items() if isinstance(ids, list)}


def _read_folder(
    folder: Path,
) -> tuple[tuple[ExternalPage, ...], dict[str, Any], dict[str, tuple[str, ...]]]:
    manifest = json.loads((folder / MANIFEST_FILE).read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError("manifest is not an object")
    pages = tuple(
        ExternalPage.from_json(json.loads(line))
        for line in (folder / PAGES_FILE).read_text(encoding="utf-8").splitlines()
        if line.strip()
    )
    return tuple(sorted(pages, key=lambda p: p.id)), manifest, _read_queries(folder)


def read_source(dest: Path, source: DocSource) -> CachedSource:
    """The cached state of one source. Never raises: an unreadable cache is a ``failed``
    standing with the reason, because a tool answer must survive a corrupt cache."""
    folder = dest if dest.is_dir() else _previous_path(dest)
    failure: dict[str, Any] | None = None
    fpath = failure_path(dest)
    if fpath.is_file():
        try:
            loaded = json.loads(fpath.read_text(encoding="utf-8"))
            failure = loaded if isinstance(loaded, dict) else {"reason": "unreadable failure record"}
        except (OSError, ValueError):
            failure = {"reason": "unreadable failure record"}
    if not folder.is_dir():
        if failure is None:
            return CachedSource(source, "never_pulled")
        return CachedSource(source, "failed", error=str(failure.get("reason") or "unknown"))
    try:
        pages, manifest, queries = _read_folder(folder)
    except (OSError, ValueError, KeyError) as exc:
        return CachedSource(source, "failed", error=f"cache unreadable: {type(exc).__name__}: {exc}")
    pulled_at = str(manifest.get("pulled_at") or "") or None
    if failure is not None:
        return CachedSource(
            source, "failed", pages, pulled_at, str(failure.get("reason") or "unknown"), manifest, queries
        )
    return CachedSource(source, "ok", pages, pulled_at, None, manifest, queries)


def _parse_stamp(stamp: str) -> datetime | None:
    try:
        return datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError:
        return None


def standing(cached: CachedSource, *, now: datetime | None = None) -> dict[str, Any]:
    """How far to trust one source's pages: status, age, ``stale`` past seven days, and why a
    failed pull failed — with the date of the data still being shown."""
    src = cached.source
    out: dict[str, Any] = {
        "source": src.name,
        "server": src.server,
        "status": cached.status,
        "pulled_at": cached.pulled_at,
        "age_days": None,
        "stale": False,
    }
    when = _parse_stamp(cached.pulled_at) if cached.pulled_at else None
    if when is not None:
        age = ((now or _now()) - when).total_seconds() / 86400
        out["age_days"] = round(age, 1)
        out["stale"] = age > STALE_AFTER_DAYS
    if cached.status == "failed":
        shown = f"showing data from {cached.pulled_at}" if cached.pulled_at else "no earlier pull to show"
        out["error"] = f"last pull failed: {cached.error}; {shown}"
    elif cached.status == "never_pulled":
        out["note"] = "never pulled — run `orchestrator mcp ingest-docs`"
    return out


def doc_pages(pages: Iterable[ExternalPage], server: str) -> list[tuple[DocPage, ExternalPage]]:
    """External pages as the ``DocPage`` rows the binder reads, each with the page it came from.

    Ids follow D18 — ``mcp:<server>/<native id>`` plus ``#<section>`` — so the ``Doc`` node is
    ``doc:mcp:<server>/<id>#<section>``. A Confluence page splits by heading exactly like a local
    markdown file (fences respected); a Jira issue stays one page, and so does a RAG chunk — it
    is already a section, cut by the server (``doc:mcp:<server>/<chunk id>``)."""
    out: list[tuple[DocPage, ExternalPage]] = []
    for page in sorted(pages, key=lambda p: p.id):
        ref = f"mcp:{server}/{page.id}"
        whole = DocPage(title=ref, text=page.text, url=page.url, source_file=ref)
        sections = split_sections(whole, fences=True) if page.kind == "confluence" else [whole]
        out.extend((section, page) for section in sections)
    return out


def normalized(text: str) -> str:
    """Whitespace collapsed and stripped — the whole of the de-dup comparison (D19, D28)."""
    return _WS_RE.sub(" ", text).strip()


def _repo_sections(repo_pages: Iterable[DocPage]) -> dict[str, str]:
    """normalized text → the repository doc id holding it (the first, in walk order)."""
    by_text: dict[str, str] = {}
    for page in repo_pages:
        key = normalized(page.text)
        if key:
            by_text.setdefault(key, f"doc:{page.title}")
    return by_text


_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")


def repo_relative(source: str, repo_root: Path | str) -> str | None:
    """A chunk's ``source`` as a repo-relative posix path, or ``None`` when it names no file
    of this checkout (a URL, a path outside the root, nothing at all).

    ``file://`` is unwrapped, backslashes become slashes, an absolute path under the resolved
    root is made relative, ``./`` and ``..`` segments are normalised. That is all: Q42 asks
    for an exact match after normalising, never a basename or suffix guess."""
    text = source.strip()
    if text.lower().startswith("file://"):
        text = unquote(text[7:])
    text = text.replace("\\", "/")
    if not text or _SCHEME_RE.match(text):
        return None
    path = PurePosixPath(text)
    if path.is_absolute():
        root = PurePosixPath(Path(repo_root).resolve().as_posix())
        try:
            text = path.relative_to(root).as_posix()
        except ValueError:
            return None
    text = posixpath.normpath(text)
    if text in ("", ".") or text == ".." or text.startswith("../"):
        return None
    return text


def _repo_files(repo_pages: Iterable[DocPage]) -> dict[str, list[str]]:
    """repo-relative doc file → the repository doc ids (its sections) read from it."""
    by_file: dict[str, list[str]] = {}
    for page in repo_pages:
        if page.source_file:
            by_file.setdefault(page.source_file, []).append(f"doc:{page.title}")
    return by_file


def collapse_stats(repo_root: Path | str, pages: Sequence[ExternalPage], server: str) -> dict[str, int]:
    """How many of a pull's sections are identical to one of the repository's own — the
    collapse rate D28 asked to be measured before any fuzzier de-dup is considered — and, for
    RAG chunks, how many name one of the repository's doc files as their source (Q42)."""
    sections = doc_pages(pages, server)
    repo_pages = read_doc_pages(repo_root) if sections else []
    repo = _repo_sections(repo_pages)
    same = sum(1 for section, _page in sections if normalized(section.text) in repo)
    out = {"sections": len(sections), "identical_to_repo_sections": same}
    if any(p.kind == "chunk" for p in pages):
        files = _repo_files(repo_pages)
        out["from_repo_doc_files"] = sum(
            1 for p in pages if p.source and repo_relative(p.source, repo_root) in files
        )
    return out


@dataclass
class ExternalBinding:
    """External docs bound against one repository's graph — what a read tool adds beside the
    repository's own doc links.

    ``meta`` carries, per admitted doc id, what a ``DocRef`` reports about its origin;
    ``also_in`` names, per *repository* doc id, the sources holding an identical copy (or, for a
    RAG chunk, indexing that very file). ``retrieved`` maps each query a query-driven ``rag``
    pull asked to the chunk doc ids it got back, and ``anchors`` every such chunk to the symbols
    the binder ties it to — listed or collapsed — so :meth:`retrieval` can count per symbol."""

    nodes: list[Node] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    meta: dict[str, dict[str, str]] = field(default_factory=dict)
    also_in: dict[str, list[str]] = field(default_factory=dict)
    standings: list[dict[str, Any]] = field(default_factory=list)
    drift: list[DocDriftFinding] = field(default_factory=list)
    retrieved: dict[str, set[str]] = field(default_factory=dict)
    anchors: dict[str, set[str]] = field(default_factory=dict)

    @property
    def doc_count(self) -> int:
        return len(self.nodes)

    def retrieval(self, node: Node, node_id: str | None = None) -> tuple[int, int] | None:
        """``(retrieved, naming)`` for a symbol a query-driven ``rag`` pull asked about.

        ``retrieved`` is how many chunks the servers returned for the symbol's query (its short
        name); ``naming`` how many of those the binder ties to *this* symbol (``node_id``, when
        the caller holds the node under another id). ``None`` when no pull asked — a method, a
        symbol past the query cap, a repository with no such source — because "0 retrieved"
        would claim a question that was never put."""
        if node.kind not in (NodeKind.MODULE, NodeKind.TYPE):
            return None
        chunks = self.retrieved.get(query_name(node.name))
        if chunks is None:
            return None
        own = node_id or node.id
        naming = sum(1 for doc_id in chunks if own in self.anchors.get(doc_id, ()))
        return len(chunks), naming

    def apply(self, batch: FactBatch) -> FactBatch:
        """Add the admitted ``Doc`` nodes and ``MENTIONS`` edges to ``batch`` (in place)."""
        for node in self.nodes:
            batch.add_node(node)
        for edge in self.edges:
            batch.add_edge(edge)
        return batch


def bind_external(
    batch: FactBatch,
    repo_root: Path | str,
    repo_key: str,
    sources: Sequence[DocSource],
    *,
    repo_pages: Sequence[DocPage] | None = None,
    cache_base: Path | None = None,
    now: datetime | None = None,
) -> ExternalBinding:
    """Bind every cached page of ``sources`` against ``batch`` — read-only on both.

    The reconciler is built from ``batch``'s non-``Doc`` nodes, so a page never "mentions" a
    doc section. ``repo_pages`` (the repository's own sections, from
    :func:`doc_source.read_doc_pages`) are what de-dup compares against; read from disk when not
    passed. Drift is collected for enumerated sources only (D20) — a ``rag`` source counts when
    its pull walked the corpus — over the sections that were not collapsed into a repository
    doc: those already count in the repository's own drift."""
    out = ExternalBinding()
    if not sources:
        return out
    moment = now or _now()
    cached = [read_source(source_cache_dir(repo_root, repo_key, s.name, base=cache_base), s) for s in sources]
    anything = any(c.pages for c in cached)
    own_pages = (read_doc_pages(repo_root) if repo_pages is None else repo_pages) if anything else []
    repo_texts = _repo_sections(own_pages)
    repo_files = _repo_files(own_pages) if any(p.kind == "chunk" for c in cached for p in c.pages) else {}
    reconciler = DocReconciler.from_nodes(
        (n for n in batch.nodes if n.kind is not NodeKind.DOC) if anything else (), repo_root=repo_root
    )

    def unique_anchors(section: DocPage) -> list[str]:
        found: list[str] = []
        for mention in extract_mentions(section):
            ids = reconciler.bind(mention, base_dir=section.base_dir).anchor_ids
            if len(ids) == 1 and ids[0] not in found:  # the local rule: one anchor, or no edge
                found.append(ids[0])
        return found

    for entry in cached:
        src = entry.source
        origin = f"mcp:{src.server}"
        label = f"{origin}/{src.name}"
        admitted: list[DocPage] = []
        collapsed = bound = 0
        for section, page in doc_pages(entry.pages, src.server):
            doc_id = f"doc:{section.title}"
            chunk = page.kind == "chunk"
            path = repo_relative(page.source, repo_root) if chunk and page.source else None
            twins = repo_files.get(path, []) if path is not None else []
            text_twin = repo_texts.get(normalized(section.text))
            if not twins and text_twin is not None:
                twins = [text_twin]
            if twins:
                for twin in twins:
                    sources_of = out.also_in.setdefault(twin, [])
                    if label not in sources_of:
                        sources_of.append(label)
                if chunk and doc_id not in out.anchors:
                    out.anchors[doc_id] = set(unique_anchors(section))
                collapsed += 1
                continue
            if doc_id in out.meta:  # two sources on one server pulled the same page: list it once
                continue
            prov = Provenance(section.source_file or section.title, section.line)
            out.nodes.append(Node(doc_id, NodeKind.DOC, section.title, "doc", prov))
            meta = {"origin": origin, "source": src.name, "url": page.url, "title": page.title}
            if chunk and page.source:
                meta["source_path"] = page.source
            out.meta[doc_id] = meta
            admitted.append(section)
            named = unique_anchors(section)
            for anchor in named:
                out.edges.append(Edge(doc_id, anchor, EdgeKind.MENTIONS, prov))
            if chunk:
                out.anchors[doc_id] = set(named)
            bound += bool(named)
        if entry.enumerated and admitted:
            _bindings, drift = reconciler.reconcile(admitted)
            out.drift.extend(f for f in drift if symbolish_drift(f.mention))
        for query, ids in entry.queries.items():
            out.retrieved.setdefault(query, set()).update(f"doc:mcp:{src.server}/{i}" for i in ids)
        row = standing(entry, now=moment)
        row.update(
            {
                "pages": len(entry.pages),
                "sections": len(admitted) + collapsed,
                "bound_sections": bound,
                "collapsed_into_repo_docs": collapsed,
            }
        )
        if entry.strategy:
            row["strategy"] = entry.strategy
        counts = entry.manifest.get("counts")
        if isinstance(counts, Mapping) and counts.get("bound"):
            row["pull_bound"] = str(counts["bound"])
        out.standings.append(row)
    return out


__all__ = [
    "ENV_CACHE_DIR",
    "MANIFEST_FILE",
    "PAGES_FILE",
    "QUERIES_FILE",
    "STALE_AFTER_DAYS",
    "STRATEGY_ENUMERATE",
    "STRATEGY_QUERY",
    "CachedSource",
    "ExternalBinding",
    "ExternalPage",
    "bind_external",
    "chunk_id",
    "collapse_stats",
    "doc_pages",
    "docs_cache_root",
    "failure_path",
    "looks_like_html",
    "normalized",
    "page_text",
    "query_name",
    "rag_queries",
    "read_source",
    "record_failure",
    "repo_cache_dir",
    "repo_relative",
    "source_cache_dir",
    "standing",
    "utc_stamp",
    "write_pull",
]
