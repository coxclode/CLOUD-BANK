"""
Fixtures globales de pytest para CLOUD BANK.

Jerarquía de fixtures:
  settings      → override de config en tests
  fake_redis    → fakeredis en memoria
  db_pool       → PostgreSQL de prueba (integration)
  app_client    → TestClient HTTP con todas las dependencias
"""

from __future__ import annotations

import asyncio
from datetime import date
from typing import AsyncGenerator, Generator
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from fakeredis.aioredis import FakeRedis
from fastapi.testclient import TestClient
from httpx import AsyncClient, ASGITransport

from config.settings import Settings, get_settings


# ── Event loop compartido para toda la sesión ─────────────────────────────────

@pytest.fixture(scope="session")
def event_loop():
    """Loop de asyncio compartido en toda la sesión de tests."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


# ── Override de settings ──────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def test_settings() -> Settings:
    """Configuración con valores seguros para tests."""
    from config.settings import (
        AppSettings, AIServicesSettings, RedisSettings,
        DatabaseSettings, SecuritySettings, AgentSettings,
        ExternalServicesSettings, ObservabilitySettings,
    )
    settings = Settings(
        app=AppSettings(environment="development", log_level="DEBUG"),
        ai_services=AIServicesSettings(url="http://localhost:8100"),
        redis=RedisSettings(url="redis://localhost:6379/15"),
        database=DatabaseSettings(url="postgresql://test:test@localhost:5432/cloudbank_test"),
        security=SecuritySettings(
            encryption_key="test-key-32-chars-exactly!!!!",
            vault_token="test-token",
        ),
        agents=AgentSettings(timeout_seconds=5.0, max_retries=1),
    )
    return settings


# ── Redis en memoria (sin servidor real) ──────────────────────────────────────

@pytest_asyncio.fixture
async def fake_redis() -> AsyncGenerator[FakeRedis, None]:
    """FakeRedis asíncrono — no requiere servidor Redis."""
    redis = FakeRedis(decode_responses=False)
    yield redis
    await redis.flushall()
    await redis.aclose()


# ── FastAPI test client ───────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def app(test_settings, fake_redis):
    """
    Aplicación FastAPI con dependencias sustituidas.
    "test-api-key" (ver BASE_HEADERS en tests/e2e) se registra en FakeRedis con
    scope ADMIN, para ejercitar el flujo real de AuthMiddleware sin mockearlo.
    """
    get_settings.cache_clear()

    from src.api.app import create_app
    import src.api.dependencies as deps
    from src.security.authentication.api_key_authenticator import ApiKeyAuthenticator, ApiKeyScope

    _app = create_app(test_settings)

    # Sustituir singletons
    deps._redis_client = fake_redis
    _app.dependency_overrides[deps.get_redis] = lambda: fake_redis

    await ApiKeyAuthenticator(fake_redis).register_key(
        raw_api_key="test-api-key-0123456789abcdef0123456789",
        client_name="test-client",
        scopes=[ApiKeyScope.ADMIN],
        rate_limit_rpm=1000,
    )

    return _app


@pytest.fixture
def client(app) -> Generator[TestClient, None, None]:
    """TestClient síncrono."""
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


@pytest_asyncio.fixture
async def async_client(app) -> AsyncGenerator[AsyncClient, None]:
    """AsyncClient para tests async."""
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as c:
        yield c


# ── Mocks de repositorios ─────────────────────────────────────────────────────

@pytest.fixture
def mock_application_repo() -> AsyncMock:
    repo = AsyncMock()
    repo.find_by_id.return_value = None
    repo.save.return_value = None
    repo.count_active_by_applicant.return_value = 0
    return repo


@pytest.fixture
def mock_decision_repo() -> AsyncMock:
    repo = AsyncMock()
    repo.find_by_application_id.return_value = None
    repo.save.return_value = None
    return repo


@pytest.fixture
def mock_orchestrator() -> AsyncMock:
    """Mock del orquestador que devuelve resultados genéricos."""
    from src.application.ports.agent_port import AgentResult, AgentOutcome
    orchestrator = AsyncMock()
    orchestrator.run_evaluation_pipeline.return_value = {
        "fraud":     AgentResult("FraudDeepAgent",     AgentOutcome.APPROVED, 0.9, 0.85, 0.1, {"fraud_score": 0.1}, [], 120.0),
        "credit":    AgentResult("CreditDeepAgent",    AgentOutcome.APPROVED, 0.8, 0.80, 0.15, {"probability_of_default": 0.15}, [], 180.0),
        "actuarial": AgentResult("ActuarialDeepAgent", AgentOutcome.APPROVED, 0.85, 0.82, 0.12, {"loss_given_default": 0.12, "suggested_interest_rate": 0.12, "maximum_approved_amount": 15000.0}, [], 160.0),
        "approval":  AgentResult("ApprovalDeepAgent",  AgentOutcome.APPROVED, 0.9, 0.88, 0.1, {"decision": "APPROVED", "approved_amount": 15000.0, "interest_rate": 0.12, "term_months": 36, "monthly_installment": 498.21, "gdpr_explanation": "Solicitud aprobada según criterios de riesgo."}, [], 90.0),
    }
    return orchestrator


@pytest.fixture
def mock_event_publisher() -> AsyncMock:
    publisher = AsyncMock()
    publisher.publish.return_value = None
    return publisher
