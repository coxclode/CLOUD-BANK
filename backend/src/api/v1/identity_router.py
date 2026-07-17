"""
Router v1: Verificación de Identidad

GET /v1/identity/dni/{numero} → Consulta RENIEC (vía Decolecta) por DNI.
"""

from __future__ import annotations

import re

import structlog
from fastapi import APIRouter, Depends, HTTPException, status

from src.api.dependencies import get_reniec_client, require_permission
from src.application.dto.identity_dto import DniLookupResponseDTO
from src.infrastructure.identity.reniec_client import ReniecClient, ReniecError, ReniecNotFoundError
from src.security.authorization.rbac import Permission

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/v1/identity", tags=["Identity Verification"])

_DNI_RE = re.compile(r"^\d{8}$")


@router.get(
    "/dni/{numero}",
    response_model=DniLookupResponseDTO,
    summary="Consultar RENIEC por DNI",
    description=(
        "Valida un DNI peruano contra RENIEC (vía Decolecta) y devuelve nombres "
        "y apellidos. No incluye fecha de nacimiento — RENIEC/Decolecta no la expone."
    ),
    responses={
        400: {"description": "Formato de DNI inválido"},
        404: {"description": "DNI no encontrado en RENIEC"},
        502: {"description": "Servicio de RENIEC no disponible"},
    },
)
async def lookup_dni(
    numero: str,
    client: ReniecClient = Depends(get_reniec_client),
    _: None = Depends(require_permission(Permission.CREDIT_EVALUATE)),
) -> DniLookupResponseDTO:
    if not _DNI_RE.match(numero):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="El DNI debe tener exactamente 8 dígitos.",
        )

    try:
        identity = await client.lookup_dni(numero)
    except ReniecNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except ReniecError as exc:
        logger.error("identity_router.lookup_dni.failed", error=str(exc))
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))

    return DniLookupResponseDTO(
        document_number=identity.document_number,
        first_name=identity.first_name,
        first_last_name=identity.first_last_name,
        second_last_name=identity.second_last_name,
        full_name=identity.full_name,
    )
