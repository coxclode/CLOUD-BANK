"""
Middleware de autenticación y autorización.
Ejecuta ANTES de que el request llegue al router.
"""

from __future__ import annotations

import time
import uuid
from typing import Callable

import structlog
from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from src.monitoring.logging.structured_logger import bind_request_context
from src.monitoring.metrics import AUTHENTICATION_FAILURES_TOTAL, HTTP_REQUESTS_TOTAL
from src.security.authentication.api_key_authenticator import AuthenticationError as ApiKeyAuthError
from src.security.authentication.jwt_authenticator import AuthenticationError as JwtAuthError

logger = structlog.get_logger(__name__)

_EXCLUDED_PATHS = frozenset({
    "/v1/admin/health/live", "/v1/admin/health/ready",
    "/docs", "/redoc", "/openapi.json",
    "/v1/auth/login",
})


class AuthMiddleware(BaseHTTPMiddleware):
    """
    Dos esquemas de autenticación soportados:
      - `X-API-Key: <key>`           → integraciones máquina-a-máquina (ApiKeyIdentity)
      - `Authorization: Bearer <jwt>` → sesión de oficiales vía frontend (Principal)
    Los endpoints de health, métricas y login están excluidos.
    """

    def __init__(self, app, authenticator=None) -> None:
        super().__init__(app)
        self._auth = authenticator

    async def _get_authenticator(self):
        """Construcción perezosa: Redis solo está disponible una vez arrancado el event loop."""
        if self._auth is None:
            from src.api.dependencies import get_redis
            from src.security.authentication.api_key_authenticator import ApiKeyAuthenticator
            self._auth = ApiKeyAuthenticator(await get_redis())
        return self._auth

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        if request.url.path in _EXCLUDED_PATHS:
            return await call_next(request)

        request_id  = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        correlation_id = request.headers.get("X-Correlation-ID", "")
        bind_request_context(request_id, correlation_id)
        request.state.request_id = request_id

        api_key = request.headers.get("X-API-Key")
        bearer_token = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()

        if not api_key and not bearer_token:
            logger.warning("auth_middleware.missing_credentials", path=request.url.path)
            AUTHENTICATION_FAILURES_TOTAL.labels(reason="missing_key").inc()
            return JSONResponse(
                status_code=401,
                content={"error": "Unauthorized", "detail": "Se requiere X-API-Key o Authorization: Bearer <jwt>."},
                headers={"WWW-Authenticate": "ApiKey"},
            )

        try:
            if api_key:
                authenticator = await self._get_authenticator()
                identity = await authenticator.authenticate(api_key)
            else:
                from config.settings import get_settings
                from src.security.authentication.jwt_authenticator import decode_access_token
                identity = decode_access_token(bearer_token, get_settings().security.jwt_secret.get_secret_value())
            request.state.identity = identity
            logger.debug("auth_middleware.authenticated", client=identity.client_name)
        except (ApiKeyAuthError, JwtAuthError) as exc:
            logger.warning("auth_middleware.auth_failed", reason=exc.reason)
            AUTHENTICATION_FAILURES_TOTAL.labels(reason="invalid_credentials").inc()
            return JSONResponse(
                status_code=401,
                content={"error": "Unauthorized", "detail": "Credenciales inválidas."},
            )

        return await call_next(request)
