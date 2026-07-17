"""
Autenticador JWT — sesión de oficiales/analistas en el frontend.

Complementa a ApiKeyAuthenticator (integraciones máquina-a-máquina). El login
es deliberadamente mínimo (credenciales de demo vía variables de entorno) —
ver docs/FREE_TIER_ARCHITECTURE.md §11 para la migración a un IdP real (Clerk/OAuth2).
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

from jose import JWTError, jwt
from passlib.context import CryptContext

from src.security.authorization.rbac import Principal, Role

_ALGORITHM = "HS256"
_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


class AuthenticationError(Exception):
    def __init__(self, reason: str = "Credenciales inválidas.") -> None:
        super().__init__(reason)
        self.reason = reason


def hash_password(plain_password: str) -> str:
    return _pwd_context.hash(plain_password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return _pwd_context.verify(plain_password, hashed_password)


def create_access_token(
    subject: str,
    role: Role,
    secret_key: str,
    expires_minutes: int = 60,
) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": subject,
        "role": role.value,
        "iat": int(time.time()),
        "exp": now + timedelta(minutes=expires_minutes),
    }
    return jwt.encode(payload, secret_key, algorithm=_ALGORITHM)


def decode_access_token(token: str, secret_key: str) -> Principal:
    try:
        payload = jwt.decode(token, secret_key, algorithms=[_ALGORITHM])
    except JWTError as exc:
        raise AuthenticationError("Token inválido o expirado.") from exc

    subject = payload.get("sub")
    role_raw = payload.get("role")
    if not subject or not role_raw:
        raise AuthenticationError("Token con formato inesperado.")

    try:
        role = Role(role_raw)
    except ValueError as exc:
        raise AuthenticationError(f"Rol desconocido en el token: '{role_raw}'.") from exc

    return Principal(subject=subject, roles=(role,), metadata={})
