"""Stubbed warehouse query tool.

Returns fixture data shaped like a real warehouse result. The handler's
contract, audit, and rate-limit envelope is real; only the data source is
mocked. Customer deployments register a `tool.query_warehouse` of the same
contract id+version pointing at their actual warehouse (Snowflake,
BigQuery, Redshift, etc.) and override this handler in the gateway's
HandlerRegistry.
"""

from __future__ import annotations

from typing import Any

from orchestrator.gateway.invocation import InvocationContext


class QueryWarehouseHandler:
    contract_id: str = "tool.query_warehouse"
    contract_version: str = "0.1.0"

    # Inline fixtures keep the stub self-contained. Real adapters replace
    # this whole class with a warehouse-aware implementation.
    _FIXTURES: dict[str, list[dict[str, Any]]] = {
        "snowflake://prod/orders": [
            {"period": "2026-01", "orders": 12_403, "revenue_usd": 482_117.55},
            {"period": "2026-02", "orders": 13_991, "revenue_usd": 540_882.10},
            {"period": "2026-03", "orders": 15_624, "revenue_usd": 612_445.33},
        ],
        "snowflake://prod/customers": [
            {"period": "2026-Q1", "active": 4_521, "new": 412, "churned": 89},
        ],
    }

    async def __call__(self, inputs: dict[str, Any], ctx: InvocationContext) -> dict[str, Any]:
        _ = ctx
        dataset_reference = str(inputs.get("dataset_reference", "")).strip()
        time_period = str(inputs.get("time_period", "")).strip()
        if not dataset_reference:
            raise ValueError("query_warehouse: 'dataset_reference' is required")
        rows = self._FIXTURES.get(dataset_reference)
        if rows is None:
            raise LookupError(f"query_warehouse: no fixture for {dataset_reference!r}")
        return {
            "dataset_reference": dataset_reference,
            "time_period": time_period,
            "rows": rows,
            "row_count": len(rows),
            "source": "stub_fixture",
        }


QUERY_WAREHOUSE_CONTRACT_PAYLOAD: dict[str, Any] = {
    "metadata": {
        "id": "tool.query_warehouse",
        "version": "0.1.0",
        "description": "Run a warehouse query and return rows. Stubbed; deployments swap the handler.",
        "tags": ["warehouse", "stub"],
    },
    "spec": {
        "purpose": "Return rows from the named dataset for the given time period.",
        "side_effects": "read",
        "idempotent": True,
        "rate_limits": {"requests_per_minute": 60, "burst": 10},
        "authentication": {"type": "none"},
        "observability": {"audit": True, "trace": True},
    },
}
