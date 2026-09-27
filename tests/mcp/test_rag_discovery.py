"""Tool-role discovery and the tool guard for ``rag`` docs sources (SSPN-82, D10, D22, D47).

Discovery reads roles off input schemas and calls nothing; the guard clears what it picked. The
schemas are the servers' published ones (``_rag_servers``) — none declares ``readOnlyHint``.
"""

from __future__ import annotations

import pytest

from orchestrator.mcp.discovery import DiscoveryError, discover
from orchestrator.mcp.doc_pull import ToolGuardError, vet_tools
from orchestrator.mcp.models import MCPServerStatus, MCPTool
from orchestrator.pkg.repos import DocSource
from tests.mcp._rag_servers import (
    BEDROCK_QUERY,
    CHROMA_ADD,
    CHROMA_GET,
    CHROMA_QUERY,
    FETCH,
    QDRANT_FIND,
    QDRANT_STORE,
    RAGIE_RETRIEVE,
    SEARCH,
    tool,
)


def _rag(**kw: object) -> DocSource:
    return DocSource("kb", str(kw.pop("server", "rag")), "rag", **kw)  # type: ignore[arg-type]


def test_a_list_tool_means_the_corpus_is_walked() -> None:
    tools = [
        tool("chroma", "chroma_query_documents", CHROMA_QUERY),
        tool("chroma", "chroma_get_documents", CHROMA_GET),
        tool("chroma", "chroma_add_documents", CHROMA_ADD),
    ]
    plan = discover(_rag(server="chroma", collection="docs"), tools)
    assert (plan.role, plan.strategy, plan.tool) == ("enumerate", "enumerate", "chroma_get_documents")
    assert (plan.collection_arg, plan.limit_arg, plan.offset_arg) == ("collection_name", "limit", "offset")
    assert plan.tools == ("chroma_get_documents",)  # the write tool is never picked


def test_without_a_list_tool_the_retrieve_tool_is_queried() -> None:
    chroma = discover(
        _rag(collection="docs"),
        [tool("c", "chroma_query_documents", CHROMA_QUERY), tool("c", "x", CHROMA_ADD)],
    )
    assert (chroma.role, chroma.strategy, chroma.query_arg, chroma.batch) == (
        "retrieve",
        "query",
        "query_texts",
        True,
    )
    assert (chroma.collection_arg, chroma.top_k_arg) == ("collection_name", "n_results")
    qdrant = discover(
        _rag(), [tool("q", "qdrant-find", QDRANT_FIND), tool("q", "qdrant-store", QDRANT_STORE)]
    )
    assert (qdrant.tool, qdrant.query_arg, qdrant.batch, qdrant.top_k_arg) == (
        "qdrant-find",
        "query",
        False,
        "",
    )
    ragie = discover(_rag(), [tool("r", "retrieve", RAGIE_RETRIEVE)])
    assert (ragie.tool, ragie.top_k_arg) == ("retrieve", "topK")


def test_bedrock_takes_the_collection_as_its_knowledge_base_id() -> None:
    plan = discover(_rag(collection="KB123"), [tool("b", "QueryKnowledgeBases", BEDROCK_QUERY)])
    assert (plan.collection_arg, plan.top_k_arg) == ("knowledge_base_id", "number_of_results")
    with pytest.raises(DiscoveryError, match="requires 'knowledge_base_id' — set rag.collection"):
        discover(_rag(), [tool("b", "QueryKnowledgeBases", BEDROCK_QUERY)])


def test_search_and_fetch_are_a_pair() -> None:
    plan = discover(_rag(), [tool("k", "search", SEARCH), tool("k", "fetch", FETCH)])
    assert (plan.role, plan.tool, plan.fetch_tool, plan.fetch_arg) == (
        "search_fetch",
        "search",
        "fetch",
        "id",
    )
    assert plan.tools == ("search", "fetch") and plan.strategy == "query"


def test_two_retrieve_candidates_are_refused_naming_both() -> None:
    tools = [tool("q", "qdrant-find", QDRANT_FIND), tool("q", "semantic_search", SEARCH)]
    with pytest.raises(DiscoveryError) as err:
        discover(_rag(server="q"), tools)
    assert "2 tools could be the retrieve tool (q:qdrant-find, q:semantic_search)" in str(err.value)
    assert "rag.tool" in str(err.value)


def test_two_list_candidates_are_refused_naming_both() -> None:
    tools = [tool("c", "get_documents", CHROMA_GET), tool("c", "scroll", CHROMA_GET)]
    with pytest.raises(DiscoveryError, match=r"list tool \(c:get_documents, c:scroll\)"):
        discover(_rag(server="c", collection="x"), tools)


def test_the_tool_override_settles_an_ambiguity_and_can_force_the_query_path() -> None:
    tools = [tool("q", "qdrant-find", QDRANT_FIND), tool("q", "semantic_search", SEARCH)]
    assert discover(_rag(tool="semantic_search"), tools).tool == "semantic_search"
    chroma = [
        tool("c", "chroma_query_documents", CHROMA_QUERY),
        tool("c", "chroma_get_documents", CHROMA_GET),
    ]
    forced = discover(_rag(collection="d", tool="chroma_query_documents"), chroma)
    assert (forced.role, forced.tool) == ("retrieve", "chroma_query_documents")
    with pytest.raises(DiscoveryError, match="rag.tool q:nope is not allow-listed or not offered"):
        discover(_rag(server="q", tool="nope"), tools)


def test_the_query_arg_override_names_the_parameter() -> None:
    odd = {"type": "object", "properties": {"prompt": {"type": "string"}}, "required": ["prompt"]}
    with pytest.raises(DiscoveryError, match="no list, search/fetch or retrieve tool"):
        discover(_rag(), [tool("r", "ask", odd)])
    plan = discover(_rag(query_arg="prompt"), [tool("r", "ask", odd)])
    assert (plan.tool, plan.query_arg) == ("ask", "prompt")
    with pytest.raises(DiscoveryError, match="has no parameter 'nope'"):
        discover(_rag(tool="ask", query_arg="nope"), [tool("r", "ask", odd)])


def test_a_collection_with_nowhere_to_go_or_a_required_one_missing_is_refused() -> None:
    with pytest.raises(DiscoveryError, match="takes no collection parameter"):
        discover(_rag(collection="docs"), [tool("r", "retrieve", RAGIE_RETRIEVE)])
    with pytest.raises(DiscoveryError, match="requires 'collection_name' — set rag.collection"):
        discover(_rag(), [tool("c", "chroma_query_documents", CHROMA_QUERY)])


def test_an_argument_nobody_can_supply_is_refused_before_any_call() -> None:
    strict = {
        "type": "object",
        "properties": {"query": {"type": "string"}, "tenant": {"type": "string"}},
        "required": ["query", "tenant"],
    }
    with pytest.raises(DiscoveryError, match=r"requires \['tenant'\], which a docs pull cannot supply"):
        discover(_rag(), [tool("r", "find", strict)])


# ---- the guard ------------------------------------------------------------------------------


def _status(*tools: MCPTool) -> MCPServerStatus:
    return MCPServerStatus(name="qdrant", tools=tools)


def test_an_unannotated_tool_is_refused_unless_trust_read_only_names_it() -> None:
    find = tool("qdrant", "qdrant-find", QDRANT_FIND)  # read_only=None, as the real server
    with pytest.raises(ToolGuardError) as err:
        vet_tools(_rag(server="qdrant"), _status(find), ["qdrant-find"])
    assert (
        "qdrant:qdrant-find (read_only=None, not declared read-only, nor named in rag.trust_read_only)"
        in str(err.value)
    )
    vetted = vet_tools(
        _rag(server="qdrant", trust_read_only=("qdrant-find",)), _status(find), ["qdrant-find"]
    )
    assert list(vetted) == ["qdrant-find"]


def test_a_declared_read_only_tool_needs_no_vouching_and_trust_never_widens_the_allow_list() -> None:
    declared = tool("qdrant", "qdrant-find", QDRANT_FIND, read_only=True)
    assert list(vet_tools(_rag(server="qdrant"), _status(declared), ["qdrant-find"])) == ["qdrant-find"]
    with pytest.raises(ToolGuardError, match=r"qdrant:qdrant-find \(not allow-listed or not offered\)"):
        vet_tools(_rag(server="qdrant", trust_read_only=("qdrant-find",)), _status(), ["qdrant-find"])


def test_the_confluence_and_jira_guard_is_unchanged() -> None:
    """P2's rule, exactly: readOnlyHint true, and no vouching list exists for these kinds."""
    status = MCPServerStatus(
        name="atlassian",
        tools=(
            MCPTool(server="atlassian", name="jira_search", read_only=True),
            MCPTool(server="atlassian", name="jira_get_issue", read_only=None),
        ),
    )
    jira = DocSource("t", "atlassian", "jira", jql="x")
    with pytest.raises(ToolGuardError) as err:
        vet_tools(jira, status)
    message = str(err.value)
    assert "atlassian:jira_get_issue (read_only=None, not declared read-only)" in message
    assert "trust_read_only" not in message
