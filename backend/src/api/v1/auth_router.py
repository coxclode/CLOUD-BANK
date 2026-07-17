"""
Router v1: Autenticación de sesión (JWT)

POST /v1/auth/login → login de oficiales para el frontend Next.js.

Auth de demostración para el scaffold académico: un único usuario "oficial"
definido por variables de entorno (CLOUDBANK_SECURITY_DEMO_OFFICER_*), sin
tabla de usuarios ni IdP real. Ver docs/FREE_TIER_ARCHITECTURE.md §11.
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from src.security.authentication.jwt_authenticator import create_access_token, verify_password
from src.security.authorization.rbac import Role

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/v1/auth", tags=["Authentication"])


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in_minutes: int


@router.post("/login", response_model=LoginResponse, summary="Login de oficial (demo)")
async def login(body: LoginRequest) -> LoginResponse:
    from config.settings import get_settings
    settings = get_settings()

    expected_username = settings.security.demo_officer_username
    password_hash = settings.security.demo_officer_password_hash.get_secret_value()

    if not password_hash or body.username != expected_username or not verify_password(body.password, password_hash):
        logger.warning("auth_router.login_failed", username=body.username)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Usuario o contraseña incorrectos.")

    role = Role(settings.security.demo_officer_role)
    token = create_access_token(
        subject=body.username,
        role=role,
        secret_key=settings.security.jwt_secret.get_secret_value(),
        expires_minutes=settings.security.jwt_expiry_minutes,
    )
    logger.info("auth_router.login_succeeded", username=body.username, role=role.value)
    return LoginResponse(access_token=token, expires_in_minutes=settings.security.jwt_expiry_minutes)
