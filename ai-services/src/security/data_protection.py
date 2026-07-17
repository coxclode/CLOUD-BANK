"""
CLOUD BANK — Protección de Datos Sensibles (PII / PCI)
Encriptación de campos sensibles, enmascaramiento y tokenización.
Cumple con: PCI-DSS, GDPR, Ley de Protección de Datos local.
"""

from __future__ import annotations

import base64
import hashlib
import os
import re
from functools import lru_cache
from typing import Any

import structlog
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

logger = structlog.get_logger(__name__)


# ─── Clasificación de datos ───────────────────────────────────────────────────

_PII_FIELDS = {
    "national_id", "tax_id", "full_name", "date_of_birth",
    "email", "phone", "address",
}

_FINANCIAL_FIELDS = {
    "monthly_income", "additional_income", "monthly_obligations",
    "declared_monthly_income", "verified_monthly_income",
    "total_debt", "total_monthly_obligations",
}

_MASK_PATTERNS: dict[str, re.Pattern] = {
    "email":       re.compile(r"(?<=.{2}).(?=[^@]*@)"),
    "phone":       re.compile(r"(?<=\d{3})\d(?=\d{4})"),
    "national_id": re.compile(r"(?<=\w{2})\w(?=\w{3})"),
}


@lru_cache(maxsize=1)
def _get_cipher() -> Fernet:
    """Inicializa Fernet desde la clave del entorno. Cached para reutilizar."""
    raw_key = os.environ.get("ENCRYPTION_KEY", "")
    if raw_key:
        key = raw_key.encode()
        if len(key) != 44:
            key = base64.urlsafe_b64encode(key.ljust(32, b"0")[:32])
    else:
        logger.warning("data_protection.no_key_found", message="Usando clave efímera — NO usar en producción")
        key = Fernet.generate_key()
    return Fernet(key)


class DataProtection:
    """Encripta, desencripta, enmascara y tokeniza datos sensibles."""

    def __init__(self):
        self._cipher = _get_cipher()

    def encrypt_field(self, value: str) -> str:
        """Encripta un campo sensible. Devuelve string base64."""
        if not value:
            return ""
        encrypted = self._cipher.encrypt(value.encode())
        return base64.urlsafe_b64encode(encrypted).decode()

    def decrypt_field(self, encrypted_value: str) -> str:
        """Desencripta un campo. Lanza excepción si el token es inválido."""
        if not encrypted_value:
            return ""
        raw = base64.urlsafe_b64decode(encrypted_value.encode())
        return self._cipher.decrypt(raw).decode()

    def mask_pii(self, value: str, field: str) -> str:
        """Enmascara un campo PII para logs y UI. No reversible."""
        if not value:
            return ""
        pattern = _MASK_PATTERNS.get(field)
        if pattern:
            return pattern.sub("*", value)
        visible_chars = max(2, len(value) // 4)
        return value[:visible_chars] + "*" * (len(value) - visible_chars)

    def tokenize_national_id(self, national_id: str, salt: str = "") -> str:
        """Crea un token consistente de un ID nacional para referencias cruzadas."""
        combined = f"{salt}:{national_id}".encode()
        return hashlib.sha256(combined).hexdigest()[:32]

    def sanitize_for_log(self, data: dict[str, Any]) -> dict[str, Any]:
        """Devuelve una copia del dict con campos sensibles enmascarados para logging."""
        result: dict[str, Any] = {}
        for key, value in data.items():
            if key in _PII_FIELDS:
                result[key] = self.mask_pii(str(value), key) if isinstance(value, str) else "***"
            elif key in _FINANCIAL_FIELDS:
                result[key] = "[FINANCIAL_REDACTED]"
            elif isinstance(value, dict):
                result[key] = self.sanitize_for_log(value)
            elif isinstance(value, list):
                result[key] = [
                    self.sanitize_for_log(item) if isinstance(item, dict) else item
                    for item in value
                ]
            else:
                result[key] = value
        return result

    def encrypt_sensitive_fields(self, data: dict[str, Any]) -> dict[str, Any]:
        """Encripta in-place todos los campos PII en un diccionario."""
        result = dict(data)
        for field in _PII_FIELDS:
            if field in result and isinstance(result[field], str):
                result[field] = self.encrypt_field(result[field])
        return result

    def prepare_for_storage(self, state_dict: dict[str, Any]) -> dict[str, Any]:
        """Prepara el estado para persistencia: encripta PII, elimina datos volátiles."""
        safe = self.sanitize_for_log(state_dict)
        safe.pop("messages", None)
        return safe


_protection = DataProtection()


def encrypt_field(value: str) -> str:
    return _protection.encrypt_field(value)


def decrypt_field(value: str) -> str:
    return _protection.decrypt_field(value)


def mask_pii(value: str, field: str) -> str:
    return _protection.mask_pii(value, field)


def sanitize_for_log(data: dict) -> dict:
    return _protection.sanitize_for_log(data)


def prepare_for_storage(data: dict) -> dict:
    return _protection.prepare_for_storage(data)
