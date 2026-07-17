"""
DTOs de salida — Verificación de Identidad (RENIEC).
"""

from __future__ import annotations

from pydantic import BaseModel


class DniLookupResponseDTO(BaseModel):
    document_number: str
    first_name: str
    first_last_name: str
    second_last_name: str
    full_name: str
