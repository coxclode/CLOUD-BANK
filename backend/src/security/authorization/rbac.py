"""
Autorización: Role-Based Access Control (RBAC)

Define roles, permisos y políticas de acceso a los recursos de la API.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from functools import lru_cache
from typing import FrozenSet


class Permission(str, Enum):
    # Evaluación crediticia
    CREDIT_EVALUATE    = "credit:evaluate"
    CREDIT_READ        = "credit:read"
    CREDIT_ADMIN       = "credit:admin"

    # Administración
    ADMIN_KEYS         = "admin:keys"
    ADMIN_METRICS      = "admin:metrics"
    ADMIN_HEALTH       = "admin:health"

    # Auditoría
    AUDIT_READ         = "audit:read"
    AUDIT_EXPORT       = "audit:export"


class Role(str, Enum):
    GUEST          = "guest"
    CLIENT         = "client"
    ANALYST        = "analyst"
    RISK_OFFICER   = "risk_officer"
    AUDITOR        = "auditor"
    ADMIN          = "admin"


_ROLE_PERMISSIONS: dict[Role, FrozenSet[Permission]] = {
    Role.GUEST: frozenset(),
    Role.CLIENT: frozenset({
        Permission.CREDIT_EVALUATE,
        Permission.CREDIT_READ,
    }),
    Role.ANALYST: frozenset({
        Permission.CREDIT_EVALUATE,
        Permission.CREDIT_READ,
        Permission.ADMIN_METRICS,
        Permission.ADMIN_HEALTH,
    }),
    Role.RISK_OFFICER: frozenset({
        Permission.CREDIT_EVALUATE,
        Permission.CREDIT_READ,
        Permission.CREDIT_ADMIN,
        Permission.ADMIN_METRICS,
        Permission.AUDIT_READ,
    }),
    Role.AUDITOR: frozenset({
        Permission.CREDIT_READ,
        Permission.AUDIT_READ,
        Permission.AUDIT_EXPORT,
        Permission.ADMIN_METRICS,
    }),
    Role.ADMIN: frozenset(Permission),
}


class AuthorizationError(Exception):
    def __init__(self, required: Permission, role: Role) -> None:
        super().__init__(
            f"Permiso '{required.value}' requerido. Rol actual: '{role.value}'."
        )
        self.required  = required
        self.role      = role


@dataclass(frozen=True)
class Principal:
    """Identidad autenticada con roles asignados."""
    subject: str
    roles: tuple[Role, ...]
    metadata: dict = ()

    @property
    def permissions(self) -> FrozenSet[Permission]:
        result = frozenset()
        for role in self.roles:
            result = result | _ROLE_PERMISSIONS.get(role, frozenset())
        return result

    def can(self, permission: Permission) -> bool:
        return permission in self.permissions

    # Compatibilidad de forma con ApiKeyIdentity (src/security/authentication/
    # api_key_authenticator.py) — permite que routers/middlewares traten ambas
    # identidades (API Key máquina-a-máquina, JWT de sesión humana) de manera uniforme.
    @property
    def client_name(self) -> str:
        return self.subject

    @property
    def key_id(self) -> str:
        return self.subject

    def require(self, permission: Permission) -> None:
        """Lanza AuthorizationError si no tiene el permiso."""
        if not self.can(permission):
            primary_role = self.roles[0] if self.roles else Role.GUEST
            raise AuthorizationError(required=permission, role=primary_role)


@lru_cache(maxsize=128)
def get_role_permissions(role: Role) -> FrozenSet[Permission]:
    return _ROLE_PERMISSIONS.get(role, frozenset())
