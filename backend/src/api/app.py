"""
CLOUD BANK — FastAPI Application Factory

Assembla la aplicación con todos los middlewares y routers.
Orden de middlewares (se ejecutan en orden inverso al registro):
  1. SecurityHeadersMiddleware (primero en respuesta, último en request)
  2. RateLimitMiddleware
  3. AuthMiddleware (último en request, primero en respuesta)
  4. CORSMiddleware
  5. TrustedHostMiddleware
"""

from __future__ import annotations

import time
import uuid
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from src.api.middleware.auth_middleware import AuthMiddleware
from src.api.middleware.rate_limit_middleware import RateLimitMiddleware
from src.api.middleware.security_headers_middleware import SecurityHeadersMiddleware
from src.api.v1.auth_router import router as auth_router
from src.api.v1.credit_router import router as credit_router
from src.api.v1.admin_router import router as admin_router
from src.api.v1.identity_router import router as identity_router
from src.monitoring.logging.structured_logger import configure_logging, bind_request_context
from src.monitoring.metrics import HTTP_REQUEST_DURATION_SECONDS, HTTP_REQUESTS_TOTAL

logger = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Inicialización y teardown de la aplicación."""
    from config.settings import get_settings
    settings = get_settings()

    configure_logging(
        environment=settings.app.environment,
        log_level=settings.app.log_level,
    )
    logger.info(
        "cloudbank.starting",
        version=settings.app.version,
        environment=settings.app.environment,
    )

    # Verificar conectividad a dependencias críticas
    from src.api.dependencies import get_redis, get_db_pool
    try:
        redis = await get_redis()
        await redis.ping()
        logger.info("cloudbank.redis_connected")
    except Exception as exc:
        logger.error("cloudbank.redis_unavailable", error=str(exc))

    yield

    logger.info("cloudbank.shutdown")


def create_app(settings=None) -> FastAPI:
    if settings is None:
        from config.settings import get_settings
        settings = get_settings()

    app = FastAPI(
        title="CLOUD BANK — Credit Evaluation API",
        description=(
            "Sistema multi-agente de evaluación de crédito personal. "
            "Pipeline de 4 Deep Agents con 10 capas de razonamiento cada uno. "
            "Cumplimiento GDPR Art. 22 / Basel III / SR 11-7."
        ),
        version=settings.app.version,
        docs_url="/docs" if settings.app.environment != "production" else None,
        redoc_url="/redoc" if settings.app.environment != "production" else None,
        openapi_url="/openapi.json" if settings.app.environment != "production" else None,
        lifespan=lifespan,
    )

    # ── Middleware (orden importante: último registrado = primero ejecutado) ──

    # 1. Security Headers (siempre al final de la respuesta)
    app.add_middleware(SecurityHeadersMiddleware)

    # 2. Rate Limiting
    async def _get_rate_limiter():
        from src.api.dependencies import get_redis
        from src.security.rate_limiting.rate_limiter import SlidingWindowRateLimiter
        redis = await get_redis()
        return SlidingWindowRateLimiter(redis)

    app.add_middleware(
        RateLimitMiddleware,
        rate_limiter=None,  # Se inicializa lazy en el middleware
    )

    # 3. Auth
    app.add_middleware(
        AuthMiddleware,
        authenticator=None,  # Se inicializa lazy
    )

    # 4. CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.app.allowed_origins,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type", "X-API-Key", "X-Request-ID", "X-Correlation-ID"],
        max_age=86400,
    )

    # 5. Trusted Hosts
    if settings.app.environment == "production":
        app.add_middleware(
            TrustedHostMiddleware,
            allowed_hosts=settings.app.allowed_hosts,
        )

    # ── Request timing middleware ─────────────────────────────────────────────

    @app.middleware("http")
    async def request_timing(request: Request, call_next):
        start = time.monotonic()
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        bind_request_context(request_id)
        request.state.request_id = request_id

        response = await call_next(request)
        elapsed = time.monotonic() - start

        endpoint = request.url.path
        method   = request.method
        code     = str(response.status_code)

        HTTP_REQUEST_DURATION_SECONDS.labels(
            method=method, endpoint=endpoint, status_code=code
        ).observe(elapsed)
        HTTP_REQUESTS_TOTAL.labels(
            method=method, endpoint=endpoint, status_code=code
        ).inc()

        response.headers["X-Request-ID"]    = request_id
        response.headers["X-Response-Time"] = f"{elapsed * 1000:.2f}ms"
        return response

    # ── Routers ───────────────────────────────────────────────────────────────
    app.include_router(auth_router)
    app.include_router(credit_router)
    app.include_router(admin_router)
    app.include_router(identity_router)

    # ── Exception Handlers ────────────────────────────────────────────────────

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, exc: RequestValidationError):
        logger.warning("api.validation_error", errors=str(exc.errors())[:200])
        return JSONResponse(
            status_code=422,
            content={
                "error":   "Validation Error",
                "detail":  exc.errors(),
                "request_id": getattr(request.state, "request_id", ""),
            },
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_error_handler(request: Request, exc: StarletteHTTPException):
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error":   exc.detail,
                "request_id": getattr(request.state, "request_id", ""),
            },
        )

    @app.exception_handler(Exception)
    async def generic_error_handler(request: Request, exc: Exception):
        logger.error("api.unhandled_error", error=str(exc), path=request.url.path)
        return JSONResponse(
            status_code=500,
            content={
                "error":   "Internal Server Error",
                "detail":  "Error inesperado. Contacte soporte técnico.",
                "request_id": getattr(request.state, "request_id", ""),
            },
        )

    return app
