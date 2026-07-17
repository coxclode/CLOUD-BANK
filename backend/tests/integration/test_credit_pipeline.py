"""
Tests de integración: Pipeline completo con Redis real.

Requieren:
  - Redis corriendo en localhost:6379 (o CLOUDBANK_REDIS_URL)
  - pytest -m integration

Verifican que el rate limiter, auth y state store funcionan juntos
con un Redis real (no FakeRedis).
"""

from __future__ import annotations

import pytest
import pytest_asyncio

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def redis_url() -> str:
    import os
    return os.getenv("CLOUDBANK_REDIS_URL", "redis://localhost:6379/15")


@pytest_asyncio.fixture(scope="module")
async def real_redis(redis_url):
    from redis.asyncio import Redis
    client = Redis.from_url(redis_url, decode_responses=False)
    try:
        await client.ping()
    except Exception:
        pytest.skip("Redis no disponible — saltando tests de integración")
    yield client
    await client.flushdb()
    await client.aclose()


class TestRateLimiterWithRealRedis:

    @pytest.mark.asyncio
    async def test_sliding_window_persists_across_calls(self, real_redis):
        from src.security.rate_limiting.rate_limiter import SlidingWindowRateLimiter
        limiter = SlidingWindowRateLimiter(real_redis, default_limit=3, window_seconds=60)

        r1 = await limiter.check("integration-test-user")
        r2 = await limiter.check("integration-test-user")
        r3 = await limiter.check("integration-test-user")

        assert r1.remaining > r2.remaining > r3.remaining
        assert r3.remaining == 0

    @pytest.mark.asyncio
    async def test_exceeding_limit_raises_error(self, real_redis):
        from src.security.rate_limiting.rate_limiter import (
            SlidingWindowRateLimiter, RateLimitExceededError,
        )
        limiter = SlidingWindowRateLimiter(real_redis, default_limit=2, window_seconds=60)
        id_ = "integration-exceed-test"

        await limiter.check(id_)
        await limiter.check(id_)
        with pytest.raises(RateLimitExceededError):
            await limiter.check(id_)


class TestRedisStateStore:

    @pytest.mark.asyncio
    async def test_save_and_retrieve_pipeline_state(self, real_redis):
        from src.infrastructure.persistence.redis_state_store import RedisEvaluationStateStore
        store = RedisEvaluationStateStore(real_redis)

        pipeline_id = "test-pipeline-001"
        state = {"status": "IN_PROGRESS", "fraud_score": 0.12}

        await store.save_state(pipeline_id, state)
        retrieved = await store.get_state(pipeline_id)

        assert retrieved is not None
        assert retrieved["fraud_score"] == 0.12

    @pytest.mark.asyncio
    async def test_nonexistent_state_returns_none(self, real_redis):
        from src.infrastructure.persistence.redis_state_store import RedisEvaluationStateStore
        store = RedisEvaluationStateStore(real_redis)
        result = await store.get_state("nonexistent-pipeline-id")
        assert result is None
