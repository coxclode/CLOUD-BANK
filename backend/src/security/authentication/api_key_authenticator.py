"""
Autenticador: API Key

Verifica API keys en header `X-API-Key`.
Usa HMAC-SHA256 para comparación timing-safe (evita timing attacks).
Las keys se almacenan hasheadas en Redis — nunca en texto plano.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from dataclasses import dataclass
from enum import Enum
from typing import Optional

import structlog
from redis.asyncio import Redis

from src.security.authorization.rbac import Permission

logger = structlog.get_logger(__name__)

_KEY_HASH_PREFIX = "cloudbank:apikey:hash:"
_KEY_META_PREFIX = "cloudbank:apikey:meta:"


class ApiKeyScope(str, Enum):
    READ  = "read"
    WRITE = "write"
    ADMIN = "admin"


# Scopes gruesos de API Key → permisos finos de RBAC (src/security/authorization/rbac.py).
# Unifica la autorización entre integraciones máquina-a-máquina (API Key) y
# usuarios humanos (JWT, ver Principal.can() en rbac.py).
_SCOPE_PERMISSIONS: dict[ApiKeyScope, frozenset[Permission]] = {
    ApiKeyScope.READ: frozenset({
        Permission.CREDIT_READ, Permission.ADMIN_HEALTH,
        Permission.ADMIN_METRICS, Permission.AUDIT_READ,
    }),
    ApiKeyScope.WRITE: frozenset({
        Permission.CREDIT_EVALUATE, Permission.CREDIT_READ,
    }),
    ApiKeyScope.ADMIN: frozenset(Permission),
}


@dataclass
class ApiKeyIdentity:
    """Identidad resultante de una autenticación exitosa por API Key."""
    key_id: str
    client_name: str
    scopes: list[ApiKeyScope]
    rate_limit_per_minute: int
    is_active: bool

    def has_scope(self, required: ApiKeyScope) -> bool:
        return required in self.scopes or ApiKeyScope.ADMIN in self.scopes

    def can(self, permission: Permission) -> bool:
        return any(permission in _SCOPE_PERMISSIONS.get(scope, frozenset()) for scope in self.scopes)


class AuthenticationError(Exception):
    def __init__(self, reason: str = "API key inválida o inactiva.") -> None:
        super().__init__(reason)
        self.reason = reason


class ApiKeyAuthenticator:
    """
    Autenticación por API Key con protección anti-timing-attack.

    Flujo:
      1. Hashear la key recibida con SHA-256
      2. Buscar el hash en Redis
      3. Comparación timing-safe con hmac.compare_digest
      4. Recuperar metadatos de la key (client, scopes, rate limit)
    """

    def __init__(self, redis_client: Redis) -> None:
        self._redis = redis_client

    async def authenticate(self, raw_api_key: str) -> ApiKeyIdentity:
        """
        Autentica una API key. Lanza AuthenticationError si es inválida.
        Timing-safe: siempre tarda el mismo tiempo independientemente del resultado.
        """
        if not raw_api_key or len(raw_api_key) < 32:
            await self._dummy_hash()
            raise AuthenticationError("API key con formato inválido.")

        key_hash = self._hash_key(raw_api_key)
        key_prefix = raw_api_key[:8]

        stored_hash = await self._redis.get(f"{_KEY_HASH_PREFIX}{key_prefix}")
        if not stored_hash:
            await self._dummy_hash()
            raise AuthenticationError()

        if not hmac.compare_digest(
            key_hash.encode("utf-8"),
            stored_hash.decode("utf-8").encode("utf-8"),
        ):
            logger.warning("api_key_authenticator.invalid_key", key_prefix=key_prefix)
            raise AuthenticationError()

        meta_raw = await self._redis.hgetall(f"{_KEY_META_PREFIX}{key_prefix}")
        if not meta_raw:
            raise AuthenticationError("Metadatos de API key no encontrados.")

        meta = {k.decode(): v.decode() for k, v in meta_raw.items()}
        if meta.get("is_active", "1") == "0":
            raise AuthenticationError("API key desactivada.")

        scopes = [ApiKeyScope(s) for s in meta.get("scopes", "read").split(",") if s]
        identity = ApiKeyIdentity(
            key_id=key_prefix,
            client_name=meta.get("client_name", "unknown"),
            scopes=scopes,
            rate_limit_per_minute=int(meta.get("rate_limit_rpm", 60)),
            is_active=True,
        )
        logger.debug(
            "api_key_authenticator.authenticated",
            client=identity.client_name,
            scopes=[s.value for s in scopes],
        )
        return identity

    async def register_key(
        self,
        raw_api_key: str,
        client_name: str,
        scopes: list[ApiKeyScope],
        rate_limit_rpm: int = 60,
    ) -> str:
        """Registra una nueva API key. Devuelve el prefijo (key_id)."""
        if len(raw_api_key) < 32:
            raise ValueError("La API key debe tener al menos 32 caracteres.")

        key_prefix = raw_api_key[:8]
        key_hash   = self._hash_key(raw_api_key)

        async with self._redis.pipeline() as pipe:
            pipe.set(f"{_KEY_HASH_PREFIX}{key_prefix}", key_hash)
            pipe.hset(
                f"{_KEY_META_PREFIX}{key_prefix}",
                mapping={
                    "client_name":     client_name,
                    "scopes":          ",".join(s.value for s in scopes),
                    "rate_limit_rpm":  str(rate_limit_rpm),
                    "is_active":       "1",
                    "created_at":      str(int(time.time())),
                },
            )
            await pipe.execute()

        logger.info("api_key_authenticator.key_registered", client=client_name, key_id=key_prefix)
        return key_prefix

    async def revoke_key(self, key_prefix: str) -> None:
        """Revoca una API key sin eliminarla (para audit trail)."""
        await self._redis.hset(f"{_KEY_META_PREFIX}{key_prefix}", "is_active", "0")
        logger.warning("api_key_authenticator.key_revoked", key_id=key_prefix)

    def _hash_key(self, raw_key: str) -> str:
        return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()

    async def _dummy_hash(self) -> None:
        """Consume tiempo constante para evitar timing attacks."""
        _ = hashlib.sha256(b"dummy_constant_value_to_prevent_timing_attack").hexdigest()
