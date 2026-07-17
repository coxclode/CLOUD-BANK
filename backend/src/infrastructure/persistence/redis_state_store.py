"""
Adaptador: Redis State Store

Implementa EvaluationStateStore usando Redis.
Persiste el estado del pipeline LangGraph para recuperación ante fallos.
TTL configurable: por defecto 24h (solicitudes deben resolverse en 1 día).
"""

from __future__ import annotations

import json
from typing import Optional

import structlog
from redis.asyncio import Redis

from src.domain.repositories.credit_repository import EvaluationStateStore

logger = structlog.get_logger(__name__)

_STATE_KEY_PREFIX = "cloudbank:pipeline:state:"
_TTL_SECONDS      = 86_400  # 24 horas


class RedisEvaluationStateStore(EvaluationStateStore):
    """
    Almacena estados de pipeline en Redis con serialización JSON.
    Thread-safe: usa el cliente asyncio de redis-py.
    """

    def __init__(self, redis_client: Redis) -> None:
        self._redis = redis_client

    async def save_state(self, pipeline_id: str, state: dict) -> None:
        key = f"{_STATE_KEY_PREFIX}{pipeline_id}"
        try:
            await self._redis.setex(
                name=key,
                time=_TTL_SECONDS,
                value=json.dumps(state, default=str),
            )
            logger.debug("redis_state_store.saved", pipeline_id=pipeline_id)
        except Exception as exc:
            logger.error("redis_state_store.save_failed", pipeline_id=pipeline_id, error=str(exc))
            raise

    async def load_state(self, pipeline_id: str) -> Optional[dict]:
        key = f"{_STATE_KEY_PREFIX}{pipeline_id}"
        try:
            raw = await self._redis.get(key)
            if raw is None:
                return None
            return json.loads(raw)
        except Exception as exc:
            logger.error("redis_state_store.load_failed", pipeline_id=pipeline_id, error=str(exc))
            return None

    async def delete_state(self, pipeline_id: str) -> None:
        key = f"{_STATE_KEY_PREFIX}{pipeline_id}"
        await self._redis.delete(key)
        logger.debug("redis_state_store.deleted", pipeline_id=pipeline_id)

    async def list_pending_pipelines(self) -> list[str]:
        pattern = f"{_STATE_KEY_PREFIX}*"
        keys = await self._redis.keys(pattern)
        prefix_len = len(_STATE_KEY_PREFIX)
        return [k.decode()[prefix_len:] for k in keys]
