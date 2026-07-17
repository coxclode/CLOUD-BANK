"""
Router v1: Evaluación Crediticia

POST /v1/credit/evaluate   → Evaluar nueva solicitud
GET  /v1/credit/{id}       → Estado de la solicitud
GET  /v1/credit/{id}/decision → Decisión de la solicitud
"""

from __future__ import annotations

import uuid
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse

from src.api.dependencies import (
    get_evaluate_use_case,
    get_retrieve_use_case,
    get_injection_guard,
    require_permission,
)
from src.application.dto.credit_application_dto import EvaluateCreditApplicationDTO
from src.application.dto.credit_decision_dto import (
    CreditApplicationStatusDTO,
    CreditDecisionResponseDTO,
)
from src.application.use_cases.evaluate_credit_application import (
    ApplicationLimitExceededError,
    CreditEvaluationError,
    EvaluateCreditApplicationUseCase,
)
from src.application.use_cases.retrieve_credit_decision import (
    ApplicationNotFoundError,
    DecisionNotFoundError,
    RetrieveCreditDecisionUseCase,
)
from src.security.authorization.rbac import Permission
from src.security.guards.prompt_injection_guard import PromptInjectionError, PromptInjectionGuard

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/v1/credit", tags=["Credit Evaluation"])


@router.post(
    "/evaluate",
    response_model=CreditDecisionResponseDTO,
    status_code=status.HTTP_200_OK,
    summary="Evaluar solicitud de crédito personal",
    description=(
        "Ejecuta el pipeline completo de evaluación crediticia con 4 Deep Agents. "
        "Devuelve la decisión APROBADO / RECHAZADO / MÁS DOCUMENTOS / ESCALADO."
    ),
    responses={
        400: {"description": "Datos de la solicitud inválidos"},
        401: {"description": "No autenticado"},
        403: {"description": "Sin autorización"},
        422: {"description": "Error de validación"},
        429: {"description": "Rate limit excedido"},
        500: {"description": "Error interno del pipeline"},
    },
)
async def evaluate_credit(
    request: Request,
    dto: EvaluateCreditApplicationDTO,
    use_case: EvaluateCreditApplicationUseCase = Depends(get_evaluate_use_case),
    guard: PromptInjectionGuard = Depends(get_injection_guard),
    _: None = Depends(require_permission(Permission.CREDIT_EVALUATE)),
) -> CreditDecisionResponseDTO:
    identity = getattr(request.state, "identity", None)
    log = logger.bind(
        request_id=getattr(request.state, "request_id", ""),
        client=identity.client_name if identity else "unknown",
        channel=dto.credit_request.channel,
    )
    log.info("credit_router.evaluate.started")

    # Guard: prompt injection en datos del usuario
    try:
        guard.scan_and_raise(dto.applicant.model_dump())
        guard.scan_and_raise(dto.credit_request.model_dump())
    except PromptInjectionError as exc:
        log.warning("credit_router.injection_detected", threats=[d.threat_type.value for d in exc.detections])
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Los datos de la solicitud contienen contenido no permitido.",
        )

    try:
        result = await use_case.execute(dto, requesting_user_id=identity.key_id if identity else "anonymous")
        log.info(
            "credit_router.evaluate.completed",
            outcome=result.outcome,
            confidence=result.confidence,
        )
        return result
    except ApplicationLimitExceededError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    except CreditEvaluationError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))
    except Exception as exc:
        log.error("credit_router.evaluate.error", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error interno durante la evaluación. Contacte soporte técnico.",
        )


@router.get(
    "/{application_id}",
    response_model=CreditApplicationStatusDTO,
    summary="Estado de una solicitud de crédito",
)
async def get_application_status(
    application_id: uuid.UUID,
    use_case: RetrieveCreditDecisionUseCase = Depends(get_retrieve_use_case),
    _: None = Depends(require_permission(Permission.CREDIT_READ)),
) -> CreditApplicationStatusDTO:
    try:
        return await use_case.get_status(application_id)
    except ApplicationNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))


@router.get(
    "/{application_id}/decision",
    response_model=CreditDecisionResponseDTO,
    summary="Decisión final de una solicitud",
)
async def get_credit_decision(
    application_id: uuid.UUID,
    use_case: RetrieveCreditDecisionUseCase = Depends(get_retrieve_use_case),
    _: None = Depends(require_permission(Permission.CREDIT_READ)),
) -> CreditDecisionResponseDTO:
    try:
        return await use_case.get_decision(application_id)
    except ApplicationNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except DecisionNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_202_ACCEPTED, detail=str(exc))
