"""
Puerto de salida: gestión de secretos.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional


class SecretManagerPort(ABC):
    """Abstrae el acceso al gestor de secretos (Vault, AWS Secrets Manager, etc.)."""

    @abstractmethod
    async def get_secret(self, secret_name: str) -> str:
        """Recupera un secreto por nombre. Lanza SecretNotFoundError si no existe."""

    @abstractmethod
    async def get_api_key(self, service_name: str) -> str:
        """Recupera la API key de un servicio externo."""

    @abstractmethod
    async def rotate_secret(self, secret_name: str) -> str:
        """Rota un secreto y devuelve el nuevo valor."""

    @abstractmethod
    async def list_secrets(self) -> list[str]:
        """Lista los nombres de todos los secretos disponibles."""


class SecretNotFoundError(Exception):
    def __init__(self, secret_name: str) -> None:
        super().__init__(f"Secreto no encontrado: '{secret_name}'")
        self.secret_name = secret_name
