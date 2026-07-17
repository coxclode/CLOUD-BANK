"""Tests unitarios: SlidingWindowRateLimiter con FakeRedis."""

from __future__ import annotations

import pytest
import pytest_asyncio
from fakeredis.aioredis import FakeRedis

from src.security.rate_limiting.rate_limiter import (
    RateLimitExceededError,
    SlidingWindowRateLimiter,
    RateLimitResult,
)


@pytest_asyncio.fixture
async def redis():
    r = FakeRedis(decode_responses=False)
    yield r
    await r.flushall()
    await r.aclose()


@pytest_asyncio.fixture
async def limiter(redis):
    return SlidingWindowRateLimiter(redis, default_limit=5, window_seconds=60)


class TestSlidingWindowRateLimiter:

    @pytest.mark.asyncio
    async def test_allows_requests_within_limit(self, limiter):
        for _ in range(5):
            result = await limiter.check("user-123")
            assert result.allowed

    @pytest.mark.asyncio
    async def test_blocks_request_beyond_limit(self, limiter):
        for _ in range(5):
            await limiter.check("user-456")
        with pytest.raises(RateLimitExceededError) as exc_info:
            await limiter.check("user-456")
        assert exc_info.value.result.allowed is False

    @pytest.mark.asyncio
    async def test_different_identifiers_independent(self, limiter):
        for _ in range(5):
            await limiter.check("user-A")
        # user-B should still be allowed
        result = await limiter.check("user-B")
        assert result.allowed

    @pytest.mark.asyncio
    async def test_remaining_decreases_per_request(self, limiter):
        result1 = await limiter.check("user-C")
        result2 = await limiter.check("user-C")
        assert result2.remaining == result1.remaining - 1

    @pytest.mark.asyncio
    async def test_result_has_correct_limit(self, limiter):
        result = await limiter.check("user-D")
        assert result.limit == 5

    @pytest.mark.asyncio
    async def test_custom_limit_overrides_default(self, limiter):
        # Custom limit of 2
        await limiter.check("user-E", limit=2)
        await limiter.check("user-E", limit=2)
        with pytest.raises(RateLimitExceededError):
            await limiter.check("user-E", limit=2)

    @pytest.mark.asyncio
    async def test_rate_limit_result_headers(self, limiter):
        result = await limiter.check("user-F")
        headers = result.headers
        assert "X-RateLimit-Limit" in headers
        assert "X-RateLimit-Remaining" in headers
        assert "X-RateLimit-Reset" in headers
        assert int(headers["X-RateLimit-Limit"]) == 5

    @pytest.mark.asyncio
    async def test_exceeded_result_includes_retry_after(self, limiter):
        for _ in range(5):
            await limiter.check("user-G")
        try:
            await limiter.check("user-G")
        except RateLimitExceededError as e:
            assert e.result.retry_after is not None
            assert e.result.retry_after > 0
