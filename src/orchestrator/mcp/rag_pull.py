"""Pull chunks from any RAG system's MCP server into the docs cache (SSPN-82).

The network half of a ``rag`` docs source. :mod:`orchestrator.mcp.discovery` has already
picked the tools and :func:`~orchestrator.mcp.doc_pull.vet_tools` cleared them; this module
calls them and turns whatever came back into :class:`~orchestrator.pkg.external_docs.ExternalPage`
chunks (``kind: "chunk"``). Binding is not here — chunks are text until a read tool binds them.

**Two strategies (D16).** With a *list* tool the corpus is walked, paged, to ``max_chunks``: the
only pull that knows what the source holds, so the only one whose unbound claims are drift.
Otherwise the pull *queries*: one query per module and class of the repository's code graph,
the most-called first, to ``max_queries`` — ``top_k`` chunks each — and records which chunks
each query returned (``queries.json``), so a read can say how many were retrieved for a symbol
and how many actually name it. Both report their bound: ``N of at least M (cap reached)`` for a
walk, ``queried N of M symbols`` for queries (invariant 7).

**One lenient normaliser (Q41).** Servers disagree on everything, so :func:`normalise` reads
every shape the supported servers publish into ``{chunk_id, text, source, title, score}``:

- Chroma — ``chroma_query_documents`` returns ``{ids, documents, metadatas, distances}``, each
  nested one list per query; ``chroma_get_documents`` the same keys, flat.
- Qdrant — ``qdrant-find`` returns text: an optional ``Results for the query '…'`` header, then
  ``<entry><content>…</content><metadata>{json}</metadata></entry>`` per hit, no id, no score.
- Bedrock Knowledge Bases — ``QueryKnowledgeBases`` returns JSON objects separated by blank
  lines, each ``{content: {text}, location: {…uri…}, score}``.
- Ragie — ``retrieve`` returns one bare text block per chunk.
- search/fetch — ``search`` returns ``{"results": [{id, title, url}]}`` (JSON text or structured
  content), then ``fetch(id)`` returns ``{id, title, text, url, metadata}`` per result.

A chunk the server gives no id gets ``sha1(text)[:12]``. A chunk with no text is dropped — it can
name nothing. A body that is an ``{"error": ...}`` fails the pull before it gets here, exactly as
a Confluence one does, so the last good cache survives.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from orchestrator.mcp.discovery import ENUMERATE, SEARCH_FETCH, RagPlan
from orchestrator.mcp.doc_pull import Pulled, PullError, _Caller
from orchestrator.mcp.models import MCPToolResult
from orchestrator.pkg.external_docs import ExternalPage, chunk_id, rag_queries
from orchestrator.pkg.repos import DocSource

if TYPE_CHECKING:
    from orchestrator.pkg.facts import FactBatch

#: Chunks asked for per page of an enumeration.
ENUMERATE_PAGE = 100

_ENTRY_RE = re.compile(r"<entry>(.*?)</entry>", re.DOTALL)
_CONTENT_RE = re.compile(r"<content>(.*?)</content>", re.DOTALL)
_METADATA_RE = re.compile(r"<metadata>(.*?)</metadata>", re.DOTALL)
_URL_RE = re.compile(r"^https?://", re.IGNORECASE)
#: A server's framing line, not a passage — Qdrant's ``Results for the query '…'`` header, and the
#: ``No information found for the query '…'`` it answers a miss with. Read as bare text, either
#: would become a chunk: the header alone is exactly what a zero-hit ``qdrant-find`` returns.
_FRAMING_RE = re.compile(r"^\s*(?:results|no (?:information|results) found) for the query\b", re.IGNORECASE)
#: Where a chunk's origin lives in its metadata, most specific first.
_SOURCE_KEYS = ("source", "path", "file_path", "filepath", "file", "filename", "uri", "url", "document_uri")
_TEXT_KEYS = ("text", "content", "document", "page_content", "chunk", "body")
_ID_KEYS = ("id", "chunk_id", "document_id", "doc_id")
_LIST_KEYS = ("results", "documents", "chunks", "matches", "data", "items", "hits", "points", "result")


@dataclass(frozen=True)
class Chunk:
    """One retrieved passage, as every server shape normalises to."""

    chunk_id: str
    text: str
    source: str = ""
    title: str = ""
    score: float | None = None

    def page(self) -> ExternalPage:
        url = self.source if _URL_RE.match(self.source) else ""
        return ExternalPage(
            id=self.chunk_id,
            title=self.title or self.source or self.chunk_id,
            text=self.text,
            url=url,
            kind="chunk",
            source=self.source,
            score=self.score,
        )


def _chunk(
    text: Any, *, cid: Any = None, meta: Any = None, title: Any = None, score: Any = None
) -> Chunk | None:
    body = text.strip() if isinstance(text, str) else ""
    if not body:
        return None
    md: Mapping[str, Any] = meta if isinstance(meta, Mapping) else {}
    source = next((str(md[k]) for k in _SOURCE_KEYS if isinstance(md.get(k), str) and md[k].strip()), "")
    name = title if isinstance(title, str) and title.strip() else md.get("title")
    ident = str(cid).strip() if isinstance(cid, str | int) and not isinstance(cid, bool) else ""
    number = score if isinstance(score, int | float) and not isinstance(score, bool) else None
    return Chunk(
        chunk_id=ident or chunk_id(body),
        text=body,
        source=source.strip(),
        title=str(name).strip() if isinstance(name, str) else "",
        score=float(number) if number is not None else None,
    )


def _find_uri(value: Any) -> str:
    """The first ``…uri`` / ``…url`` string anywhere in a Bedrock-style ``location`` object."""
    if isinstance(value, Mapping):
        for key, item in value.items():
            if isinstance(item, str) and str(key).lower().endswith(("uri", "url")) and item.strip():
                return item.strip()
        for item in value.values():
            if found := _find_uri(item):
                return found
    return ""


def _nested(value: Any, i: int, j: int | None = None) -> Any:
    try:
        row = value[i]
        return row if j is None else row[j]
    except (IndexError, KeyError, TypeError):
        return None


def _from_chroma(data: Mapping[str, Any]) -> list[Chunk]:
    ids, docs, metas = data.get("ids") or [], data.get("documents") or [], data.get("metadatas")
    out: list[Chunk] = []
    if ids and isinstance(ids[0], list):  # query: one list per query text
        for i, row in enumerate(ids):
            for j, cid in enumerate(row):
                c = _chunk(_nested(docs, i, j), cid=cid, meta=_nested(metas, i, j))
                if c is not None:
                    out.append(c)
        return out
    for i, cid in enumerate(ids):
        c = _chunk(_nested(docs, i), cid=cid, meta=_nested(metas, i))
        if c is not None:
            out.append(c)
    return out


def _from_object(item: Mapping[str, Any]) -> list[Chunk]:
    """One JSON object: a chunk (Bedrock result, fetch answer, generic hit) or a list holder."""
    if "ids" in item and "documents" in item:
        return _from_chroma(item)
    content = item.get("content")
    if isinstance(content, Mapping) and isinstance(content.get("text"), str):  # Bedrock
        meta = dict(item.get("metadata") or {}) if isinstance(item.get("metadata"), Mapping) else {}
        uri = _find_uri(item.get("location"))
        if uri and not any(meta.get(k) for k in _SOURCE_KEYS):
            meta["uri"] = uri
        c = _chunk(content["text"], cid=item.get("id"), meta=meta, score=item.get("score"))
        return [c] if c is not None else []
    text = next((item[k] for k in _TEXT_KEYS if isinstance(item.get(k), str) and item[k].strip()), None)
    if text is not None:
        meta = dict(item.get("metadata") or {}) if isinstance(item.get("metadata"), Mapping) else {}
        for key in ("url", "source", "uri", "path"):
            if isinstance(item.get(key), str) and item[key].strip() and not meta.get(key):
                meta[key] = item[key]
        cid = next((item[k] for k in _ID_KEYS if item.get(k) not in (None, "")), None)
        c = _chunk(text, cid=cid, meta=meta, title=item.get("title"), score=item.get("score"))
        return [c] if c is not None else []
    for key in _LIST_KEYS:
        if isinstance(item.get(key), list | Mapping):
            return _from_json(item[key])
    return []


def _from_json(value: Any) -> list[Chunk]:
    if isinstance(value, Mapping):
        return _from_object(value)
    if isinstance(value, list):
        out: list[Chunk] = []
        for item in value:
            if isinstance(item, str):
                out.extend(_from_text(item))
            else:
                out.extend(_from_json(item))
        return out
    if isinstance(value, str):
        return _from_text(value)
    return []


def _from_entries(text: str) -> list[Chunk]:
    """Qdrant: ``<entry><content>…</content><metadata>{json}</metadata></entry>`` per hit."""
    out: list[Chunk] = []
    for entry in _ENTRY_RE.findall(text):
        content = _CONTENT_RE.search(entry)
        raw_meta = _METADATA_RE.search(entry)
        meta: Any = None
        if raw_meta is not None:
            try:
                meta = json.loads(raw_meta.group(1))
            except json.JSONDecodeError:
                meta = None
        c = _chunk(content.group(1) if content else "", meta=meta)
        if c is not None:
            out.append(c)
    return out


def _json_objects(text: str) -> list[Any] | None:
    """Every JSON object or array in ``text`` back to back (blank lines or nothing between) — or
    ``None`` when any stretch of it is not one. A bare ``42`` or ``"x"`` is prose, not JSON."""
    decoder = json.JSONDecoder()
    values: list[Any] = []
    at, end = 0, len(text)
    while True:
        while at < end and text[at].isspace():
            at += 1
        if at >= end:
            return values or None
        if text[at] not in "{[":
            return None
        try:
            value, at = decoder.raw_decode(text, at)
        except json.JSONDecodeError:
            return None
        values.append(value)


def _from_text(text: str) -> list[Chunk]:
    if "<entry>" in text:
        return _from_entries(text)
    objects = _json_objects(text)
    if objects is not None:
        return _from_json(objects[0] if len(objects) == 1 else objects)
    if _FRAMING_RE.match(text) and "\n" not in text.strip():
        return []
    c = _chunk(text)
    return [c] if c is not None else []


def normalise(data: Any, result: MCPToolResult) -> list[Chunk]:
    """Every chunk in one tool answer, whatever shape the server used.

    Order matters and each step has a reason: parsed JSON first (Chroma, a fetch answer); then
    Qdrant's ``<entry>`` markup in the *joined* text (its header block would otherwise read as a
    chunk); then JSON objects back to back (Bedrock); then structured content (a server that
    only fills ``structuredContent``); last, each text block as one bare chunk (Ragie)."""
    if isinstance(data, Mapping | list):
        return _dedupe(_from_json(data))
    if data is None and result.text.strip() == "null":
        return []  # a tool that returned nothing (``qdrant-find`` on a miss), JSON-encoded
    if "<entry>" in result.text:
        return _dedupe(_from_entries(result.text))
    objects = _json_objects(result.text)
    if objects is not None:
        return _dedupe(_from_json(objects))
    structured = result.structured
    if isinstance(structured, Mapping) and set(structured) == {"result"}:
        structured = structured["result"]
    if isinstance(structured, Mapping | list) and (found := _from_json(structured)):
        return _dedupe(found)
    blocks = result.blocks or ((result.text,) if result.text.strip() else ())
    return _dedupe([c for block in blocks for c in _from_text(block)])


def _dedupe(chunks: Iterable[Chunk]) -> list[Chunk]:
    seen: set[str] = set()
    out: list[Chunk] = []
    for c in chunks:
        if c.chunk_id not in seen:
            seen.add(c.chunk_id)
            out.append(c)
    return out


def search_ids(data: Any, result: MCPToolResult) -> list[str]:
    """The result ids a ``search`` answered with — JSON text first, structured content second."""
    for holder in (data, result.structured):
        if isinstance(holder, Mapping) and set(holder) == {"result"}:
            holder = holder["result"]
        items = holder.get("results") if isinstance(holder, Mapping) else holder
        if isinstance(items, list):
            ids = [str(i["id"]) for i in items if isinstance(i, Mapping) and i.get("id") not in (None, "")]
            if ids or items == []:
                return list(dict.fromkeys(ids))
    raise PullError(f"search answered with no results list: {result.text[:200]!r}")


def _base_args(plan: RagPlan, source: DocSource) -> dict[str, Any]:
    return {plan.collection_arg: source.collection} if plan.collection_arg and source.collection else {}


async def _enumerate(caller: _Caller, source: DocSource, plan: RagPlan) -> Pulled:
    chunks: dict[str, Chunk] = {}
    seen = offset = 0
    truncated = False
    page = min(ENUMERATE_PAGE, source.max_chunks)
    while True:
        args = _base_args(plan, source)
        if plan.limit_arg:
            args[plan.limit_arg] = page if plan.offset_arg else source.max_chunks
        if plan.offset_arg:
            args[plan.offset_arg] = offset
        data, result = await caller.call_result(plan.tool, args)
        got = normalise(data, result)
        seen += len(got)
        fresh = [c for c in got if c.chunk_id not in chunks]
        room = source.max_chunks - len(chunks)
        chunks.update((c.chunk_id, c) for c in fresh[:room])
        if len(fresh) > room:
            truncated = True
            break
        full = len(got) >= page
        if len(chunks) >= source.max_chunks and full and plan.offset_arg:
            truncated = True  # a full page at the cap: the corpus may hold more
            break
        if not (plan.offset_arg and plan.limit_arg) or not full or not fresh:
            break
        offset += len(got)
    return Pulled(
        pages=[c.page() for c in chunks.values()],
        cap=source.max_chunks,
        discovered=max(seen, len(chunks)),
        truncated=truncated,
    )


async def _query(caller: _Caller, source: DocSource, plan: RagPlan, graph: Callable[[], FactBatch]) -> Pulled:
    queries, candidates = rag_queries(graph(), source.max_queries)
    chunks: dict[str, Chunk] = {}
    asked: dict[str, list[str]] = {}
    for query in queries:
        args = _base_args(plan, source)
        args[plan.query_arg] = [query] if plan.batch else query
        if plan.top_k_arg:
            args[plan.top_k_arg] = source.top_k
        data, result = await caller.call_result(plan.tool, args)
        if plan.role == SEARCH_FETCH:
            got: list[Chunk] = []
            for rid in search_ids(data, result)[: source.top_k]:
                if rid in chunks:
                    got.append(chunks[rid])
                    continue
                fdata, fresult = await caller.call_result(plan.fetch_tool, {plan.fetch_arg: rid})
                fetched = normalise(fdata, fresult)
                if fetched:
                    got.append(Chunk(rid, fetched[0].text, fetched[0].source, fetched[0].title))
        else:
            got = normalise(data, result)[: source.top_k]
        asked[query] = [c.chunk_id for c in got]
        for c in got:
            chunks.setdefault(c.chunk_id, c)
    return Pulled(
        pages=[c.page() for c in chunks.values()],
        cap=source.max_queries,
        discovered=len(chunks),
        queried=len(queries),
        candidates=candidates,
        queries=asked,
    )


async def pull_rag(
    caller: _Caller, source: DocSource, plan: RagPlan, graph: Callable[[], FactBatch]
) -> Pulled:
    """One ``rag`` source's pull, by the strategy discovery chose."""
    if plan.role == ENUMERATE:
        return await _enumerate(caller, source, plan)
    return await _query(caller, source, plan, graph)


def rag_plan_of(source: DocSource, plan: RagPlan) -> list[dict[str, Any]]:
    """``--dry-run``'s answer for a ``rag`` source — what would be called, nothing touched."""
    base = _base_args(plan, source)
    if plan.role == ENUMERATE:
        args = {**base}
        if plan.limit_arg:
            args[plan.limit_arg] = min(ENUMERATE_PAGE, source.max_chunks)
        if plan.offset_arg:
            args[plan.offset_arg] = "<paged>"
        return [{"tool": plan.tool, "args": args, "max_chunks": source.max_chunks}]
    ask = f"<short name of each module/class, most-called first, at most {source.max_queries}>"
    args = {**base, plan.query_arg: [ask] if plan.batch else ask}
    if plan.top_k_arg:
        args[plan.top_k_arg] = source.top_k
    steps: list[dict[str, Any]] = [{"tool": plan.tool, "args": args}]
    if plan.fetch_tool:
        steps.append({"tool": plan.fetch_tool, "args": {plan.fetch_arg: f"<each of the top {source.top_k}>"}})
    return steps


__all__ = ["ENUMERATE_PAGE", "Chunk", "normalise", "pull_rag", "rag_plan_of", "search_ids"]
