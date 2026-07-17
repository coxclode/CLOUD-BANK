"""
Cliente HTTP hacia RENIEC (Perú) vía Decolecta (https://decolecta.com).

Único punto del backend que sabe que Decolecta existe. Solo devuelve nombres
y apellidos asociados al DNI — Decolecta no expone fecha de nacimiento ni
otros datos personales, por lo que esos campos siguen siendo ingresados
manualmente por el solicitante.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx
import structlog

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class ReniecIdentity:
    document_number: str
    first_name: str
    first_last_name: str
    second_last_name: str
    full_name: str


class ReniecError(Exception):
    """Error genérico de comunicación con Decolecta/RENIEC."""


class ReniecNotFoundError(ReniecError):
    """El DNI no existe en RENIEC."""


class ReniecClient:
    def __init__(self, http_client: httpx.AsyncClient, base_url: str, api_key: str) -> None:
        self._client = http_client
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key

    async def lookup_dni(self, dni: str) -> ReniecIdentity:
        try:
            response = await self._client.get(
                self._base_url,
                params={"numero": dni},
                headers={"Authorization": f"Bearer {self._api_key}"},
            )
        except httpx.HTTPError as exc:
            logger.error("reniec_client.request_failed", error=str(exc))
            raise ReniecError("No se pudo contactar al servicio de RENIEC.") from exc

        if response.status_code == 404:
            raise ReniecNotFoundError(f"DNI '{dni}' no encontrado en RENIEC.")
        if response.status_code != 200:
            logger.error(
                "reniec_client.unexpected_status",
                status_code=response.status_code,
                body=response.text[:200],
            )
            raise ReniecError("El servicio de RENIEC devolvió un error inesperado.")

        data = response.json()
        return ReniecIdentity(
            document_number=data.get("document_number", dni),
            first_name=data.get("first_name", ""),
            first_last_name=data.get("first_last_name", ""),
            second_last_name=data.get("second_last_name", ""),
            full_name=data.get("full_name", ""),
        )
