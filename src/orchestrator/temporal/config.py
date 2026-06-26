"""Sprint 13.1 + 13.8: Temporal connection config and trace_id propagation.

Auto-detects the deployment shape from environment variables:

  - ``TEMPORAL_API_KEY`` set → Temporal Cloud over TLS, API-key auth.
  - ``TEMPORAL_API_KEY`` unset → plaintext connection to a local server
    (docker-compose default at ``localhost:7233``).

``TEMPORAL_NAMESPACE`` partitions environments. Defaults to ``default``,
which matches the docker-compose auto-setup image's pre-created namespace.

``TEMPORAL_TASK_QUEUE`` names the work queue that workers subscribe to
and workflows submit against. One queue per orchestrator instance keeps
the dev path simple; production tenants can split by tenant id later.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from temporalio.client import Client
from temporalio.service import TLSConfig

from orchestrator.obs import tracing


@dataclass(frozen=True)
class TemporalConfig:
    """Resolved Temporal connection parameters."""

    host: str
    namespace: str
    task_queue: str
    api_key: str | None
    use_tls: bool

    @property
    def is_cloud(self) -> bool:
        """True when targeting Temporal Cloud (API key + TLS)."""
        return self.api_key is not None

    @classmethod
    def from_env(cls) -> TemporalConfig:
        """Build the config from environment variables.

        Defaults match ``docker-compose.dev.yml``: ``localhost:7233``, the
        ``default`` namespace, and a ``orchestrator-tasks`` task queue.
        """
        api_key = os.getenv("TEMPORAL_API_KEY") or None
        return cls(
            host=os.getenv("TEMPORAL_HOST", "localhost:7233"),
            namespace=os.getenv("TEMPORAL_NAMESPACE", "default"),
            task_queue=os.getenv("TEMPORAL_TASK_QUEUE", "orchestrator-tasks"),
            api_key=api_key,
            # Cloud connections require TLS; local docker-compose talks plaintext.
            use_tls=bool(api_key),
        )


async def connect_client(config: TemporalConfig | None = None) -> Client:
    """Open a Temporal client. Cached state lives on the workflow worker,
    not here — every caller gets a fresh handle.

    Cloud auth uses the API-key flow; local connections skip TLS entirely.
    Both paths share the same Workflow / Activity registration so the rest
    of the orchestrator is auth-agnostic.
    """
    cfg = config or TemporalConfig.from_env()
    tls: TLSConfig | bool = False
    if cfg.use_tls:
        tls = TLSConfig()

    return await Client.connect(
        cfg.host,
        namespace=cfg.namespace,
        api_key=cfg.api_key,
        tls=tls,
        # Cross-process tracing (Phase 3): empty/no-op unless OTEL is configured.
        interceptors=tracing.temporal_interceptors(),
    )
