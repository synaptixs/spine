from __future__ import annotations

import pytest

from orchestrator.gateway.invocation import InvocationContext
from orchestrator.gateway.tools import QueryWarehouseHandler


def _ctx() -> InvocationContext:
    return InvocationContext(
        tool_id="tool.query_warehouse",
        tool_version="0.1.0",
        trace_id="t",
        actor="dev",
    )


async def test_returns_fixture_rows_for_known_dataset() -> None:
    handler = QueryWarehouseHandler()
    out = await handler(
        {"dataset_reference": "snowflake://prod/orders", "time_period": "2026-Q1"},
        _ctx(),
    )
    assert out["row_count"] == 3
    assert out["source"] == "stub_fixture"
    assert out["rows"][0]["orders"] == 12_403


async def test_missing_dataset_reference_rejected() -> None:
    handler = QueryWarehouseHandler()
    with pytest.raises(ValueError, match="dataset_reference"):
        await handler({}, _ctx())


async def test_unknown_dataset_returns_lookup_error() -> None:
    handler = QueryWarehouseHandler()
    with pytest.raises(LookupError):
        await handler({"dataset_reference": "snowflake://nope/no"}, _ctx())
