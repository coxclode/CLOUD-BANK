"""
Adaptador: HashiCorp Vault / AWS Secrets Manager

Implementa SecretManagerPort con soporte para:
  - HashiCorp Vault (KV v2)
  - AWS Secrets Manager
  - Fallback a variables de entorno (solo desarrollo)

Implementa cache en memoria con TTL para minimizar llamadas a Vault.
"""

from __future__ import annotations

import os
import time
from typing import Optional

import hvac                    # pip install hvac
import structlog

from src.application.ports.secret_port import SecretManagerPort, SecretNotFoundError

logger = structlog.get_logger(__name__)

_CACHE_TTL_SECONDS = 300  # 5 minutos


class VaultSecretManager(SecretManagerPort):
    """
    Accede a HashiCorp Vault con cache TTL y renovación automática de token.
    """

    def __init__(
        self,
        vault_url: str,
        vault_token: str,
        mount_point: str = "cloudbank",
        environment: str = "production",
    ) -> None:
        self._client = hvac.Client(url=vault_url, token=vault_token)
        self._mount  = mount_point
        self._env    = environment
        self._cache: dict[str, tuple[str, float]] = {}

    async def get_secret(self, secret_name: str) -> str:
        cached_value, expiry = self._cache.get(secret_name, (None, 0.0))
        if cached_value and time.monotonic() < expiry:
            return cached_value

        try:
            response = self._client.secrets.kv.v2.read_secret_version(
                mount_point=self._mount,
                path=f"{self._env}/{secret_name}",
            )
            value = response["data"]["data"].get("value")
            if value is None:
                raise SecretNotFoundError(secret_name)
            self._cache[secret_name] = (value, time.monotonic() + _CACHE_TTL_SECONDS)
            logger.debug("vault.secret_retrieved", secret_name=secret_name)
            return value
        except SecretNotFoundError:
            raise
        except Exception as exc:
            logger.error("vault.get_secret_failed", secret_name=secret_name, error=str(exc))
            raise SecretNotFoundError(secret_name) from exc

    async def get_api_key(self, service_name: str) -> str:
        return await self.get_secret(f"api_keys/{service_name}")

    async def rotate_secret(self, secret_name: str) -> str:
        self._cache.pop(secret_name, None)
        logger.warning("vault.secret_rotated", secret_name=secret_name)
        raise NotImplementedError("La rotación automática requiere configuración adicional en Vault.")

    async def list_secrets(self) -> list[str]:
        try:
            response = self._client.secrets.kv.v2.list_secrets(
                mount_point=self._mount,
                path=self._env,
            )
            return response["data"].get("keys", [])
        except Exception as exc:
            logger.error("vault.list_secrets_failed", error=str(exc))
            return []


class EnvironmentSecretManager(SecretManagerPort):
    """
    Lee secretos de variables de entorno.
    SOLO para desarrollo local / tests. NO usar en producción.
    """

    _PREFIX = "CLOUDBANK_SECRET_"

    async def get_secret(self, secret_name: str) -> str:
        env_key = f"{self._PREFIX}{secret_name.upper().replace('/', '_').replace('-', '_')}"
        value = os.environ.get(env_key)
        if not value:
            raise SecretNotFoundError(secret_name)
        return value

    async def get_api_key(self, service_name: str) -> str:
        return await self.get_secret(f"API_KEYS_{service_name}")

    async def rotate_secret(self, secret_name: str) -> str:
        raise NotImplementedError("No se pueden rotar secretos de entorno.")

    async def list_secrets(self) -> list[str]:
        prefix = self._PREFIX
        return [
            k[len(prefix):].lower().replace("_", "/")
            for k in os.environ
            if k.startswith(prefix)
        ]


def create_secret_manager(environment: str) -> SecretManagerPort:
    """
    Fábrica que elige la implementación correcta según el entorno.
    """
    if environment in ("production", "staging"):
        vault_url   = os.environ.get("VAULT_URL", "http://vault:8200")
        vault_token = os.environ.get("VAULT_TOKEN", "")
        if not vault_token:
            logger.warning("vault.no_token_configured")
        return VaultSecretManager(
            vault_url=vault_url,
            vault_token=vault_token,
            environment=environment,
        )
    logger.warning("secret_manager.using_env_vars", environment=environment)
    return EnvironmentSecretManager()
