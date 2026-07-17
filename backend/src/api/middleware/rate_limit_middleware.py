"""
Middleware de rate limiting por IP + API Key.
Aplica límites diferenciados según el tipo de endpoint.
"""

from __future__ import annotations

from typing import Callable

import structlog
from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from src.monitoring.metrics import RATE_LIMIT_HITS_TOTAL
from src.security.rate_limiting.rate_limiter import RateLimitExceededError

logger = structlog.get_logger(__name__)

_EXEMPT_PATHS = frozenset({
    "/v1/admin/health/live", "/v1/admin/health/ready",
})

_ENDPOINT_LIMITS = {
    "/v1/credit/evaluate": 10,
    "/v1/credit":          100,
    "/admin":              20,
}


class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    Rate limiting por IP (primario) + por API Key (secundario).
    Los límites son configurables por endpoint.
    """

    def __init__(self, app, rate_limiter=None) -> None:
        super().__init__(app)
        self._limiter = rate_limiter

    async def _get_limiter(self):
        """Construcción perezosa: Redis solo está disponible una vez arrancado el event loop."""
        if self._limiter is None:
            from src.api.dependencies import get_redis
            from src.security.rate_limiting.rate_limiter import SlidingWindowRateLimiter
            self._limiter = SlidingWindowRateLimiter(await get_redis())
        return self._limiter

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        if request.url.path in _EXEMPT_PATHS:
            return await call_next(request)

        client_ip = request.headers.get("X-Forwarded-For", request.client.host if request.client else "unknown")
        client_ip = client_ip.split(",")[0].strip()

        identity = getattr(request.state, "identity", None)
        limit_per_minute = _ENDPOINT_LIMITS.get(request.url.path, 60)

        if identity:
            limit_per_minute = min(limit_per_minute, getattr(identity, "rate_limit_per_minute", limit_per_minute))
            identifier = f"key:{identity.key_id}"
        else:
            identifier = f"ip:{client_ip}"

        limiter = await self._get_limiter()
        try:
            rl_result = await limiter.check(identifier, limit=limit_per_minute)
        except RateLimitExceededError as exc:
            RATE_LIMIT_HITS_TOTAL.labels(
                identifier_type="api_key" if identity else "ip"
            ).inc()
            logger.warning(
                "rate_limit_middleware.exceeded",
                identifier=identifier,
                count=exc.result.current_count,
                limit=exc.result.limit,
            )
            return JSONResponse(
                status_code=429,
                content={
                    "error": "Too Many Requests",
                    "detail": f"Límite excedido: {exc.result.current_count}/{exc.result.limit} req/min.",
                    "retry_after": exc.result.retry_after,
                },
                headers=exc.result.headers,
            )

        response = await call_next(request)
        for k, v in rl_result.headers.items():
            response.headers[k] = v
        return response
