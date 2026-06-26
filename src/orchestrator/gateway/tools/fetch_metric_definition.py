"""fetch_metric_definition: look up a pinned definition from the registry glossary.

Agents call this when they need the authoritative definition of a metric
(e.g. "what counts as 'active customer' for Q1?"). The glossary is
populated via the registry's /v1/glossary endpoints; this handler is a
thin read.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

from orchestrator.gateway.invocation import InvocationContext

DEFAULT_REGISTRY_URL = "http://localhost:8000"


class FetchMetricDefinitionHandler:
    contract_id: str = "tool.fetch_metric_definition"
    contract_version: str = "0.1.0"

    def __init__(self, *, base_url: str | None = None, client: httpx.AsyncClient | None = None) -> None:
        resolved = base_url or os.getenv("ORCHESTRATOR_REGISTRY_URL") or DEFAULT_REGISTRY_URL
        self._base_url = resolved.rstrip("/")
        self._client = client

    async def __call__(self, inputs: dict[str, Any], ctx: InvocationContext) -> dict[str, Any]:
        term = str(inputs.get("term", "")).strip()
        version = inputs.get("version")
        if not term:
            raise ValueError("fetch_metric_definition: 'term' is required")

        suffix = f"/{version}" if version else ""
        url = f"{self._base_url}/v1/glossary/{term}{suffix}"
        headers = {"X-API-Key": os.getenv("ORCHESTRATOR_API_KEY", "dev-key")}
        if ctx.trace_id:
            headers["X-Trace-Id"] = ctx.trace_id

        owns_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=httpx.Timeout(10.0))
        try:
            response = await client.get(url, headers=headers)
        finally:
            if owns_client:
                await client.aclose()

        if response.status_code == 404:
            raise LookupError(f"fetch_metric_definition: no published definition for {term!r}")
        response.raise_for_status()
        row = response.json()
        spec = row.get("spec") or {}
        return {
            "term": row.get("id", term),
            "version": row.get("version"),
            "canonical_value": spec.get("canonical_value"),
            "definition": spec.get("definition"),
            "source": spec.get("source"),
            "owner": spec.get("owner"),
            "formula": spec.get("formula"),
        }


FETCH_METRIC_DEFINITION_CONTRACT_PAYLOAD: dict[str, Any] = {
    "metadata": {
        "id": "tool.fetch_metric_definition",
        "version": "0.1.0",
        "description": "Read a pinned metric definition from the registry glossary.",
        "tags": ["glossary", "definition"],
    },
    "spec": {
        "purpose": "Return the authoritative definition for a glossary term.",
        "side_effects": "read",
        "idempotent": True,
        "rate_limits": {"requests_per_minute": 120, "burst": 20},
        "authentication": {"type": "none"},
        "observability": {"audit": True, "trace": True},
    },
}
