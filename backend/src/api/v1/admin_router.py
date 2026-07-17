"""
Router v1: Administración y Operaciones

GET /v1/admin/health/live   → Liveness (Kubernetes)
GET /v1/admin/health/ready  → Readiness (Kubernetes)
GET /v1/admin/health        → Full health check
GET /v1/admin/metrics       → Prometheus metrics
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response
from fastapi.responses import JSONResponse, PlainTextResponse

from src.api.dependencies import get_health_checker, require_permission
from src.monitoring.health.health_checker import HealthChecker, HealthStatus
from src.monitoring.metrics import get_metrics_output, get_metrics_content_type
from src.security.authorization.rbac import Permission

router = APIRouter(prefix="/v1/admin", tags=["Admin"])


@router.get("/health/live", include_in_schema=False)
async def liveness(checker: HealthChecker = Depends(get_health_checker)) -> dict:
    return await checker.liveness()


@router.get("/health/ready", include_in_schema=False)
async def readiness(checker: HealthChecker = Depends(get_health_checker)):
    health = await checker.readiness()
    status_code = 200 if health.is_ready else 503
    return JSONResponse(content=health.to_dict(), status_code=status_code)


@router.get("/health", summary="Estado detallado del sistema")
async def full_health(
    checker: HealthChecker = Depends(get_health_checker),
    _: None = Depends(require_permission(Permission.ADMIN_HEALTH)),
):
    health = await checker.readiness()
    status_code = 200 if health.status == HealthStatus.HEALTHY else (
        207 if health.status == HealthStatus.DEGRADED else 503
    )
    return JSONResponse(content=health.to_dict(), status_code=status_code)


@router.get("/metrics", include_in_schema=False)
async def metrics() -> Response:
    output = get_metrics_output()
    return Response(content=output, media_type=get_metrics_content_type())
