"""Onboard MCP tools onto the gateway: publish contracts + register handlers.

This closes Phase 1: the derived ``ToolContract``s are published into the
registry and the ``MCPToolHandler``s are registered in the gateway's
``HandlerRegistry``, so ``load_published_tools`` matches them and they become
invocable at ``/v1/tools/{id}/{version}/invoke`` with the gateway's full
rate-limit + audit + approval path. Idempotent: re-onboarding skips contracts
and handlers already present.
"""

from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from orchestrator.gateway.handlers import HandlerRegistry
from orchestrator.mcp.config import load_mcp_configs
from orchestrator.mcp.handler import MCPRegisteredTool, build_mcp_tools
from orchestrator.mcp.registry import MCPRegistry

logger = logging.getLogger("orchestrator.mcp.onboard")


def register_mcp_handlers(handler_registry: HandlerRegistry, built: list[MCPRegisteredTool]) -> list[str]:
    """Register each MCP handler (idempotent). Returns the contract ids registered."""
    ids: list[str] = []
    for item in built:
        handler = item.handler
        if handler_registry.get(handler.contract_id, handler.contract_version) is None:
            handler_registry.register(handler)
        ids.append(handler.contract_id)
    return ids


async def publish_mcp_contracts(session: AsyncSession, built: list[MCPRegisteredTool]) -> list[str]:
    """Publish each derived ToolContract (idempotent). Returns ids newly published."""
    from orchestrator.registry._common import LifecycleState
    from orchestrator.registry.db.models import ToolContractRow
    from orchestrator.registry.repositories import VersionedRepo

    repo = VersionedRepo(session, ToolContractRow)
    published: list[str] = []
    for item in built:
        contract = item.contract
        cid, version = contract.metadata.id, contract.metadata.version
        if await repo.get_by_id_version(cid, version) is not None:
            continue
        await repo.create(
            id=cid,
            version=version,
            description=contract.metadata.description,
            tags=list(contract.metadata.tags),
            spec=contract.spec.model_dump(mode="json"),
            status=LifecycleState.PUBLISHED,
        )
        published.append(cid)
    return published


async def onboard_mcp_tools(
    session: AsyncSession,
    handler_registry: HandlerRegistry,
    *,
    config_path: str | Path | None = None,
    mcp_registry: MCPRegistry | None = None,
) -> list[str]:
    """Discover configured MCP tools, publish their contracts, register handlers.

    No-op (empty list) when no MCP servers are configured. Commits the session.
    """
    configs = load_mcp_configs(config_path)
    if not configs:
        return []
    registry = mcp_registry or MCPRegistry(configs)
    built = await build_mcp_tools(registry, configs=configs)
    published = await publish_mcp_contracts(session, built)
    register_mcp_handlers(handler_registry, built)
    await session.commit()
    logger.info("mcp.onboard", extra={"discovered": len(built), "newly_published": len(published)})
    return [item.contract.metadata.id for item in built]


__all__ = ["onboard_mcp_tools", "publish_mcp_contracts", "register_mcp_handlers"]
