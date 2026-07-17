"""
Health Checker — CLOUD BANK

Endpoints:
  GET /health/live   → Liveness: el proceso está vivo (para Kubernetes)
  GET /health/ready  → Readiness: puede recibir tráfico (para Kubernetes)
  GET /health/full   → Full: estado detallado de todos los componentes

El readiness falla si cualquier dependencia crítica está caída.
El liveness siempre responde 200 si el proceso está corriendo.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional

import structlog

logger = structlog.get_logger(__name__)


class HealthStatus(str, Enum):
    HEALTHY   = "healthy"
    DEGRADED  = "degraded"
    UNHEALTHY = "unhealthy"


@dataclass
class ComponentHealth:
    name: str
    status: HealthStatus
    latency_ms: Optional[float]
    details: dict[str, Any]
    is_critical: bool

    @property
    def is_healthy(self) -> bool:
        return self.status == HealthStatus.HEALTHY

    @property
    def is_failing(self) -> bool:
        return self.status == HealthStatus.UNHEALTHY


@dataclass
class SystemHealth:
    status: HealthStatus
    version: str
    environment: str
    timestamp: float
    components: list[ComponentHealth]
    total_latency_ms: float

    @property
    def is_ready(self) -> bool:
        """Listo para recibir tráfico si todos los componentes críticos están sanos."""
        critical = [c for c in self.components if c.is_critical]
        return all(c.is_healthy for c in critical)

    def to_dict(self) -> dict:
        return {
            "status":           self.status.value,
            "version":          self.version,
            "environment":      self.environment,
            "timestamp":        self.timestamp,
            "total_latency_ms": round(self.total_latency_ms, 2),
            "ready":            self.is_ready,
            "components": [
                {
                    "name":        c.name,
                    "status":      c.status.value,
                    "latency_ms":  round(c.latency_ms, 2) if c.latency_ms else None,
                    "is_critical": c.is_critical,
                    "details":     c.details,
                }
                for c in self.components
            ],
        }


class HealthChecker:
    """
    Verifica el estado de todos los componentes del sistema.
    Cada check se ejecuta en paralelo con timeout de 5s.
    """

    CHECK_TIMEOUT = 5.0

    def __init__(
        self,
        version: str,
        environment: str,
        redis_client=None,
        db_pool=None,
        ai_services_client=None,
        ai_services_url: str | None = None,
    ) -> None:
        self._version      = version
        self._environment  = environment
        self._redis        = redis_client
        self._db           = db_pool
        self._ai_services_client = ai_services_client
        self._ai_services_url    = ai_services_url

    async def liveness(self) -> dict:
        return {"status": "alive", "timestamp": time.time(), "version": self._version}

    async def readiness(self) -> SystemHealth:
        """Checks completos en paralelo."""
        start = time.monotonic()

        checks = await asyncio.gather(
            self._check_redis(),
            self._check_database(),
            self._check_ai_services(),
            self._check_self(),
            return_exceptions=True,
        )

        components = []
        for result in checks:
            if isinstance(result, Exception):
                components.append(ComponentHealth(
                    name="unknown",
                    status=HealthStatus.UNHEALTHY,
                    latency_ms=None,
                    details={"error": str(result)},
                    is_critical=False,
                ))
            else:
                components.append(result)

        critical_unhealthy = any(c.is_failing and c.is_critical for c in components)
        any_degraded = any(c.status == HealthStatus.DEGRADED for c in components)

        if critical_unhealthy:
            overall = HealthStatus.UNHEALTHY
        elif any_degraded:
            overall = HealthStatus.DEGRADED
        else:
            overall = HealthStatus.HEALTHY

        total_ms = (time.monotonic() - start) * 1000

        return SystemHealth(
            status=overall,
            version=self._version,
            environment=self._environment,
            timestamp=time.time(),
            components=components,
            total_latency_ms=total_ms,
        )

    async def _check_redis(self) -> ComponentHealth:
        if not self._redis:
            return ComponentHealth("redis", HealthStatus.UNHEALTHY, None, {"error": "not_configured"}, True)
        start = time.monotonic()
        try:
            async with asyncio.timeout(self.CHECK_TIMEOUT):
                await self._redis.ping()
            latency = (time.monotonic() - start) * 1000
            info = await self._redis.info("memory")
            return ComponentHealth(
                name="redis",
                status=HealthStatus.HEALTHY,
                latency_ms=latency,
                details={"used_memory_human": info.get("used_memory_human", "unknown")},
                is_critical=True,
            )
        except Exception as exc:
            return ComponentHealth("redis", HealthStatus.UNHEALTHY, None, {"error": str(exc)}, True)

    async def _check_database(self) -> ComponentHealth:
        if not self._db:
            return ComponentHealth("postgres", HealthStatus.UNHEALTHY, None, {"error": "not_configured"}, True)
        start = time.monotonic()
        try:
            async with asyncio.timeout(self.CHECK_TIMEOUT):
                async with self._db.acquire() as conn:
                    result = await conn.fetchval("SELECT 1")
                    assert result == 1
            latency = (time.monotonic() - start) * 1000
            return ComponentHealth(
                name="postgres",
                status=HealthStatus.HEALTHY,
                latency_ms=latency,
                details={"connection_pool_size": self._db.get_size()},
                is_critical=True,
            )
        except Exception as exc:
            return ComponentHealth("postgres", HealthStatus.UNHEALTHY, None, {"error": str(exc)}, True)

    async def _check_ai_services(self) -> ComponentHealth:
        """ai-services es una dependencia no crítica: si está caído, el backend
        sigue respondiendo status/decisión ya persistidos, solo /v1/credit/evaluate
        se degrada (ver AiServicesOrchestratorAdapter._degraded_result)."""
        if not self._ai_services_client or not self._ai_services_url:
            return ComponentHealth("ai_services", HealthStatus.DEGRADED, None, {"error": "not_configured"}, False)
        start = time.monotonic()
        try:
            async with asyncio.timeout(self.CHECK_TIMEOUT):
                response = await self._ai_services_client.get(f"{self._ai_services_url.rstrip('/')}/health")
                response.raise_for_status()
            latency = (time.monotonic() - start) * 1000
            return ComponentHealth("ai_services", HealthStatus.HEALTHY, latency, {}, False)
        except Exception as exc:
            return ComponentHealth("ai_services", HealthStatus.UNHEALTHY, None, {"error": str(exc)}, False)

    async def _check_self(self) -> ComponentHealth:
        import psutil
        try:
            process  = psutil.Process()
            cpu_pct  = process.cpu_percent(interval=0.1)
            mem_mb   = process.memory_info().rss / 1024 / 1024
            status   = HealthStatus.HEALTHY
            if mem_mb > 1024:
                status = HealthStatus.DEGRADED
            return ComponentHealth(
                name="self",
                status=status,
                latency_ms=None,
                details={"cpu_percent": cpu_pct, "memory_mb": round(mem_mb, 1)},
                is_critical=False,
            )
        except Exception as exc:
            return ComponentHealth("self", HealthStatus.DEGRADED, None, {"error": str(exc)}, False)
