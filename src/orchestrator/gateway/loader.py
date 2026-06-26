"""Match registered handlers against published ToolContracts in the registry."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from orchestrator.gateway.handlers import HandlerRegistry, ToolHandler
from orchestrator.registry._common import LifecycleState
from orchestrator.registry.db.models import ToolContractRow


@dataclass(frozen=True)
class LoadedTool:
    handler: ToolHandler
    contract: ToolContractRow

    @property
    def contract_id(self) -> str:
        return self.contract.id

    @property
    def contract_version(self) -> str:
        return self.contract.version

    @property
    def spec(self) -> dict[str, Any]:
        return self.contract.spec_json


class LoaderReport:
    """Result of a load pass: matched tools plus diagnostics for mismatches."""

    def __init__(
        self,
        *,
        loaded: list[LoadedTool],
        unmatched_handlers: list[tuple[str, str]],
        unhandled_contracts: list[tuple[str, str]],
    ) -> None:
        self.loaded = loaded
        self.unmatched_handlers = unmatched_handlers
        self.unhandled_contracts = unhandled_contracts

    def by_id_version(self, contract_id: str, version: str) -> LoadedTool | None:
        for t in self.loaded:
            if t.contract_id == contract_id and t.contract_version == version:
                return t
        return None


async def load_published_tools(session: AsyncSession, registry: HandlerRegistry) -> LoaderReport:
    """Pull published ToolContracts whose (id, version) match a registered handler.

    Mismatches are reported, not raised, so the gateway can start with a
    partial registration and the operator can decide what to do about it.
    """
    handler_keys = set(registry.keys())
    if not handler_keys:
        return LoaderReport(loaded=[], unmatched_handlers=[], unhandled_contracts=[])

    stmt = select(ToolContractRow).where(ToolContractRow.status == LifecycleState.PUBLISHED.value)
    rows = list((await session.execute(stmt)).scalars().all())
    by_key = {(r.id, r.version): r for r in rows}

    loaded: list[LoadedTool] = []
    unmatched: list[tuple[str, str]] = []
    for cid, cver in handler_keys:
        contract = by_key.get((cid, cver))
        handler = registry.get(cid, cver)
        if contract is None or handler is None:
            unmatched.append((cid, cver))
            continue
        loaded.append(LoadedTool(handler=handler, contract=contract))

    unhandled = [k for k in by_key if k not in handler_keys]
    return LoaderReport(loaded=loaded, unmatched_handlers=unmatched, unhandled_contracts=unhandled)
