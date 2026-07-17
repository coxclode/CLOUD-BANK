"""
ai-services — Punto de entrada FastAPI

Servicio interno, sin exposición pública directa: solo el backend lo llama.
No toca la base de datos ni conoce entidades de dominio del backend — recibe
un dict de datos de solicitud, ejecuta el grafo LangGraph de 4 Deep Agents y
devuelve los resultados normalizados. Ver docs/SERVICE_CONTRACTS.md.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from functools import lru_cache

import structlog
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from src.api.schemas import PipelineEvaluateRequest, PipelineEvaluateResponse
from src.core.config import get_settings
from src.observability.logger import configure_logging
from src.observability.tracer import configure_tracer
from src.orchestrator.graph.credit_evaluation_graph import LangGraphOrchestrator
from src.orchestrator.nodes.agent_execution_node import (
    ActuarialExecutionNode,
    ApprovalExecutionNode,
    CreditExecutionNode,
    FraudExecutionNode,
)
from src.orchestrator.nodes.finalization_node import (
    AuditFinalizationNode,
    ErrorHandlerNode,
    HumanEscalationNode,
)
from src.orchestrator.nodes.input_validation_node import InputValidationNode

logger = structlog.get_logger(__name__)


@lru_cache(maxsize=1)
def get_orchestrator() -> LangGraphOrchestrator:
    """
    Composition root de ai-services. Construye el pipeline de 4 Deep Agents
    exactamente como antes lo hacía backend/src/api/dependencies.py, ahora
    detrás de la frontera HTTP de este servicio.
    """
    from src.agents.adapters import (
        ActuarialDeepAgentAdapter,
        ApprovalDeepAgentAdapter,
        CreditDeepAgentAdapter,
        FraudDeepAgentAdapter,
    )

    return LangGraphOrchestrator(
        fraud_node=FraudExecutionNode(FraudDeepAgentAdapter()),
        credit_node=CreditExecutionNode(CreditDeepAgentAdapter()),
        actuarial_node=ActuarialExecutionNode(ActuarialDeepAgentAdapter()),
        approval_node=ApprovalExecutionNode(ApprovalDeepAgentAdapter()),
        validation_node=InputValidationNode(),
        audit_node=AuditFinalizationNode(),
        error_node=ErrorHandlerNode(),
        escalation_node=HumanEscalationNode(),
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_logging(log_level=settings.observability.log_level, json_format=True)
    configure_tracer(
        service_name="cloudbank-ai-services",
        environment=settings.observability.environment,
        otlp_endpoint=settings.observability.otel_endpoint,
    )
    logger.info("ai_services.starting")
    get_orchestrator()  # fuerza la construcción/health de los agentes al boot
    yield
    logger.info("ai_services.shutdown")


def create_app() -> FastAPI:
    app = FastAPI(
        title="CLOUD BANK — ai-services",
        description=(
            "Motor de IA interno: LangGraph + 4 Deep Agents + LLM. "
            "Solo accesible desde el backend, nunca desde el frontend."
        ),
        version="1.0.0",
        lifespan=lifespan,
    )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.error("ai_services.unhandled_error", error=str(exc), path=request.url.path)
        return JSONResponse(status_code=500, content={"error": "internal_error", "detail": str(exc)})

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok", "service": "ai-services"}

    @app.post("/v1/pipeline/evaluate", response_model=PipelineEvaluateResponse)
    async def evaluate_pipeline(body: PipelineEvaluateRequest) -> PipelineEvaluateResponse:
        pipeline_id = body.pipeline_id or str(uuid.uuid4())
        orchestrator = get_orchestrator()
        results = await orchestrator.run_pipeline(
            application_data=body.application_data,
            application_id=body.application_id,
            pipeline_id=pipeline_id,
        )
        return PipelineEvaluateResponse.from_pipeline_results(results)

    return app


app = create_app()
