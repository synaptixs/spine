from __future__ import annotations

from orchestrator.gateway.rate_limit import RateLimiter


async def test_first_calls_allowed_up_to_burst() -> None:
    limiter = RateLimiter()
    spec = {"requests_per_minute": 60, "burst": 3}
    for _ in range(3):
        allowed, retry = await limiter.check("tool.x@0.1.0", spec)
        assert allowed
        assert retry == 0.0


async def test_exceeding_burst_returns_retry_after() -> None:
    limiter = RateLimiter()
    spec = {"requests_per_minute": 60, "burst": 1}
    allowed, _ = await limiter.check("tool.x@0.1.0", spec)
    assert allowed
    allowed, retry = await limiter.check("tool.x@0.1.0", spec)
    assert not allowed
    assert 0 < retry <= 1.5


async def test_no_limits_uses_default() -> None:
    limiter = RateLimiter()
    for _ in range(10):
        allowed, _ = await limiter.check("tool.x@0.1.0", None)
        assert allowed


async def test_separate_tools_have_separate_buckets() -> None:
    limiter = RateLimiter()
    spec = {"requests_per_minute": 60, "burst": 1}
    a, _ = await limiter.check("tool.a@0.1.0", spec)
    b, _ = await limiter.check("tool.b@0.1.0", spec)
    assert a and b
