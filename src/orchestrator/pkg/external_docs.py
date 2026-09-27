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
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

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
    """One pulled document, as cached: a Confluence page or a Jira issue.

    ``id`` is the source's own identifier (page id, issue key), so a doc id built from it is
    stable across pulls and points back at the page."""

    id: str
    title: str
    text: str
    url: str = ""
    kind: str = "confluence"

    def to_json(self) -> dict[str, str]:
        return {"id": self.id, "title": self.title, "url": self.url, "text": self.text, "kind": self.kind}

    @classmethod
    def from_json(cls, raw: Mapping[str, Any]) -> ExternalPage:
        return cls(
            id=str(raw["id"]),
            title=str(raw.get("title") or raw["id"]),
            text=str(raw.get("text") or ""),
            url=str(raw.get("url") or ""),
            kind=str(raw.get("kind") or "confluence"),
        )


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


def write_pull(dest: Path, pages: Iterable[ExternalPage], manifest: Mapping[str, Any]) -> dict[str, Any]:
    """Replace ``dest`` with a fresh pull — whole, or not at all (D37).

    Written to a temporary folder beside ``dest`` and swapped in with ``os.replace``, so a
    reader sees the old pull or the new one, never half of either. A crash before the swap
    leaves the old folder untouched; one between the two renames leaves it at
    ``.<name>.previous``, which :func:`read_source` falls back to. Success clears any
    ``failure.json`` an earlier attempt left. Returns the manifest as written."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix=f".{dest.name}.tmp-", dir=dest.parent))
    try:
        ordered = sorted(pages, key=lambda p: p.id)
        body = "".join(json.dumps(p.to_json(), sort_keys=True, ensure_ascii=False) + "\n" for p in ordered)
        (tmp / PAGES_FILE).write_text(body, encoding="utf-8")
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


def _read_folder(folder: Path) -> tuple[tuple[ExternalPage, ...], dict[str, Any]]:
    manifest = json.loads((folder / MANIFEST_FILE).read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError("manifest is not an object")
    pages = tuple(
        ExternalPage.from_json(json.loads(line))
        for line in (folder / PAGES_FILE).read_text(encoding="utf-8").splitlines()
        if line.strip()
    )
    return tuple(sorted(pages, key=lambda p: p.id)), manifest


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
        pages, manifest = _read_folder(folder)
    except (OSError, ValueError, KeyError) as exc:
        return CachedSource(source, "failed", error=f"cache unreadable: {type(exc).__name__}: {exc}")
    pulled_at = str(manifest.get("pulled_at") or "") or None
    if failure is not None:
        return CachedSource(
            source, "failed", pages, pulled_at, str(failure.get("reason") or "unknown"), manifest
        )
    return CachedSource(source, "ok", pages, pulled_at, None, manifest)


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
    markdown file (fences respected); a Jira issue stays one page."""
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


def collapse_stats(repo_root: Path | str, pages: Sequence[ExternalPage], server: str) -> dict[str, int]:
    """How many of a pull's sections are identical to one of the repository's own — the
    collapse rate D28 asked to be measured before any fuzzier de-dup is considered."""
    sections = doc_pages(pages, server)
    repo = _repo_sections(read_doc_pages(repo_root)) if sections else {}
    same = sum(1 for section, _page in sections if normalized(section.text) in repo)
    return {"sections": len(sections), "identical_to_repo_sections": same}


@dataclass
class ExternalBinding:
    """External docs bound against one repository's graph — what a read tool adds beside the
    repository's own doc links.

    ``meta`` carries, per admitted doc id, what a ``DocRef`` reports about its origin;
    ``also_in`` names, per *repository* doc id, the sources holding an identical copy."""

    nodes: list[Node] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    meta: dict[str, dict[str, str]] = field(default_factory=dict)
    also_in: dict[str, list[str]] = field(default_factory=dict)
    standings: list[dict[str, Any]] = field(default_factory=list)
    drift: list[DocDriftFinding] = field(default_factory=list)

    @property
    def doc_count(self) -> int:
        return len(self.nodes)

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
    passed. Drift is collected for enumerated sources only (D20), over the sections that were
    not collapsed into a repository doc — those already count in the repository's own drift."""
    out = ExternalBinding()
    if not sources:
        return out
    moment = now or _now()
    cached = [read_source(source_cache_dir(repo_root, repo_key, s.name, base=cache_base), s) for s in sources]
    anything = any(c.pages for c in cached)
    repo_texts = (
        _repo_sections(read_doc_pages(repo_root) if repo_pages is None else repo_pages) if anything else {}
    )
    reconciler = DocReconciler.from_nodes(
        (n for n in batch.nodes if n.kind is not NodeKind.DOC) if anything else (), repo_root=repo_root
    )
    for entry in cached:
        src = entry.source
        origin = f"mcp:{src.server}"
        admitted: list[DocPage] = []
        collapsed = bound = 0
        for section, page in doc_pages(entry.pages, src.server):
            twin = repo_texts.get(normalized(section.text))
            if twin is not None:
                sources_of = out.also_in.setdefault(twin, [])
                label = f"{origin}/{src.name}"
                if label not in sources_of:
                    sources_of.append(label)
                collapsed += 1
                continue
            doc_id = f"doc:{section.title}"
            if doc_id in out.meta:  # two sources on one server pulled the same page: list it once
                continue
            prov = Provenance(section.source_file or section.title, section.line)
            out.nodes.append(Node(doc_id, NodeKind.DOC, section.title, "doc", prov))
            out.meta[doc_id] = {"origin": origin, "source": src.name, "url": page.url, "title": page.title}
            admitted.append(section)
            named = False
            for mention in extract_mentions(section):
                anchors = reconciler.bind(mention, base_dir=section.base_dir).anchor_ids
                if len(anchors) == 1:  # the local rule: an unambiguous anchor, or no edge
                    out.edges.append(Edge(doc_id, anchors[0], EdgeKind.MENTIONS, prov))
                    named = True
            bound += named
        if src.enumerated and admitted:
            _bindings, drift = reconciler.reconcile(admitted)
            out.drift.extend(f for f in drift if symbolish_drift(f.mention))
        row = standing(entry, now=moment)
        row.update(
            {
                "pages": len(entry.pages),
                "sections": len(admitted) + collapsed,
                "bound_sections": bound,
                "collapsed_into_repo_docs": collapsed,
            }
        )
        out.standings.append(row)
    return out


__all__ = [
    "ENV_CACHE_DIR",
    "MANIFEST_FILE",
    "PAGES_FILE",
    "STALE_AFTER_DAYS",
    "CachedSource",
    "ExternalBinding",
    "ExternalPage",
    "bind_external",
    "collapse_stats",
    "doc_pages",
    "docs_cache_root",
    "failure_path",
    "looks_like_html",
    "normalized",
    "page_text",
    "read_source",
    "record_failure",
    "repo_cache_dir",
    "source_cache_dir",
    "standing",
    "utc_stamp",
    "write_pull",
]
