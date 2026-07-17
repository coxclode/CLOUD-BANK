"""
Inyección de dependencias — FastAPI

Este módulo es el punto de ensamblaje (Composition Root) de toda la aplicación.
Las dependencias se instancian aquí y se inyectan en los routers.

Jerarquía:
  Infraestructura (Redis, Postgres, Vault)
    → Repositorios (adaptadores)
      → Puertos (interfaces)
        → Use Cases
          → Router

Ciclo de vida:
  - Singleton: conexiones a bases de datos, clientes, configuración
  - Por request: loggers, contextos de auditoría
"""

from __future__ import annotations

from functools import lru_cache
from typing import Callable

import asyncpg
import httpx
import structlog
from fastapi import Depends, HTTPException, Request, status
from redis.asyncio import Redis

from src.application.use_cases.evaluate_credit_application import (
    EvaluateCreditApplicationUseCase,
    OrchestratorPort,
)
from src.application.use_cases.retrieve_credit_decision import RetrieveCreditDecisionUseCase
from src.domain.services.credit_policy_service import CreditPolicyService
from src.infrastructure.identity.reniec_client import ReniecClient
from src.infrastructure.messaging.event_publisher import RedisStreamEventPublisher
from src.infrastructure.persistence.postgres_repositories import (
    PostgresCreditApplicationRepository,
    PostgresCreditDecisionRepository,
)
from src.infrastructure.persistence.redis_state_store import RedisEvaluationStateStore
from src.monitoring.health.health_checker import HealthChecker
from src.security.authentication.api_key_authenticator import ApiKeyAuthenticator
from src.security.authorization.rbac import Permission
from src.security.guards.prompt_injection_guard import PromptInjectionGuard
from src.security.rate_limiting.rate_limiter import SlidingWindowRateLimiter

logger = structlog.get_logger(__name__)


# ── Singletons ────────────────────────────────────────────────────────────────

_redis_client: Redis | None = None
_db_pool: asyncpg.Pool | None = None
_ai_services_http_client: httpx.AsyncClient | None = None
_reniec_http_client: httpx.AsyncClient | None = None
_injection_guard = PromptInjectionGuard()
_policy_service  = CreditPolicyService()


async def get_redis() -> Redis:
    global _redis_client
    if _redis_client is None:
        from config.settings import get_settings
        settings = get_settings()
        _redis_client = Redis.from_url(
            str(settings.redis.url),
            encoding="utf-8",
            decode_responses=False,
            socket_connect_timeout=5,
            socket_timeout=5,
        )
    return _redis_client


async def get_db_pool() -> asyncpg.Pool:
    global _db_pool
    if _db_pool is None:
        from config.settings import get_settings
        settings = get_settings()
        _db_pool = await asyncpg.create_pool(
            dsn=str(settings.database.url),
            min_size=5,
            max_size=20,
            command_timeout=30,
        )
    return _db_pool


async def get_ai_services_http_client() -> httpx.AsyncClient:
    global _ai_services_http_client
    if _ai_services_http_client is None:
        from config.settings import get_settings
        settings = get_settings()
        _ai_services_http_client = httpx.AsyncClient(timeout=settings.ai_services.timeout_secs)
    return _ai_services_http_client


async def get_reniec_http_client() -> httpx.AsyncClient:
    global _reniec_http_client
    if _reniec_http_client is None:
        _reniec_http_client = httpx.AsyncClient(timeout=10.0)
    return _reniec_http_client


async def get_reniec_client(
    http_client: httpx.AsyncClient = Depends(get_reniec_http_client),
) -> ReniecClient:
    from config.settings import get_settings
    settings = get_settings()
    return ReniecClient(
        http_client=http_client,
        base_url=settings.external.reniec_api_url,
        api_key=settings.external.reniec_api_key.get_secret_value(),
    )


async def get_authenticator(redis: Redis = Depends(get_redis)) -> ApiKeyAuthenticator:
    return ApiKeyAuthenticator(redis)


async def get_rate_limiter(redis: Redis = Depends(get_redis)) -> SlidingWindowRateLimiter:
    return SlidingWindowRateLimiter(redis)


def get_injection_guard() -> PromptInjectionGuard:
    return _injection_guard


async def get_health_checker(
    redis: Redis = Depends(get_redis),
    db: asyncpg.Pool = Depends(get_db_pool),
    ai_services_client: httpx.AsyncClient = Depends(get_ai_services_http_client),
) -> HealthChecker:
    from config.settings import get_settings
    settings = get_settings()
    return HealthChecker(
        version=settings.app.version,
        environment=settings.app.environment,
        redis_client=redis,
        db_pool=db,
        ai_services_client=ai_services_client,
        ai_services_url=settings.ai_services.url,
    )


# ── Repositorios ──────────────────────────────────────────────────────────────

async def get_application_repo(
    db: asyncpg.Pool = Depends(get_db_pool),
) -> PostgresCreditApplicationRepository:
    return PostgresCreditApplicationRepository(db)


async def get_decision_repo(
    db: asyncpg.Pool = Depends(get_db_pool),
) -> PostgresCreditDecisionRepository:
    return PostgresCreditDecisionRepository(db)


async def get_state_store(
    redis: Redis = Depends(get_redis),
) -> RedisEvaluationStateStore:
    return RedisEvaluationStateStore(redis)


async def get_event_publisher(
    redis: Redis = Depends(get_redis),
) -> RedisStreamEventPublisher:
    return RedisStreamEventPublisher(redis)


# ── Orquestador (HTTP hacia ai-services — ver docs/SERVICE_CONTRACTS.md) ──────

async def get_orchestrator(
    http_client: httpx.AsyncClient = Depends(get_ai_services_http_client),
) -> OrchestratorPort:
    from config.settings import get_settings
    from src.infrastructure.ai_services import AiServicesOrchestratorAdapter

    settings = get_settings()
    return AiServicesOrchestratorAdapter(
        http_client=http_client,
        base_url=settings.ai_services.url,
    )


# ── Use Cases ─────────────────────────────────────────────────────────────────

async def get_evaluate_use_case(
    app_repo   = Depends(get_application_repo),
    dec_repo   = Depends(get_decision_repo),
    state_store = Depends(get_state_store),
    orchestrator = Depends(get_orchestrator),
    publisher  = Depends(get_event_publisher),
) -> EvaluateCreditApplicationUseCase:
    from src.infrastructure.messaging.event_publisher import NullEventPublisher
    notification_service = NullEventPublisher()  # Reemplazar con NotificationAdapter real
    return EvaluateCreditApplicationUseCase(
        application_repo=app_repo,
        decision_repo=dec_repo,
        state_store=state_store,
        orchestrator=orchestrator,
        policy_service=_policy_service,
        event_publisher=publisher,
        notification_service=notification_service,
    )


async def get_retrieve_use_case(
    app_repo = Depends(get_application_repo),
    dec_repo = Depends(get_decision_repo),
) -> RetrieveCreditDecisionUseCase:
    return RetrieveCreditDecisionUseCase(app_repo, dec_repo)


# ── Autorización ─────────────────────────────────────────────────────────────

def require_permission(permission: Permission) -> Callable:
    async def _check(request: Request):
        identity = getattr(request.state, "identity", None)
        if not identity:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="No autenticado.")
        if not identity.can(permission):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Permiso requerido: '{permission.value}'.",
            )
    return _check
