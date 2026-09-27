"""Which tools of a RAG server a ``rag`` docs source calls — read off their input schemas (SSPN-82).

Every retrieval system publishes its own MCP server, and none agree on names: Chroma's
``chroma_query_documents`` takes ``query_texts``, Qdrant's ``qdrant-find`` takes ``query``,
Bedrock's ``QueryKnowledgeBases`` takes ``query`` plus a ``knowledge_base_id``. Onboarding one by
configuration alone means recognising a tool's **role** from what it declares, which is what this
module does — and nothing more. It calls nothing.

**The roles (D10).**

- *list* — enumerates the corpus: named like ``get_documents`` / ``list_documents`` / ``scroll``,
  takes a collection parameter, and requires no query. When one exists the pull walks the whole
  corpus (D16), because a walk is the only pull whose unbound claims can be called drift.
- *search / fetch* — tools named exactly ``search`` and ``fetch`` (the pair ChatGPT-style
  connectors publish): ``search`` returns ids, ``fetch`` the text of one.
- *retrieve* — anything else that requires a string ``query`` / ``q`` / ``question`` /
  ``search_query`` / ``query_text`` / ``text``, or takes an array ``query_texts``.

``rag.tool`` and ``rag.query_arg`` override detection. **Two candidates for one role and no
override is a refusal naming both** — one line of configuration is cheaper than a pull that
quietly asked the wrong tool and produced a plausible, wrong picture.

A schema that requires something the pull cannot supply (a ``collection_name`` with no
``rag.collection`` configured, an argument nobody can guess) is refused here too, before any call,
rather than discovered as a server error mid-pull.

The read-only guard is not here: :func:`orchestrator.mcp.doc_pull.vet_tools` clears the tools this
module picks, with the source's ``trust_read_only`` (D22, D47).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from typing import Any

from orchestrator.mcp.doc_pull import ToolGuardError
from orchestrator.mcp.models import MCPTool
from orchestrator.pkg.repos import DocSource

#: Required string parameters that make a tool a retriever.
QUERY_PARAMS = ("query", "q", "question", "search_query", "query_text", "text")
#: The array form — Chroma's ``query_texts``: one query per element, results nested per query.
BATCH_QUERY_PARAM = "query_texts"
#: Where ``rag.collection`` goes. ``knowledge_base_id`` is Bedrock's name for the same thing.
COLLECTION_PARAMS = ("collection_name", "collection", "knowledge_base_id")
#: How many results a retriever returns — ``rag.top_k`` goes in the first one a tool declares.
TOP_K_PARAMS = ("n_results", "top_k", "topK", "k", "number_of_results", "num_results", "max_results", "limit")
#: The id ``fetch`` takes.
FETCH_ID_PARAMS = ("id", "document_id", "doc_id")

_LIST_NAME_RE = re.compile(
    r"(?:^|[_-])(?:get|list)[_-]?(?:documents|docs|chunks|points|records)$|(?:^|[_-])scroll$",
    re.IGNORECASE,
)

ENUMERATE = "enumerate"
RETRIEVE = "retrieve"
SEARCH_FETCH = "search_fetch"


class DiscoveryError(ToolGuardError):
    """No tool, or more than one, fits the role a ``rag`` pull needs — refused before any call."""


@dataclass(frozen=True)
class RagPlan:
    """What a ``rag`` pull will call, and how its arguments are spelled on this server."""

    role: str  # enumerate | retrieve | search_fetch
    tool: str
    query_arg: str = ""  # retrieve / search: where the query goes
    batch: bool = False  # the query goes in as a one-element list (``query_texts``)
    collection_arg: str = ""  # where ``rag.collection`` goes, when configured
    top_k_arg: str = ""  # retrieve: where ``rag.top_k`` goes, when the tool takes one
    limit_arg: str = ""  # enumerate: page size
    offset_arg: str = ""  # enumerate: paging
    fetch_tool: str = ""  # search_fetch: the tool that returns one result's text
    fetch_arg: str = ""

    @property
    def tools(self) -> tuple[str, ...]:
        """Every tool the pull calls — what the guard must clear."""
        return (self.tool, self.fetch_tool) if self.fetch_tool else (self.tool,)

    @property
    def strategy(self) -> str:
        """``enumerate`` (a walk of the corpus) or ``query`` (retrieve and search/fetch alike)."""
        return ENUMERATE if self.role == ENUMERATE else "query"

    def describe(self) -> dict[str, Any]:
        out: dict[str, Any] = {"role": self.role, "strategy": self.strategy, "tool": self.tool}
        for key in ("query_arg", "collection_arg", "top_k_arg", "limit_arg", "offset_arg", "fetch_tool"):
            if value := getattr(self, key):
                out[key] = value
        if self.batch:
            out["batch"] = True
        return out


def _props(tool: MCPTool) -> dict[str, Any]:
    props = tool.input_schema.get("properties")
    return props if isinstance(props, dict) else {}


def _required(tool: MCPTool) -> set[str]:
    req = tool.input_schema.get("required")
    return {str(r) for r in req} if isinstance(req, list) else set()


def _types(prop: Any) -> set[str]:
    """The JSON types a property admits — ``type`` as a string or list, or across ``anyOf``."""
    if not isinstance(prop, dict):
        return set()
    declared = prop.get("type")
    out = {declared} if isinstance(declared, str) else {t for t in declared or () if isinstance(t, str)}
    for alt in prop.get("anyOf") or prop.get("oneOf") or ():
        out |= _types(alt)
    return out


def query_param(tool: MCPTool) -> tuple[str, bool] | None:
    """``(param, batch)`` when ``tool`` looks like a retriever, else ``None``."""
    props, required = _props(tool), _required(tool)
    for name in QUERY_PARAMS:
        if name in required and "string" in _types(props.get(name)):
            return name, False
    if "array" in _types(props.get(BATCH_QUERY_PARAM)):
        return BATCH_QUERY_PARAM, True
    return None


def _first(tool: MCPTool, names: Iterable[str]) -> str:
    props = _props(tool)
    return next((n for n in names if n in props), "")


def is_list_tool(tool: MCPTool) -> bool:
    """Named like an enumerator, takes a collection, requires no query."""
    required = _required(tool)
    return (
        bool(_LIST_NAME_RE.search(tool.name))
        and bool(_first(tool, COLLECTION_PARAMS[:2]))
        and not any(q in required for q in QUERY_PARAMS)
    )


def _finish(source: DocSource, tool: MCPTool, plan: RagPlan, supplied: set[str]) -> RagPlan:
    """Check ``plan`` can be called as configured: the collection has somewhere to go, and every
    required argument is one the pull supplies."""
    where = f"source {source.name!r}: {source.server}:{tool.name}"
    if source.collection and not plan.collection_arg:
        raise DiscoveryError(
            f"{where} takes no collection parameter ({'/'.join(COLLECTION_PARAMS)}) "
            f"but rag.collection is {source.collection!r}"
        )
    if plan.collection_arg and plan.collection_arg in _required(tool) and not source.collection:
        raise DiscoveryError(f"{where} requires {plan.collection_arg!r} — set rag.collection")
    unfilled = sorted(_required(tool) - supplied - ({plan.collection_arg} if source.collection else set()))
    if unfilled:
        raise DiscoveryError(f"{where} requires {unfilled}, which a docs pull cannot supply")
    return plan


def _enumerate_plan(source: DocSource, tool: MCPTool) -> RagPlan:
    limit = "limit" if "limit" in _props(tool) else ""
    offset = "offset" if "offset" in _props(tool) else ""
    plan = RagPlan(
        role=ENUMERATE,
        tool=tool.name,
        collection_arg=_first(tool, COLLECTION_PARAMS),
        limit_arg=limit,
        offset_arg=offset,
    )
    return _finish(source, tool, plan, {limit, offset} - {""})


def _retrieve_plan(source: DocSource, tool: MCPTool, *, role: str = RETRIEVE, fetch: str = "") -> RagPlan:
    found = query_param(tool)
    if source.query_arg:
        if source.query_arg not in _props(tool):
            raise DiscoveryError(
                f"source {source.name!r}: {source.server}:{tool.name} has no parameter "
                f"{source.query_arg!r} (rag.query_arg)"
            )
        arg, batch = source.query_arg, "array" in _types(_props(tool)[source.query_arg])
    elif found is None:
        raise DiscoveryError(
            f"source {source.name!r}: {source.server}:{tool.name} has no query parameter "
            f"({'/'.join(QUERY_PARAMS)} or {BATCH_QUERY_PARAM}) — set rag.query_arg"
        )
    else:
        arg, batch = found
    top_k = "" if role == SEARCH_FETCH else _first(tool, TOP_K_PARAMS)
    plan = RagPlan(
        role=role,
        tool=tool.name,
        query_arg=arg,
        batch=batch,
        collection_arg=_first(tool, COLLECTION_PARAMS),
        top_k_arg=top_k,
        fetch_tool=fetch,
    )
    return _finish(source, tool, plan, {arg, top_k} - {""})


def _pair_plan(source: DocSource, search: MCPTool, fetch: MCPTool) -> RagPlan:
    plan = _retrieve_plan(source, search, role=SEARCH_FETCH, fetch=fetch.name)
    fetch_arg = _first(fetch, FETCH_ID_PARAMS)
    if not fetch_arg:
        raise DiscoveryError(
            f"source {source.name!r}: {source.server}:fetch takes no id ({'/'.join(FETCH_ID_PARAMS)})"
        )
    unfilled = sorted(_required(fetch) - {fetch_arg})
    if unfilled:
        raise DiscoveryError(
            f"source {source.name!r}: {source.server}:fetch requires {unfilled}, "
            "which a docs pull cannot supply"
        )
    return replace(plan, fetch_arg=fetch_arg)


def _ambiguous(source: DocSource, role: str, names: list[str], fix: str) -> DiscoveryError:
    listed = ", ".join(f"{source.server}:{n}" for n in names)
    return DiscoveryError(
        f"source {source.name!r}: {len(names)} tools could be the {role} tool ({listed}) — "
        f"refusing to guess; {fix}"
    )


def discover(source: DocSource, tools: Iterable[MCPTool]) -> RagPlan:
    """The plan for a ``rag`` pull over ``tools`` (the server's allow-listed tools), or
    :class:`DiscoveryError` naming why none, or which several, fit. Calls nothing."""
    offered: Mapping[str, MCPTool] = {t.name: t for t in sorted(tools, key=lambda t: t.name)}
    if source.tool:
        chosen = offered.get(source.tool)
        if chosen is None:
            raise DiscoveryError(
                f"source {source.name!r}: rag.tool {source.server}:{source.tool} is not allow-listed "
                f"or not offered — offered: {sorted(offered) or 'none'}"
            )
        if is_list_tool(chosen) and not source.query_arg:
            return _enumerate_plan(source, chosen)
        if chosen.name == "search" and "fetch" in offered:
            return _pair_plan(source, chosen, offered["fetch"])
        return _retrieve_plan(source, chosen)
    if not source.query_arg:
        lists = [n for n, t in offered.items() if is_list_tool(t)]
        if len(lists) > 1:
            raise _ambiguous(source, "list", lists, "name one with rag.tool")
        if lists:
            return _enumerate_plan(source, offered[lists[0]])
    candidates = [
        n
        for n, t in offered.items()
        if n != "fetch"
        and not is_list_tool(t)
        and (source.query_arg in _props(t) if source.query_arg else query_param(t) is not None)
    ]
    if "search" in offered and "fetch" in offered and set(candidates) <= {"search"}:
        return _pair_plan(source, offered["search"], offered["fetch"])
    if len(candidates) > 1:
        raise _ambiguous(source, "retrieve", candidates, "name one with rag.tool")
    if not candidates:
        raise DiscoveryError(
            f"source {source.name!r}: no list, search/fetch or retrieve tool among "
            f"{[f'{source.server}:{n}' for n in offered] or 'no allow-listed tools'} — "
            "name one with rag.tool (and rag.query_arg)"
        )
    return _retrieve_plan(source, offered[candidates[0]])


__all__ = [
    "BATCH_QUERY_PARAM",
    "COLLECTION_PARAMS",
    "ENUMERATE",
    "QUERY_PARAMS",
    "RETRIEVE",
    "SEARCH_FETCH",
    "TOP_K_PARAMS",
    "DiscoveryError",
    "RagPlan",
    "discover",
    "is_list_tool",
    "query_param",
]
