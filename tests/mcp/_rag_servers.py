"""In-process stand-ins for the RAG MCP servers a ``rag`` docs source onboards (SSPN-82).

Each reproduces its server's published tool schema and answer shape — not its retrieval quality:
a "query" matches documents whose text contains the query, case-insensitively. None declares
``readOnlyHint`` unless told to, because (verified from their source) neither chroma-mcp nor
mcp-server-qdrant does.

- :class:`FakeChroma` — ``chroma_query_documents`` (nested ``{ids, documents, metadatas,
  distances}``), ``chroma_get_documents`` (flat, ``limit``/``offset``), ``chroma_add_documents``.
- :class:`FakeQdrant` — ``qdrant-find``: a header block then one ``<entry>`` block per hit.
- :class:`FakeBedrock` — ``QueryKnowledgeBases``: JSON objects separated by blank lines.
- :class:`FakeRagie` — ``retrieve``: one bare text block per chunk.
- :class:`FakeSearchFetch` — ``search`` → ``{"results": [{id, title, url}]}`` (text and structured),
  ``fetch(id)`` → ``{id, title, text, url, metadata}``.
"""

from __future__ import annotations

import json
from typing import Any

from orchestrator.mcp.config import MCPServerConfig
from orchestrator.mcp.models import MCPTool, MCPToolResult
from orchestrator.mcp.registry import MCPRegistry

Doc = tuple[str, str, dict[str, Any]]  # (id, text, metadata)

_STR = {"type": "string"}
_INT_OR_NULL = {"anyOf": [{"type": "integer"}, {"type": "null"}], "default": None}

CHROMA_QUERY = {
    "type": "object",
    "properties": {
        "collection_name": _STR,
        "query_texts": {"type": "array", "items": _STR},
        "n_results": {"type": "integer", "default": 5},
        "where": {"anyOf": [{"type": "object"}, {"type": "null"}], "default": None},
        "include": {"type": "array", "items": _STR},
    },
    "required": ["collection_name", "query_texts"],
}
CHROMA_GET = {
    "type": "object",
    "properties": {
        "collection_name": _STR,
        "ids": {"anyOf": [{"type": "array", "items": _STR}, {"type": "null"}], "default": None},
        "include": {"type": "array", "items": _STR},
        "limit": _INT_OR_NULL,
        "offset": _INT_OR_NULL,
    },
    "required": ["collection_name"],
}
CHROMA_ADD = {
    "type": "object",
    "properties": {
        "collection_name": _STR,
        "documents": {"type": "array", "items": _STR},
        "ids": {"type": "array", "items": _STR},
    },
    "required": ["collection_name", "documents", "ids"],
}
QDRANT_FIND = {
    "type": "object",
    "properties": {"query": _STR, "collection_name": _STR},
    "required": ["query"],
}
QDRANT_STORE = {
    "type": "object",
    "properties": {"information": _STR, "metadata": {"type": "object"}, "collection_name": _STR},
    "required": ["information"],
}
BEDROCK_QUERY = {
    "type": "object",
    "properties": {
        "query": _STR,
        "knowledge_base_id": _STR,
        "number_of_results": {"type": "integer", "default": 10},
        "reranking": {"type": "boolean", "default": False},
    },
    "required": ["query", "knowledge_base_id"],
}
RAGIE_RETRIEVE = {
    "type": "object",
    "properties": {"query": _STR, "topK": {"type": "integer", "default": 8}, "rerank": {"type": "boolean"}},
    "required": ["query"],
}
SEARCH = {"type": "object", "properties": {"query": _STR}, "required": ["query"]}
FETCH = {"type": "object", "properties": {"id": _STR}, "required": ["id"]}


def tool(server: str, name: str, schema: dict[str, Any], read_only: bool | None = None) -> MCPTool:
    return MCPTool(server=server, name=name, input_schema=schema, read_only=read_only)


class FakeRag:
    """The shared part: a corpus, a tool list, a call log, and an optional soft failure."""

    server = "rag"
    schemas: dict[str, dict[str, Any]] = {}

    def __init__(
        self,
        docs: list[Doc] | None = None,
        *,
        read_only: dict[str, bool | None] | None = None,
        only: tuple[str, ...] | None = None,
        soft_fail_after: int | None = None,
    ) -> None:
        self.docs = docs or []
        self.read_only = read_only or {}
        self.only = only
        self.soft_fail_after = soft_fail_after
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def tools(self) -> list[MCPTool]:
        names = self.only or tuple(self.schemas)
        return [tool(self.server, n, self.schemas[n], self.read_only.get(n)) for n in names]

    async def list_tools(self) -> list[MCPTool]:
        return self.tools()

    def hits(self, query: str, k: int) -> list[Doc]:
        return [d for d in self.docs if query.lower() in d[1].lower()][:k]

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> MCPToolResult:
        self.calls.append((name, arguments))
        if self.soft_fail_after is not None and len(self.calls) > self.soft_fail_after:
            return MCPToolResult(text=json.dumps({"error": "Collection unavailable: 401 Unauthorized"}))
        return self.answer(name, arguments)

    def answer(self, name: str, arguments: dict[str, Any]) -> MCPToolResult:
        raise NotImplementedError

    def registry(self, allow: tuple[str, ...] | None = None) -> MCPRegistry:
        cfg = MCPServerConfig(
            name=self.server, url="http://x", allow=allow or tuple(self.only or self.schemas)
        )
        return MCPRegistry([cfg], client_factory=lambda _c: self)


class FakeChroma(FakeRag):
    server = "chroma"
    schemas = {
        "chroma_add_documents": CHROMA_ADD,
        "chroma_get_documents": CHROMA_GET,
        "chroma_query_documents": CHROMA_QUERY,
    }

    def answer(self, name: str, arguments: dict[str, Any]) -> MCPToolResult:
        if name == "chroma_query_documents":
            per_query = [self.hits(q, arguments.get("n_results", 5)) for q in arguments["query_texts"]]
            body = {
                "ids": [[d[0] for d in hits] for hits in per_query],
                "documents": [[d[1] for d in hits] for hits in per_query],
                "metadatas": [[d[2] or None for d in hits] for hits in per_query],
                "distances": [[0.1 * (i + 1) for i in range(len(hits))] for hits in per_query],
            }
            return MCPToolResult(text=json.dumps(body))
        if name == "chroma_get_documents":
            start = arguments.get("offset") or 0
            limit = arguments.get("limit")
            window = self.docs[start : None if limit is None else start + limit]
            body = {
                "ids": [d[0] for d in window],
                "documents": [d[1] for d in window],
                "metadatas": [d[2] or None for d in window],
            }
            return MCPToolResult(text=json.dumps(body))
        raise AssertionError(f"unexpected tool {name}")


class FakeQdrant(FakeRag):
    server = "qdrant"
    schemas = {"qdrant-find": QDRANT_FIND, "qdrant-store": QDRANT_STORE}

    def answer(self, name: str, arguments: dict[str, Any]) -> MCPToolResult:
        assert name == "qdrant-find", name
        query = arguments["query"]
        blocks = [f"Results for the query '{query}'"] + [
            f"<entry><content>{d[1]}</content><metadata>{json.dumps(d[2] or None)}</metadata></entry>"
            for d in self.hits(query, 10)
        ]
        return MCPToolResult(text="".join(blocks), blocks=tuple(blocks))


class FakeBedrock(FakeRag):
    server = "bedrock"
    schemas = {"QueryKnowledgeBases": BEDROCK_QUERY}

    def answer(self, name: str, arguments: dict[str, Any]) -> MCPToolResult:
        hits = self.hits(arguments["query"], arguments.get("number_of_results", 10))
        objects = [
            {
                "content": {"text": d[1], "type": "TEXT"},
                "location": {"type": "S3", "s3Location": {"uri": d[2].get("source", "")}},
                "score": 0.9 - 0.1 * i,
            }
            for i, d in enumerate(hits)
        ]
        return MCPToolResult(text="\n\n".join(json.dumps(o) for o in objects))


class FakeRagie(FakeRag):
    server = "ragie"
    schemas = {"retrieve": RAGIE_RETRIEVE}

    def answer(self, name: str, arguments: dict[str, Any]) -> MCPToolResult:
        blocks = tuple(d[1] for d in self.hits(arguments["query"], arguments.get("topK", 8)))
        return MCPToolResult(text="".join(blocks), blocks=blocks)


class FakeSearchFetch(FakeRag):
    server = "kb"
    schemas = {"fetch": FETCH, "search": SEARCH}

    def answer(self, name: str, arguments: dict[str, Any]) -> MCPToolResult:
        if name == "search":
            results = [
                {"id": d[0], "title": d[2].get("title", d[0]), "url": d[2].get("url", "")}
                for d in self.hits(arguments["query"], 20)
            ]
            body = {"results": results}
            return MCPToolResult(text=json.dumps(body), structured=body)
        doc = next(d for d in self.docs if d[0] == arguments["id"])
        body2 = {
            "id": doc[0],
            "title": doc[2].get("title", doc[0]),
            "text": doc[1],
            "url": doc[2].get("url", ""),
        }
        return MCPToolResult(text=json.dumps({**body2, "metadata": doc[2]}))


__all__ = [
    "FakeBedrock",
    "FakeChroma",
    "FakeQdrant",
    "FakeRag",
    "FakeRagie",
    "FakeSearchFetch",
    "tool",
]
