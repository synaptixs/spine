"""In-memory token-bucket rate limiter, keyed per tool.

Single-process for now. Sufficient until the gateway runs more than one
worker — at which point a shared store (Redis) takes over with the same
interface.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field


@dataclass
class _Bucket:
    capacity: int
    refill_per_second: float
    tokens: float = 0.0
    updated_at: float = field(default_factory=time.monotonic)

    def take(self, now: float) -> tuple[bool, float]:
        """Try to take one token. Returns (allowed, retry_after_seconds)."""
        elapsed = now - self.updated_at
        self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_per_second)
        self.updated_at = now
        if self.tokens >= 1.0:
            self.tokens -= 1.0
            return True, 0.0
        deficit = 1.0 - self.tokens
        retry_after = deficit / self.refill_per_second if self.refill_per_second > 0 else float("inf")
        return False, retry_after


class RateLimiter:
    """Per-tool token bucket with sane defaults for tools that omit limits."""

    DEFAULT_PER_MINUTE = 600  # generous; explicit contract values override

    def __init__(self) -> None:
        self._buckets: dict[str, _Bucket] = {}
        self._lock = asyncio.Lock()

    @staticmethod
    def _bucket_from_spec(rate_limits: dict[str, int] | None) -> _Bucket:
        per_minute = (rate_limits or {}).get("requests_per_minute") or RateLimiter.DEFAULT_PER_MINUTE
        burst = (rate_limits or {}).get("burst") or per_minute
        return _Bucket(capacity=burst, refill_per_second=per_minute / 60.0, tokens=float(burst))

    async def check(self, tool_key: str, rate_limits: dict[str, int] | None) -> tuple[bool, float]:
        async with self._lock:
            bucket = self._buckets.get(tool_key)
            if bucket is None:
                bucket = self._bucket_from_spec(rate_limits)
                self._buckets[tool_key] = bucket
            return bucket.take(time.monotonic())

    def reset(self) -> None:
        self._buckets.clear()
