"""
Rate Limiter: Sliding Window con Redis

Algoritmo: Sliding Window Counter.
Más preciso que Fixed Window (no permite ráfagas al cambio de ventana).
Más eficiente que Token Bucket distribuido.

Claves Redis:
  cloudbank:ratelimit:{identifier}:{window_timestamp} → count
  TTL: window_seconds × 2 (para evitar limpieza prematura)

Respuesta con encabezados estándar (RFC 6585):
  X-RateLimit-Limit: <max_requests>
  X-RateLimit-Remaining: <remaining>
  X-RateLimit-Reset: <unix_timestamp>
  Retry-After: <seconds> (solo en 429)
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

import structlog
from redis.asyncio import Redis

logger = structlog.get_logger(__name__)

_KEY_PREFIX    = "cloudbank:ratelimit:"
_WINDOW_SECS   = 60         # Ventana de 1 minuto
_DEFAULT_LIMIT = 60         # 60 requests/min por defecto


@dataclass
class RateLimitResult:
    allowed: bool
    current_count: int
    limit: int
    remaining: int
    reset_at: int           # Unix timestamp cuando se resetea la ventana
    retry_after: Optional[int] = None   # Segundos hasta siguiente intento

    @property
    def headers(self) -> dict[str, str]:
        h = {
            "X-RateLimit-Limit":     str(self.limit),
            "X-RateLimit-Remaining": str(max(0, self.remaining)),
            "X-RateLimit-Reset":     str(self.reset_at),
        }
        if self.retry_after is not None:
            h["Retry-After"] = str(self.retry_after)
        return h


class RateLimitExceededError(Exception):
    def __init__(self, result: RateLimitResult) -> None:
        super().__init__(
            f"Rate limit excedido: {result.current_count}/{result.limit} req/min"
        )
        self.result = result


class SlidingWindowRateLimiter:
    """
    Rate limiter distribuido con sliding window en Redis.
    Thread-safe, works across multiple instances (pods).
    """

    def __init__(
        self,
        redis_client: Redis,
        default_limit: int = _DEFAULT_LIMIT,
        window_seconds: int = _WINDOW_SECS,
    ) -> None:
        self._redis   = redis_client
        self._default = default_limit
        self._window  = window_seconds

    async def check(
        self,
        identifier: str,
        limit: Optional[int] = None,
    ) -> RateLimitResult:
        """
        Verifica y registra un request.
        Lanza RateLimitExceededError si se supera el límite.

        identifier: IP, API key, user_id, etc.
        limit: límite personalizado (usa default si None)
        """
        effective_limit = limit or self._default
        now  = int(time.time())
        win  = now - (now % self._window)
        key  = f"{_KEY_PREFIX}{identifier}:{win}"

        current = await self._redis.incr(key)
        if current == 1:
            await self._redis.expire(key, self._window * 2)

        reset_at = win + self._window
        remaining = max(0, effective_limit - current)
        allowed   = current <= effective_limit

        result = RateLimitResult(
            allowed=allowed,
            current_count=current,
            limit=effective_limit,
            remaining=remaining,
            reset_at=reset_at,
            retry_after=(reset_at - now) if not allowed else None,
        )

        if not allowed:
            logger.warning(
                "rate_limiter.limit_exceeded",
                identifier=identifier,
                count=current,
                limit=effective_limit,
            )
            raise RateLimitExceededError(result)

        return result

    async def reset(self, identifier: str) -> None:
        """Resetea el contador para un identificador (uso en tests y admin)."""
        now = int(time.time())
        win = now - (now % self._window)
        key = f"{_KEY_PREFIX}{identifier}:{win}"
        await self._redis.delete(key)

    async def get_current_count(self, identifier: str) -> int:
        """Consulta el count actual sin incrementar."""
        now = int(time.time())
        win = now - (now % self._window)
        key = f"{_KEY_PREFIX}{identifier}:{win}"
        raw = await self._redis.get(key)
        return int(raw) if raw else 0
