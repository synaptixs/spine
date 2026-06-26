"""Sprint 13: Temporal integration.

The synchronous ``POST /v1/tasks`` path (Sprint 5+) keeps running unchanged.
This package adds a side-by-side workflow-driven path so long-running tasks,
human approvals, and worker restarts behave correctly.

Connection model is auto-detecting:

  - When ``TEMPORAL_API_KEY`` is set, we treat ``TEMPORAL_HOST`` as a
    Temporal Cloud namespace endpoint and connect over TLS with the key.
  - Otherwise we connect plaintext to ``TEMPORAL_HOST`` (default
    ``localhost:7233``), which matches the docker-compose service shipped
    in ``docker-compose.dev.yml``.

A no-Temporal local install still works: ``TemporalConfig.from_env()`` is
called lazily inside the workflow routing path, so unit tests that never
touch the Temporal endpoint don't need the server up.
"""

from orchestrator.temporal.config import TemporalConfig, connect_client

__all__ = ["TemporalConfig", "connect_client"]
